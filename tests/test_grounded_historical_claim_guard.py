"""The real Chat Answer boundary carries one grounded-history rule in every mode.

These are deterministic prompt/input regressions; real-model behavior is evaluated
separately by the frozen AC01 calibration.
"""
from pathlib import Path

import pytest

from Conversation_Memory.adapter.models import MemoryContext, RecallPolicy
from core.contracts import ChatRequest
from core.draft_store import JsonlDraftStore
from core.message_runtime import MessageRuntime


class Context:
    def __init__(self, turns):
        self.turns = turns

    def get_recent_context(self):
        return self.turns


class Memory:
    def __init__(self, rendered):
        self.rendered = rendered
        self.queries = []

    def recall(self, query, policy):
        self.queries.append(query)
        return MemoryContext(query, rendered_text=self.rendered)


class Answer:
    client_kind = "model"

    def __init__(self):
        self.requests = []

    def generate(self, recent_context, user_message, *, system_prompt):
        self.requests.append((recent_context, user_message, system_prompt))
        return "scripted answer"


@pytest.mark.parametrize(
    "message,near,memory,expected_fragment",
    [
        ("上次在河岸发生了什么？", [], "", None),
        ("你还记得上次夜航吗？", [], "", None),
        ("上次我只替主持了一次，没有答应长期接任。现在帮我回一句。", [], "", None),
        ("那次录制最后怎么样？", [], "[USER] 我用提示卡完成了录制。", "[USER] 我用提示卡完成了录制。"),
        ("我以前答应接任了吗？", [], "[LUMINA] 我猜用户已经答应接任。", "[LUMINA] 我猜用户已经答应接任。"),
        ("这次我主动想办大一点，不用沿用以前的小规模。", [], "[USER] 目前两人读书比较舒服，不代表永远不扩大。", "[USER] 目前两人读书比较舒服，不代表永远不扩大。"),
        ("上次那件事还想聊。", [{"role": "user", "text": "上次我说过只帮一次。"}], "", None),
    ],
)
def test_grounded_history_guard_is_shared_without_hiding_supported_history(
    tmp_path: Path, message, near, memory, expected_fragment
):
    background = (Path(__file__).resolve().parents[1] / "prompts/chat_background.md").read_text(encoding="utf-8").strip()
    answer = Answer()
    retriever = Memory(memory)
    runtime = MessageRuntime(
        hot_store=JsonlDraftStore(tmp_path / "hot.jsonl"),
        draft_context_provider=Context(near),
        model_client=answer,
        chat_background=background,
        recall_enabled=True,
        memory_retriever=retriever,
        recall_policy=RecallPolicy(),
    )
    runtime.handle_chat(ChatRequest(message=message))
    assert retriever.queries == [message]
    assert len(answer.requests) == 1
    actual_near, actual_message, prompt = answer.requests[0]
    assert actual_near == near
    assert actual_message == message
    assert prompt.startswith(background)
    assert "不凭语境补造共同经历" in prompt
    assert "有确切且相关的证据时，可以自然地承接过去" in prompt
    if expected_fragment is None:
        assert "[Internal historical evidence" not in prompt
    else:
        assert expected_fragment in prompt
        assert "[Internal historical evidence" in prompt


def test_guard_reaches_no_long_term_memory_answer(tmp_path: Path):
    background = (Path(__file__).resolve().parents[1] / "prompts/chat_background.md").read_text(encoding="utf-8").strip()
    answer = Answer()
    runtime = MessageRuntime(
        hot_store=JsonlDraftStore(tmp_path / "hot.jsonl"),
        draft_context_provider=Context([]),
        model_client=answer,
        chat_background=background,
        recall_enabled=False,
    )
    runtime.handle_chat(ChatRequest(message="上次那件事以后，我还想去。"))
    assert len(answer.requests) == 1
    recent, message, prompt = answer.requests[0]
    assert recent == [] and message == "上次那件事以后，我还想去。"
    assert "不凭语境补造共同经历" in prompt
    assert "[Internal historical evidence" not in prompt
