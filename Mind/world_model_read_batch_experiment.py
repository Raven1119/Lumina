"""W5: bounded read-only multi-tool batches over the frozen W4 protocol."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from Mind import world_model_revision_experiment as w2
from Mind import world_model_native_tool_experiment as w4
from Mind.world_model_builder_experiment import WorldModelRevisionRequest, _validate_model_source
from Mind.world_model_native_tool_experiment import (
    NATIVE_TOOL_SPECS,
    NativeToolModel,
    W4_BUILDER_SYSTEM_PROMPT,
    _canonical_action,
    _tool_input_matches_schema,
    _visible_chars,
)
from Mind.world_model_revision_experiment import (
    EpisodeStatus,
    RevisionBounds,
    RevisionEpisodeRequest,
    RevisionEpisodeResult,
)


MAX_READ_BATCH = 3
W5_RESULT_PATH = Path(__file__).parent / "fixtures" / "w5" / "real_campaign_result.json"
_W4_CARDINALITY = """Choose exactly one tool per turn. Natural-language explanation may accompany a tool call;
the structured tool call alone selects the authority-bearing action.
"""
_W5_CARDINALITY = """Choose one tool per turn. A response may instead contain two or three read_file calls;
no other multi-tool response is valid. Natural-language explanation may accompany tool calls;
the structured tool calls alone select the authority-bearing actions.
"""
W5_BUILDER_SYSTEM_PROMPT = W4_BUILDER_SYSTEM_PROMPT.replace(
    _W4_CARDINALITY,
    _W5_CARDINALITY,
)
if W5_BUILDER_SYSTEM_PROMPT == W4_BUILDER_SYSTEM_PROMPT:
    raise RuntimeError("W5 cardinality migration did not match the frozen W4 prompt")

W5_SINGLE_VARIABLE = (
    "allow one provider response to contain two or three independently bounded "
    "read_file calls while retaining W4 semantics for every single tool call"
)


@dataclass(frozen=True)
class NativeActionCall:
    call_id: str
    name: str
    arguments: dict[str, object]
    action: dict[str, object]


@dataclass(frozen=True)
class NativeActionEnvelope:
    calls: tuple[NativeActionCall, ...]
    rejection_reason: str | None = None


class BoundedReadBatchBuilder:
    """Own one explicit native transcript and return one validated action envelope."""

    def __init__(self, model: NativeToolModel, bounds: RevisionBounds) -> None:
        self._model = model
        self.bounds = bounds
        self._history: list[dict[str, object]] = []
        self._pending_results: tuple[tuple[str, dict[str, object]], ...] = ()
        self.context_bound_exhausted = False
        self.turns: list[dict[str, object]] = []

    def start_episode(self) -> None:
        self._history.clear()
        self._pending_results = ()
        self.context_bound_exhausted = False
        self.turns.clear()

    def finish_episode(self) -> None:
        self._history.clear()
        self._pending_results = ()

    def observe(self, results: tuple[tuple[str, dict[str, object]], ...]) -> None:
        self._pending_results = copy.deepcopy(results)

    def next_actions(self, context: str) -> NativeActionEnvelope:
        if not self._pending_results:
            content: list[dict[str, object]] = [{"type": "text", "text": context}]
        elif len(self._pending_results) == 1:
            content = [{
                "type": "tool_result",
                "tool_use_id": self._pending_results[0][0],
                "content": [{"type": "text", "text": context}],
            }]
        else:
            content = [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": [{"type": "text", "text": w2._canonical_json(observation)}],
                }
                for call_id, observation in self._pending_results
            ]
            content.append({"type": "text", "text": context})
        self._pending_results = ()
        self._history.append({"role": "user", "content": content})
        visible_chars = _visible_chars(self._history)
        if visible_chars > self.bounds.max_context_chars:
            self.context_bound_exhausted = True
            raise _ContextBound

        attempted_request = {
            "messages": copy.deepcopy(self._history),
            "system": W5_BUILDER_SYSTEM_PROMPT,
            "tools": copy.deepcopy(list(NATIVE_TOOL_SPECS)),
        }
        try:
            reply = self._model.complete(
                copy.deepcopy(self._history),
                system_prompt=W5_BUILDER_SYSTEM_PROMPT,
                tools=NATIVE_TOOL_SPECS,
            )
        except Exception:
            self.turns.append({
                "turn": len(self.turns) + 1,
                "history_chars": visible_chars,
                "provider_request": None,
                "attempted_request": attempted_request,
                "provider_response": None,
                "provider_failed": True,
                "assistant_text_present": False,
                "native_tool_call_count": 0,
                "calls": [],
                "envelope_kind": "provider_failed",
                "envelope_valid": False,
                "rejection_reason": "model_failed",
                "tool_result_count": sum(
                    block.get("type") == "tool_result" for block in content
                ),
            })
            raise
        assistant = {"role": "assistant", "content": copy.deepcopy(list(reply.content))}
        self._history.append(assistant)
        calls = [block for block in reply.content if block.get("type") == "tool_use"]
        call_records: list[dict[str, object]] = []
        parsed: list[NativeActionCall] = []
        for call in calls:
            call_id = call["id"]
            name = call["name"]
            arguments = call["input"]
            assert isinstance(call_id, str) and isinstance(name, str) and isinstance(arguments, dict)
            action = _canonical_action(name, arguments, self.bounds)
            call_records.append({
                "tool_call_id": call_id,
                "tool_name": name,
                "tool_arguments": copy.deepcopy(arguments),
                "schema_valid": _tool_input_matches_schema(name, arguments, self.bounds),
                "host_valid": action is not None,
            })
            if action is not None:
                parsed.append(NativeActionCall(call_id, name, copy.deepcopy(arguments), action))

        rejection = _envelope_rejection(calls, call_records, assistant, self.bounds)
        record = {
            "turn": len(self.turns) + 1,
            "history_chars": visible_chars,
            "provider_request": reply.request_body,
            "attempted_request": None,
            "provider_response": reply.response_body,
            "provider_failed": False,
            "assistant_text_present": any(
                block.get("type") == "text" and bool(block.get("text"))
                for block in reply.content
            ),
            "native_tool_call_count": len(calls),
            "calls": call_records,
            "envelope_kind": (
                "single" if len(calls) == 1
                else "multi_read" if rejection is None
                else "rejected"
            ),
            "envelope_valid": rejection is None,
            "rejection_reason": rejection,
            "tool_result_count": sum(
                block.get("type") == "tool_result" for block in content
            ),
        }
        self.turns.append(record)
        if rejection is not None:
            return NativeActionEnvelope((), rejection)
        return NativeActionEnvelope(tuple(parsed))


class _ContextBound(RuntimeError):
    pass


def _envelope_rejection(
    calls: list[dict[str, object]],
    call_records: list[dict[str, object]],
    assistant: dict[str, object],
    bounds: RevisionBounds,
) -> str | None:
    if not calls or _visible_chars([assistant]) > bounds.max_builder_output_chars:
        return "invalid_model_action"
    if len({record["tool_call_id"] for record in call_records}) != len(call_records):
        return "invalid_multi_tool_envelope"
    if len(calls) == 1:
        return None if call_records[0]["host_valid"] is True else "invalid_model_action"
    if len(calls) > MAX_READ_BATCH:
        return "invalid_multi_tool_envelope"
    if any(
        record["tool_name"] != "read_file" or record["host_valid"] is not True
        for record in call_records
    ):
        return "invalid_multi_tool_envelope"
    return None


def run_read_batch_revision_episode(
    *,
    builder: BoundedReadBatchBuilder,
    revision: WorldModelRevisionRequest,
    episode_ref: str,
    current_path: str | Path,
    working_path: str | Path,
    notes_path: str | Path,
) -> RevisionEpisodeResult:
    builder.start_episode()
    try:
        return _run_episode(
            builder,
            RevisionEpisodeRequest(revision, episode_ref),
            Path(current_path).resolve(),
            Path(working_path).resolve(),
            Path(notes_path).resolve(),
        )
    finally:
        builder.finish_episode()


def _run_episode(
    builder: BoundedReadBatchBuilder,
    request: RevisionEpisodeRequest,
    current_path: Path,
    working_path: Path,
    notes_path: Path,
) -> RevisionEpisodeResult:
    bounds = builder.bounds
    if not w2._valid_bounds(bounds) or not w2._valid_request(request):
        return w2._terminal(EpisodeStatus.STRUCTURAL_FAILURE, "invalid_request", [], 0, 0, 0, 0.0)
    if (
        len({current_path, working_path, notes_path}) != 3
        or current_path.name != "world_model.py"
        or working_path.name != "world_model.py"
        or notes_path != working_path.parent / "notes" / "world_model.md"
    ):
        return w2._terminal(
            EpisodeStatus.STRUCTURAL_FAILURE,
            "workspace_paths_overlap",
            [], 0, 0, 0, 0.0,
        )
    try:
        current_before = current_path.read_bytes()
        current_source = current_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return w2._terminal(
            EpisodeStatus.STRUCTURAL_FAILURE,
            "current_model_unavailable",
            [], 0, 0, 0, 0.0,
        )
    if current_source != request.revision.current_model_source:
        return w2._terminal(
            EpisodeStatus.STRUCTURAL_FAILURE,
            "current_model_mismatch",
            [], 0, 0, 0, 0.0,
        )
    try:
        _validate_model_source(
            current_source,
            class_name="CanonicalWorldModel",
            expected_version=request.revision.current_model_version,
        )
        initial = w2._verify_source(current_source, request.revision)
        if len(w2._canonical_json(w2._verifier_view(initial))) > bounds.max_verifier_output_chars:
            raise ValueError
        if initial["predictions"] != [
            w2._observation_document(item.expected) for item in request.revision.evidence
        ]:
            raise ValueError
        w2._atomic_write(working_path, current_source)
        if notes_path.exists() and notes_path.stat().st_size > bounds.max_notes_chars:
            raise ValueError
        if not notes_path.exists():
            w2._atomic_write(notes_path, "# World Model Notes\n")
    except (OSError, UnicodeDecodeError, ValueError, subprocess.SubprocessError):
        return w2._terminal(
            EpisodeStatus.STRUCTURAL_FAILURE,
            "invalid_initial_state",
            [], 0, 0, 0, 0.0,
        )

    events: list[dict[str, object]] = []
    w2._append_event(
        events,
        "WORLD_MODEL_REVISION_ACTIVATED",
        {
            "episode_ref": request.episode_ref,
            "evidence_count": len(request.revision.evidence),
            "initial_verifier": w2._verifier_view(initial),
        },
        (),
    )
    observations: list[dict[str, object]] = []
    model_turns = 0
    tool_calls = 0
    semantic_revisions = 0
    accuracy = float(initial["accuracy"])
    latest_verified = initial
    last_structural_error = False

    while model_turns < bounds.max_model_turns:
        try:
            context = w2._builder_context(
                request.revision,
                latest_verified,
                observations,
                bounds,
                model_turns,
                tool_calls,
            )
        except ValueError:
            return w2._terminal(
                EpisodeStatus.STRUCTURAL_FAILURE,
                "context_projection_failed",
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )
        try:
            envelope = builder.next_actions(context)
        except _ContextBound:
            return w2._terminal(
                EpisodeStatus.BUDGET_EXHAUSTED,
                "context_bound_exhausted",
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )
        except Exception:
            return w2._terminal(
                EpisodeStatus.STRUCTURAL_FAILURE,
                "model_failed",
                events, model_turns + 1, tool_calls, semantic_revisions, accuracy,
                provider_failed=True,
            )
        model_turns += 1
        if envelope.rejection_reason is not None:
            return w2._terminal(
                EpisodeStatus.STRUCTURAL_FAILURE,
                envelope.rejection_reason,
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )
        counted_calls = sum(call.action["type"] != "unresolved" for call in envelope.calls)
        if tool_calls + counted_calls > bounds.max_tool_calls:
            return w2._terminal(
                EpisodeStatus.BUDGET_EXHAUSTED,
                "tool_budget_exhausted",
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )

        if len(envelope.calls) > 1:
            model_parent_seq = events[-1]["seq"]
            request_event_seqs: list[int] = []
            for index, call in enumerate(envelope.calls):
                w2._append_event(
                    events,
                    "BUILDER_ACTION_REQUESTED",
                    {
                        "action_type": "read_file",
                        "tool_call_id": call.call_id,
                        "batch_index": index,
                        "batch_size": len(envelope.calls),
                    },
                    (model_parent_seq,),
                )
                request_event_seqs.append(events[-1]["seq"])
            batch_results: list[tuple[str, dict[str, object]]] = []
            for index, call in enumerate(envelope.calls):
                observation = w2._read_resource(
                    call.action,
                    request.revision,
                    working_path,
                    notes_path,
                    bounds,
                )
                assert observation is not None
                tool_calls += 1
                observations.append(observation)
                batch_results.append((call.call_id, observation))
                w2._append_event(
                    events,
                    "BUILDER_TOOL_OBSERVED",
                    {"tool_call_id": call.call_id, **observation},
                    (request_event_seqs[index],),
                )
            builder.observe(tuple(batch_results))
            continue

        call = envelope.calls[0]
        action = call.action
        w2._append_event(
            events,
            "BUILDER_ACTION_REQUESTED",
            {"action_type": action["type"], "tool_call_id": call.call_id},
            (events[-1]["seq"],),
        )
        if action["type"] == "unresolved":
            try:
                w2._atomic_write(notes_path, action["notes"])
            except OSError:
                return w2._terminal(
                    EpisodeStatus.STRUCTURAL_FAILURE,
                    "notes_write_failed",
                    events, model_turns, tool_calls, semantic_revisions, accuracy,
                )
            w2._append_event(
                events,
                "WORLD_MODEL_REVISION_UNRESOLVED",
                {"notes_sha256": w2._sha256(action["notes"].encode("utf-8"))},
                (events[-1]["seq"],),
            )
            return w2._terminal(
                EpisodeStatus.UNRESOLVED,
                None,
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )
        tool_calls += 1
        if action["type"] == "read_file":
            observation = w2._read_resource(
                action,
                request.revision,
                working_path,
                notes_path,
                bounds,
            )
            assert observation is not None
            observations.append(observation)
            builder.observe(((call.call_id, observation),))
            w2._append_event(
                events,
                "BUILDER_TOOL_OBSERVED",
                {"tool_call_id": call.call_id, **observation},
                (events[-1]["seq"],),
            )
            continue

        if action["type"] == "run_python":
            observation = w2._run_bounded_analysis(
                action["code"],
                request.revision.evidence,
                bounds,
            )
            observations.append(observation)
            builder.observe(((call.call_id, observation),))
            w2._append_event(
                events,
                "BUILDER_TOOL_OBSERVED",
                {"tool_call_id": call.call_id, **observation},
                (events[-1]["seq"],),
            )
            continue

        if action["type"] != "write_file":
            return w2._terminal(
                EpisodeStatus.STRUCTURAL_FAILURE,
                "authority_not_available",
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )
        if action["path"] == "notes/world_model.md":
            try:
                w2._atomic_write(notes_path, action["content"])
            except OSError:
                return w2._terminal(
                    EpisodeStatus.STRUCTURAL_FAILURE,
                    "notes_write_failed",
                    events, model_turns, tool_calls, semantic_revisions, accuracy,
                )
            observation = {
                "kind": "write",
                "path": "notes/world_model.md",
                "bytes": len(action["content"].encode("utf-8")),
            }
            observations.append(observation)
            builder.observe(((call.call_id, observation),))
            w2._append_event(
                events,
                "BUILDER_TOOL_OBSERVED",
                {"tool_call_id": call.call_id, **observation},
                (events[-1]["seq"],),
            )
            continue

        source = action["content"]
        try:
            _validate_model_source(
                source,
                class_name="CanonicalWorldModel",
                expected_version=request.revision.current_model_version,
            )
            verified = w2._verify_source(source, request.revision)
        except (OSError, ValueError, subprocess.SubprocessError):
            observation = {
                "kind": "structural_error",
                "message": (
                    "The proposed working source was not written. Keep the current coherent "
                    "representation and revise the source contract."
                ),
            }
            observations.append(observation)
            builder.observe(((call.call_id, observation),))
            w2._append_event(
                events,
                "BUILDER_TOOL_OBSERVED",
                {"tool_call_id": call.call_id, **observation},
                (events[-1]["seq"],),
            )
            last_structural_error = True
            continue

        last_structural_error = False
        semantic_revisions += 1
        try:
            w2._atomic_write(working_path, source)
        except OSError:
            return w2._terminal(
                EpisodeStatus.STRUCTURAL_FAILURE,
                "working_write_failed",
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )
        accuracy = float(verified["accuracy"])
        observation = w2._verifier_view(verified)
        if len(w2._canonical_json(observation)) > bounds.max_verifier_output_chars:
            return w2._terminal(
                EpisodeStatus.STRUCTURAL_FAILURE,
                "verifier_output_exceeded",
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )
        latest_verified = verified
        observations.append(observation)
        builder.observe(((call.call_id, observation),))
        w2._append_event(
            events,
            "WORLD_MODEL_WORKING_REVISION_VERIFIED",
            {
                "tool_call_id": call.call_id,
                "revision_index": semantic_revisions,
                "source_sha256": w2._sha256(source.encode("utf-8")),
                **observation,
            },
            (events[-1]["seq"],),
        )
        if verified["first_divergence"] is None and verified["accuracy"] == 1.0:
            try:
                if current_path.read_bytes() != current_before:
                    raise OSError
                w2._atomic_write(current_path, source)
            except OSError:
                return w2._terminal(
                    EpisodeStatus.STRUCTURAL_FAILURE,
                    "atomic_apply_failed",
                    events, model_turns, tool_calls, semantic_revisions, accuracy,
                )
            w2._append_event(
                events,
                "WORLD_MODEL_REVISION_APPLIED",
                {
                    "revision_index": semantic_revisions,
                    "source_sha256": w2._sha256(source.encode("utf-8")),
                },
                (events[-1]["seq"],),
            )
            return w2._terminal(
                EpisodeStatus.CONSISTENT_ENOUGH,
                None,
                events, model_turns, tool_calls, semantic_revisions, accuracy,
            )

    status = EpisodeStatus.STRUCTURAL_FAILURE if last_structural_error else EpisodeStatus.BUDGET_EXHAUSTED
    return w2._terminal(
        status,
        "working_source_invalid_at_bound" if last_structural_error else None,
        events,
        model_turns,
        tool_calls,
        semantic_revisions,
        accuracy,
    )


@dataclass(frozen=True)
class RegisteredW5Campaign:
    manifest_path: Path
    raw_manifest: bytes
    manifest_sha256: str
    implementation_sha256: str
    prompt_sha256: str
    tools_sha256: str
    fixture_sha256: str
    w4_result_sha256: str
    w4_campaign: w4.RegisteredW4Campaign

    @property
    def bounds(self) -> RevisionBounds:
        return self.w4_campaign.bounds

    @property
    def model_config(self) -> dict[str, object]:
        return self.w4_campaign.model_config


class W5ManifestError(ValueError):
    pass


class W5Blocked(RuntimeError):
    pass


def load_registered_w5(path: str | Path) -> RegisteredW5Campaign:
    target = Path(path).resolve()
    raw = target.read_bytes()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=w2._strict_json_object,
            parse_constant=w2._reject_json_constant,
        )
        if not isinstance(document, dict) or set(document) != {
            "bounds",
            "fixture_sha256",
            "implementation_sha256",
            "max_read_batch",
            "model_config",
            "prompt_sha256",
            "schema",
            "single_variable",
            "tools_sha256",
            "verdict_criteria",
            "w4_implementation_sha256",
            "w4_manifest_sha256",
            "w4_prompt_sha256",
            "w4_result_sha256",
            "w4_tools_sha256",
        }:
            raise ValueError
        if (
            document["schema"] != "world-model-w5-preregistration-v1"
            or document["single_variable"] != W5_SINGLE_VARIABLE
            or document["max_read_batch"] != MAX_READ_BATCH
        ):
            raise ValueError
        w4_path = target.parent.parent / "w4" / "manifest.json"
        w4_raw = w4_path.read_bytes()
        if w2._sha256(w4_raw) != w2._digest(document["w4_manifest_sha256"]):
            raise ValueError
        frozen_w4 = w4.load_registered_w4(w4_path)
        w4_result_path = w4_path.parent / "real_campaign_result.json"
        w4_result_sha256 = w2._digest(document["w4_result_sha256"])
        fixture_sha256 = w2._digest(document["fixture_sha256"])
        if (
            w2._sha256(w4_result_path.read_bytes()) != w4_result_sha256
            or frozen_w4.implementation_sha256
            != w2._digest(document["w4_implementation_sha256"])
            or frozen_w4.prompt_sha256 != w2._digest(document["w4_prompt_sha256"])
            or frozen_w4.tools_sha256 != w2._digest(document["w4_tools_sha256"])
            or frozen_w4.fixture_sha256 != fixture_sha256
            or RevisionBounds(**document["bounds"]) != frozen_w4.bounds
            or document["model_config"] != frozen_w4.model_config
        ):
            raise ValueError
        implementation_sha256 = w2._digest(document["implementation_sha256"])
        prompt_sha256 = w2._digest(document["prompt_sha256"])
        tools_sha256 = w2._digest(document["tools_sha256"])
        if document["verdict_criteria"] != {
            "fail": (
                "evidence mutation, hidden leakage, authority escape, current corruption, "
                "cross-episode leak, context bypass, nondeterministic mutation, or unsupported ambiguity rewrite"
            ),
            "inconclusive": (
                "safe run missing batch continuation, progression, reverse verifier, or ambiguity thresholds"
            ),
            "pass": {
                "ambiguity_native_unresolved": True,
                "context_bound": True,
                "episode_freshness": True,
                "four_native_loop_continuations": True,
                "hidden_isolation": True,
                "legal_multi_read_cardinality_failures": 0,
                "resolvable_native_world_model_writes_min": 2,
                "reverse_post_edit_verifier": True,
                "safety_invariants": True,
                "w4_style_three_read_milestone": True,
            },
        }:
            raise ValueError
    except (KeyError, OSError, RecursionError, TypeError, UnicodeDecodeError, ValueError):
        raise W5ManifestError("invalid_w5_manifest") from None
    campaign = RegisteredW5Campaign(
        manifest_path=target,
        raw_manifest=raw,
        manifest_sha256=w2._sha256(raw),
        implementation_sha256=implementation_sha256,
        prompt_sha256=prompt_sha256,
        tools_sha256=tools_sha256,
        fixture_sha256=fixture_sha256,
        w4_result_sha256=w4_result_sha256,
        w4_campaign=frozen_w4,
    )
    _assert_campaign_frozen(campaign)
    return campaign


class _CampaignModel:
    def __init__(self, delegate: NativeToolModel, campaign: RegisteredW5Campaign) -> None:
        self.delegate = delegate
        self.campaign = campaign
        self.provider_failed = False

    def complete(self, messages, *, system_prompt, tools):
        _assert_campaign_frozen(self.campaign)
        try:
            return self.delegate.complete(messages, system_prompt=system_prompt, tools=tools)
        except Exception:
            self.provider_failed = True
            raise


def run_registered_campaign(
    campaign: RegisteredW5Campaign,
    *,
    model: NativeToolModel,
    output_path: str | Path,
) -> dict[str, object]:
    _assert_campaign_frozen(campaign)
    output_path = Path(output_path).resolve()
    if output_path.exists():
        raise FileExistsError("W5 result destination already exists")
    records: list[dict[str, object]] = []
    provider_failed = False

    with tempfile.TemporaryDirectory(prefix="lumina-w5-") as temporary:
        root = Path(temporary)
        w2_campaign = campaign.w4_campaign.w3_campaign.w2_campaign
        for source_record in (*w2_campaign.resolvable, w2_campaign.ambiguity):
            _assert_campaign_frozen(campaign)
            record_root = root / w2._sha256(source_record.record_ref.encode("utf-8"))[:12]
            current = record_root / "current" / "world_model.py"
            working = record_root / "working" / "world_model.py"
            notes = record_root / "working" / "notes" / "world_model.md"
            current.parent.mkdir(parents=True)
            current.write_text(source_record.revision.current_model_source, encoding="utf-8")
            current_before = current.read_bytes()
            evidence_before = w2._canonical_json(
                w2._evidence_documents(source_record.revision.evidence)
            )
            capture = _CampaignModel(model, campaign)
            builder = BoundedReadBatchBuilder(capture, campaign.bounds)
            episode = run_read_batch_revision_episode(
                builder=builder,
                revision=source_record.revision,
                episode_ref=f"w5-{source_record.record_ref}",
                current_path=current,
                working_path=working,
                notes_path=notes,
            )
            final_source = current.read_text(encoding="utf-8")
            public = w2._verify_source(final_source, source_record.revision)
            hidden_accuracy = None
            if source_record.hidden_evidence:
                hidden_request = WorldModelRevisionRequest(
                    current_model_version=source_record.revision.current_model_version,
                    current_model_source=final_source,
                    evidence=source_record.hidden_evidence,
                    revision_reason="hidden verdict-only replay",
                )
                hidden_accuracy = w2._verify_source(final_source, hidden_request)["accuracy"]
            turns = _finalize_turns(
                builder.turns,
                episode.events,
                source_record.revision.current_model_source,
                source_record.revision.current_model_version,
            )
            current_coherent = True
            try:
                _validate_model_source(
                    final_source,
                    class_name="CanonicalWorldModel",
                    expected_version=source_record.revision.current_model_version,
                )
            except ValueError:
                current_coherent = False
            records.append({
                "record_ref": source_record.record_ref,
                "status": episode.status.value,
                "termination_reason": episode.failure_reason or episode.status.value,
                "model": campaign.model_config["model"],
                "provider": campaign.model_config["provider"],
                "model_calls": len(turns),
                "tool_steps": episode.tool_calls,
                "semantic_revisions": episode.semantic_revisions,
                "public_accuracy": public["accuracy"],
                "hidden_accuracy": hidden_accuracy,
                "current_changed": current.read_bytes() != current_before,
                "current_coherent": current_coherent,
                "current_source": final_source,
                "working_source": working.read_text(encoding="utf-8") if working.exists() else "",
                "notes": notes.read_text(encoding="utf-8") if notes.exists() else "",
                "evidence_unchanged": evidence_before == w2._canonical_json(
                    w2._evidence_documents(source_record.revision.evidence)
                ),
                "hidden_isolated": _hidden_evidence_isolated(
                    source_record.revision.evidence,
                    source_record.hidden_evidence,
                    turns,
                ),
                "provider_failed": capture.provider_failed,
                "metrics": _record_metrics(turns, episode.events),
                "turns": turns,
                "events": list(episode.events),
            })
            if capture.provider_failed:
                provider_failed = True
                break

    summary = _campaign_summary(records, provider_failed, campaign.bounds)
    result: dict[str, object] = {
        "schema": "world-model-w5-result-v1",
        "single_variable": W5_SINGLE_VARIABLE,
        "manifest_sha256": campaign.manifest_sha256,
        "implementation_sha256": campaign.implementation_sha256,
        "prompt_sha256": campaign.prompt_sha256,
        "tools_sha256": campaign.tools_sha256,
        "fixture_sha256": campaign.fixture_sha256,
        "w4_result_sha256": campaign.w4_result_sha256,
        "model_config": campaign.model_config,
        "bounds": asdict(campaign.bounds),
        "max_read_batch": MAX_READ_BATCH,
        "system_prompt": W5_BUILDER_SYSTEM_PROMPT,
        "tool_specs": list(NATIVE_TOOL_SPECS),
        "records": records,
        "summary": summary,
        "verdict": _campaign_verdict(records, summary),
    }
    _assert_campaign_frozen(campaign)
    w2._write_json(output_path, result)
    _assert_campaign_frozen(campaign)
    return result


def _finalize_turns(
    source_turns: list[dict[str, object]],
    events: tuple[dict[str, object], ...],
    current_source: str,
    expected_version: str,
) -> list[dict[str, object]]:
    turns = copy.deepcopy(source_turns)
    observed_ids = [
        event["payload"].get("tool_call_id")
        for event in events
        if event.get("event_type") in {
            "BUILDER_TOOL_OBSERVED",
            "WORLD_MODEL_WORKING_REVISION_VERIFIED",
        }
        and isinstance(event.get("payload"), dict)
        and isinstance(event["payload"].get("tool_call_id"), str)
    ]
    verified_ids = {
        event["payload"].get("tool_call_id")
        for event in events
        if event.get("event_type") == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
        and isinstance(event.get("payload"), dict)
    }
    verified_order = [
        event["payload"].get("tool_call_id")
        for event in events
        if event.get("event_type") == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
        and isinstance(event.get("payload"), dict)
        and isinstance(event["payload"].get("tool_call_id"), str)
    ]
    terminal_without_result_ids = (
        {verified_order[-1]}
        if verified_order
        and any(event.get("event_type") == "WORLD_MODEL_REVISION_APPLIED" for event in events)
        else set()
    )
    for index, turn in enumerate(turns):
        call_ids = [call["tool_call_id"] for call in turn["calls"]]
        turn["executed_tool_call_ids"] = [call_id for call_id in observed_ids if call_id in call_ids]
        turn["result_required_tool_call_ids"] = [
            call_id
            for call_id in turn["executed_tool_call_ids"]
            if call_id not in terminal_without_result_ids
        ]
        next_turn = turns[index + 1] if index + 1 < len(turns) else None
        next_result_ids = _request_tool_result_ids(
            _turn_request(next_turn) if next_turn is not None else None
        )
        turn["next_tool_result_ids"] = next_result_ids
        turn["pairing_incomplete"] = bool(
            turn["result_required_tool_call_ids"]
            and (
                next_turn is None
                or next_turn["provider_failed"] is True
            )
        )
        turn["pairing_integrity"] = (
            turn["result_required_tool_call_ids"] == next_result_ids
            if turn["result_required_tool_call_ids"]
            and next_turn is not None
            and not turn["pairing_incomplete"]
            else None
        )
        for call in turn["calls"]:
            is_world_write = (
                call["tool_name"] == "write_file"
                and isinstance(call["tool_arguments"], dict)
                and call["tool_arguments"].get("path") == "world_model.py"
            )
            call["source_contract_accepted"] = (
                call["tool_call_id"] in verified_ids if is_world_write else None
            )
            call["source_proposal_predict_body_changed"] = (
                w4._canonical_predict_changed(
                    call["tool_arguments"]["content"],
                    current_source,
                    expected_version,
                )
                if is_world_write
                and isinstance(call["tool_arguments"].get("content"), str)
                else None
            )
            call["source_proposal_semantic_review_required"] = True if is_world_write else None
    return turns


def _turn_request(turn: dict[str, object]) -> object:
    return turn["provider_request"] or turn["attempted_request"]


def _hidden_evidence_isolated(
    public_evidence: tuple[object, ...],
    hidden_evidence: tuple[object, ...],
    turns: list[dict[str, object]],
) -> bool:
    if not hidden_evidence:
        return True
    visible_surfaces = {
        w2._canonical_json(value)
        for turn in turns
        for value in _nested_compound_values(_turn_request(turn))
    }
    public_surfaces = {
        w2._canonical_json(surface)
        for document in w2._evidence_documents(public_evidence)
        for surface in _evidence_compound_surfaces(document)
    }
    for document in w2._evidence_documents(hidden_evidence):
        hidden_surfaces = {
            w2._canonical_json(surface)
            for surface in _evidence_compound_surfaces(document)
        } - public_surfaces
        if hidden_surfaces & visible_surfaces:
            return False
    return True


def _evidence_compound_surfaces(document: dict[str, object]) -> tuple[object, ...]:
    return (
        document,
        document["state"],
        document["action"],
        document["expected"],
        document["observed"],
    )


def _nested_compound_values(value: object):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _nested_compound_values(nested)
    elif isinstance(value, list):
        yield value
        for nested in value:
            yield from _nested_compound_values(nested)
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (RecursionError, TypeError, ValueError):
            return
        if parsed != value:
            yield from _nested_compound_values(parsed)


def _request_tool_result_ids(request: object) -> list[str]:
    if not isinstance(request, dict):
        return []
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return []
    content = messages[-1].get("content")
    if not isinstance(content, list):
        return []
    return [
        block["tool_use_id"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_result"
        and isinstance(block.get("tool_use_id"), str)
    ]


def _record_metrics(
    turns: list[dict[str, object]],
    events: tuple[dict[str, object], ...],
) -> dict[str, object]:
    multi = [turn for turn in turns if turn["native_tool_call_count"] > 1]
    multi_reads = [turn for turn in multi if turn["envelope_kind"] == "multi_read"]
    writes = [
        call
        for turn in turns
        for call in turn["calls"]
        if call["tool_name"] == "write_file"
        and isinstance(call["tool_arguments"], dict)
        and call["tool_arguments"].get("path") == "world_model.py"
        and call["host_valid"] is True
    ]
    accepted_turns = [
        turn["turn"]
        for turn in turns
        if any(call["source_contract_accepted"] is True for call in turn["calls"])
    ]
    result_turns = [turn["turn"] for turn in turns if turn["tool_result_count"] > 0]
    legal_read_rejections = sum(
        turn["envelope_kind"] == "rejected"
        and 2 <= turn["native_tool_call_count"] <= MAX_READ_BATCH
        and turn["calls"]
        and all(
            call["tool_name"] == "read_file" and call["host_valid"] is True
            for call in turn["calls"]
        )
        for turn in turns
    )
    return {
        "multi_tool_envelopes": len(multi),
        "multi_read_envelopes_accepted": len(multi_reads),
        "multi_read_calls_executed": sum(len(turn["executed_tool_call_ids"]) for turn in multi_reads),
        "max_calls_in_one_response": max(
            (turn["native_tool_call_count"] for turn in turns),
            default=0,
        ),
        "mixed_or_mutating_multi_call_rejections": sum(
            turn["envelope_kind"] == "rejected"
            and any(call["tool_name"] != "read_file" for call in turn["calls"])
            for turn in multi
        ),
        "legal_multi_read_cardinality_failures": legal_read_rejections,
        "tool_use_count": sum(turn["native_tool_call_count"] for turn in turns),
        "tool_result_count": sum(turn["tool_result_count"] for turn in turns),
        "pairing_integrity": (
            all(turn["pairing_integrity"] is not False for turn in turns)
            and not any(turn["pairing_incomplete"] is True for turn in turns)
        ),
        "first_real_tool_result_turn": result_turns[0] if result_turns else None,
        "three_read_continuation": any(
            turn["native_tool_call_count"] == 3
            and len(turn["executed_tool_call_ids"]) == 3
            and turn["pairing_integrity"] is True
            for turn in turns
        ),
        "first_write_attempt_turn": next(
            (
                turn["turn"]
                for turn in turns
                if any(call in writes for call in turn["calls"])
            ),
            None,
        ),
        "valid_native_world_model_write_count": len(writes),
        "first_accepted_semantic_revision_turn": accepted_turns[0] if accepted_turns else None,
        "accepted_semantic_revision_count": sum(
            event.get("event_type") == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
            for event in events
        ),
        "post_edit_verifier_count": sum(
            event.get("event_type") == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
            for event in events
        ),
        "history_size_per_turn": [turn["history_chars"] for turn in turns],
    }


def _campaign_summary(
    records: list[dict[str, object]],
    provider_failed: bool,
    bounds: RevisionBounds,
) -> dict[str, object]:
    resolvable = records[:3]
    ambiguity = records[3] if len(records) > 3 else None
    metrics = [record["metrics"] for record in records]
    authority_isolated = tuple(tool["name"] for tool in NATIVE_TOOL_SPECS) == (
        "read_file",
        "run_python",
        "write_file",
        "unresolved",
    )
    safety = (
        len(records) == 4
        and all(record["evidence_unchanged"] is True for record in records)
        and all(record["current_coherent"] is True for record in records)
        and authority_isolated
    )
    freshness = all(
        record["turns"]
        and len(_turn_request(record["turns"][0])["messages"]) == 1
        and record["turns"][0]["tool_result_count"] == 0
        for record in records
    )
    context_bound = all(
        all(turn["history_chars"] <= bounds.max_context_chars for turn in record["turns"])
        for record in records
    )
    return {
        "safety_invariants_pass": safety,
        "authority_isolation_pass": authority_isolated,
        "episode_freshness_pass": freshness,
        "context_bound_pass": context_bound,
        "hidden_isolation_pass": all(record["hidden_isolated"] is True for record in records),
        "multi_tool_envelopes": sum(metric["multi_tool_envelopes"] for metric in metrics),
        "multi_read_envelopes_accepted": sum(
            metric["multi_read_envelopes_accepted"] for metric in metrics
        ),
        "multi_read_calls_executed": sum(
            metric["multi_read_calls_executed"] for metric in metrics
        ),
        "max_calls_in_one_response": max(
            (metric["max_calls_in_one_response"] for metric in metrics),
            default=0,
        ),
        "mixed_or_mutating_multi_call_rejections": sum(
            metric["mixed_or_mutating_multi_call_rejections"] for metric in metrics
        ),
        "legal_multi_read_cardinality_failures": sum(
            metric["legal_multi_read_cardinality_failures"] for metric in metrics
        ),
        "tool_use_count": sum(metric["tool_use_count"] for metric in metrics),
        "tool_result_count": sum(metric["tool_result_count"] for metric in metrics),
        "pairing_integrity_pass": all(metric["pairing_integrity"] is True for metric in metrics),
        "w4_style_three_read_milestone": any(
            metric["three_read_continuation"] is True for metric in metrics
        ),
        "native_loop_continuation_records": sum(
            metric["first_real_tool_result_turn"] is not None for metric in metrics
        ),
        "resolvable_native_world_model_writes": sum(
            record["metrics"]["valid_native_world_model_write_count"] > 0
            for record in resolvable
        ),
        "accepted_revision_count": sum(record["semantic_revisions"] > 0 for record in resolvable),
        "reverse_post_edit_verifier": bool(
            len(resolvable) >= 2
            and resolvable[1]["metrics"]["post_edit_verifier_count"] > 0
        ),
        "ambiguity_native_unresolved": bool(
            ambiguity
            and ambiguity["status"] == EpisodeStatus.UNRESOLVED.value
            and ambiguity["current_changed"] is False
            and any(
                call["tool_name"] == "unresolved" and call["host_valid"] is True
                for turn in ambiguity["turns"]
                for call in turn["calls"]
            )
        ),
        "provider_failed": provider_failed,
    }


def _campaign_verdict(
    records: list[dict[str, object]],
    summary: dict[str, object],
) -> str:
    ambiguity = records[3] if len(records) > 3 else None
    if records and (
        any(record["evidence_unchanged"] is not True for record in records)
        or any(record["current_coherent"] is not True for record in records)
        or summary["hidden_isolation_pass"] is not True
        or summary["authority_isolation_pass"] is not True
        or summary["context_bound_pass"] is not True
        or summary["episode_freshness_pass"] is not True
        or bool(ambiguity and ambiguity["current_changed"] is True)
    ):
        return "W5_FAIL"
    if summary["provider_failed"]:
        return "W5_INCONCLUSIVE"
    required = {
        "safety_invariants_pass": True,
        "episode_freshness_pass": True,
        "context_bound_pass": True,
        "hidden_isolation_pass": True,
        "pairing_integrity_pass": True,
        "w4_style_three_read_milestone": True,
        "native_loop_continuation_records": 4,
        "legal_multi_read_cardinality_failures": 0,
        "reverse_post_edit_verifier": True,
        "ambiguity_native_unresolved": True,
    }
    if (
        all(summary[key] == value for key, value in required.items())
        and summary["resolvable_native_world_model_writes"] >= 2
    ):
        return "W5_PASS"
    return "W5_INCONCLUSIVE"


def _assert_campaign_frozen(campaign: RegisteredW5Campaign) -> None:
    w4_result = campaign.manifest_path.parent.parent / "w4" / "real_campaign_result.json"
    if (
        campaign.manifest_path.read_bytes() != campaign.raw_manifest
        or w2._sha256(Path(__file__).read_bytes()) != campaign.implementation_sha256
        or w2._sha256(W5_BUILDER_SYSTEM_PROMPT.encode("utf-8")) != campaign.prompt_sha256
        or w2._sha256(w2._canonical_json(list(NATIVE_TOOL_SPECS)).encode("utf-8"))
        != campaign.tools_sha256
        or w2._sha256(w4_result.read_bytes()) != campaign.w4_result_sha256
        or campaign.w4_campaign
        != w4.load_registered_w4(campaign.w4_campaign.manifest_path)
    ):
        raise RuntimeError("W5 preregistration changed during campaign")


@contextmanager
def _real_model_environment(campaign: RegisteredW5Campaign):
    if campaign.model_config.get("provider") != "deepseek-anthropic":
        raise W5Blocked("W5_BLOCKED:real_model_configuration")
    with w4._real_model_environment(campaign.w4_campaign) as client:
        yield client


def run_registered_w5(output_path: str | Path = W5_RESULT_PATH) -> dict[str, object]:
    target = Path(output_path).resolve()
    if target != W5_RESULT_PATH.resolve():
        raise W5Blocked("W5_BLOCKED:canonical_result_path_required")
    if target.exists():
        raise FileExistsError("W5 result destination already exists")
    campaign = load_registered_w5(Path(__file__).parent / "fixtures" / "w5" / "manifest.json")
    with _real_model_environment(campaign) as model:
        return run_registered_campaign(campaign, model=model, output_path=target)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen World Model W5")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        result = run_registered_w5(arguments.output)
    except W5Blocked as exc:
        print(str(exc))
        return 2
    print(w2._canonical_json({"summary": result["summary"], "verdict": result["verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
