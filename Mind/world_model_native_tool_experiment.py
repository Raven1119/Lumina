"""W4: provider-native Builder tool protocol over the frozen W3 episode."""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

import httpx

from Mind.world_model_builder_experiment import WorldModelRevisionRequest
from Mind import world_model_revision_experiment as w2
from Mind import world_model_stateful_revision_experiment as w3
from Mind.world_model_revision_experiment import (
    BUILDER_SYSTEM_PROMPT,
    DEFAULT_BOUNDS,
    EpisodeStatus,
    RevisionBounds,
    RevisionEpisodeRequest,
    RevisionEpisodeResult,
    run_revision_episode,
)


_TEXT_PROTOCOL = '''Return exactly one JSON object and no Markdown. Choose one action:
{"type":"read_file","path":"world_model.py"}
{"type":"read_file","path":"notes/world_model.md"}
{"type":"read_file","path":"evidence.json","start":0,"count":2}
{"type":"run_python","code":"<bounded analysis over the preloaded evidence variable>"}
{"type":"write_file","path":"notes/world_model.md","content":"<bounded notes>"}
{"type":"write_file","path":"world_model.py","content":"<complete Python source>"}
{"type":"unresolved","notes":"<competing hypotheses and a discriminating observation>"}

'''
_NATIVE_PROTOCOL = '''Use the provided tools for inspection, revision, or unresolved termination.
Choose exactly one tool per turn. Natural-language explanation may accompany a tool call;
the structured tool call alone selects the authority-bearing action.

'''
W4_BUILDER_SYSTEM_PROMPT = BUILDER_SYSTEM_PROMPT.replace(
    _TEXT_PROTOCOL,
    _NATIVE_PROTOCOL,
)
if W4_BUILDER_SYSTEM_PROMPT == BUILDER_SYSTEM_PROMPT:
    raise RuntimeError("W4 protocol migration did not match the frozen W3 prompt")

W4_SINGLE_VARIABLE = (
    "replace the W3 plain-text JSON action envelope with the DeepSeek "
    "Anthropic-compatible native tool_use/tool_result protocol"
)


NATIVE_TOOL_SPECS = (
    {
        "name": "read_file",
        "description": (
            "Read one bounded logical Builder resource. evidence.json requires "
            "start and count; count cannot exceed 2."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "enum": [
                        "world_model.py",
                        "notes/world_model.md",
                        "evidence.json",
                    ],
                },
                "start": {"type": "integer", "minimum": 0},
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": DEFAULT_BOUNDS.max_evidence_items_per_read,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_python",
        "description": (
            "Run the existing restricted, fresh analysis surface over the "
            "preloaded public evidence variable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": DEFAULT_BOUNDS.max_python_source_chars,
                }
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write one isolated Builder file. A world_model.py write is "
            "validated and deterministically verified before atomic apply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "enum": ["world_model.py", "notes/world_model.md"],
                },
                "content": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": DEFAULT_BOUNDS.max_model_source_chars,
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "unresolved",
        "description": (
            "End the episode without changing the current model when public "
            "evidence cannot distinguish competing dynamics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": DEFAULT_BOUNDS.max_notes_chars,
                }
            },
            "required": ["notes"],
            "additionalProperties": False,
        },
    },
)


@dataclass(frozen=True)
class NativeProviderTurn:
    request_body: dict[str, object]
    response_body: dict[str, object]
    content: tuple[dict[str, object], ...]


class NativeToolModel(Protocol):
    def complete(
        self,
        messages: list[dict[str, object]],
        *,
        system_prompt: str,
        tools: tuple[dict[str, object], ...],
    ) -> NativeProviderTurn:
        ...


class NativeToolModelError(RuntimeError):
    pass


class DeepSeekAnthropicNativeToolClient:
    """Experiment-only synchronous Anthropic Messages tool adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
        transport: Callable[[dict[str, object]], object] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._http_client = httpx.Client(timeout=timeout) if transport is None else None
        self._transport = transport or self._post

    def complete(
        self,
        messages: list[dict[str, object]],
        *,
        system_prompt: str,
        tools: tuple[dict[str, object], ...],
    ) -> NativeProviderTurn:
        body: dict[str, object] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "thinking": {"type": "disabled"},
            "system": system_prompt,
            "messages": copy.deepcopy(messages),
            "tools": copy.deepcopy(list(tools)),
        }
        try:
            raw = self._transport(body)
        except NativeToolModelError:
            raise
        except Exception:
            raise NativeToolModelError("provider_request_failed") from None
        if not isinstance(raw, Mapping) or not isinstance(raw.get("content"), list):
            raise NativeToolModelError("provider_response_invalid")
        content: list[dict[str, object]] = []
        for block in raw["content"]:
            if not isinstance(block, Mapping):
                raise NativeToolModelError("provider_response_invalid")
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                content.append(dict(block))
                continue
            if (
                kind == "tool_use"
                and isinstance(block.get("id"), str)
                and bool(block["id"])
                and isinstance(block.get("name"), str)
                and isinstance(block.get("input"), Mapping)
            ):
                content.append({
                    "type": "tool_use",
                    "id": block["id"],
                    "name": block["name"],
                    "input": dict(block["input"]),
                })
                continue
            raise NativeToolModelError("provider_response_invalid")
        if not content:
            raise NativeToolModelError("provider_response_invalid")
        return NativeProviderTurn(body, copy.deepcopy(dict(raw)), tuple(content))

    def _post(self, body: dict[str, object]) -> object:
        assert self._http_client is not None
        try:
            response = self._http_client.post(
                f"{self._base_url}/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "x-api-key": self._api_key,
                },
                json=body,
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            raise NativeToolModelError("provider_request_failed") from None

    def close(self) -> None:
        if self._http_client is not None:
            self._http_client.close()


class NativeWorldModelRevisionBuilder:
    """Translate real native calls to the frozen W2 canonical executor seam."""

    def __init__(self, model: NativeToolModel, bounds: RevisionBounds) -> None:
        self._model = model
        self.bounds = bounds
        self._history: list[dict[str, object]] = []
        self._pending_tool_call_id: str | None = None
        self._context_bound_exhausted = False
        self.turns: list[dict[str, object]] = []

    def start_episode(self) -> None:
        self._history.clear()
        self._pending_tool_call_id = None
        self._context_bound_exhausted = False
        self.turns.clear()

    def finish_episode(self) -> None:
        self._history.clear()
        self._pending_tool_call_id = None

    def next_action(self, context: str) -> str:
        if self._pending_tool_call_id is None:
            message = {
                "role": "user",
                "content": [{"type": "text", "text": context}],
            }
        else:
            message = {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": self._pending_tool_call_id,
                    "content": [{"type": "text", "text": context}],
                }],
            }
        self._history.append(message)
        visible_chars = _visible_chars(self._history)
        if visible_chars > self.bounds.max_context_chars:
            self._context_bound_exhausted = True
            raise _EpisodeContextBound
        reply = self._model.complete(
            copy.deepcopy(self._history),
            system_prompt=W4_BUILDER_SYSTEM_PROMPT,
            tools=NATIVE_TOOL_SPECS,
        )
        assistant = {"role": "assistant", "content": copy.deepcopy(list(reply.content))}
        self._history.append(assistant)
        text = "".join(
            str(block["text"])
            for block in reply.content
            if block.get("type") == "text"
        )
        calls = [block for block in reply.content if block.get("type") == "tool_use"]
        record: dict[str, object] = {
            "turn": len(self.turns) + 1,
            "history_chars": visible_chars,
            "provider_request": reply.request_body,
            "provider_response": reply.response_body,
            "assistant_text": text,
            "assistant_text_present": bool(text),
            "native_tool_use_present": bool(calls),
            "native_tool_call_count": len(calls),
            "tool_call_id": None,
            "tool_name": None,
            "tool_arguments": None,
            "schema_valid": False,
            "host_valid": False,
            "tool_result_visible": _last_message_is_tool_result(reply.request_body),
            "invalid_action_envelope": len(calls) != 1,
        }
        self.turns.append(record)
        if len(calls) != 1 or _visible_chars([assistant]) > self.bounds.max_builder_output_chars:
            return ""
        call = calls[0]
        call_id = call["id"]
        name = call["name"]
        arguments = call["input"]
        assert isinstance(call_id, str) and isinstance(name, str) and isinstance(arguments, dict)
        record.update({
            "tool_call_id": call_id,
            "tool_name": name,
            "tool_arguments": copy.deepcopy(arguments),
        })
        action = _canonical_action(name, arguments, self.bounds)
        record["schema_valid"] = _tool_input_matches_schema(
            name,
            arguments,
            self.bounds,
        )
        record["host_valid"] = action is not None
        if action is None:
            return ""
        self._pending_tool_call_id = call_id
        return w2._canonical_json(action)


def run_native_revision_episode(
    *,
    builder: NativeWorldModelRevisionBuilder,
    revision: WorldModelRevisionRequest,
    episode_ref: str,
    current_path: str | Path,
    working_path: str | Path,
    notes_path: str | Path,
) -> RevisionEpisodeResult:
    builder.start_episode()
    try:
        result = run_revision_episode(
            builder=builder,
            request=RevisionEpisodeRequest(revision, episode_ref),
            current_path=current_path,
            working_path=working_path,
            notes_path=notes_path,
        )
        if builder._context_bound_exhausted:
            events = (*result.events, {
                "seq": len(result.events) + 1,
                "event_type": "WORLD_MODEL_REVISION_CONTEXT_BOUND_EXHAUSTED",
                "payload": {"max_context_chars": builder.bounds.max_context_chars},
                "source_event_seqs": [result.events[-1]["seq"]] if result.events else [],
            })
            return replace(
                result,
                status=EpisodeStatus.BUDGET_EXHAUSTED,
                failure_reason="context_bound_exhausted",
                model_turns=max(0, result.model_turns - 1),
                events=events,
                provider_failed=False,
            )
        return result
    finally:
        builder.finish_episode()


class _EpisodeContextBound(RuntimeError):
    pass


def _tool_input_matches_schema(
    name: str,
    arguments: dict[str, object],
    bounds: RevisionBounds,
) -> bool:
    if name == "read_file":
        if not set(arguments) <= {"path", "start", "count"} or "path" not in arguments:
            return False
        if arguments["path"] not in {
            "world_model.py",
            "notes/world_model.md",
            "evidence.json",
        }:
            return False
        if "start" in arguments and (
            type(arguments["start"]) is not int or arguments["start"] < 0
        ):
            return False
        return "count" not in arguments or (
            type(arguments["count"]) is int
            and 1 <= arguments["count"] <= bounds.max_evidence_items_per_read
        )
    if name == "run_python":
        code = arguments.get("code")
        return (
            set(arguments) == {"code"}
            and isinstance(code, str)
            and 1 <= len(code) <= bounds.max_python_source_chars
        )
    if name == "write_file":
        path = arguments.get("path")
        content = arguments.get("content")
        return (
            set(arguments) == {"path", "content"}
            and path in {"world_model.py", "notes/world_model.md"}
            and isinstance(content, str)
            and 1 <= len(content) <= bounds.max_model_source_chars
        )
    if name == "unresolved":
        notes = arguments.get("notes")
        return (
            set(arguments) == {"notes"}
            and isinstance(notes, str)
            and 1 <= len(notes) <= bounds.max_notes_chars
        )
    return False


def _canonical_action(
    name: str,
    arguments: dict[str, object],
    bounds: RevisionBounds,
) -> dict[str, object] | None:
    if name == "read_file":
        path = arguments.get("path")
        if path == "evidence.json":
            if (
                set(arguments) != {"path", "start", "count"}
                or type(arguments.get("start")) is not int
                or type(arguments.get("count")) is not int
                or arguments["start"] < 0
                or not 1 <= arguments["count"] <= bounds.max_evidence_items_per_read
            ):
                return None
        elif path in {"world_model.py", "notes/world_model.md"}:
            if set(arguments) != {"path"}:
                return None
        else:
            return None
        return {"type": name, **arguments}
    if name == "run_python":
        code = arguments.get("code")
        if (
            set(arguments) != {"code"}
            or not isinstance(code, str)
            or not code.strip()
            or len(code) > bounds.max_python_source_chars
        ):
            return None
        return {"type": name, "code": code}
    if name == "write_file":
        path = arguments.get("path")
        content = arguments.get("content")
        limit = bounds.max_model_source_chars if path == "world_model.py" else bounds.max_notes_chars
        if (
            set(arguments) != {"path", "content"}
            or path not in {"world_model.py", "notes/world_model.md"}
            or not isinstance(content, str)
            or not content
            or len(content) > limit
        ):
            return None
        return {"type": name, "path": path, "content": content}
    if name == "unresolved":
        notes = arguments.get("notes")
        if (
            set(arguments) != {"notes"}
            or not isinstance(notes, str)
            or not notes.strip()
            or len(notes) > bounds.max_notes_chars
        ):
            return None
        return {"type": name, "notes": notes}
    return None


def _visible_chars(messages: list[dict[str, object]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                total += len(block["text"])
            elif block.get("type") == "tool_use":
                total += len(str(block.get("id", "")))
                total += len(str(block.get("name", "")))
                total += len(w2._canonical_json(block.get("input", {})))
            elif block.get("type") == "tool_result":
                total += len(str(block.get("tool_use_id", "")))
                nested = block.get("content")
                if isinstance(nested, list):
                    total += _visible_chars([{"content": nested}])
    return total


def _last_message_is_tool_result(body: dict[str, object]) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    content = messages[-1].get("content")
    return bool(
        isinstance(content, list)
        and content
        and isinstance(content[0], dict)
        and content[0].get("type") == "tool_result"
    )


@dataclass(frozen=True)
class RegisteredW4Campaign:
    manifest_path: Path
    raw_manifest: bytes
    manifest_sha256: str
    implementation_sha256: str
    prompt_sha256: str
    tools_sha256: str
    fixture_sha256: str
    w3_campaign: w3.RegisteredW3Campaign

    @property
    def bounds(self) -> RevisionBounds:
        return self.w3_campaign.bounds

    @property
    def model_config(self) -> dict[str, object]:
        return dict(self.w3_campaign.model_config)


class W4ManifestError(ValueError):
    pass


class W4Blocked(RuntimeError):
    pass


def load_registered_w4(path: str | Path) -> RegisteredW4Campaign:
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
            "model_config",
            "prompt_sha256",
            "schema",
            "single_variable",
            "tools_sha256",
            "verdict_criteria",
            "w3_implementation_sha256",
            "w3_manifest_sha256",
            "w3_prompt_sha256",
        }:
            raise ValueError
        if (
            document["schema"] != "world-model-w4-preregistration-v1"
            or document["single_variable"] != W4_SINGLE_VARIABLE
        ):
            raise ValueError
        w3_path = target.parent.parent / "w3" / "manifest.json"
        w3_raw = w3_path.read_bytes()
        w3_manifest_sha256 = w2._digest(document["w3_manifest_sha256"])
        if w2._sha256(w3_raw) != w3_manifest_sha256:
            raise ValueError
        frozen_w3 = w3.load_registered_w3(w3_path)
        fixture_sha256 = w2._digest(document["fixture_sha256"])
        if (
            frozen_w3.implementation_sha256
            != w2._digest(document["w3_implementation_sha256"])
            or frozen_w3.prompt_sha256 != w2._digest(document["w3_prompt_sha256"])
            or frozen_w3.fixture_sha256 != fixture_sha256
            or RevisionBounds(**document["bounds"]) != frozen_w3.bounds
            or document["model_config"] != frozen_w3.model_config
        ):
            raise ValueError
        implementation_sha256 = w2._digest(document["implementation_sha256"])
        prompt_sha256 = w2._digest(document["prompt_sha256"])
        tools_sha256 = w2._digest(document["tools_sha256"])
        if document["verdict_criteria"] != {
            "fail": (
                "evidence mutation, hidden leakage, current corruption, authority escape, "
                "context-bound bypass, unsupported ambiguity rewrite, or prior-episode leakage"
            ),
            "inconclusive": (
                "safe outcome missing native-protocol, ambiguity, write-attempt, or reverse-verifier thresholds"
            ),
            "pass": {
                "ambiguity_native_unresolved": True,
                "context_bound": True,
                "episode_freshness": True,
                "hidden_isolation": True,
                "invalid_action_envelope_count": 0,
                "resolvable_native_world_model_writes_min": 2,
                "reverse_post_edit_verifier": True,
                "safety_invariants": True,
            },
        }:
            raise ValueError
    except (KeyError, OSError, RecursionError, TypeError, UnicodeDecodeError, ValueError):
        raise W4ManifestError("invalid_w4_manifest") from None
    campaign = RegisteredW4Campaign(
        manifest_path=target,
        raw_manifest=raw,
        manifest_sha256=w2._sha256(raw),
        implementation_sha256=implementation_sha256,
        prompt_sha256=prompt_sha256,
        tools_sha256=tools_sha256,
        fixture_sha256=fixture_sha256,
        w3_campaign=frozen_w3,
    )
    _assert_campaign_frozen(campaign)
    return campaign


class _CampaignNativeModel:
    def __init__(self, delegate: NativeToolModel, campaign: RegisteredW4Campaign) -> None:
        self.delegate = delegate
        self.campaign = campaign
        self.provider_failed = False

    def complete(self, messages, *, system_prompt, tools):
        _assert_campaign_frozen(self.campaign)
        try:
            return self.delegate.complete(
                messages,
                system_prompt=system_prompt,
                tools=tools,
            )
        except Exception:
            self.provider_failed = True
            raise


def run_registered_campaign(
    campaign: RegisteredW4Campaign,
    *,
    model: NativeToolModel,
    output_path: str | Path,
) -> dict[str, object]:
    output_path = Path(output_path).resolve()
    if output_path.exists():
        raise FileExistsError("W4 result destination already exists")
    records: list[dict[str, object]] = []
    provider_failed = False

    with tempfile.TemporaryDirectory(prefix="lumina-w4-") as temporary:
        root = Path(temporary)
        w2_campaign = campaign.w3_campaign.w2_campaign
        source_records = (*w2_campaign.resolvable, w2_campaign.ambiguity)
        for record in source_records:
            _assert_campaign_frozen(campaign)
            record_root = root / w2._sha256(record.record_ref.encode("utf-8"))[:12]
            current = record_root / "current" / "world_model.py"
            working = record_root / "working" / "world_model.py"
            notes = record_root / "working" / "notes" / "world_model.md"
            current.parent.mkdir(parents=True)
            current.write_text(record.revision.current_model_source, encoding="utf-8")
            current_before = current.read_bytes()
            evidence_before = w2._canonical_json(
                w2._evidence_documents(record.revision.evidence)
            )
            capture = _CampaignNativeModel(model, campaign)
            builder = NativeWorldModelRevisionBuilder(capture, campaign.bounds)
            episode = run_native_revision_episode(
                builder=builder,
                revision=record.revision,
                episode_ref=f"w4-{record.record_ref}",
                current_path=current,
                working_path=working,
                notes_path=notes,
            )
            final_source = current.read_text(encoding="utf-8")
            public = w2._verify_source(final_source, record.revision)
            hidden_accuracy = None
            if record.hidden_evidence:
                hidden_request = WorldModelRevisionRequest(
                    current_model_version=record.revision.current_model_version,
                    current_model_source=final_source,
                    evidence=record.hidden_evidence,
                    revision_reason="hidden verdict-only replay",
                )
                hidden_accuracy = w2._verify_source(final_source, hidden_request)["accuracy"]
            turns = _turn_records(
                builder.turns,
                episode.events,
                current_source=record.revision.current_model_source,
                expected_version=record.revision.current_model_version,
            )
            request_text = w2._canonical_json([
                turn["provider_request"] for turn in turns
            ])
            hidden_payload = (
                w2._canonical_json(w2._evidence_documents(record.hidden_evidence))
                if record.hidden_evidence
                else ""
            )
            current_coherent = True
            try:
                w2._validate_model_source(
                    final_source,
                    class_name="CanonicalWorldModel",
                    expected_version=record.revision.current_model_version,
                )
            except ValueError:
                current_coherent = False
            records.append({
                "record_ref": record.record_ref,
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
                "working_source": (
                    working.read_text(encoding="utf-8") if working.exists() else ""
                ),
                "notes": notes.read_text(encoding="utf-8") if notes.exists() else "",
                "evidence_unchanged": evidence_before == w2._canonical_json(
                    w2._evidence_documents(record.revision.evidence)
                ),
                "hidden_isolated": not hidden_payload or hidden_payload not in request_text,
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
        "schema": "world-model-w4-result-v1",
        "single_variable": W4_SINGLE_VARIABLE,
        "manifest_sha256": campaign.manifest_sha256,
        "implementation_sha256": campaign.implementation_sha256,
        "prompt_sha256": campaign.prompt_sha256,
        "tools_sha256": campaign.tools_sha256,
        "fixture_sha256": campaign.fixture_sha256,
        "model_config": campaign.model_config,
        "bounds": asdict(campaign.bounds),
        "system_prompt": W4_BUILDER_SYSTEM_PROMPT,
        "tool_specs": list(NATIVE_TOOL_SPECS),
        "records": records,
        "summary": summary,
        "verdict": _campaign_verdict(records, summary),
    }
    _assert_campaign_frozen(campaign)
    w2._write_json(output_path, result)
    _assert_campaign_frozen(campaign)
    return result


def _turn_records(
    native_turns: list[dict[str, object]],
    events: tuple[dict[str, object], ...],
    *,
    current_source: str,
    expected_version: str,
) -> list[dict[str, object]]:
    action_positions = [
        index for index, event in enumerate(events)
        if event.get("event_type") == "BUILDER_ACTION_REQUESTED"
    ]
    turns = copy.deepcopy(native_turns)
    for index, turn in enumerate(turns):
        start = action_positions[index] + 1 if index < len(action_positions) else len(events)
        end = action_positions[index + 1] if index + 1 < len(action_positions) else len(events)
        results = [
            {"event_type": event["event_type"], "payload": event["payload"]}
            for event in events[start:end]
        ]
        turn["tool_result"] = results
        next_turn = turns[index + 1] if index + 1 < len(turns) else None
        turn["tool_result_visible_next_turn"] = (
            bool(next_turn and next_turn["tool_result_visible"])
            if results
            else None
        )
        turn["tool_call_id_paired_next_turn"] = (
            _request_pairs_tool_call(
                next_turn["provider_request"],
                turn["tool_call_id"],
            )
            if results and next_turn is not None and isinstance(turn["tool_call_id"], str)
            else None
        )
        turn["source_contract_accepted"] = (
            any(item["event_type"] == "WORLD_MODEL_WORKING_REVISION_VERIFIED" for item in results)
            if turn["tool_name"] == "write_file"
            and isinstance(turn["tool_arguments"], dict)
            and turn["tool_arguments"].get("path") == "world_model.py"
            else None
        )
        turn["source_proposal_predict_body_changed"] = (
            _canonical_predict_changed(
                turn["tool_arguments"]["content"],
                current_source,
                expected_version,
            )
            if turn["tool_name"] == "write_file"
            and isinstance(turn["tool_arguments"], dict)
            and turn["tool_arguments"].get("path") == "world_model.py"
            and isinstance(turn["tool_arguments"].get("content"), str)
            else None
        )
        turn["source_proposal_semantic_review_required"] = (
            True
            if turn["tool_name"] == "write_file"
            and isinstance(turn["tool_arguments"], dict)
            and turn["tool_arguments"].get("path") == "world_model.py"
            else None
        )
    return turns


def _canonical_predict_changed(
    proposed_source: str,
    current_source: str,
    expected_version: str,
) -> bool:
    """Classify a model-semantic proposal without widening source acceptance."""

    def predict_body(source: str) -> str:
        tree = ast.parse(source, mode="exec")
        body = [
            item for item in tree.body
            if not (
                isinstance(item, ast.Expr)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)
            )
        ]
        if len(body) != 1 or not isinstance(body[0], ast.ClassDef):
            raise ValueError
        class_node = body[0]
        if class_node.name != "CanonicalWorldModel":
            raise ValueError
        version_matches = any(
            isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
            and item.targets[0].id == "version"
            and isinstance(item.value, ast.Constant)
            and item.value.value == expected_version
            for item in class_node.body
        )
        functions = [
            item for item in class_node.body
            if isinstance(item, ast.FunctionDef) and item.name == "predict"
        ]
        if not version_matches or len(functions) != 1:
            raise ValueError
        function = functions[0]
        if (
            function.decorator_list
            or function.args.posonlyargs
            or function.args.vararg is not None
            or function.args.kwonlyargs
            or function.args.kwarg is not None
            or function.args.defaults
            or function.args.kw_defaults
            or [argument.arg for argument in function.args.args]
            != ["self", "state", "action"]
        ):
            raise ValueError
        return ast.dump(ast.Module(body=function.body, type_ignores=[]), include_attributes=False)

    try:
        return predict_body(proposed_source) != predict_body(current_source)
    except (SyntaxError, ValueError):
        return False


def _record_metrics(
    turns: list[dict[str, object]],
    events: tuple[dict[str, object], ...],
) -> dict[str, object]:
    writes = [
        turn for turn in turns
        if turn["tool_name"] == "write_file"
        and isinstance(turn["tool_arguments"], dict)
        and turn["tool_arguments"].get("path") == "world_model.py"
    ]
    valid_writes = [
        turn for turn in writes
        if turn["schema_valid"] is True and turn["host_valid"] is True
    ]
    accepted = [turn for turn in writes if turn["source_contract_accepted"] is True]
    return {
        "native_tool_call_count": sum(
            int(turn.get("native_tool_call_count", turn["native_tool_use_present"]))
            for turn in turns
        ),
        "schema_invalid_tool_calls": sum(
            turn["native_tool_use_present"] is True and turn["schema_valid"] is False
            for turn in turns
        ),
        "host_rejected_tool_calls": sum(
            turn["native_tool_use_present"] is True and turn["host_valid"] is False
            for turn in turns
        ),
        "plain_text_without_tool_count": sum(
            turn["assistant_text_present"] is True
            and turn["native_tool_use_present"] is False
            for turn in turns
        ),
        "mixed_text_plus_tool_count": sum(
            turn["assistant_text_present"] is True
            and turn["native_tool_use_present"] is True
            for turn in turns
        ),
        "invalid_action_envelope_count": sum(
            turn["invalid_action_envelope"] is True for turn in turns
        ),
        "first_write_attempt_turn": writes[0]["turn"] if writes else None,
        "first_accepted_semantic_revision_turn": (
            accepted[0]["turn"] if accepted else None
        ),
        "valid_native_world_model_write_count": len(valid_writes),
        "predict_body_changed_world_model_write_count": sum(
            turn["source_proposal_predict_body_changed"] is True
            for turn in writes
        ),
        "accepted_semantic_revision_count": sum(
            event.get("event_type") == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
            for event in events
        ),
        "post_edit_verifier_count": sum(
            event.get("event_type") == "WORLD_MODEL_WORKING_REVISION_VERIFIED"
            for event in events
        ),
        "history_size_per_turn": [turn["history_chars"] for turn in turns],
        "tool_result_visible_next_turn": [
            turn["tool_result_visible_next_turn"] for turn in turns
        ],
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
        and len(record["turns"][0]["provider_request"]["messages"]) == 1
        and record["turns"][0]["tool_result_visible"] is False
        for record in records
    )
    context_bound = all(
        all(turn["history_chars"] <= bounds.max_context_chars for turn in record["turns"])
        for record in records
    )
    invalid_envelopes = sum(metric["invalid_action_envelope_count"] for metric in metrics)
    ambiguity_native_unresolved = bool(
        ambiguity
        and ambiguity["status"] == EpisodeStatus.UNRESOLVED.value
        and ambiguity["current_changed"] is False
        and any(
            turn["tool_name"] == "unresolved" and turn["host_valid"] is True
            for turn in ambiguity["turns"]
        )
    )
    reverse_verifier = bool(
        len(resolvable) >= 2
        and resolvable[1]["metrics"]["post_edit_verifier_count"] > 0
    )
    return {
        "safety_invariants_pass": safety,
        "authority_isolation_pass": authority_isolated,
        "episode_freshness_pass": freshness,
        "context_bound_pass": context_bound,
        "hidden_isolation_pass": all(
            record["hidden_isolated"] is True for record in records
        ),
        "native_protocol_pass": all(
            turn["provider_request"].get("tools")
            and isinstance(turn["provider_response"].get("content"), list)
            for record in records
            for turn in record["turns"]
        ),
        "tool_call_pairing_pass": all(
            turn["tool_call_id_paired_next_turn"] is not False
            for record in records
            for turn in record["turns"]
        ),
        "native_tool_call_count": sum(metric["native_tool_call_count"] for metric in metrics),
        "schema_invalid_tool_calls": sum(metric["schema_invalid_tool_calls"] for metric in metrics),
        "host_rejected_tool_calls": sum(metric["host_rejected_tool_calls"] for metric in metrics),
        "plain_text_without_tool_count": sum(metric["plain_text_without_tool_count"] for metric in metrics),
        "mixed_text_plus_tool_count": sum(metric["mixed_text_plus_tool_count"] for metric in metrics),
        "invalid_action_envelope_count": invalid_envelopes,
        "resolvable_native_world_model_writes": sum(
            record["metrics"]["valid_native_world_model_write_count"] > 0
            for record in resolvable
        ),
        "accepted_revision_count": sum(
            record["semantic_revisions"] > 0 for record in resolvable
        ),
        "reverse_post_edit_verifier": reverse_verifier,
        "ambiguity_native_unresolved": ambiguity_native_unresolved,
        "provider_failed": provider_failed,
    }


def _campaign_verdict(
    records: list[dict[str, object]],
    summary: dict[str, object],
) -> str:
    ambiguity = records[3] if len(records) > 3 else None
    if (
        records
        and (
            any(record["evidence_unchanged"] is not True for record in records)
            or any(record["current_coherent"] is not True for record in records)
            or summary["hidden_isolation_pass"] is not True
            or summary["context_bound_pass"] is not True
            or summary["episode_freshness_pass"] is not True
            or bool(ambiguity and ambiguity["current_changed"] is True)
        )
    ):
        return "W4_FAIL"
    if summary["provider_failed"]:
        return "W4_INCONCLUSIVE"
    required = {
        "safety_invariants_pass": True,
        "episode_freshness_pass": True,
        "context_bound_pass": True,
        "hidden_isolation_pass": True,
        "native_protocol_pass": True,
        "tool_call_pairing_pass": True,
        "invalid_action_envelope_count": 0,
        "ambiguity_native_unresolved": True,
        "reverse_post_edit_verifier": True,
    }
    if (
        all(summary[key] == value for key, value in required.items())
        and summary["resolvable_native_world_model_writes"] >= 2
    ):
        return "W4_PASS"
    return "W4_INCONCLUSIVE"


def _request_pairs_tool_call(request: object, call_id: str) -> bool:
    if not isinstance(request, dict):
        return False
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    content = messages[-1].get("content")
    return bool(
        isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], dict)
        and content[0].get("type") == "tool_result"
        and content[0].get("tool_use_id") == call_id
    )


def _assert_campaign_frozen(campaign: RegisteredW4Campaign) -> None:
    if (
        campaign.manifest_path.read_bytes() != campaign.raw_manifest
        or w2._sha256(Path(__file__).read_bytes()) != campaign.implementation_sha256
        or w2._sha256(W4_BUILDER_SYSTEM_PROMPT.encode("utf-8")) != campaign.prompt_sha256
        or w2._sha256(w2._canonical_json(list(NATIVE_TOOL_SPECS)).encode("utf-8"))
        != campaign.tools_sha256
    ):
        raise RuntimeError("W4 preregistration changed during campaign")


@contextmanager
def _real_model_environment(campaign: RegisteredW4Campaign):
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    config = campaign.model_config
    if (
        not api_key
        or config != {
            "base_url": "https://api.deepseek.com/anthropic",
            "max_tokens": 1600,
            "model": "deepseek-v4-pro",
            "provider": "deepseek-anthropic",
            "request_timeout_seconds": 45,
            "temperature": 0.0,
            "thinking": "disabled",
        }
    ):
        raise W4Blocked("W4_BLOCKED:real_model_configuration")
    client = DeepSeekAnthropicNativeToolClient(
        api_key=api_key,
        base_url=str(config["base_url"]),
        model=str(config["model"]),
        max_tokens=int(config["max_tokens"]),
        temperature=float(config["temperature"]),
        timeout=float(config["request_timeout_seconds"]),
    )
    try:
        yield client
    finally:
        client.close()


def run_registered_w4(output_path: str | Path) -> dict[str, object]:
    campaign = load_registered_w4(Path(__file__).parent / "fixtures" / "w4" / "manifest.json")
    with _real_model_environment(campaign) as model:
        return run_registered_campaign(campaign, model=model, output_path=output_path)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen World Model W4")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        result = run_registered_w4(arguments.output)
    except W4Blocked as exc:
        print(str(exc))
        return 2
    print(w2._canonical_json({"summary": result["summary"], "verdict": result["verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
