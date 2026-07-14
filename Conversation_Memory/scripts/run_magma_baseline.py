"""Run an unmodified MAGMA baseline against a synthetic LoCoMo fixture."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
UPSTREAM = WORKSPACE / "upstream" / "MAGMA"
FIXTURE = WORKSPACE / "fixtures" / "magma_baseline_locomo.json"
OUTPUT = WORKSPACE / "baseline_output"

# The setup step downloads the upstream default model once. Baseline reruns use
# that cache deterministically and must not require network access.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# memory/__init__.py imports llm_judge.py, which constructs an OpenAI client
# during import. A non-secret placeholder permits import; removing it directly
# afterwards selects MemoryBuilder's documented no-LLM fallback.
os.environ.setdefault("OPENAI_API_KEY", "baseline-placeholder-not-a-secret")
sys.path.insert(0, str(UPSTREAM))

from load_dataset import load_locomo_dataset  # noqa: E402
from memory.graph_db import LinkType  # noqa: E402
from memory.memory_builder import MemoryBuilder  # noqa: E402
from memory.query_engine import QueryEngine  # noqa: E402
from memory.temporal_parser import TemporalParser  # noqa: E402

if os.environ.get("OPENAI_API_KEY") == "baseline-placeholder-not-a-secret":
    os.environ.pop("OPENAI_API_KEY")


def main() -> int:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sample = load_locomo_dataset(FIXTURE)[0]
    builder = MemoryBuilder(
        cache_dir=str(OUTPUT), use_episodes=False, embedding_model="minilm"
    )
    build_stats = builder.build_memory(sample)
    builder.save()

    reloaded = MemoryBuilder(
        cache_dir=str(OUTPUT), use_episodes=False, embedding_model="minilm"
    )
    reloaded.load()
    restart_probe = {
        "nodes_after_load": len(reloaded.trg.graph_db.nodes),
        "vectors_after_load": reloaded.trg.vector_db.size(),
        "keyword_terms_after_load": len(reloaded.node_index),
    }

    query_engine = QueryEngine(
        trg_memory=builder.trg, node_index=builder.node_index
    )
    recall = {}
    for qa in sample.qa:
        context, rendered = query_engine.query(qa.question, top_k=5)
        recall[qa.question] = {
            "node_ids": [node.node_id for node in context.anchor_nodes],
            "node_text": [node.content_narrative for node in context.anchor_nodes],
            "rendered_context": rendered,
            "metadata": context.metadata,
        }

    parser = TemporalParser()
    base = datetime.fromisoformat("2026-07-14T10:00:00")
    relative_time = {
        "expression": "yesterday",
        "reference": base.isoformat(),
        "normalized": parser.extract_temporal_reference("yesterday", base).isoformat(),
        "timezone_preserved": base.tzinfo is not None,
    }
    link_types = Counter(
        link.link_type.value if isinstance(link.link_type, LinkType) else str(link.link_type)
        for link in builder.trg.graph_db.links.values()
    )
    link_subtypes = Counter(
        link.properties.get("sub_type", "")
        for link in builder.trg.graph_db.links.values()
    )
    result = {
        "build_stats": build_stats,
        "physical_graphs": 1,
        "graph_backend": type(builder.trg.graph_db).__name__,
        "nodes": len(builder.trg.graph_db.nodes),
        "edges": len(builder.trg.graph_db.links),
        "link_types": dict(sorted(link_types.items())),
        "link_subtypes": dict(sorted(link_subtypes.items())),
        "vector_backend": type(builder.trg.vector_db).__name__,
        "vectors": builder.trg.vector_db.size(),
        "restart_probe": restart_probe,
        "relative_time": relative_time,
        "recall": recall,
    }
    result_path = OUTPUT / "baseline_result.json"
    serialized = json.dumps(result, indent=2, default=str)
    result_path.write_text(serialized, encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
