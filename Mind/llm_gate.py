"""The default v2 Chat Recall gate; no query editing or cognitive loop."""
from __future__ import annotations

from core.model_client import ModelClient
from Mind.interfaces import MindDecision

_SYSTEM_PROMPT = """你是 Lumina 的 Recall 门控。判断一条用户消息是否需要 Conversation Memory（长期对话记忆）才能可靠回答。

规则：
- 仅凭当前可见的聊天上下文就能可靠回答 → 输出 false
- 不能可靠回答（包括需要查证记忆才能诚实地回答，或诚实地承认不知道）→ 输出 true

只输出一个单词：true 或 false。不要输出任何其他内容，不要回答消息本身。"""

_USER_TEMPLATE = """[当前可见的聊天上下文]
{context}

[待判断的用户消息]
{message}

[你的判断，只输出 true 或 false]"""


class LlmMindGate:
    prompt_version = "mind-gate-v2"

    def __init__(self, model_client: ModelClient) -> None:
        self._model_client = model_client

    def decide(self, user_message: str, recent_context: list[dict[str, str]]) -> MindDecision:
        context_text = "\n".join(
            f"{item.get('role', '?')}: {item.get('text', '')}"
            for item in recent_context
        ) or "（无）"
        raw = self._model_client.generate(
            [], _USER_TEMPLATE.format(context=context_text, message=user_message),
            system_prompt=_SYSTEM_PROMPT,
        )
        return MindDecision(recall=_parse_boolean(raw))


def _parse_boolean(raw: str) -> bool:
    normalized = raw.strip().lower().rstrip("。.!！")
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError("unparseable mind gate output")
