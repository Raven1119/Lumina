# Conversation Memory v1

This is the authority for the current memory organ. The implementation is
`Conversation_Memory/engine/`, with the small production boundary in
`facade.py`. The laboratory under `Memory_lab/` calls this engine and retains
B1, P8, and P9 as isolated comparison presets. Production uses P8. The
[task card](../../docs/TASK_memory_v1.md) and [results](../../docs/RESULTS_memory_v1.md)
record the migration and its measured limits.

## Ownership and state

Cold Draft is the only source of raw conversation text. `core/` owns Hot,
Cold, summary, provenance, and Chat sequencing. Memory stores a derived Cold
turn index, graph state, an independent Cold cursor, recall traces, and Dream
receipts in `data/memory_v1/memory.sqlite` by default. SQLite uses WAL and
one connection per thread. Importing Memory opens neither SQLite nor BGE.

`MemoryV1.recall_and_render(message, hot, now)` reads the most recent committed
graph snapshot and returns the P8 rendered block and IDs that entered the
context. It does not write a trace. Chat records a trace only after its
assistant turn is stored, keyed by that assistant turn ID, with the three
private answer columns. `inspect` returns memory text, time label, π, pattern
flag, source count, and recent Dream log without private paths or provider
responses.

Dream is the only graph writer. It accepts a complete ordered Cold snapshot,
processes at most one bounded pair-aligned window, snapshots the eligible
trace IDs before model calls, and advances its own cursor only after integration,
pattern processing, and selected trace consumption complete. The Cold owner's
old pending/consumed markers are retained for historical data but do not drive
this cursor. A failed window stays pending; three consecutive failures pause
automatic Dream until a manual run succeeds. Dream's lock serializes runs;
model calls occur without Chat's write lock. The SQLite commit and Cold read
use short coordination. Responses are cached before application.

## P8 algorithm

P8 is frozen by the versioned `integrate_v4.md`, `pattern_v2.md`, and
`answer_v5.md` prompts. Integration uses old-memory reminders and source IDs
from the current Cold window. Chat's 32-character hexadecimal turn IDs are
shown as `t01` style aliases in the prompt and translated back during apply;
lab short IDs remain unchanged. Integration `used` entries are validated
against the answer and prior context with P8's common-word stoplist before
recall reinforcement and co-recall edges. Pattern candidates are selected by
P8's similarity, recurrence, and same-day rules; the associative slot uses
surprise ranking. `render_v4` shows near, remote, and core memories using the
same bounded local computation as the lab.

The engine's exact numeric parameters are in `engine/config.py` and the
versioned prompt text is in `prompts/`. B1 is a raw-window baseline without
Dream; P9 remains a lab-only trace-pattern candidate. The two development
sets and R0 byte comparison are the regression contract for production P8.

## Model and failure policy

Chat answer, rolling summary, Memory integration and pattern calls resolve
their model through `model_policy.py` and `config/model.toml`. The initial
default is DeepSeek-V4.1-Flash (`deepseek-flash`); `LUMINA_MODEL` sets a global model and
`LUMINA_CHAT_MODEL` or `LUMINA_MEMORY_MODEL` can override their roles.
Memory keeps its separate response cache and P8 lab fixtures keep their frozen
model identity. Production accepts only local BGE-M3 at
revision `5617a9f61b028005a4858fdac845db406aefb181` and weights SHA-256
`b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38`.
Unavailable embedding makes Chat recall empty and prevents automatic Dream;
there is no production MiniLM or hash fallback. Answer uses one call and
private three-column parsing; malformed JSON is recovered when it contains a
reply label, and natural language is returned as the reply.

## History

The old graph adapter and its read candidates were removed from the active
code in this migration. The `archive/pre-memory-v1` local tag preserves that
code. Earlier lab decisions and results remain in
[Memory Lab history](../../Memory_lab/docs/HISTORY.md). Historical test scores
are not evidence for a new runtime configuration.
