# Draft turn provenance v2

Every new Hot/Cold user or assistant turn carries `turn_id`, `created_at`,
`source_timezone`, and `timezone_source` alongside immutable role and text.
`core/turn_provenance.py` creates these fields. Hot compaction copies the full
storage turn into Cold before dropping it from Hot. The history API projects
only public ID, role, content, and UTC timestamp.

`core/memory_adapter.py` converts native turns to Memory's +08:00 input using
the injected clock and `LUMINA_DEFAULT_TIMEZONE`. Chat reads every raw Hot
turn, preserving its time. A legacy Hot turn without native time may be used
as recent context at the injected current time and receives a stable local
content/index identity for that read; it is not a claim about its historical
time. Explicit Cold rebuild skips legacy turns without timestamps and reports
the skipped count. Stored Cold bytes are unchanged.

The rolling summary adds `summary_until`, the time of its last archived turn.
Old summary records without this field remain valid; the shared Answer
request builder emits the summary without a date line. New summaries show
“截至 M月D日” as a separate user message. The builder formats user Hot turns
with their original time and states the gap from the most recent Hot turn to
the current message.

Memory traces are keyed by the stored assistant turn ID after Answer. Dream
maps 32-character hexadecimal Chat IDs to short `t01` aliases only inside
model prompts; accepted source IDs are translated back before graph writes.
The old Cold consumed marker does not drive Memory's new cursor. See
[Memory design](../Conversation_Memory/docs/DESIGN.md) for graph and Dream
ownership.
