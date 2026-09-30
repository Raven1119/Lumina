"""Runtime roles share one default while retaining explicit role choices."""

import pytest
import model_policy

from Conversation_Memory.model_client import build_memory_model_from_env
from core.main import create_app
from core.model_client import build_model_client_from_env
from core.model_client import MockModelClient
from Execution.deepseek_model import DeepSeekModel
from model_policy import CONFIG_PATH, DEFAULT_MODEL, load_model_config, model_for


ROLES = ("chat", "mind", "analysis", "execution", "memory")


def test_shared_default_and_override_priority() -> None:
    assert DEFAULT_MODEL == "deepseek-flash"
    assert load_model_config(CONFIG_PATH) == (DEFAULT_MODEL, {})
    assert {model_for(role, {}) for role in ROLES} == {DEFAULT_MODEL}
    env = {"LUMINA_MODEL": "global-model", "LUMINA_MEMORY_MODEL": "memory-model"}
    assert {model_for(role, env) for role in ROLES[:-1]} == {"global-model"}
    assert model_for("memory", env) == "memory-model"
    assert model_for("memory", env, override="explicit-model") == "explicit-model"


def test_file_role_selection_and_environment_precedence(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "model.toml"
    config_path.write_text('default = "file-global"\n[roles]\nmemory = "file-memory"\n')
    default, roles = load_model_config(config_path)
    monkeypatch.setattr(model_policy, "DEFAULT_MODEL", default)
    monkeypatch.setattr(model_policy, "_CONFIGURED_ROLES", roles)
    assert model_for("chat", {}) == "file-global"
    assert model_for("memory", {}) == "file-memory"
    assert model_for("memory", {"LUMINA_MODEL": "env-global"}) == "file-memory"
    assert model_for("chat", {"LUMINA_MODEL": "env-global"}) == "env-global"
    assert model_for("memory", {"LUMINA_MODEL": "env-global",
                                "LUMINA_MEMORY_MODEL": "env-memory"}) == "env-memory"


def test_invalid_model_file_is_rejected(tmp_path) -> None:
    config_path = tmp_path / "model.toml"
    config_path.write_text('default = "deepseek-flash"\n[roles]\nmemroy = "typo"\n')
    with pytest.raises(ValueError, match="invalid_model_roles"):
        load_model_config(config_path)


@pytest.mark.parametrize("selection", ["", "  "])
def test_empty_override_is_rejected(selection: str) -> None:
    with pytest.raises(ValueError, match="empty_model_selection"):
        model_for("chat", {"LUMINA_MODEL": selection})


def test_runtime_adapters_use_shared_and_role_models(tmp_path, monkeypatch) -> None:
    env = {"LUMINA_MODEL_MODE": "real", "DEEPSEEK_API_KEY": "test-key",
           "LUMINA_MODEL": "shared-model", "LUMINA_MEMORY_MODEL": "memory-model"}
    chat = build_model_client_from_env(env)
    memory = build_memory_model_from_env(tmp_path, env)
    assert chat._model == "shared-model"
    assert memory.model == "memory-model"

    monkeypatch.setenv("LUMINA_MODEL", "shared-model")
    monkeypatch.setenv("LUMINA_EXECUTION_MODEL", "execution-model")
    assert DeepSeekModel(transport=lambda _: {}).identifier == "execution-model"


def test_chat_memory_receipts_use_the_selected_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LUMINA_MODEL", "global-model")
    monkeypatch.setenv("LUMINA_MEMORY_MODEL", "memory-model")
    app = create_app(env_file_path=None, model_client=MockModelClient(),
                     draft_store_path=tmp_path / "hot.jsonl",
                     memory_dir=tmp_path / "memory", enable_compaction=False)
    assert app.state.message_runtime._memory.config.llm_model == "memory-model"
