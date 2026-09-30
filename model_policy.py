"""One provider model policy for the live Chat, cognition, Execution and Memory paths.

Transports and call budgets stay with their owning organs. Historical lab runs
keep their recorded model identities; this policy applies to new runtime calls.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path


ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
ANTHROPIC_MESSAGES_URL = f"{ANTHROPIC_BASE_URL}/v1/messages"
OPENAI_CHAT_URL = "https://api.deepseek.com/chat/completions"

_ROLE_OVERRIDES = {
    "chat": "LUMINA_CHAT_MODEL",
    "mind": "LUMINA_MIND_MODEL",
    "analysis": "LUMINA_ANALYSIS_MODEL",
    "execution": "LUMINA_EXECUTION_MODEL",
    "memory": "LUMINA_MEMORY_MODEL",
}

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "model.toml"


def load_model_config(path: Path = CONFIG_PATH) -> tuple[str, dict[str, str]]:
    """Read one startup policy; reject missing or misspelled selections."""
    with path.open("rb") as source:
        config = tomllib.load(source)
    if set(config) - {"default", "roles"}:
        raise ValueError("unknown_model_config_key")
    default = config.get("default")
    roles = config.get("roles", {})
    if not isinstance(default, str) or not default.strip():
        raise ValueError("empty_model_selection")
    if not isinstance(roles, dict) or set(roles) - set(_ROLE_OVERRIDES):
        raise ValueError("invalid_model_roles")
    selected: dict[str, str] = {}
    for role, value in roles.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError("empty_model_selection")
        selected[role] = value.strip()
    return default.strip(), selected


DEFAULT_MODEL, _CONFIGURED_ROLES = load_model_config()


def model_for(role: str, environ: Mapping[str, str] | None = None, *,
              override: str | None = None) -> str:
    """Resolve explicit > role env > role file > global env > file default."""
    if role not in _ROLE_OVERRIDES:
        raise ValueError("unknown_model_role")
    env = os.environ if environ is None else environ
    selected = (override if override is not None else
                env.get(_ROLE_OVERRIDES[role], _CONFIGURED_ROLES.get(role,
                    env.get("LUMINA_MODEL", DEFAULT_MODEL))))
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("empty_model_selection")
    return selected.strip()
