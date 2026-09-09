"""Event-owner persistence and source boundaries, with no live provider or workspace action."""
import json
from contextlib import ExitStack
from dataclasses import replace

import pytest

from Mind.organ import MindOrgan, reply
from Mind.model import MindModel
from Nervous.organ import Event, NervousOrgan
from Nervous.storage import plain


def native(name, value):
    return {"content": [{"type": "tool_use", "id": "call", "name": name, "input": value}], "stop_reason": "tool_use"}


def final(updates=(), next=None):
    return native("cognitive_step", {"type": "cognitive_step", "updates": list(updates),
                                   "next": next or {"type": "no_change"}})


class Script:
    def __init__(self, *answers):
        self.answers, self.wires = list(answers), []

    def __call__(self, wire):
        self.wires.append(wire)
        assert self.answers, "unexpected model call"
        return self.answers.pop(0)


class NoAnalysis:
    def analyze(self, *args):
        raise AssertionError("Unexpected analysis")


class EmptyExecution:
    def __init__(self):
        self.decisions = []

    def poll(self):
        return ()

    def published(self, event_id):
        raise AssertionError("No pending publication")

    def handle(self, event):
        if event.kind == "execution.inspect":
            return (reply(event, "mind.results", "execution.snapshot", {**plain(event.data),
                    "snapshot": {"files": [], "sources": [], "execution_ref": None, "status": None}}),)
        assert event.kind == "mind.decision"
        self.decisions.append(event)
        return ()

    def advance(self):
        return False

    def status(self):
        return {"status": "idle"}


def organ(tmp_path, script, calls=None, analysis=None):
    return MindOrgan(tmp_path / "mind", calls, goal="Deliver a scoped assessment.",
                     execution_protocol="Use the existing completion protocol.",
                     model=MindModel(script), analysis=analysis or NoAnalysis())


@pytest.mark.parametrize("cut", ["cognition_accepted", "organ_saved"])
def test_event_restarts_after_commit_before_ack_without_another_call(tmp_path, monkeypatch, cut):
    class Crash(BaseException):
        pass
    script = Script(final())
    body = EmptyExecution()
    hit = False
    original_save = MindOrgan.save
    original_complete = NervousOrgan.complete
    def save(self):
        nonlocal hit
        if cut == "cognition_accepted" and not hit and any(x["status"] == "accepted" for x in self.state["activities"].values()):
            hit = True
            raise Crash()
        return original_save(self)
    def complete(self, event_id, target, **kwargs):
        nonlocal hit
        if cut == "organ_saved" and not hit and target == "mind.results":
            hit = True
            raise Crash()
        return original_complete(self, event_id, target, **kwargs)
    monkeypatch.setattr(MindOrgan, "save", save)
    monkeypatch.setattr(NervousOrgan, "complete", complete)
    with NervousOrgan(tmp_path / "nervous") as nervous:
        mind = organ(tmp_path, script, nervous.calls)
        nervous.submit("Deliver a scoped assessment.", "USER_GOAL")
        try:
            with pytest.raises(Crash):
                nervous.run(mind, body)
        finally:
            mind.close()
    assert hit and len(script.wires) == 1
    with NervousOrgan(tmp_path / "nervous") as nervous:
        mind = organ(tmp_path, script, nervous.calls)
        try:
            result = nervous.run(mind, body)
            assert result["mind"]["revision"] == 1 and result["mind"]["active"] is None
            assert len(script.wires) == 1 and len(body.decisions) == 1
            assert body.decisions[0].data["directive"] is None
            assert not any(result["pending"].values())
            nervous.run(mind, body)
            assert len(script.wires) == 1
        finally:
            mind.close()


def test_handled_event_identity_and_authority_remain_bound(tmp_path):
    mind = organ(tmp_path, Script())
    event = Event("user-1", "user", "mind", "user.input", {"text": "New evidence.", "event_type": "USER_MESSAGE"})
    try:
        first = mind.handle(event)
        assert mind.handle(event) == first
        with pytest.raises(ValueError, match="event_identity_conflict"):
            mind.handle(replace(event, data={"text": "Different evidence.", "event_type": "USER_MESSAGE"}))
        with pytest.raises(ValueError):
            mind.handle(replace(event, source="execution"))
    finally:
        mind.close()


def test_sources_are_exact_copies_with_distinct_role_and_currentness(tmp_path):
    mind = organ(tmp_path, Script())
    try:
        old = {"ref": "source-old", "text": "Old measured content.", "origin": "execution", "label": "a.txt"}
        mind.put_source(old)
        mind.state["active"] = "active"
        mind.state["activities"]["active"] = {"snapshot": {"files": [{"file": "a.txt", "ref": "source-new"}]}}
        assert mind.source_info("source-old")["workspace_version"] == "superseded"
        copy = mind.source_record("source-old")
        copy["text"] = "Changed returned copy"
        assert mind.source_record("source-old")["text"] == old["text"]
        with pytest.raises(ValueError, match="source_identity_conflict"):
            mind.put_source({**old, "origin": "computation"})
    finally:
        mind.close()


def test_analysis_round_trip_returns_to_same_cognition_through_nervous(tmp_path):
    record = {"ref": "input-1", "text": "Input is a conditional assumption.", "origin": "execution"}
    script = Script(native("analyze_world_model", {"refs": ["input-1"], "question": "Which result remains conditional?", "model_ref": ""}),
                    final([{"kind": "belief", "id": "new:conditional", "claim": "The model result is conditional, not a new observation.",
                            "status": "supported", "basis": [{"ref": "activation:observation"}]}]))
    class Analysis:
        def __init__(self):
            self.requests = []
        def analyze(self, request_ref, request):
            self.requests.append((request_ref, plain(request)))
            return {"capability": "analyze_world_model", "origin": "computation", "text": json.dumps({
                "kind": "EXPLICIT_UNKNOWN", "answer": "The model result is conditional, not a new observation.", "model_ref": ""})}
    analysis = Analysis()
    body = EmptyExecution()
    original = body.handle
    def handle(event):
        emitted = original(event)
        if event.kind == "execution.inspect":
            snapshot = plain(emitted[0].data)
            snapshot["snapshot"].update(files=[{"file": "input.txt", "ref": "input-1"}], sources=[record])
            return (replace(emitted[0], data=snapshot),)
        return emitted
    body.handle = handle
    with NervousOrgan(tmp_path / "nervous") as nervous:
        mind = organ(tmp_path, script, nervous.calls, analysis)
        try:
            nervous.submit("Deliver a scoped assessment.", "USER_GOAL")
            result = nervous.run(mind, body)
            assert result["mind"]["revision"] == 1 and not result["mind"]["active"]
            assert len(script.wires) == 2 and len(analysis.requests) == 1
            item = mind.cognition.inspect().items[0]
            assert mind.source_record(item["basis"][0]["ref"])["origin"] == "computation"
            events = nervous._load()["events"]
            kinds = [event["kind"] for event in events]
            assert "analysis.request" in kinds and "analysis.result" in kinds
            assert len(body.decisions) == 1
        finally:
            mind.close()


def test_failure_retains_processing_obligation_without_fallback_direction(tmp_path):
    script = Script(*[native("wrong_tool", {}) for _ in range(6)])
    with NervousOrgan(tmp_path / "nervous") as nervous:
        mind = organ(tmp_path, script, nervous.calls)
        body = EmptyExecution()
        try:
            nervous.submit("Deliver a scoped assessment.", "USER_GOAL")
            result = nervous.run(mind, body)
            assert result["mind"]["revision"] == 0 and len(result["mind"]["unresolved"]) == 1
            assert not body.decisions
            nervous.run(mind, body)
            assert len(script.wires) == 6
        finally:
            mind.close()


@pytest.mark.parametrize('expiry', ['before_acceptance', 'before_action'])
def test_expired_direction_reassesses_original_user_event_after_restart(tmp_path, expiry):
    from Execution.model import EXECUTION_PROTOCOL
    from Execution.runtime import Execution, advisory
    from Execution.test_runtime import Python

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    observed = workspace / 'measurement.txt'
    observed.write_text('Measured size: 1.', encoding='utf-8')
    goal = 'Prepare a scoped report from the current measurement.'
    old_direction, new_direction = 'Use the observed size of one.', 'Use the newly observed size of two.'
    wires = []
    def transport(role, wire):
        wires.append((role, wire))
        if role == 'execution':
            return native('wait', {'event_type': 'REPORT_INPUT'})
        assert role == 'mind'
        count = sum(r == 'mind' for r, _ in wires)
        if count == 1:
            if expiry == 'before_acceptance':
                observed.write_text('Measured size: 2.', encoding='utf-8')
            return final(next={'type': 'directive', 'text': old_direction})
        if count == 2:
            assert 'Measured size: 2.' in json.dumps(wire, ensure_ascii=False)
            return final(next={'type': 'directive', 'text': new_direction})
        assert count == 3  # The receiving Wait is important feedback, closed by NoChange.
        return final()

    def reopen():
        stack = ExitStack()
        nervous = NervousOrgan(tmp_path / 'state/nervous', transport=transport)
        stack.callback(nervous.close)
        mind = MindOrgan(tmp_path / 'state/mind', nervous.calls, goal=goal,
                         execution_protocol=EXECUTION_PROTOCOL)
        stack.callback(mind.close)
        execution = Execution(tmp_path / 'state/execution', nervous.calls, workspace, Python(workspace))
        stack.callback(execution.close)
        return stack, nervous, mind, execution

    stack, nervous, mind, execution = reopen()
    with stack:
        original = nervous.submit(goal, 'USER_GOAL')
        nervous.run(mind, execution, max_steps=4)
        first_activity = next(iter(mind.state['activities'].values()))
        owner_source = mind.state['owners'][0]['ref']
        assert first_activity['output']['text'] == old_direction
        if expiry == 'before_action':
            observed.write_text('Measured size: 2.', encoding='utf-8')
            assert execution.advance()
        assert execution.state['outbox']
        assert all(role == 'mind' for role, _ in wires)

    stack, nervous, mind, execution = reopen()
    with stack:
        result = nervous.run(mind, execution)
        assert result['execution']['status'] == 'waiting'
        assert result['mind']['revision'] == 3 and not result['mind']['unresolved']
        assert not any(result['pending'].values())
        successor = next(work for work in mind.state['activities'].values() if work.get('origin_activity_id'))
        assert successor['origin_activity_id'] == first_activity['id']
        assert successor['owner_input'] == first_activity['owner_input']
        assert successor['owner_input']['event_id'] == original.event_id
        assert mind.state['owners'][0]['ref'] == owner_source
        assert len(execution.state['owners']) == 1
        assert execution.state['task']['business_goal'] == goal
        wire = next(wire for role, wire in wires if role == 'execution')
        assert any(message['content'] == advisory(new_direction) for message in wire['messages'])
        assert all(message['content'] != advisory(old_direction) for message in wire['messages'])
        before = len(wires)
        nervous.run(mind, execution)
        assert len(wires) == before


@pytest.mark.parametrize('error', ['invalid_model_reference', 'builder_terminal_or_cardinality_failure',
                                 'builder_call_outcome_unknown'])
def test_expected_analysis_failure_is_durable_and_does_not_block_new_owner_input(tmp_path, error):
    class FailedAnalysis:
        def __init__(self):
            self.calls = 0
        def analyze(self, *_):
            self.calls += 1
            raise ValueError(error)
    analysis = FailedAnalysis()
    script = Script(native('analyze_world_model', {'refs': ['selected-source'],
        'question': 'Assess the original observation.', 'model_ref': ''}), final())
    body = EmptyExecution()
    with NervousOrgan(tmp_path / 'nervous') as nervous:
        mind = organ(tmp_path, script, nervous.calls, analysis)
        mind.put_source({'ref': 'selected-source', 'text': 'An observed constraint.', 'origin': 'execution'})
        try:
            nervous.submit('Deliver a scoped assessment.', 'USER_GOAL')
            result = nervous.run(mind, body)
            assert result['mind']['active'] is None and result['mind']['unresolved'][0]['error'] == 'model_failed'
            assert not any(result['pending'].values()) and not body.decisions
            failures = list((mind.directory / 'failures').glob('*.json'))
            assert len(failures) == 1 and json.loads(failures[0].read_text())['detail'] == error
        finally:
            mind.close()
    with NervousOrgan(tmp_path / 'nervous') as nervous:
        mind = organ(tmp_path, script, nervous.calls, analysis)
        try:
            nervous.run(mind, body)
            assert analysis.calls == 1 and len(script.wires) == 1
            nervous.submit('Record the bounded failure; no workspace action is needed.')
            result = nervous.run(mind, body)
            assert result['mind']['revision'] == 1 and result['mind']['active'] is None
            assert analysis.calls == 1 and len(script.wires) == 2
            assert len(body.decisions) == 1 and body.decisions[0].data['directive'] is None
        finally:
            mind.close()


@pytest.mark.parametrize('failed_rounds', [1, 2])
def test_explicit_retry_reprocesses_failed_event_without_rewriting_history(tmp_path, failed_rounds):
    script = Script(*[native('wrong_tool', {}) for _ in range(6 * failed_rounds)], final())
    body = EmptyExecution()
    with NervousOrgan(tmp_path / 'nervous') as nervous:
        mind = organ(tmp_path, script, nervous.calls)
        try:
            nervous.submit('Deliver a scoped assessment.', 'USER_GOAL')
            nervous.run(mind, body)
            original_id = mind.retry_activity()
            original = dict(mind.state['activities'][original_id])
            owner_ref = mind.state['owners'][0]['ref']
        finally:
            mind.close()
    with NervousOrgan(tmp_path / 'nervous') as nervous:
        mind = organ(tmp_path, script, nervous.calls)
        try:
            for attempt in range(failed_rounds):
                failed_id = mind.retry_activity()
                retry = Event('retry-' + failed_id, 'user', 'mind', 'mind.retry', {'activity_id': failed_id})
                nervous.publish(retry)
                result = nervous.run(mind, body)
                successor = mind.state['activities'][failed_id]['superseded_by']
                assert mind.state['activities'][successor]['retry_of'] == failed_id
                if attempt + 1 < failed_rounds:
                    assert len(result['mind']['unresolved']) == attempt + 2
            assert result['mind']['revision'] == 1 and not result['mind']['unresolved']
            assert mind.state['activities'][original_id]['status'] == original['status'] == 'failed'
            assert mind.state['activities'][original_id]['error'] == original['error']
            assert mind.state['activities'][successor]['owner_input'] == original['owner_input']
            assert len(mind.state['owners']) == 1 and mind.state['owners'][0]['ref'] == owner_ref
            traces = [path for path in (mind.directory / 'cognition').glob('activation-*.jsonl')
                      if '.native.' not in path.name]
            assert len(traces) == failed_rounds + 1
            assert len(script.wires) == 6 * failed_rounds + 1
            assert len(body.decisions) == 1 and body.decisions[0].data['directive'] is None
            assert not nervous.publish(retry)
            nervous.run(mind, body)
            assert len(script.wires) == 6 * failed_rounds + 1
            with pytest.raises(ValueError, match='no_failed_cognitive_activity'):
                mind.retry_activity()
        finally:
            mind.close()


def test_unknown_evidence_ref_returns_failure_and_explicit_retry_can_finish(tmp_path):
    from Execution.model import EXECUTION_PROTOCOL
    from Execution.runtime import Execution
    from Execution.test_runtime import Python

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    script = Script(native('read_evidence', {'refs': ['unknown-source']}), final())
    with NervousOrgan(tmp_path / 'state/nervous') as nervous:
        mind = MindOrgan(tmp_path / 'state/mind', nervous.calls,
            goal='Assess the available evidence.', execution_protocol=EXECUTION_PROTOCOL,
            model=MindModel(script))
        execution = Execution(tmp_path / 'state/execution', nervous.calls, workspace, Python(workspace))
        try:
            nervous.submit('Assess the available evidence.', 'USER_GOAL')
            result = nervous.run(mind, execution)
            assert result['mind']['active'] is None and result['mind']['unresolved'][0]['error'] == 'model_failed'
            assert not any(result['pending'].values()) and len(script.wires) == 1
            failed_id = mind.retry_activity()
            nervous.publish(Event('retry-read', 'user', 'mind', 'mind.retry', {'activity_id': failed_id}))
            result = nervous.run(mind, execution)
            assert result['mind']['revision'] == 1 and not result['mind']['unresolved']
            assert result['execution']['status'] == 'not_started' and len(script.wires) == 2
        finally:
            execution.close()
            mind.close()


@pytest.mark.parametrize("capacity", [False, True])
def test_prediction_observation_prefetch_is_explicit_and_unread_is_not_acknowledged(tmp_path, monkeypatch, capacity):
    script = Script(final())
    comparisons = []
    def compare(run, contract, observed, **kwargs):
        comparisons.append(observed)
        return {"status": "compared", "comparison": "match"}
    monkeypatch.setattr("Mind.organ.compare_observation_contract", compare)
    class Analysis(NoAnalysis):
        def model(self, ref):
            return {"run": {"test": "fixture"}}
    mind = organ(tmp_path, script, analysis=Analysis())
    mind.state["predictions"]["prediction-1"] = {"ref": "prediction-1", "activity_id": "previous-activity",
        "observation_file": "observed.json", "before_observation_ref": "old-source", "check_spec": {}}
    user = Event("user-1", "user", "mind", "user.input", {"event_type": "USER_MESSAGE", "text": "Review the new run."})
    try:
        inspect = mind.handle(user)[0]
        snapshot = reply(inspect, "mind.results", "execution.snapshot", {**plain(inspect.data), "snapshot": {
            "files": [{"file": "observed.json", "ref": "new-observation"}], "sources": [],
            "execution_ref": None, "status": None, "unread_observation_refs": ["new-observation"]}})
        request = mind.handle(snapshot)[0]
        assert request.kind == "evidence.read" and request.data["purpose"] == "analysis"
        assert len(script.wires) == 0 and comparisons == []
        if capacity:
            body = {"read_result": "capacity-v1", "status": "not_read", "reason": "response_capacity"}
        else:
            body = {"read_result": "sources-v1", "sources": [{"ref": "new-observation", "text": '{"count": 5}', "origin": "execution"}]}
        result = reply(request, "mind.results", "evidence.result", {
            "activity_id": request.data["activity_id"], "request_ref": request.data["request_ref"],
            "observation": {"capability": "read_evidence", "origin": "execution", "text": json.dumps(body)}, "records": []})
        emitted = mind.handle(result)
        assert len(script.wires) == 1 and len(emitted) == 1
        reviewed = plain(emitted[0].data["reviewed"])["predictions"]
        if capacity:
            assert comparisons == [] and reviewed == {}
            assert "reviewed_source" not in mind.state["predictions"]["prediction-1"]
            assert "observation_source_not_read" in json.dumps(script.wires)
        else:
            assert comparisons == [{"count": 5}]
            assert reviewed == {"prediction-1": "new-observation"}
    finally:
        mind.close()


def test_read_metadata_cannot_replace_the_actual_observation_text(tmp_path):
    script = Script(native("read_evidence", {"refs": ["unloaded"]}))
    mind = organ(tmp_path, script)
    try:
        user = Event("user-1", "user", "mind", "user.input", {"event_type": "USER_MESSAGE", "text": "Read the original."})
        inspect = mind.handle(user)[0]
        snapshot = reply(inspect, "mind.results", "execution.snapshot", {**plain(inspect.data),
            "snapshot": {"files": [{"file": "source.txt", "ref": "unloaded"}], "sources": [], "execution_ref": None, "status": None}})
        request = mind.handle(snapshot)[0]
        result = reply(request, "mind.results", "evidence.result", {"activity_id": request.data["activity_id"],
            "request_ref": request.data["request_ref"], "observation": {"capability": "read_evidence", "origin": "execution",
                "text": json.dumps({"read_result": "sources-v1", "sources": [{"ref": "unloaded", "text": "Actual original", "origin": "execution"}]})},
            "records": [{"ref": "unloaded", "text": "Substituted inference", "origin": "execution"}]})
        with pytest.raises(ValueError, match="source_metadata_conflict"):
            mind.handle(result)
        assert "unloaded" not in mind.state["sources"] and len(script.wires) == 1
    finally:
        mind.close()


def test_prediction_watch_replay_does_not_include_later_review_bookkeeping(tmp_path):
    class Analysis(NoAnalysis):
        def model(self, ref):
            return {"check_spec": {"object": "declared-object"}, "observation_file": "observed.json"}
    mind = organ(tmp_path, Script(), analysis=Analysis())
    try:
        event = Event("analysis-result", "mind.analysis", "mind.results", "analysis.result", {})
        work = {"id": "activity", "snapshot": {"files": []}}
        observation = {"text": json.dumps({"model_ref": "prediction", "kind": "COMPUTED_CONDITIONAL"})}
        first = mind.register_prediction(event, work, observation)
        mind.state["predictions"]["prediction"]["reviewed_source"] = "future-observation"
        replay = mind.register_prediction(event, work, observation)
        assert first == replay and "reviewed_source" not in replay[0].data
    finally:
        mind.close()
