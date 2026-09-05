from pathlib import Path

from fastapi.testclient import TestClient

from Conversation_Memory.adapter.models import MemoryContext
from core.main import create_app
from core.model_client import MockModelClient
from Execution.execution import ClaimComplete, IPythonCode, NativeModelDecision
from Mind.constant_gate import ConstantMindGate


ROOT_SURFACE = (
    "ipython(code: str)",
    "wait(event_type: str)",
    "claim_complete()",
)


class _NoMemory:
    def recall(self, query, policy):
        return MemoryContext("")


class _ScriptedExecutionModel:
    identifier = "scripted-execution"
    tool_contracts = ROOT_SURFACE

    def __init__(self, actions):
        self._actions = iter(actions)
        self.received_requests = []

    def decide(self, request):
        self.received_requests.append(request)
        return next(self._actions)


def _app(tmp_path: Path, execution_model):
    return create_app(
        draft_store_path=tmp_path / "draft" / "hot.jsonl",
        cold_draft_path=tmp_path / "draft" / "cold.jsonl",
        compaction_state_path=tmp_path / "draft" / "state.json",
        model_client=MockModelClient(),
        env_file_path=None,
        recall_enabled=False,
        memory_retriever=_NoMemory(),
        mind_gate=ConstantMindGate(),
        execution_root=tmp_path / "execution",
        execution_model=execution_model,
    )


def test_manual_execution_runs_in_an_isolated_workspace_and_returns_verified(
    tmp_path: Path,
) -> None:
    model = _ScriptedExecutionModel(
        [
            IPythonCode(
                "from pathlib import Path\n"
                "Path('artifact.txt').write_text('answer', encoding='utf-8')\n"
                "Path('.lumina-complete').write_text('verified', encoding='utf-8')"
            ),
            ClaimComplete(),
        ]
    )
    client = TestClient(_app(tmp_path, model))

    response = client.post(
        "/api/execution",
        json={"goal": "Create artifact.txt containing answer."},
    )

    assert response.status_code == 200
    document = response.json()
    assert set(document) == {"execution_id", "status", "result", "verified"}
    assert document["execution_id"].startswith("execution-")
    assert document["status"] == "completed"
    assert document["result"] == "verified"
    assert document["verified"] is True
    assert str(tmp_path) not in response.text

    run_directories = tuple((tmp_path / "execution").iterdir())
    assert len(run_directories) == 1
    workspace = run_directories[0] / "workspace"
    assert (workspace / "artifact.txt").read_text(encoding="utf-8") == "answer"
    assert (workspace / ".lumina-complete").read_text(encoding="utf-8") == "verified"
    assert (run_directories[0] / "state" / "events.jsonl").is_file()
    assert tuple(model.received_requests[0].available_tools) == ROOT_SURFACE

    chat = client.post("/api/chat", json={"message": "hello"})
    assert chat.status_code == 200
    assert chat.json()["phase"] == "mock_chat"


def test_manual_execution_rejects_blank_and_unbounded_goals_before_start(
    tmp_path: Path,
) -> None:
    model = _ScriptedExecutionModel([])
    client = TestClient(_app(tmp_path, model))

    blank = client.post("/api/execution", json={"goal": "   "})
    oversized = client.post("/api/execution", json={"goal": "x" * 4_097})

    assert blank.status_code == 400
    assert blank.json() == {"detail": "goal is required"}
    assert oversized.status_code == 422
    assert model.received_requests == []
    assert not (tmp_path / "execution").exists()


def test_manual_execution_projects_provider_failure_without_internal_leak(
    tmp_path: Path,
) -> None:
    model = _ScriptedExecutionModel(
        [
            NativeModelDecision(
                None,
                {"Authorization": "Bearer private-token"},
                {"provider_body": "private-body"},
                failure=f"model_provider:private:{tmp_path}",
            )
        ]
    )

    response = TestClient(_app(tmp_path, model)).post(
        "/api/execution",
        json={"goal": "Attempt the task."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["result"] is None
    assert response.json()["verified"] is False
    assert "private" not in response.text
    assert "Bearer" not in response.text
    assert str(tmp_path) not in response.text


def test_manual_execution_sanitizes_workspace_creation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _ScriptedExecutionModel([])
    client = TestClient(_app(tmp_path, model), raise_server_exceptions=False)

    def fail_mkdir(*args, **kwargs):
        raise OSError(f"private workspace failure: {tmp_path}")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    response = client.post(
        "/api/execution",
        json={"goal": "Attempt the task."},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "execution_unavailable",
            "message": "Execution could not complete",
        }
    }
    assert "private" not in response.text
    assert str(tmp_path) not in response.text
    assert model.received_requests == []


def test_manual_execution_continues_after_completion_rejection(
    tmp_path: Path,
) -> None:
    model = _ScriptedExecutionModel(
        [
            ClaimComplete(),
            IPythonCode(
                "open('.lumina-complete', 'w', encoding='utf-8').write('verified')"
            ),
            ClaimComplete(),
        ]
    )

    response = TestClient(_app(tmp_path, model)).post(
        "/api/execution",
        json={"goal": "Finish only after the environment is ready."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["verified"] is True
    assert len(model.received_requests) == 3
