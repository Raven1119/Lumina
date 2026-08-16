from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from Conversation_Memory.adapter.models import IngestionResult, MemoryContext
from core.contracts import MemoryTurn
from core.draft_store import HotDraftSummary
from core.main import create_app
from Mind.constant_gate import ConstantMindGate


class _HistoryModel:
    client_kind = "model"

    def __init__(self) -> None:
        self.generate_calls = 0
        self.summary_calls = 0

    def generate(self, recent_context, user_message, *, system_prompt):
        self.generate_calls += 1
        return f"reply:{user_message}"

    def summarize_hot_draft(self, old_summary, moved_turns):
        self.summary_calls += 1
        return "private rolling summary"


class _HistoryMemory:
    ingestion_version = "grounded-span-v2"

    def __init__(self) -> None:
        self.recall_calls = 0
        self.ingest_calls = 0

    def recall(self, query, policy):
        self.recall_calls += 1
        return MemoryContext("")

    def ingest(self, segment):
        self.ingest_calls += 1
        return IngestionResult(
            segment_id=segment.segment_id,
            ingestion_version=self.ingestion_version,
            status="completed",
            memory_ids=tuple(
                f"memory-{index}"
                for index, _turn in enumerate(segment.turns)
            ),
        )


def _app(
    tmp_path: Path,
    *,
    model: _HistoryModel | None = None,
    memory: _HistoryMemory | None = None,
    **kwargs,
):
    # Pin the stage-1 gate: history tests assert Chat/Draft behavior, not
    # Mind gate selection (which is covered in tests/test_chat_api.py).
    kwargs.setdefault("mind_gate", ConstantMindGate())
    return create_app(
        draft_store_path=tmp_path / "hot.jsonl",
        cold_draft_path=tmp_path / "cold.jsonl",
        compaction_state_path=tmp_path / "state.json",
        model_client=model or _HistoryModel(),
        env_file_path=None,
        recall_enabled=False,
        memory_retriever=memory or _HistoryMemory(),
        **kwargs,
    )


def _turn(
    index: int,
    *,
    text: str | None = None,
    turn_id: str | None = None,
) -> MemoryTurn:
    return MemoryTurn(
        turn_id=turn_id or f"turn-{index:03d}",
        role="user" if index % 2 == 0 else "assistant",
        text=text or f"body-{index:03d}",
        created_at=datetime(2026, 8, 1, tzinfo=UTC)
        + timedelta(minutes=index),
        source_timezone="Asia/Shanghai",
        timezone_source="client",
    )


def _append_hot(app, turns: list[MemoryTurn]) -> None:
    for turn in turns:
        app.state.hot_draft_store.append_turn(turn)


def test_empty_history_contract(tmp_path: Path) -> None:
    response = TestClient(_app(tmp_path)).get("/api/history")
    assert response.status_code == 200
    assert response.json() == {
        "turns": [],
        "has_more": False,
        "next_before": None,
    }


def test_hot_only_history_excludes_summary_and_internal_fields(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    turns = [_turn(0), _turn(1)]
    app.state.hot_draft_store.replace_contents_atomically(
        HotDraftSummary(
            "secret summary",
            1,
            8,
            "2026-08-01T01:00:00Z",
        ),
        turns,
    )

    response = TestClient(app).get("/api/history")
    assert response.status_code == 200
    payload = response.json()
    assert payload["turns"] == [
        {
            "turn_id": "turn-000",
            "role": "user",
            "content": "body-000",
            "timestamp": "2026-08-01T00:00:00.000000Z",
        },
        {
            "turn_id": "turn-001",
            "role": "assistant",
            "content": "body-001",
            "timestamp": "2026-08-01T00:01:00.000000Z",
        },
    ]
    assert set(payload) == {"turns", "has_more", "next_before"}
    assert all(
        set(turn) == {"turn_id", "role", "content", "timestamp"}
        for turn in payload["turns"]
    )
    public = response.text
    for private_value in (
        "secret summary",
        "segment_id",
        "source_timezone",
        "timezone_source",
        "record_type",
        "metadata",
    ):
        assert private_value not in public


def test_real_compaction_and_explicit_dream_preserve_full_history(
    tmp_path: Path,
) -> None:
    model = _HistoryModel()
    memory = _HistoryMemory()
    app = _app(
        tmp_path,
        model=model,
        memory=memory,
        retain_recent_raw_turns=2,
        max_raw_turns_before_compression=4,
    )
    client = TestClient(app)

    for index in range(3):
        response = client.post(
            "/api/chat",
            json={"message": f"message-{index}", "client_timezone": "UTC"},
        )
        assert response.status_code == 200
    expected = [
        value
        for index in range(3)
        for value in (f"message-{index}", f"reply:message-{index}")
    ]
    before_dream = client.get("/api/history?limit=40").json()
    assert [turn["content"] for turn in before_dream["turns"]] == expected
    assert "private rolling summary" not in str(before_dream)
    assert len(app.state.cold_draft_store.list_pending()) == 1

    dream = client.post("/api/dream/run")
    assert dream.status_code == 200
    assert dream.json()["consumed"] == 1
    assert memory.ingest_calls == 1
    assert app.state.cold_draft_store.list_pending() == []
    after_dream = client.get("/api/history?limit=40").json()
    assert after_dream == before_dream


def test_cold_wins_turn_id_overlap_without_reordering(tmp_path: Path) -> None:
    app = _app(tmp_path)
    cold_turns = [
        _turn(0, text="cold-before"),
        _turn(1, text="cold-winner", turn_id="shared"),
    ]
    app.state.cold_draft_store.append_segment(
        [turn.storage_turn() for turn in cold_turns],
        segment_id="private-segment",
    )
    _append_hot(
        app,
        [
            _turn(1, text="hot-duplicate", turn_id="shared"),
            _turn(2, text="hot-after"),
        ],
    )

    payload = TestClient(app).get("/api/history").json()
    assert [turn["turn_id"] for turn in payload["turns"]] == [
        "turn-000",
        "shared",
        "turn-002",
    ]
    assert [turn["content"] for turn in payload["turns"]] == [
        "cold-before",
        "cold-winner",
        "hot-after",
    ]
    assert sum(
        turn["turn_id"] == "shared" for turn in payload["turns"]
    ) == 1
    assert "private-segment" not in str(payload)


def test_cursor_pages_are_complete_non_overlapping_and_survive_restart(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    turns = [_turn(index) for index in range(90)]
    _append_hot(app, turns)
    first = TestClient(app).get("/api/history").json()
    assert [turn["turn_id"] for turn in first["turns"]] == [
        f"turn-{index:03d}" for index in range(50, 90)
    ]
    assert first["has_more"] is True
    assert first["next_before"] == "turn-050"

    restarted = TestClient(_app(tmp_path))
    second = restarted.get(
        "/api/history",
        params={"limit": 40, "before": first["next_before"]},
    ).json()
    third = restarted.get(
        "/api/history",
        params={"limit": 40, "before": second["next_before"]},
    ).json()
    assert [turn["turn_id"] for turn in second["turns"]] == [
        f"turn-{index:03d}" for index in range(10, 50)
    ]
    assert second["has_more"] is True
    assert second["next_before"] == "turn-010"
    assert [turn["turn_id"] for turn in third["turns"]] == [
        f"turn-{index:03d}" for index in range(10)
    ]
    assert third["has_more"] is False
    assert third["next_before"] is None
    combined = third["turns"] + second["turns"] + first["turns"]
    assert [turn["turn_id"] for turn in combined] == [
        turn.turn_id for turn in turns
    ]


def test_invalid_cursor_is_safe_stable_400(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _append_hot(app, [_turn(0)])
    response = TestClient(app).get(
        "/api/history",
        params={"before": "missing-private-cursor"},
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "code": "invalid_history_cursor",
            "message": "history cursor is invalid",
        }
    }
    assert "body-000" not in response.text


def test_history_read_is_non_mutating_and_calls_no_runtime_subsystem(
    tmp_path: Path,
) -> None:
    model = _HistoryModel()
    memory = _HistoryMemory()
    app = _app(tmp_path, model=model, memory=memory)
    cold_turn = _turn(0)
    app.state.cold_draft_store.append_segment(
        [cold_turn.storage_turn()],
        segment_id="pending",
    )
    _append_hot(app, [_turn(1)])
    hot_path = tmp_path / "hot.jsonl"
    cold_path = tmp_path / "cold.jsonl"
    before = (hot_path.read_bytes(), cold_path.read_bytes())
    call_order: list[str] = []
    hot_reader = app.state.hot_draft_store.list_all_raw
    cold_reader = app.state.cold_draft_store.list_all_turns

    def read_hot():
        call_order.append("hot")
        return hot_reader()

    def read_cold():
        call_order.append("cold")
        return cold_reader()

    app.state.hot_draft_store.list_all_raw = read_hot
    app.state.cold_draft_store.list_all_turns = read_cold
    response = TestClient(app).get("/api/history")

    assert response.status_code == 200
    assert call_order == ["hot", "cold"]
    assert model.generate_calls == 0
    assert model.summary_calls == 0
    assert memory.recall_calls == 0
    assert memory.ingest_calls == 0
    assert (hot_path.read_bytes(), cold_path.read_bytes()) == before
    assert not (tmp_path / "state.json").exists()
