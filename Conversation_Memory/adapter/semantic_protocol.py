"""Fixed read-only use labels shared by Mind parsing and Memory packing."""

USES = frozenset({"history", "analogy"})
HISTORY_RELATIONS = frozenset({"same_event", "same_entity_background", "historical_boundary"})
ANALOGY_RELATIONS = frozenset({
    "similar_constraint", "similar_failure_pattern", "similar_tradeoff",
    "similar_preference", "similar_workflow",
})
GRAPH_INTENTS = frozenset({"none", "same_event_detail", "analogy", "disambiguation", "boundary"})

# Historical names remain for import compatibility.
_HISTORY = HISTORY_RELATIONS
_ANALOGY = ANALOGY_RELATIONS


def semantic_schema_guidance(*, base: bool) -> str:
    """Render the same enumerations used by validation into the selector contract."""
    values = lambda group: ", ".join(sorted(group))
    guidance = (f"use MUST be one of: {values(USES)}. "
                f"For history relation MUST be one of: {values(HISTORY_RELATIONS)}. "
                f"For analogy relation MUST be one of: {values(ANALOGY_RELATIONS)}. ")
    if base:
        guidance += (f"graph_intent MUST be one of: {values(GRAPH_INTENTS)}. "
                     "seek_graph=false requires graph_intent=none and graph_need=\"\". "
                     "seek_graph=true requires a non-none graph_intent and a specific "
                     "1–240 character graph_need describing missing evidence, never an answer.")
    return guidance


def validate_use_relation(use: object, relation: object) -> bool:
    return (type(use) is str and type(relation) is str
            and ((use == "history" and relation in HISTORY_RELATIONS)
                 or (use == "analogy" and relation in ANALOGY_RELATIONS)))


def usage_guidance(use: str, relation: str) -> str:
    if not validate_use_relation(use, relation):
        raise ValueError("invalid_semantic_selection")
    if use == "history":
        extra = (" A newer explicit user decision overrides this old boundary."
                 if relation == "historical_boundary" else "")
        return (
            "[Memory use: HISTORY; relation=" + relation + ". "
            "Use this Fact only within its recorded speaker, time and scope. "
            "Do not infer missing outcome, permission, identity or current status."
            + extra + "]"
        )
    return (
        "[Memory use: ANALOGY; relation=" + relation + ". "
        "This is a different past experience. Use it only as a comparison or "
        "possible lesson. Do not transfer its people, event identity, outcome, "
        "permission or status to the current case.]"
    )
