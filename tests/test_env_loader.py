import os
from pathlib import Path

from core.env_loader import load_env_file


def test_missing_env_file_is_safe(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "missing.env") == {}


def test_env_file_loads_simple_and_quoted_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LUMINA_TEST_ONE", raising=False)
    monkeypatch.delenv("LUMINA_TEST_TWO", raising=False)
    path = tmp_path / ".env.local"
    path.write_text(
        "# comment\nLUMINA_TEST_ONE=one\nLUMINA_TEST_TWO='two words'\n",
        encoding="utf-8",
    )
    assert load_env_file(path) == {
        "LUMINA_TEST_ONE": "one",
        "LUMINA_TEST_TWO": "two words",
    }


def test_process_environment_has_precedence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LUMINA_PRECEDENCE", "process")
    path = tmp_path / ".env.local"
    path.write_text("LUMINA_PRECEDENCE=file\n", encoding="utf-8")
    assert load_env_file(path) == {}
    assert os.environ["LUMINA_PRECEDENCE"] == "process"


def test_malformed_lines_are_skipped_and_values_are_not_printed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("LUMINA_SAFE_VALUE", raising=False)
    path = tmp_path / ".env.local"
    path.write_text(
        "BAD-LABEL=value\nNO_EQUALS\nLUMINA_SAFE_VALUE=private-test-value\n",
        encoding="utf-8",
    )
    loaded = load_env_file(path)
    captured = capsys.readouterr()
    assert loaded == {"LUMINA_SAFE_VALUE": "private-test-value"}
    assert captured.out == ""
    assert captured.err == ""


def test_env_local_is_gitignored() -> None:
    assert ".env.local" in Path(".gitignore").read_text(encoding="utf-8")
