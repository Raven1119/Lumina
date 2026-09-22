"""Immutable, bounded payload files owned by the existing Memory backend.

Only ingestion writes/repairs these files. Reads use a generated content ID,
never a checkpoint scan, and never repair. There is deliberately no read cache.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

BODY_VERSION = "memory-body-v1"
FORMATION_BODY_VERSION = "grounded-formation-v7"
MAX_BODY_BYTES = 65536
_ID = re.compile(r"body_v1:([0-9a-f]{64})\Z")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal_body(content):
    digest = hashlib.sha256(encoded(content)).hexdigest()
    return {**content, "body_ref": "body_v1:" + digest, "body_digest": digest}


def validate_body(body):
    from .models import SourceProvenance
    if not isinstance(body, dict) or set(body) != {
        "version", "formation_version", "source_digest", "segment_id", "conversation_id",
        "block_position", "slots", "complete", "receipts", "body_ref", "body_digest",
    }:
        raise ValueError("body_payload_invalid")
    content = {k: v for k, v in body.items() if k not in {"body_ref", "body_digest"}}
    if seal_body(content) != body or body["version"] != BODY_VERSION or body["formation_version"] != FORMATION_BODY_VERSION:
        raise ValueError("body_payload_digest_invalid")
    if (type(body["block_position"]) is not int or body["block_position"] < 0
            or type(body["complete"]) is not bool or not isinstance(body["slots"], list)
            or not 1 <= len(body["slots"]) <= 8
            or set(body["receipts"]) != {"F1", "F2"}):
        raise ValueError("body_payload_invalid")
    for receipt in body["receipts"].values():
        if (not isinstance(receipt, dict) or set(receipt) != {"request_digest", "response_digest", "parsed_digest", "execution"}
                or any(not re.fullmatch(r"[0-9a-f]{64}", receipt.get(k, "")) for k in ("request_digest", "response_digest", "parsed_digest"))
                or receipt["execution"] not in {"provider", "deterministic_empty"}):
            raise ValueError("body_receipt_invalid")
    seen = set()
    for pos, slot in enumerate(body["slots"]):
        if not isinstance(slot, dict) or slot.get("position") != pos:
            raise ValueError("body_slot_invalid")
        if slot.get("status") != "supported":
            if set(slot) != {"position", "status"} or slot["status"] not in {"rejected", "insufficient", "invalid"} or body["complete"]:
                raise ValueError("body_gap_invalid")
            continue
        if set(slot) != {"position", "status", "unit_id", "text", "provenance", "source_refs", "used_source_ids"}:
            raise ValueError("body_unit_invalid")
        provenance = SourceProvenance(**slot["provenance"])
        prefix = "User stated: " if provenance.source_role == "user" else "Lumina stated: "
        if (not isinstance(slot["unit_id"], str) or not re.fullmatch(r"grounded_memory_v7:[0-9a-f]{64}", slot["unit_id"])
                or slot["unit_id"] in seen or not isinstance(slot["text"], str)
                or not slot["text"].startswith(prefix) or not slot["text"][len(prefix):].strip()
                or len(slot["text"]) > 2016
                or provenance.segment_id != body["segment_id"] or provenance.conversation_id != body["conversation_id"]
                or provenance.ingestion_version != FORMATION_BODY_VERSION):
            raise ValueError("body_unit_invalid")
        refs = slot["source_refs"]
        if (not isinstance(refs, list) or not refs or len(refs) > 96
                or any(not isinstance(r, dict) or set(r) != {"turn_id", "source_role", "source_timestamp", "source_timezone", "source_digest"}
                       or r["source_role"] not in {"user", "assistant"}
                       or not re.fullmatch(r"[0-9a-f]{64}", r["source_digest"]) for r in refs)
                or not isinstance(slot["used_source_ids"], list)
                or not set(slot["used_source_ids"]).issubset({r["turn_id"] for r in refs})
                or provenance.turn_id not in slot["used_source_ids"]):
            raise ValueError("body_source_invalid")
        origin = next((r for r in refs if r["turn_id"] == provenance.turn_id), None)
        if not origin or any(origin[k] != getattr(provenance, k) for k in ("source_role", "source_timestamp", "source_timezone")):
            raise ValueError("body_origin_invalid")
        seen.add(slot["unit_id"])
    if not seen or len(encoded(body)) > MAX_BODY_BYTES:
        raise ValueError("body_payload_size_invalid")
    return body


@dataclass(frozen=True)
class BodyRead:
    payload: dict | None = None
    bytes_read: int = 0
    error: str | None = None


class BodyPayloadStore:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, body_ref):
        match = _ID.fullmatch(body_ref) if isinstance(body_ref, str) else None
        if match is None:
            raise ValueError("body_ref_invalid")
        digest = match[1]
        return self.root / digest[:2] / (digest + ".json")

    def read(self, body_ref, *, max_bytes=MAX_BODY_BYTES):
        consumed = 0
        try:
            path = self._path(body_ref)
            limit = min(MAX_BODY_BYTES, max_bytes)
            if type(limit) is not int or limit <= 0 or path.is_symlink():
                return BodyRead(error="body_read_budget_or_path")
            if path.stat().st_size > limit:
                return BodyRead(error="body_read_budget")
            with path.open("rb") as handle:
                raw = handle.read(limit)
            consumed = len(raw)
            body = validate_body(json.loads(raw))
            if body["body_ref"] != body_ref:
                raise ValueError("body_ref_mismatch")
            return BodyRead(body, consumed)
        except Exception:
            return BodyRead(bytes_read=consumed, error="body_unavailable")

    def put(self, body, *, repair=False):
        validate_body(body)
        path = self._path(body["body_ref"])
        prior = self.read(body["body_ref"])
        if prior.payload == body:
            return False
        if path.exists() and not repair:
            raise ValueError("body_immutable_conflict")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".body-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encoded(body)); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name == "posix":
                fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return True
