"""Safety checks for the maintained isolated real-MAGMA entry."""
import json
import os
from pathlib import Path
import socket

import pytest

from scripts import first_hit_memory_check as check


def test_work_dir_accepts_only_new_or_empty_paths(tmp_path):
    nested = tmp_path / "new" / "isolated"
    assert check.prepare_work_dir(nested) == nested.resolve()
    assert nested.is_dir() and not list(nested.iterdir())
    assert check.prepare_work_dir(nested) == nested.resolve()


def test_existing_content_is_preserved_and_never_reset(tmp_path):
    original = tmp_path / "original.txt"
    original.write_text("unrelated", encoding="utf-8")
    with pytest.raises(check.CheckFailure, match="work_dir_not_empty"):
        check.prepare_work_dir(tmp_path)
    assert original.read_text(encoding="utf-8") == "unrelated"
    assert list(tmp_path.iterdir()) == [original]


@pytest.mark.parametrize("candidate", [check.ROOT, check.ROOT / "data" / "new-first-hit",
                                       check.ROOT / ".git" / "new-first-hit",
                                       check.ROOT / "Conversation_Memory" / "upstream" / "new"])
def test_protected_workspace_locations_are_rejected_before_writing(candidate):
    with pytest.raises(check.CheckFailure, match="work_dir_protected"):
        check.prepare_work_dir(candidate)


def test_symbolic_parent_is_rejected(tmp_path, monkeypatch):
    linked = tmp_path / "linked"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or original(path))
    # An actual absent target would not have lstat; create the ordinary directory
    # first to exercise the path gate without Windows symlink privileges.
    linked.mkdir()
    with pytest.raises(check.CheckFailure, match="work_dir_link_rejected"):
        check.prepare_work_dir(linked / "new")
    assert not (linked / "new").exists()


def test_run_disables_dotenv_network_and_restores_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-parent-value")
    def isolated(_directory):
        import dotenv
        assert dotenv.load_dotenv(tmp_path / "must-not-read.env") is False
        assert os.environ["OPENAI_API_KEY"] == "first-hit-check-placeholder"
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        with socket.socket() as client:
            with pytest.raises(RuntimeError, match="network_disabled_for_check"):
                client.connect(("127.0.0.1", 1))
        print("synthetic private body must not reach stdout")
        return {"status": "passed"}
    monkeypatch.setattr(check, "_run", isolated)
    assert check.run_check(tmp_path) == {"status": "passed"}
    assert os.environ["OPENAI_API_KEY"] == "synthetic-parent-value"


def test_cli_failure_is_safe_and_does_not_report_exception_body(tmp_path, monkeypatch, capsys):
    def fail(_directory):
        raise RuntimeError("private source / credential / local path")
    monkeypatch.setattr(check, "run_check", fail)
    assert check.main(["--work-dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "failed", "stage": "run",
                                      "safe_error_code": "first_hit_check_failed"}
    assert captured.err == ""


def test_cli_refuses_nonempty_without_attempting_model_import(tmp_path, monkeypatch, capsys):
    (tmp_path / "keep").write_bytes(b"preserve")
    monkeypatch.setattr(check, "_run", lambda _path: pytest.fail("runtime was loaded"))
    assert check.main(["--work-dir", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["safe_error_code"] == "work_dir_not_empty"
    assert (tmp_path / "keep").read_bytes() == b"preserve"
