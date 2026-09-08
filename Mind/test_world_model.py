"""Host verification plus opt-in tests against the actual pinned Docker image."""

import copy
import json
import os
import subprocess

import pytest

from Mind import world_model as wm


SERVICE_MODEL = '''
def init_state(observation):
    return dict(observation, checks=0)

def transition(state, action):
    if action["operation"] == "deploy":
        state.update(version=action["version"], status="pending", checks=0)
    elif action["operation"] == "check":
        state["checks"] += 1
        if state["checks"] >= 2:
            state["status"] = "ready"
    elif action["operation"] == "promote":
        state["status"] = "live" if state["status"] == "ready" else "failed"
    return state

def render(state):
    return {"version": state["version"], "status": state["status"]}

def outcome(state):
    return {"live": "complete", "failed": "failed"}.get(state["status"], "ongoing")
'''
INITIAL = {"version": "v1", "status": "live"}
ACTIONS = [{"operation": "deploy", "version": "v2"}, {"operation": "check"},
           {"operation": "check"}, {"operation": "promote"}]
OBSERVATIONS = [{"version": "v2", "status": status} for status in ("pending", "pending", "ready", "live")]
OUTCOMES = ["complete", "ongoing", "ongoing", "ongoing", "complete"]
PREDICTION = {"initial": {"observation": INITIAL, "outcome": OUTCOMES[0]},
              "steps": [{"observation": observation, "outcome": outcome}
                        for observation, outcome in zip(OBSERVATIONS, OUTCOMES[1:])]}

docker = pytest.mark.skipif(os.environ.get("LUMINA_TEST_WORLD_MODEL_DOCKER") != "1",
                            reason="explicit isolated Docker validation only")


def test_host_verification_keeps_future_evidence_out_of_compute(monkeypatch):
    requests = []

    def compute(request):
        requests.append(copy.deepcopy(request))
        return json.dumps(PREDICTION).encode()

    monkeypatch.setattr(wm, "_compute", compute)
    run = wm.run_model(SERVICE_MODEL, INITIAL, ACTIONS)
    checked = wm.verify_run(run, OBSERVATIONS, observed_outcomes=OUTCOMES)
    assert checked["initialization"]["status"] == checked["dynamics"]["status"] == "matched"
    assert checked["outcome"]["status"] == "matched"
    assert checked["first_divergence"] is None
    changed = copy.deepcopy(OBSERVATIONS)
    changed[-1]["status"] = "failed"
    falsified = wm.verify_run(run, changed)
    assert falsified["first_divergence"] == {"phase": "dynamics", "step": 3}
    assert falsified["outcome"]["status"] == "unobserved"
    assert requests == [{"source": SERVICE_MODEL, "initial_observation": INITIAL, "actions": ACTIONS}]
    assert run["kind"] == "COMPUTED"


def test_unknowns_missing_observations_and_outcomes_are_not_success(monkeypatch):
    predicted = copy.deepcopy(PREDICTION)
    predicted["steps"][1]["observation"]["status"] = None
    predicted["steps"][2]["outcome"] = "unknown"
    monkeypatch.setattr(wm, "_compute", lambda _: json.dumps(predicted).encode())
    run = wm.run_model(SERVICE_MODEL, INITIAL, ACTIONS)
    checked = wm.verify_run(run, OBSERVATIONS, observed_outcomes=OUTCOMES)
    assert checked["dynamics"]["status"] == checked["outcome"]["status"] == "unknown"
    assert checked["dynamics_coverage"] == 7 / 8
    assert checked["dynamics"]["matched"] == 3
    assert checked["first_divergence"] is None
    unseen = wm.verify_run(run, [None] * 4)
    assert unseen["dynamics_coverage"] is None
    assert unseen["dynamics"]["status"] == unseen["outcome"]["status"] == "unobserved"
    labels = list(OUTCOMES)
    labels[0] = "ongoing"
    assert wm.verify_run(run, OBSERVATIONS, observed_outcomes=labels)["first_divergence"] == {
        "phase": "outcome", "step": -1}
    assert wm._compare({"value": True}, {"value": 1})["status"] == "mismatch"
    assert wm._compare({"value": None}, {"value": 1})["status"] == "unknown"
    assert wm._compare({"other": None}, {"value": 1})["status"] == "mismatch"


def test_untrusted_output_and_request_bounds(monkeypatch):
    monkeypatch.setattr(wm, "_compute", lambda _: pytest.fail("invalid input reached Docker"))
    for source, initial, actions in [("x" * 16_001, INITIAL, ACTIONS), (SERVICE_MODEL, INITIAL, ACTIONS * 5),
                                     (SERVICE_MODEL, {"n": 10**500}, []),
                                     (SERVICE_MODEL, {"value": float("nan")}, [])]:
        with pytest.raises(ValueError):
            wm.run_model(source, initial, actions)
    for raw in [b'{"initial":{},"initial":{},"steps":[]}', b'{"initial":null,"steps":[]}',
                b'{"initial":{"observation":{"value":NaN},"outcome":"complete"},"steps":[]}',
                b'{"verified":true}']:
        monkeypatch.setattr(wm, "_compute", lambda _, raw=raw: raw)
        with pytest.raises(wm.ModelComputationError, match="invalid_computation_output"):
            wm.run_model(SERVICE_MODEL, INITIAL, [])
    monkeypatch.setattr(wm, "_compute", lambda _: json.dumps(PREDICTION).encode())
    run = wm.run_model(SERVICE_MODEL, INITIAL, ACTIONS)
    run["request"]["actions"][0]["version"] = "different"
    with pytest.raises(ValueError, match="provenance mismatch"):
        wm.verify_run(run, OBSERVATIONS)


@docker
def test_actual_stateful_rollout_and_competing_model():
    run = wm.run_model(SERVICE_MODEL, INITIAL, ACTIONS)
    checked = wm.verify_run(run, OBSERVATIONS, observed_outcomes=OUTCOMES)
    assert run["prediction"] == PREDICTION
    assert checked["dynamics"]["matched"] == 4  # Includes a visible no-op with a latent state change.
    assert checked["outcome"]["matched"] == 5
    premature = wm.run_model(SERVICE_MODEL.replace('>= 2', '>= 1'), INITIAL, ACTIONS)
    falsified = wm.verify_run(premature, OBSERVATIONS, observed_outcomes=OUTCOMES)
    assert falsified["initialization"]["status"] == "matched"
    assert falsified["first_divergence"] == {"phase": "dynamics", "step": 1}


@docker
def test_actual_isolation_and_resource_configuration(tmp_path, monkeypatch):
    canary = tmp_path / "host-only.txt"
    canary.write_text("synthetic-private-canary", encoding="utf-8")
    monkeypatch.setenv("LUMINA_MODEL_TEST_SECRET", "synthetic-env-canary")
    # Only the path string is input; no bind mount makes the file accessible.
    probe = '''
import os
import pathlib
import socket

def init_state(observation):
    status = dict(line.split(":", 1) for line in pathlib.Path("/proc/self/status").read_text().splitlines() if ":" in line)
    result = {"uid": os.getuid(), "caps": status["CapEff"].strip(), "nnp": status["NoNewPrivs"].strip(),
              "host_file": pathlib.Path(observation["path"]).exists(),
              "host_secret": "LUMINA_MODEL_TEST_SECRET" in os.environ,
              "provider_secret": "DEEPSEEK_API_KEY" in os.environ,
              "memory": pathlib.Path("/sys/fs/cgroup/memory.max").read_text().strip(),
              "swap": pathlib.Path("/sys/fs/cgroup/memory.swap.max").read_text().strip(),
              "pids": pathlib.Path("/sys/fs/cgroup/pids.max").read_text().strip(),
              "cpu": pathlib.Path("/sys/fs/cgroup/cpu.max").read_text().strip()}
    for name, path in (("root_write", "/forbidden"), ("workspace_write", "/workspace/forbidden")):
        try:
            pathlib.Path(path).write_text("probe")
            result[name] = True
        except OSError:
            result[name] = False
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=0.2):
            result["network"] = True
    except OSError:
        result["network"] = False
    return result

def transition(state, action):
    return state

def render(state):
    return state

def outcome(state):
    return "unknown"
'''
    run = wm.run_model(probe, {"path": str(canary)}, [])
    got = run["prediction"]["initial"]["observation"]
    assert got == {"uid": 65534, "caps": "0000000000000000", "nnp": "1", "host_file": False,
                   "host_secret": False, "provider_secret": False, "memory": "268435456", "swap": "0",
                   "pids": "32", "cpu": "100000 100000", "root_write": False,
                   "workspace_write": False, "network": False}
    assert canary.read_text() == "synthetic-private-canary"


@docker
def test_actual_output_memory_and_model_errors_are_not_predictions():
    for source, code in [("print('x' * 70000)\n" + SERVICE_MODEL, "computation_output_limit"),
                         ("raise RuntimeError('private details')\n", "model_load_error"),
                         ("x = bytearray(512 * 1024 * 1024)\n", "computation_failed")]:
        with pytest.raises(wm.ModelComputationError, match=f"^{code}$"):
            wm.run_model(source, INITIAL, [])


@docker
def test_actual_timeout_removes_owned_container_and_child(monkeypatch):
    names = []
    original = subprocess.Popen

    def popen(command, *args, **kwargs):
        if "--name" in command:
            names.append(command[command.index("--name") + 1])
        return original(command, *args, **kwargs)

    monkeypatch.setattr(wm.subprocess, "Popen", popen)
    source = "import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\ntime.sleep(120)\n"
    with pytest.raises(wm.ModelComputationError, match="^computation_timeout$"):
        wm.run_model(source, INITIAL, [])
    assert len(names) == 1
    remaining = subprocess.run(["docker", "ps", "--all", "--filter", f"name=^{names[0]}$", "--format", "{{.Names}}"],
                               capture_output=True, text=True, timeout=8, check=True)
    assert remaining.stdout.strip() == ""


@pytest.mark.parametrize('change,expected', [
    ({}, 'compared'), ({'version':'v3'}, 'not_applicable'),
    ({'platform':None}, 'unverified'), ({'status':None}, 'incomparable'),
    ({'observation_time':'before_promotion'}, 'not_applicable')])
def test_forecast_alignment_precedes_quantity_comparison(monkeypatch, change, expected):
    monkeypatch.setattr(wm, '_compute', lambda _: json.dumps(PREDICTION).encode())
    run = wm.run_model(SERVICE_MODEL, INITIAL, ACTIONS)
    contract = {'action':{'version':'v2'}, 'conditions':{'platform':'test'},
        'object':'service', 'when':'after_promotion',
        'quantities':{'status':{'meaning':'Reported deployment state','unit':'state'}}}
    observed = {'version':'v2','platform':'test','observation_object':'service',
        'observation_time':'after_promotion','status':'live', **change}
    result = wm.compare_observation_contract(run, contract, observed)
    assert result['status'] == expected
    assert (result['comparison'] is not None) == (expected == 'compared')
    if expected == 'compared':
        assert result['comparison']['status'] == 'matched'
        observed['status'] = 'failed'
        mismatch = wm.compare_observation_contract(run, contract, observed)
        assert mismatch['status'] == 'compared'
        assert mismatch['comparison']['status'] == 'mismatch'
    assert wm.compare_observation_contract(run, contract, observed, fresh=False)['status'] == 'unverified'
    contract['conditions']['version'] = 'v2'
    with pytest.raises(ValueError, match='ambiguous_observation_contract'):
        wm.compare_observation_contract(run, contract, None)
