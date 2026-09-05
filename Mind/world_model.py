"""Bounded, isolated computation and independent checks for small world models.

Modified portions from Tycho f68912a (Apache-2.0), sandbox.py and
wmlib_template.py. Copyright 2026 Jens Lehmann, Andrei Aioanei, and Sahar Vahdati.
License and adaptation record: docs/cognition_minimal/{TYCHO_LICENSE.txt,
ISOLATED_MODEL_SOURCE_AUDIT.md}. No runtime capability is exposed by this module.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import tempfile
import threading
import uuid

IMAGE = "python@sha256:3b3706a90cb23f04fabb0d255824f9a70ceb46177041898133dd5a35f3a50f0a"
PROTOCOL = "isolated-world-model-v1"
OUTCOMES = {"ongoing", "complete", "failed", "unknown"}


class ModelComputationError(RuntimeError):
    """Safe failure code; provider bodies and container stderr are not exposed."""


# This runner receives source, initial observation and actions ONLY. Gold future
# observations/outcomes are checked in the host after the container has exited.
_RUNNER = r'''
import copy
import json
import sys

request = json.load(sys.stdin)
phase = "load"
step = -1
try:
    model = {"__name__": "world_model"}
    exec(compile(request["source"], "<world-model>", "exec"), model)
    for name in ("init_state", "transition", "render", "outcome"):
        if not callable(model.get(name)):
            raise ValueError("missing model interface")
    phase = "initialization"
    state = model["init_state"](copy.deepcopy(request["initial_observation"]))

    def snapshot():
        global phase
        phase = "render"
        observation = model["render"](copy.deepcopy(state))
        phase = "outcome"
        outcome = model["outcome"](copy.deepcopy(state))
        return {"observation": observation, "outcome": outcome}

    result = {"initial": snapshot(), "steps": []}
    for step, action in enumerate(request["actions"]):
        phase = "transition"
        state = model["transition"](copy.deepcopy(state), copy.deepcopy(action))
        result["steps"].append(snapshot())
    phase = "serialization"
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
except Exception:
    encoded = json.dumps({"error": {"phase": phase, "step": step}})
sys.stdout.write(encoded)
'''


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fields(value: object) -> None:
    # ponytail: scalar fields cover the first local task models; add a structured
    # observation bridge only when a concrete model needs nested observations.
    if type(value) is not dict or not 1 <= len(value) <= 16:
        raise ValueError("model fields exceed bounds")
    for key, item in value.items():
        if type(key) is not str or not 1 <= len(key) <= 64:
            raise ValueError("invalid model field name")
        if item is None or type(item) is bool:
            continue
        if type(item) is str and len(item) <= 256:
            continue
        if type(item) in (int, float) and abs(item) <= 10**12 and math.isfinite(item):
            continue
        raise ValueError("invalid model field value")


def _request(source: str, initial_observation: dict, actions: list[dict]) -> dict:
    if type(source) is not str or not 1 <= len(source) <= 16_000:
        raise ValueError("model source exceeds bounds")
    _fields(initial_observation)
    if type(actions) is not list or len(actions) > 16:
        raise ValueError("model actions exceed bounds")
    for action in actions:
        _fields(action)
    request = copy.deepcopy({"source": source, "initial_observation": initial_observation, "actions": actions})
    if len(_json(request).encode("utf-8")) > 65_536:
        raise ValueError("model request exceeds bounds")
    return request


def _command(name: str) -> list[str]:
    # Restriction flags ported from Tycho PythonSandbox.command. The container
    # has no mounts; only its disposable /tmp is writable. Never fall back to host.
    return [
        "docker", "run", "--rm", "--pull", "never", "--name", name, "--init", "-i",
        "--network", "none", "--log-driver", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "32", "--memory", "256m", "--memory-swap", "256m", "--cpus", "1",
        "--ulimit", "nofile=64:64", "--ulimit", "fsize=1048576:1048576",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
        "--user", "65534:65534", "--workdir", "/tmp", "--env", "HOME=/tmp",
        IMAGE, "python", "-I", "-S", "-B", "-c", _RUNNER,
    ]


def _compute(request: dict) -> bytes:
    name = f"lumina-mind-model-{uuid.uuid4().hex}"
    captured: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    truncated = threading.Event()

    # Adapted from Tycho run_script.drain: keep draining after the storage cap
    # so excess output cannot deadlock the pipe or grow host memory without bound.
    def drain(key, stream, limit):
        retained = 0
        while chunk := stream.read(8192):
            remaining = max(0, limit - retained)
            if remaining:
                captured[key].append(chunk[:remaining])
                retained += min(len(chunk), remaining)
            if len(chunk) > remaining:
                truncated.set()

    try:
        with tempfile.TemporaryFile() as request_stream:
            request_stream.write(_json(request).encode("utf-8"))
            request_stream.seek(0)
            process = subprocess.Popen(
                _command(name), stdin=request_stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            readers = [threading.Thread(target=drain, args=(key, stream, limit), daemon=True)
                       for key, stream, limit in (("stdout", process.stdout, 65_536),
                                                  ("stderr", process.stderr, 8192))]
            for reader in readers:
                reader.start()
            interrupted = False
            try:
                process.wait(timeout=10)
            except BaseException:
                interrupted = True
                raise
            finally:
                if interrupted:
                    # Container removal kills all its processes, including children.
                    # Target is an internally generated name, never model-supplied.
                    cleanup_ok = False
                    try:
                        stopped = subprocess.run(["docker", "rm", "-f", name],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                 timeout=8, check=False)
                        cleanup_ok = stopped.returncode == 0
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=5)
                    if not cleanup_ok:
                        raise ModelComputationError("container_cleanup_failed")
                for reader in readers:
                    reader.join(timeout=5)
                if any(reader.is_alive() for reader in readers):
                    raise ModelComputationError("container_output_incomplete")
                process.stdout.close()
                process.stderr.close()
    except subprocess.TimeoutExpired as exc:
        raise ModelComputationError("computation_timeout") from exc
    except OSError as exc:
        raise ModelComputationError("container_unavailable") from exc
    if truncated.is_set():
        raise ModelComputationError("computation_output_limit")
    if process.returncode or captured["stderr"]:
        raise ModelComputationError("computation_failed")
    return b"".join(captured["stdout"])


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate model output key")
        result[key] = value
    return result


def _prediction(value: object, steps: int) -> None:
    if type(value) is not dict or set(value) != {"initial", "steps"}:
        raise ValueError("invalid model prediction")
    if type(value["steps"]) is not list or len(value["steps"]) != steps:
        raise ValueError("invalid model prediction length")
    for snapshot in [value["initial"], *value["steps"]]:
        if type(snapshot) is not dict or set(snapshot) != {"observation", "outcome"}:
            raise ValueError("invalid model snapshot")
        _fields(snapshot["observation"])
        if type(snapshot["outcome"]) is not str or snapshot["outcome"] not in OUTCOMES:
            raise ValueError("invalid model outcome")


def run_model(source: str, initial_observation: dict, actions: list[dict]) -> dict:
    """Compute one rollout. This result is a prediction, never reality evidence.

    Source defines init_state(observation), transition(state, action),
    render(state) -> scalar-field dict, outcome(state) -> ongoing/complete/failed/
    unknown. Render nulls are explicit abstentions. Latent state is model-owned.
    """
    request = _request(source, initial_observation, actions)
    raw = _compute(request)
    try:
        prediction = json.loads(raw, object_pairs_hook=_strict_object)
        if type(prediction) is dict and set(prediction) == {"error"}:
            error = prediction["error"]
            phases = {"load", "initialization", "render", "outcome", "transition", "serialization"}
            if type(error) is dict and type(error.get("phase")) is str and error["phase"] in phases:
                raise ModelComputationError(f"model_{error['phase']}_error")
        _prediction(prediction, len(actions))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ModelComputationError("invalid_computation_output") from exc
    return {"kind": "COMPUTED", "protocol": PROTOCOL, "runtime": IMAGE,
            "source_digest": _digest(source), "request_digest": _digest(_json(request)),
            "request": request, "prediction": prediction}


def _compare(predicted: dict, observed: dict | None) -> dict:
    if observed is None:
        return {"status": "unobserved", "matched_fields": 0, "claimed_fields": 0,
                "observed_fields": 0, "coverage": None, "differing_fields": []}
    _fields(observed)
    total = sum(value is not None for value in observed.values())
    claimed = sum(value is not None and predicted.get(key) is not None for key, value in observed.items())
    differing = sorted(set(predicted) ^ set(observed))
    matched = 0
    for key, actual in observed.items():
        value = predicted.get(key)
        if value is not None and actual is not None:
            if type(value) is type(actual) and value == actual:
                matched += 1
            elif key not in differing:
                differing.append(key)
    status = ("mismatch" if differing else "unobserved" if not total else
              "unknown" if claimed < total else "matched")
    return {"status": status, "matched_fields": matched, "claimed_fields": claimed,
            "observed_fields": total, "coverage": claimed / total if total else None,
            "differing_fields": sorted(differing)}


def _summary(checks: list[dict]) -> dict:
    statuses = [item["status"] for item in checks]
    status = next((item for item in ("mismatch", "unknown", "unobserved") if item in statuses),
                  "matched" if checks else "unobserved")
    return {"status": status, "matched": statuses.count("matched"),
            "observed": sum(item != "unobserved" for item in statuses), "checks": checks}


def verify_run(run: dict, observed_steps: list[dict | None],
               *, observed_outcomes: list[str | None] | None = None) -> dict:
    """Host-only comparison. Later gold data never reaches run_model or its stdin.

    Checks describe only the supplied trajectory; matching these observations
    does not establish a general causal model or a prospective prediction.
    """
    if type(run) is not dict or set(run) != {"kind", "protocol", "runtime", "source_digest",
                                           "request_digest", "request", "prediction"}:
        raise ValueError("invalid computed model record")
    request = run["request"]
    if type(request) is not dict or set(request) != {"source", "initial_observation", "actions"}:
        raise ValueError("invalid computed model request")
    request = _request(**request)
    if (run["kind"] != "COMPUTED" or run["protocol"] != PROTOCOL or run["runtime"] != IMAGE
            or run["source_digest"] != _digest(request["source"])
            or run["request_digest"] != _digest(_json(request))):
        raise ValueError("computed model provenance mismatch")
    prediction = run["prediction"]
    count = len(request["actions"])
    _prediction(prediction, count)
    if type(observed_steps) is not list or len(observed_steps) != count:
        raise ValueError("observed trajectory length mismatch")
    if observed_outcomes is None:
        observed_outcomes = [None] * (count + 1)
    if type(observed_outcomes) is not list or len(observed_outcomes) != count + 1:
        raise ValueError("observed outcome length mismatch")
    initialization = _compare(prediction["initial"]["observation"], request["initial_observation"])
    dynamics = [_compare(step["observation"], actual)
                for step, actual in zip(prediction["steps"], observed_steps, strict=True)]
    outcomes = []
    for snapshot, actual in zip([prediction["initial"], *prediction["steps"]], observed_outcomes, strict=True):
        if actual is not None and (type(actual) is not str or actual not in OUTCOMES):
            raise ValueError("invalid observed outcome")
        predicted = snapshot["outcome"]
        status = ("unobserved" if actual in (None, "unknown") else "unknown" if predicted == "unknown"
                  else "matched" if predicted == actual else "mismatch")
        outcomes.append({"status": status, "predicted": predicted, "observed": actual})
    divergence = None
    for frame, check in enumerate([initialization, *dynamics]):
        phase = "initialization" if frame == 0 else "dynamics"
        if check["status"] != "mismatch":
            phase = "outcome" if outcomes[frame]["status"] == "mismatch" else None
        if phase is not None:
            divergence = {"phase": phase, "step": frame - 1}
            break
    claimed = sum(item["claimed_fields"] for item in dynamics)
    observed = sum(item["observed_fields"] for item in dynamics)
    return {"kind": "CHECKED", "source_digest": run["source_digest"], "request_digest": run["request_digest"],
            "initialization": initialization, "dynamics": _summary(dynamics), "outcome": _summary(outcomes),
            "dynamics_coverage": claimed / observed if observed else None, "first_divergence": divergence}
