# Domain Docs

This is a single-context repository.

## Before exploring, read these

- `CONTEXT.md` at the repository root, when present.
- Relevant ADRs under `docs/adr/`, when present.

If these files do not exist, proceed silently. Do not create placeholders
merely because they are absent. The `domain-modeling` workflow creates them
lazily when terminology or decisions are actually resolved.

## Layout

```text
/
  CONTEXT.md
  docs/adr/
```

## Vocabulary

Use domain terms as defined in `CONTEXT.md`. Avoid synonyms that the glossary
explicitly rejects.

If a required concept is missing, reconsider whether the term is being invented
or record the genuine gap for `domain-modeling`.

## ADR conflicts

If proposed work conflicts with an ADR, surface the conflict explicitly instead
of silently overriding the decision.
