"""Immutable advisory guidance; the receiving Execution owns delivery receipts."""
from dataclasses import dataclass


@dataclass(frozen=True)
class DirectiveApplication:
    directive_id: str
    decision_id: str
    text: str

    def as_model_context(self) -> str:
        return (
            "[Mind Supervisor Directive]\n"
            "Treat this high-level guidance as a strong advisory prior, "
            "not an order or execution plan.\n"
            f"{self.text}"
        )
