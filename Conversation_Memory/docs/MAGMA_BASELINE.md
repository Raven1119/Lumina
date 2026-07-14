# MAGMA Baseline

## 1. Upstream Revision

- Repository: `https://github.com/FredJiang0324/MAGMA.git`
- Commit: `467cb70b67ac337b22fdb42194d37c04ad701b62`
- Recorded: `2026-07-14T11:46:02.3969089+08:00`
- Branch: `main`; upstream status was clean before and after the baseline.

## 2. Repository and License

`LICENSE` exists and contains the MIT License (copyright 2024 Anonymous Authors).
The checkout contains `main.py`, LoCoMo and LongMemEval evaluation scripts,
`memory/`, `utils/`, example JSON, and the included `data/locomo10.json`.

## 3. Environment

- OS: Windows, PowerShell execution environment.
- Python: CPython 3.13.9.
- Virtual environment: `Conversation_Memory/.venv`.
- pip/setuptools/wheel: 26.1.2 / 83.0.0 / 0.47.0.
- Core resolved versions include NetworkX 3.6.1, NumPy 2.5.1, SciPy
  1.18.0, scikit-learn 1.9.0, faiss-cpu 1.14.3, PyTorch 2.13.0,
  Transformers 5.13.1, Sentence Transformers 5.6.0, OpenAI 2.45.0,
  tiktoken 0.13.0, and pytest 9.1.1.

## 4. Dependency Installation

The exact upstream command was attempted:

```powershell
Conversation_Memory/.venv/Scripts/python.exe -m pip install -r Conversation_Memory/upstream/MAGMA/requirements.txt
```

It failed while installing the development-only Jupyter dependency tree because
Windows long paths were unavailable. The first root error was an `OSError [Errno
2]` for a JupyterLab widget `.js.map` path under `.venv/share/jupyter/labextensions`.
No upstream requirement was edited. The unchanged core, embedding, LLM,
utilities, and pytest requirement specifiers were then installed directly;
`pip check` reported no broken requirements.

The default `all-MiniLM-L6-v2` model was downloaded once from Hugging Face and
subsequent runs set `HF_HUB_OFFLINE=1`. The model cache is not a deliverable.

## 5. Configuration and Credentials

README and source advertise `OPENAI_API_KEY` via environment or `.env`.
`MemoryBuilder` has a simple extraction fallback and MiniLM provides local
embeddings. `OllamaController` also exists in `utils/memory_layer.py`, but the
main baseline path initializes OpenAI when a key is present.

There is a source-level contradiction: `memory/__init__.py` imports
`memory/llm_judge.py`, which constructs `OpenAI(...)` at import time. With no
key, package import and pytest collection fail before fallback initialization.
The baseline wrapper supplies a non-secret placeholder only during imports,
removes it before constructing `MemoryBuilder`, and therefore exercises the
documented no-LLM fallback. No API request or real credential is used.

## 6. Repository Structure

- Entry points: `main.py`, `test_fixed_memory.py`,
  `test_longmemeval_chunked.py`.
- Dataset loaders: `load_dataset.py`, `load_longmemeval.py`, and a duplicate
  `utils/load_dataset.py`.
- Core: `memory/memory_builder.py`, `trg_memory.py`, `graph_db.py`,
  `vector_db.py`, `query_engine.py`, `temporal_parser.py`, and
  `answer_formatter.py`.
- Dependencies: root `requirements.txt`; no `pyproject.toml` or package metadata.
- Configuration: environment variables and CLI flags; `.env.example` exists.
- Persistence: configured cache directory containing `graph.json`,
  `vectors/index.faiss`, `vectors/metadata.json`, and `keyword_index.json`.

The implementation uses one physical `NetworkXGraphDB`, not four physical
graphs. Temporal, semantic, causal, and entity views are represented by edge
types/subtypes in that one graph.

## 7. Available Tests

The repository provides evaluation scripts named `test_*.py`, but they are not
a normal isolated unit suite. Command attempted:

```powershell
cd Conversation_Memory/upstream/MAGMA
../../.venv/Scripts/python.exe -m pytest --collect-only -q
```

Result: zero tests collected and three collection errors after 62.20 seconds.
All errors originate at `memory/llm_judge.py:12`, where `OpenAI` is constructed
without credentials. The affected modules are `memory/test_harness.py`,
`test_fixed_memory.py`, and `test_longmemeval_chunked.py`. These scripts also
depend on external models/API credentials and, for full evaluation, datasets.

## 8. Baseline Execution

The Lumina-owned wrapper imports only public upstream loaders/classes and does
not copy or rewrite MAGMA logic. It loads one synthetic LoCoMo sample, runs
`MemoryBuilder.build_memory`, persists it, reloads it, and runs three
`QueryEngine.query(..., top_k=5)` calls.

Observed deterministic aggregate results from a clean output directory:

- 6 event nodes, 72 edges, 6 FAISS vectors;
- link types: 24 temporal, 42 semantic, 6 causal;
- entity batch count: 0;
- restart probe: 6 nodes, 6 vectors, 62 keyword terms.

## 9. Synthetic Input

`fixtures/magma_baseline_locomo.json` contains two sessions and six turns about
Raven's membrane experiment. It includes people, an experiment and solvent,
chronology, causal language, semantic overlap, `yesterday`, and `today`. No real
Draft or user data is present.

## 10. Memory Write Result

The write completed without changing upstream code. `MemoryBuilder.build_memory`
created all six event nodes. Batch construction created temporal, semantic,
causal `RESPONSE_TO`, QA, context, and temporal-proximity edges. Entity edges
were not created because `extract_event` leaves `entities=[]` when no LLM is
configured; its `_simple_entity_extraction` is only reached from LLM exception
branches, not from the no-controller branch.

The reported “causal” links represent dialogue flow (`RESPONSE_TO` and
`ANSWERED_BY`), not verified semantic causation from the word “because”.

## 11. Recall Result

All three required queries returned five bounded nodes and included the correct
evidence:

- completion query included “I completed the membrane experiment yesterday”;
- failure query ranked the solvent-evaporation explanation first;
- change query ranked “I changed the solvent...” first.

This is retrieval/context generation, not an LLM-produced final answer. The
query router is rule-based, retrieval combines vector, keyword, and full-node
scan ranks with reciprocal-rank fusion, then uses adaptive BFS and heuristic
reranking. Node UUIDs are newly generated on each rebuild, so identifiers and
tie ordering are not stable across clean rebuilds.

## 12. Generated Storage

The run generated `graph.json`, `vectors/index.faiss`,
`vectors/metadata.json`, `keyword_index.json`, and a diagnostic
`baseline_result.json` under `Conversation_Memory/baseline_output/`. These
generated databases/results are removed after inspection and must not be
committed. A reload recovered 6 nodes, 6 vectors, and 62 keyword terms.

## 13. Relative-Time Behavior

`TemporalParser.extract_temporal_reference("yesterday",
datetime(2026, 7, 14, 10, 0))` returned `2026-07-13T10:00:00`.

However, `MemoryBuilder.extract_event` only passes LLM-returned
`dates_mentioned` through this parser. In no-LLM mode it does not scan the raw
turn text for relative expressions, so persisted nodes retain `yesterday` and
`today` as raw text without normalized metadata. `parse_session_timestamp`
returns naive datetimes; no timezone is parsed or preserved. The reference is
the session timestamp plus a synthetic `dia_id`-derived hour offset, not an
explicit source-message timestamp.

## 14. External Dependencies

- Hugging Face is required once for the default MiniLM model unless already
  cached.
- OpenAI is required for LLM extraction, LLM evaluation, and final answer
  generation on the evaluation paths.
- MiniLM embedding, deterministic graph construction, persistence, and context
  recall run without an API key after the model is cached.
- Full LoCoMo is included; full LongMemEval must be downloaded separately.

## 15. Failures and Limitations

1. Full `requirements.txt` installation fails on Windows long-path handling in
   the Jupyter development dependency tree.
2. No-key pytest collection fails at `memory/llm_judge.py:12`.
3. Four graphs are not physically separated; they are edge views in one graph.
4. No-LLM extraction does not populate entity metadata in `MemoryBuilder`.
5. Relative time is not normalized from raw text in the no-LLM write path and
   timezone is absent.
6. Query output is limited by node count (`top_k`) and traversal budgets, but
   rendered context has no token or character ceiling.
7. Public provenance is limited to node metadata such as speaker, session,
   `dia_id`, timestamp, and original text; it has no Lumina segment provenance.
8. Several fallbacks catch broad exceptions and log warnings; the import-time
   OpenAI construction is not caught.

## 16. Reproduction Commands

```powershell
python -m venv Conversation_Memory/.venv
Conversation_Memory/.venv/Scripts/python.exe -m pip install --upgrade pip setuptools wheel
Conversation_Memory/.venv/Scripts/python.exe -m pip install -r Conversation_Memory/upstream/MAGMA/requirements.txt
# If Windows long paths block only the Jupyter dev tree, install unchanged
# requirements lines 2-20 plus pytest, as documented above.
Conversation_Memory/.venv/Scripts/python.exe Conversation_Memory/scripts/run_magma_baseline.py
git -c safe.directory='C:/Users/wmywb/PycharmProjects/Lumina/Conversation_Memory/upstream/MAGMA' -C Conversation_Memory/upstream/MAGMA status --short
python -m pytest -q
git diff --check
```

## 17. Baseline Verdict

**PARTIAL**

Unmodified MAGMA successfully writes, indexes, persists, reloads, and recalls
synthetic data without an API key after MiniLM is cached. The baseline is not
PASS because the no-key path does not construct the entity view, relative-time
normalization is not integrated into that write path, timezone is discarded,
the official test collection fails without credentials, and the full declared
dependency install is blocked by Windows long paths.
