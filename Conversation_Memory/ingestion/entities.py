import re

_IGNORED = frozenset({"The", "This", "That", "I", "Yes", "No", "Today", "Yesterday", "Tomorrow"})


def extract_entities(text: str, configured: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Deterministic proper-name fallback; intentionally not a full NER system."""
    found = list(configured)
    found.extend(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text))
    return tuple(dict.fromkeys(item.strip() for item in found if item.strip() and item not in _IGNORED))
