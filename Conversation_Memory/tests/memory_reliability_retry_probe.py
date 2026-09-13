"""Provider-free public-ingest restart probe against an explicit source snapshot."""
import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_root.resolve()
    sys.path[:0] = [str(source / "Conversation_Memory"), str(source)]
    from adapter.grounded_formation import FORMATION_ENTITY_VERSION
    from adapter.magma_adapter import MagmaMemoryAdapter
    from adapter.models import ColdDraftSegment, ColdDraftTurn
    from ingestion.state_store import IngestionStateStore

    class Model:
        def __init__(self, output):
            self.output, self.calls = output, []

        def generate(self, recent_context, user_message, *, system_prompt):
            kind = "extract" if system_prompt.startswith("Extract source-grounded") else "verify"
            self.calls.append(kind)
            if kind == "extract":
                return self.output
            payload = json.loads(user_message)
            return json.dumps({"units": [{"supported": True} for _ in payload["units"]], "mentions": [{"supported": True, "identity_supported": True} for _ in payload["mentions"]]})

    class Backend:
        def __init__(self):
            self.events, self.mentions, self.persist_calls = {}, {}, 0

        def find_entity_candidates(self, surface, *, limit):
            return ()

        def upsert_entity_mentions(self, records):
            self.mentions.update({m["mention_id"]: m for m in records})

        def find_memory_id(self, evidence_id):
            return evidence_id if evidence_id in self.events else None

        def add_event(self, text, timestamp, metadata):
            self.events[metadata["evidence_id"]] = metadata
            return metadata["evidence_id"]

        def persist(self):
            self.persist_calls += 1

        def create_relationships(self, ids):
            pass

    now = datetime(2026, 9, 13, tzinfo=UTC)
    text = "Test probe Wren-10 has mass 2 kg and width 4 cm."
    segment = ColdDraftSegment("retry-probe", "synthetic-probe", "pending_digest", (ColdDraftTurn("p1", "user", text, now, "UTC", "client"),), now, "UTC", "2")
    mention = {"handle": "w", "surface": "Wren-10", "turn_id": "p1", "occurrence": 0, "identity": "named", "same_as": None, "distinct_from": [], "identity_source_refs": []}
    def unit(relation, value, span):
        return {"text": "Wren-10 " + relation + " " + value, "subject": "Wren-10", "relation": relation, "value": value, "source_refs": [{"turn_id": "p1", "supporting_span": span}], "referenced_time": None, "subject_mention": "w", "object_mention": None, "mentions": ["w"]}
    outputs = {"bad_citation": json.dumps({"mentions": [mention], "units": [unit("mass", "2 kg", text), unit("width", "4 cm", "not in the source")]}), "bad_json": "{ invalid provider JSON"}
    report = {"measurement": "deterministic public-ingest orchestration only; zero provider calls", "scenarios": []}
    with tempfile.TemporaryDirectory(prefix="lumina-reliability-retry-probe-") as temp:
        for name, output in outputs.items():
            backend, model = Backend(), Model(output)
            store = IngestionStateStore(Path(temp) / name / "state.json")
            item = {"case": name, "attempts": []}
            for index in range(2):
                before = len(model.calls)
                adapter = MagmaMemoryAdapter(backend, store, ingestion_version=FORMATION_ENTITY_VERSION, formation_model=model)
                result = adapter.ingest(segment)
                item["attempts"].append({"attempt": index + 1, "result": asdict(result), "calls": model.calls[before:], "checkpoint": store.read_all(), "event_count": len(backend.events), "mention_count": len(backend.mentions), "persist_calls": backend.persist_calls})
            report["scenarios"].append(item)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
