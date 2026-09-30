# Dream work

Read root `AGENTS.md` and [Memory v1 design](../Conversation_Memory/docs/DESIGN.md).
`Dream/runner.py` is the explicit CLI boundary. The app's automatic trigger
runs one bounded window after a real Chat reply when the embedding, cursor, and
threshold are ready. The same memory facade performs both paths.

The Cold owner supplies immutable original turns. Memory owns its own cursor,
receipts, and graph writes. The old Cold pending/consumed state does not drive
Dream. Run model calls under Dream's own serial lock, outside Chat's write
lock. Only the Cold snapshot read and the graph commit briefly coordinate.
A failed window keeps its cursor. After three consecutive failures, automatic
runs pause; manual runs can recover. Stop the service before CLI rebuild or
cursor changes.

Use temporary Cold and Memory directories for tests. Keep model responses and
private paths out of public API errors.
