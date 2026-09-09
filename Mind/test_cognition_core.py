"""Current cognition invariants. Scripted models test mechanisms, not judgment quality."""
from dataclasses import replace
import json

import pytest

from Mind.cognition import Cognition, Evidence, MindInput, MindResultEvent
from Mind.model import MindModel
from Mind.task_view import execution_goal
from Mind.trace import MindTrace, MODEL_OUTPUT_RECORDED, CAPABILITY_REQUESTED, ACTIVATION_FINISHED
from Nervous.storage import plain


def step(updates=(), next=None, **extra):
    return {"type": "cognitive_step", "updates": list(updates),
            "next": next or {"type": "no_change"}, **extra}


def response(value, name="cognitive_step"):
    return {"content": [{"type": "tool_use", "id": "call", "name": name, "input": value}],
            "stop_reason": "tool_use"}


class Script:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.wires = []

    def __call__(self, wire):
        self.wires.append(wire)
        assert self.responses, "unexpected additional model call"
        value = self.responses.pop(0)
        return value(wire) if callable(value) else value


def event(identity="event-1", evidence=()):
    return MindInput(identity, "Review the new source.", "goal-1", 0,
                     "Deliver a sourced assessment.", None, None, tuple(evidence))


def belief(identity, claim, source="source", **extra):
    return {"kind": "belief", "id": identity, "claim": claim, "status": "supported",
            "basis": [{"ref": source}], **extra}


def test_selective_revision_preserves_correct_knowledge_and_wrong_history(tmp_path):
    initial = event(evidence=[Evidence("source", "Only subset A was measured; B is unobserved.", "execution")])
    script = Script(response(step([belief("new:wrong", "Both subsets were measured.",
                                         discriminator="All future samples must remain absent."),
                                   belief("new:right", "Subset A was measured.")])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(initial).status == "accepted"
        before = [plain(i) for i in mind.inspect().items]
        old, correct = before
    script = Script(response(step([belief(old["id"], "Subset B is currently unobserved.")],
                                  next={"type": "directive", "text": "Keep the conclusion limited to measured subset A."})))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        result = mind.activate(event("event-2"))
        assert result.status == "accepted" and result.output["type"] == "directive"
        after = [plain(i) for i in mind.inspect().items]
        assert after[1] == correct
        assert after[0]["id"] == old["id"] and "discriminator" not in after[0]
        assert after[0]["claim"] == "Subset B is currently unobserved."
        assert mind.read_source("source")["text"] == initial.evidence[0].text
    historical = json.loads((tmp_path / "cognition.json").read_text())
    commits = [r for r in historical["records"] if r["kind"] == "accepted"]
    assert commits[0]["updates"][0]["claim"] == "Both subsets were measured."
    with Cognition(directory=tmp_path, model=MindModel(Script())) as restarted:
        assert [plain(i) for i in restarted.inspect().items] == after
        assert restarted.activate(event("event-2")).status == "duplicate"


def test_nochange_commits_understanding_without_creating_guidance(tmp_path):
    script = Script(response(step([belief("new:known", "The source is incomplete.")])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        receipt = mind.activate(event(evidence=[Evidence("source", "Source incomplete.", "execution")]))
        assert receipt.output == {"type": "no_change"}
        assert mind.inspect().revision == 1
    assert not any("MIND_DIRECTIVE_ISSUED" in p.read_text() for p in tmp_path.glob("*.jsonl"))


def test_explicit_current_selection_retires_only_selected_history(tmp_path):
    first = event(evidence=[Evidence("source", "Historical observations.", "execution")])
    script = Script(response(step([belief("new:a", "Observation A."), belief("new:b", "Observation B.")])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        mind.activate(first)
        keep = mind.inspect().items[1]["id"]
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(current=[keep]))))) as mind:
        assert mind.activate(event("event-2")).status == "accepted"
        assert [i["id"] for i in mind.inspect().items] == [keep]


def test_consultation_results_are_temporary_and_exactly_once(tmp_path):
    script = Script(response({"refs": ["empty"]}, "read_evidence"),
                    response(step([belief("new:empty", "The source contains zero characters.", "empty")])))
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=("read_evidence",)) as mind:
        waiting = mind.activate(event())
        assert waiting.status == "waiting"
        assert mind.inspect().revision == 0 and not mind.inspect().items
    result = MindResultEvent(waiting.request.request_ref, {"capability": "read_evidence", "origin": "execution",
        "text": json.dumps({"read_result": "sources-v1", "sources": [{"ref": "empty", "text": "", "origin": "execution"}]})})
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=("read_evidence",)) as mind:
        assert mind.activate(event()).request == waiting.request
        assert len(script.wires) == 1
        assert mind.accept_result(result).status == "accepted"
        assert mind.read_source("empty")["text"] == ""
        assert mind.accept_result(result).status == "duplicate"
        assert len(script.wires) == 2
        with pytest.raises(ValueError, match="result_identity_conflict"):
            mind.accept_result(replace(result, error="model_failed"))


def test_invalid_final_update_is_atomic_and_not_nochange(tmp_path):
    script = Script(response(step([belief("new:known", "Known fact.")])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        mind.activate(event(evidence=[Evidence("source", "Known fact.", "execution")]))
        before = mind.inspect()
    # Raw adapter bypasses native preflight to exercise the final state owner.
    class Invalid:
        def generate_from_trace(self, trace, projection):
            return json.dumps(step([belief(before.items[0]["id"], "Rewritten.", "missing")]))
    with Cognition(directory=tmp_path, model=Invalid()) as mind:
        receipt = mind.activate(event("event-2"))
        assert receipt.status == "failed" and receipt.error == "ungrounded_basis"
        assert receipt.output is None and mind.inspect() == before


@pytest.mark.parametrize("cut", ["native_result", "logical_output", "terminal", "accepted"])
def test_crash_cuts_recover_known_output_without_duplicate_model_call(tmp_path, monkeypatch, cut):
    class Crash(BaseException):
        pass
    script = Script(response(step([belief("new:known", "Observed fact.")])))
    original_append = MindTrace.append
    original_native = MindTrace.append_native
    original_commit = Cognition._append
    hit = False
    def appended(self, kind, *args, **kwargs):
        nonlocal hit
        result = original_append(self, kind, *args, **kwargs)
        if not hit and ((cut == "logical_output" and kind == MODEL_OUTPUT_RECORDED) or
                        (cut == "terminal" and kind == ACTIVATION_FINISHED)):
            hit = True
            raise Crash()
        return result
    def native(self, **payload):
        nonlocal hit
        result = original_native(self, **payload)
        if not hit and cut == "native_result" and payload["kind"] == "result":
            hit = True
            raise Crash()
        return result
    def committed(self, record):
        nonlocal hit
        result = original_commit(self, record)
        if not hit and cut == "accepted" and record["kind"] == "accepted":
            hit = True
            raise Crash()
        return result
    monkeypatch.setattr(MindTrace, "append", appended)
    monkeypatch.setattr(MindTrace, "append_native", native)
    monkeypatch.setattr(Cognition, "_append", committed)
    initial = event(evidence=[Evidence("source", "Observed fact.", "execution")])
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        with pytest.raises(Crash):
            mind.activate(initial)
    assert hit and len(script.wires) == 1
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.has_replayable_result(initial.event_id)
        assert mind.activate(initial).status in {"accepted", "duplicate"}
        assert mind.inspect().revision == 1
        assert len(script.wires) == 1


def test_unknown_provider_outcome_is_not_resampled(tmp_path):
    class Crash(BaseException):
        pass
    script = Script(lambda wire: (_ for _ in ()).throw(Crash()))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        with pytest.raises(Crash):
            mind.activate(event())
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        receipt = mind.activate(event())
        assert receipt.status == "failed" and receipt.output is None
        assert mind.inspect().revision == 0 and len(script.wires) == 1


def test_owner_identity_and_projected_goal_survive_restart(tmp_path):
    task = {"business_goal": "Report incomplete evidence honestly.", "execution_protocol": "Use marker protocol."}
    initial = replace(event(), goal=execution_goal(task), owner_task=task)
    script = Script(response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        mind.activate(initial)
        assert "marker protocol" not in json.dumps(script.wires)
    with Cognition(directory=tmp_path, model=MindModel(Script())) as mind:
        with pytest.raises(ValueError, match="event_identity_conflict"):
            mind.activate(replace(initial, trigger="Changed event body."))
        with pytest.raises(ValueError, match="intention_conflict"):
            mind.activate(replace(initial, event_id="event-2", goal="Different goal.", owner_task=None))


def test_state_and_returned_evidence_cannot_be_mutated(tmp_path):
    script = Script(response(step([belief("new:known", "Observed fact.")])))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        mind.activate(event(evidence=[Evidence("source", "Observed fact.", "execution")]))
        with pytest.raises(TypeError):
            mind.inspect().items[0]["claim"] = "Mutated"
        with pytest.raises(TypeError):
            mind.read_source("source")["origin"] = "computation"
        with pytest.raises(ValueError, match="evidence_identity_conflict"):
            mind.activate(event("event-2", [Evidence("source", "Replacement text.", "execution")]))


@pytest.mark.parametrize("cut", ["receipt", "observation"])
def test_result_receipt_crash_resumes_once_and_keeps_computation_origin(tmp_path, monkeypatch, cut):
    from Mind.trace import CAPABILITY_OBSERVED
    class Crash(BaseException):
        pass
    script = Script(response({"refs": ["source"], "question": "Compare the conditional cases.",
                              "model_ref": ""}, "analyze_world_model"),
                    response(step([belief("new:conditional", "Under the stated assumption, the computed result is five.",
                                          "activation:observation")])))
    initial = event(evidence=[Evidence("source", "Assume a fixed input of five.", "execution")])
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=("analyze_world_model",)) as mind:
        waiting = mind.activate(initial)
    result = MindResultEvent(waiting.request.request_ref, {"capability": "analyze_world_model", "origin": "computation",
        "text": json.dumps({"answer": "For the stated input, computed result is five.", "unknowns": "Actual input unobserved."})})
    original_commit = Cognition._append
    original_trace = MindTrace.append
    hit = False
    def commit(self, record):
        nonlocal hit
        value = original_commit(self, record)
        if not hit and cut == "receipt" and record["kind"] == "result_received":
            hit = True
            raise Crash()
        return value
    def append(self, kind, *args, **kwargs):
        nonlocal hit
        value = original_trace(self, kind, *args, **kwargs)
        if not hit and cut == "observation" and kind == CAPABILITY_OBSERVED:
            hit = True
            raise Crash()
        return value
    monkeypatch.setattr(Cognition, "_append", commit)
    monkeypatch.setattr(MindTrace, "append", append)
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=("analyze_world_model",)) as mind:
        with pytest.raises(Crash):
            mind.accept_result(result)
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=("analyze_world_model",)) as mind:
        receipt = mind.accept_result(result)
        assert receipt.status == "accepted"
        item = mind.inspect().items[0]
        ref = item["basis"][0]["ref"]
        assert ref.startswith("activation-") and ref.endswith(":observation")
        assert mind.read_source(ref)["origin"] == "computation"
        assert "Actual input unobserved" in mind.read_source(ref)["text"]
        assert len(script.wires) == 2


def test_guidance_text_is_preserved_verbatim(tmp_path):
    text = "  Limit the conclusion to the observed subset.\n"
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(next={"type": "directive", "text": text}))))) as mind:
        assert mind.activate(event()).output["text"] == text


def test_two_consultations_keep_state_uncommitted_and_sources_separate(tmp_path):
    script = Script(response({"refs": ["new-source"]}, "read_evidence"),
                    response({"refs": ["new-source"], "question": "Explain the bounded inference.", "model_ref": ""}, "analyze_world_model"),
                    response(step([belief("new:fact", "The measured input is five.", "new-source"),
                                   belief("new:calculation", "Conditional calculation yields ten.", "activation:observation:6")])))
    caps = ("read_evidence", "analyze_world_model")
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=caps) as mind:
        first = mind.activate(event())
    read = MindResultEvent(first.request.request_ref, {"capability": "read_evidence", "origin": "execution",
        "text": json.dumps({"read_result": "sources-v1", "sources": [{"ref": "new-source", "text": "The measured input is five.", "origin": "execution"}]})})
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=caps) as mind:
        second = mind.accept_result(read)
        assert second.status == "waiting" and mind.inspect().revision == 0
    analyzed = MindResultEvent(second.request.request_ref, {"capability": "analyze_world_model", "origin": "computation",
        "text": json.dumps({"answer": "Conditional calculation yields ten."})})
    with Cognition(directory=tmp_path, model=MindModel(script), available_capabilities=caps) as mind:
        result = mind.accept_result(analyzed)
        assert result.status == "accepted" and len(script.wires) == 3
        items = mind.inspect().items
        assert mind.read_source(items[0]["basis"][0]["ref"])["origin"] == "execution"
        assert mind.read_source(items[1]["basis"][0]["ref"])["origin"] == "computation"



def test_second_writer_and_corrupt_history_fail_before_model(tmp_path):
    script = Script()
    with Cognition(directory=tmp_path, model=MindModel(script)):
        with pytest.raises(OSError):
            Cognition(directory=tmp_path, model=MindModel(script))
    (tmp_path / "cognition.json").write_text('{"version":1,"records":[],"sha256":"corrupt"}')
    with pytest.raises(ValueError, match="invalid_cognition_history"):
        Cognition(directory=tmp_path, model=MindModel(script))
    assert script.wires == []


def test_native_repair_prefix_cannot_be_silently_lost(tmp_path):
    from Mind.trace import TraceError
    script = Script(response({}, "wrong_tool"), response(step()))
    with Cognition(directory=tmp_path, model=MindModel(script)) as mind:
        assert mind.activate(event()).status == "accepted"
    path = next(tmp_path.glob("*.native.jsonl"))
    records = path.read_text().splitlines()
    changed = json.loads(records[1])
    changed["response"]["content"][0]["name"] = "altered_history"
    records[1] = json.dumps(changed)
    path.write_text("\n".join(records) + "\n", encoding="utf-8")
    trace = MindTrace.reopen(next(p for p in tmp_path.glob("*.jsonl") if ".native." not in p.name))
    with pytest.raises(TraceError, match="native_prefix_lost"):
        trace.native_records()



def test_issued_guidance_cannot_differ_from_the_model_submission(tmp_path):
    from Mind.trace import TraceError
    with Cognition(directory=tmp_path, model=MindModel(Script(response(step(next={"type": "directive", "text": "Keep the original scope."}))))) as mind:
        mind.activate(event())
    path = next(p for p in tmp_path.glob("*.jsonl") if ".native." not in p.name)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[-1]["payload"]["text"] = "Host-authored different direction."
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    with pytest.raises(TraceError, match="model_result_mismatch"):
        MindTrace.reopen(path)
