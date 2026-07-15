# Draft Turn Provenance V2

## Scope

New chat turns now carry native, Lumina-owned provenance through Hot Draft,
Cold-first compaction, manual Dream ingestion, MAGMA events, and Recall
evidence. This is a data-contract upgrade only. It does not add summaries,
automatic Dream, chat-time ingestion, Recall injection, or a conversation
session system.

## Turn contract

Every newly created user and assistant/fallback turn has:

```json
{
  "turn_id": "opaque-stable-id",
  "role": "user",
  "text": "synthetic example",
  "created_at": "2026-07-14T14:00:00.000000Z",
  "source_timezone": "America/New_York",
  "timezone_source": "client"
}
```

`turn_id` is generated with the injected `TurnIdFactory` before the first
persistence attempt. User and assistant turns receive separate IDs, and the
same text is never an identity. Retrying the same created DTO reuses its ID;
the Hot store treats an exact repeated ID/record as an idempotent append.

`created_at` comes from the injected `Clock`. The user time is captured after
basic API validation and before model generation. The assistant/fallback time
is captured after its final text is known. Both must be aware and are stored as
RFC 3339 UTC. They are separate instants.

## Client timezone

`POST /api/chat` accepts optional `client_timezone`. The browser sends
`Intl.DateTimeFormat().resolvedOptions().timeZone` when available. Python
`zoneinfo.ZoneInfo` validates it; valid IANA names are recorded with
`timezone_source=client`.

Missing or invalid values use `LUMINA_DEFAULT_TIMEZONE`, or `UTC` when it is
unset or invalid, and record `timezone_source=configured_default`. The field is
not forwarded to the model provider and is not included in public responses.
No IP lookup or external timezone service is used.

## Hot and Cold persistence

New Hot JSONL records use `schema_version=2` and preserve all turn fields.
Reloading a Hot store reconstructs the same ID, instant, IANA timezone, and
timezone source. Model context remains role/text only.

The compactor serializes each `DraftTurn` as a complete immutable turn object.
It never regenerates an ID, substitutes compaction time, or applies a segment
timezone. A Cold segment is V2 when it contains native turns:

```json
{
  "schema_version": 2,
  "segment_id": "opaque-segment-id",
  "created_at": "2026-07-15T04:10:00+00:00",
  "source": "hot_draft_precompression",
  "state": "pending_digest",
  "turns": [
    {
      "turn_id": "opaque-stable-id",
      "role": "user",
      "text": "synthetic example",
      "created_at": "2026-07-15T03:55:00.000000Z",
      "source_timezone": "America/New_York",
      "timezone_source": "client"
    }
  ]
}
```

The segment `created_at` describes segment creation only. Cold append must
still durably succeed before logical compaction state advances. Physical Hot
Draft remains append-only.

## Dream and Conversation Memory

Dream checks each turn independently. A complete native provenance set uses
the turn's own ID, `created_at`, timezone, and timezone source. A partial set is
rejected rather than completed with invented values. Mixed transition segments
can therefore contain native turns and role/text-only legacy turns without
mislabeling either.

Conversation Memory passes each turn timestamp to its corresponding MAGMA
event. Relative expressions are resolved after converting the instant into
that turn's source timezone, so midnight and daylight-saving boundaries follow
the named local calendar. Chinese and English temporal expressions use the same
Lumina-owned half-open aware-UTC interval contract; event time remains the turn
`created_at`, while mentioned time is retained separately in
`temporal_mentions`/`dates_mentioned`. Directed Chinese weekdays use previous
or next calendar-week semantics. Recall `SourceProvenance` includes
`timezone_source` in addition to segment, conversation, turn, timestamp,
timezone, and ingestion version.

The current production Draft path still has no real conversation/thread ID.
Dream continues to derive `cold-draft:{segment_id}`; this is an adapter identity,
not a browser-session claim.

## Legacy compatibility

Existing role/text-only Hot lines and schema-1/unschematized Cold records remain
readable. They are never rewritten or automatically migrated. Existing
consumed segments and MAGMA data are not re-ingested.

For a legacy Cold turn, Dream truthfully uses:

```text
turn_id = segment_id + deterministic source index
timestamp = segment.created_at
timezone = explicit segment timezone or its aware offset
timezone_source = legacy_segment_fallback
```

This fallback is not described as a precise user timezone. Old Recall metadata
without the new field also projects as `legacy_segment_fallback`.

## Current boundaries

The rejected cosine relevance experiment is not part of the Recall path.
Recall is not injected into `/api/chat`. There is no automatic migration,
summary compression, physical Hot truncation, automatic Dream, or full
conversation/thread identity.
