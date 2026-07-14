from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class IngestionStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @staticmethod
    def key(segment_id: str, ingestion_version: str) -> str:
        return f"{segment_id}:{ingestion_version}"

    def read_all(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("state_corrupt") from exc
        if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
            raise ValueError("state_corrupt")
        return data

    def get(self, key: str) -> dict[str, Any] | None:
        return self.read_all().get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        data = self.read_all()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
