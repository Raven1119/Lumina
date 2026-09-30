"""Small JSON persistence primitives shared by the owning organs."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def sync_directory(directory):
    """Persist renamed/new directory entries where the OS supports directory fsync.

    Windows retains file fsync + atomic replace; this is not a promise of
    directory-entry durability through power loss on every filesystem.
    """
    if os.name == 'nt':
        return
    handle = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def write_json(path, value):
    """Atomic durable replacement; callers hold the owning organ's writer lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.state-', delete=False) as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def inspect_safely(reader, *args):
    """Diagnostic projection only: preserve other owners when one cannot be read."""
    try:
        return reader(*args)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        return {'status': 'unavailable', 'diagnostic': {'issue': {'kind': type(error).__name__}}}


def inspect_jsonl(path, accept, *, max_line_bytes=1048576):
    """Validate a bounded prefix in memory; never repair/truncate the source log."""
    count = 0
    issue = None
    try:
        with Path(path).open('rb') as stream:
            while raw := stream.readline(max_line_bytes + 2):
                if len(raw) > max_line_bytes + 1 or not raw.endswith(b'\n'):
                    issue = {'line': count + 1, 'kind': 'incomplete_record' if len(raw) <= max_line_bytes + 1 else 'record_too_large'}
                    break
                try:
                    accept(json.loads(raw))
                except (ValueError, TypeError, KeyError, IndexError) as error:
                    issue = {'line': count + 1, 'kind': type(error).__name__}
                    break
                count += 1
    except OSError as error:
        issue = {'line': count + 1, 'kind': type(error).__name__}
    return {'valid_records': count, 'issue': issue}
