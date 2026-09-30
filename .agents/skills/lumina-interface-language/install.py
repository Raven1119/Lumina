#!/usr/bin/env python3
"""Install the local Lumina design skill, without network or business-code changes.

Python 3.9+. Example: python install.py --project "D:/Projects/Lumina"
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import sys
import tempfile
from datetime import datetime, timezone

NAME = "lumina-interface-language"
BEGIN = "<!-- BEGIN lumina-interface-language -->"
END = "<!-- END lumina-interface-language -->"
AGENTS = {"codex": ".agents", "kimi": ".kimi", "claude": ".claude"}
ROOT = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(source: Path) -> dict:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("name") != NAME or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unexpected package manifest.")
    for relative, expected in manifest["files"].items():
        part = PurePosixPath(relative)
        if part.is_absolute() or PureWindowsPath(relative).drive or ".." in part.parts or "\\" in relative:
            raise ValueError("Unsafe path in manifest: " + relative)
        path = source / relative
        if any(p.is_symlink() for p in [path, *path.parents] if p != source.parent):
            raise ValueError("Refusing a symlink in source: " + relative)
        if not path.is_file() or digest(path) != expected:
            raise ValueError("Package integrity check failed: " + relative)
    return manifest


def instruction_path(project: Path, agent: str) -> Path:
    if agent == "claude":
        return project / "CLAUDE.md"
    override = project / "AGENTS.override.md"
    if agent == "codex" and override.exists():
        if override.is_symlink() or not override.is_file():
            raise ValueError("Invalid AGENTS.override.md.")
        if override.read_bytes().strip():
            return override
    return project / "AGENTS.md"


def block_for(relative_skill: str) -> str:
    return f"""{BEGIN}
## Lumina frontend design language

For this project's frontend appearance and interaction work, read
`{relative_skill}/SKILL.md` first.
Visual authority: its `assets/reference.html`, tokens and screenshots.
Motion authority: its `assets/motion-reference.html` (Research Interface Language 1.0.0 / V9).
Use Lumina's mineral / depth / stone palettes; preserve the accepted matte visual language.
Do not substitute the Research/CyberScientist visual skin, old purple palette,
custom plate animations, or approximations of the approved 15 motion mechanisms.
Keep real business behavior and project layout; demo geometry is not a mandatory avatar.
Apply only required effects. Follow CHECKLIST.md and report actual test results.
{END}"""


def merged_instructions(old: bytes, block: str) -> bytes:
    """Preserve all bytes outside the owned block, including BOM and CRLF."""
    newline = b"\r\n" if b"\r\n" in old else b"\n"
    begin, end = BEGIN.encode(), END.encode()
    if old.count(begin) != old.count(end) or old.count(begin) > 1:
        raise ValueError("Malformed managed instruction markers; nothing changed.")
    encoded = block.encode("utf-8").replace(b"\n", newline)
    if begin in old:
        a, b = old.index(begin), old.index(end)
        if b < a:
            raise ValueError("Managed markers out of order; nothing changed.")
        return old[:a] + encoded + old[b + len(end):]
    suffix = b"" if not old or old.endswith(newline * 2) else (newline if old.endswith(newline) else newline * 2)
    return old + suffix + encoded + newline


def same_install(destination: Path, manifest: dict, source: Path) -> bool:
    if not destination.is_dir() or destination.is_symlink():
        return False
    allowed = set(manifest["files"]) | {"manifest.json", ".installed.json"}
    paths = list(destination.rglob("*"))
    if any(p.is_symlink() for p in paths):
        return False
    actual = {p.relative_to(destination).as_posix() for p in paths if p.is_file() and "__pycache__" not in p.parts}
    if actual - allowed:
        return False
    return all((destination / rel).is_file() and digest(destination / rel) == sha for rel, sha in manifest["files"].items()) and (destination / "manifest.json").is_file() and (destination / "manifest.json").read_bytes() == (source / "manifest.json").read_bytes()


def atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".lui-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(source: Path, *, project: Path | None, agent: str, replace: bool) -> dict:
    if agent not in AGENTS:
        raise ValueError("Unknown agent: " + agent)
    manifest = load_manifest(source)
    base = project.expanduser().resolve() if project is not None else Path.home().resolve()
    if not base.is_dir():
        raise ValueError("Project/home directory must exist: " + str(base))
    skill_relative = f"{AGENTS[agent]}/skills/{NAME}"
    destination = base / skill_relative
    for directory in [base / AGENTS[agent], base / AGENTS[agent] / "skills", destination]:
        if directory.is_symlink():
            raise ValueError("Refusing to write through a symlink: " + str(directory))
    instruction = instruction_path(base, agent) if project is not None else None
    if instruction and (instruction.is_symlink() or (instruction.exists() and not instruction.is_file())):
        raise ValueError("Instruction file must be a regular, non-symlink file.")
    old = instruction.read_bytes() if instruction and instruction.exists() else b""
    new = merged_instructions(old, block_for(skill_relative)) if instruction else b""
    unchanged = same_install(destination, manifest, source)
    if destination.exists() and not unchanged and not replace:
        raise ValueError("A different or locally edited skill exists. Use --replace to back it up: " + str(destination))
    if source.resolve() == destination.resolve() and not unchanged:
        raise ValueError("Cannot replace the running source. Use a separate extracted copy.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = base / ".design-language-backups" / (NAME + "-" + stamp)
    if backup_root.parent.is_symlink():
        raise ValueError("Refusing to write backups through a symlink.")
    backups, backup_skill, stage, installed_new = [], None, None, False
    try:
        if not unchanged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".lui-stage-", dir=destination.parent))
            for relative in list(manifest["files"]) + ["manifest.json"]:
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, target)
            (stage / ".installed.json").write_text(json.dumps({"name": NAME, "version": manifest["version"], "agent": agent, "installed_at": stamp}, indent=2) + "\n", encoding="utf-8")
            if destination.exists():
                backup_root.mkdir(parents=True, exist_ok=True)
                backup_skill = backup_root / "previous-skill"
                shutil.move(str(destination), str(backup_skill))
                backups.append(str(backup_skill))
            os.replace(stage, destination)
            stage, installed_new = None, True
        if instruction and new != old:
            if instruction.exists():
                backup_root.mkdir(parents=True, exist_ok=True)
                previous_doc = backup_root / (instruction.name + ".bak")
                previous_doc.write_bytes(old)
                backups.append(str(previous_doc))
            atomic_write(instruction, new)
    except Exception:
        if installed_new and destination.exists():
            shutil.rmtree(destination)
        if backup_skill and backup_skill.exists():
            shutil.move(str(backup_skill), str(destination))
        raise
    finally:
        if stage and stage.exists():
            shutil.rmtree(stage)
    return {"status": "already current" if unchanged and (not instruction or new == old) else "installed", "skill": str(destination), "instructions": str(instruction) if instruction else None, "backups": backups}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project", type=Path, help="Existing project root.")
    scope.add_argument("--global", dest="global_scope", action="store_true", help="Install to the current user's skill directory; no global instructions changed.")
    parser.add_argument("--agent", choices=AGENTS, default="codex")
    parser.add_argument("--replace", action="store_true", help="Back up before replacing a changed installation.")
    args = parser.parse_args()
    try:
        result = install(ROOT, project=args.project, agent=args.agent, replace=args.replace)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Start a new agent session and ask it to use lumina-interface-language.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
