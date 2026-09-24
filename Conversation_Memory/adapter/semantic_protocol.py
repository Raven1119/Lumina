"""Fixed read-only use labels shared by Mind parsing and Memory packing."""

_HISTORY = frozenset({"same_event", "same_entity_background", "historical_boundary"})
_ANALOGY = frozenset({
    "similar_constraint", "similar_failure_pattern", "similar_tradeoff",
    "similar_preference", "similar_workflow",
})


def validate_use_relation(use: object, relation: object) -> bool:
    return (type(use) is str and type(relation) is str
            and ((use == "history" and relation in _HISTORY)
                 or (use == "analogy" and relation in _ANALOGY)))


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
