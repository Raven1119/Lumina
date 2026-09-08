"""Experiment E0 data bridge from Directive delivery to one Root decision."""

from __future__ import annotations

from dataclasses import replace

from Mind.directive import DirectiveApplication
from Mind.trace import MAX_DECISION_ID_CHARS, MAX_DIRECTIVE_CHARS
from Mind.task_view import directive_limit


FIXED_DIRECTIVE_TEXT = "Re-check assumption X before continuing."
MAX_RENDERED_DIRECTIVE_CHARS = 1_200

DecisionAdvisory = tuple[str, str]


def decision_advisory_from(
    application: object, *, contract=None,
) -> DecisionAdvisory | None:
    """Project one validated D application into inert Execution input data."""
    if type(application) is not DirectiveApplication:
        return None
    decision_id = application.decision_id
    text = application.text
    if (
        type(decision_id) is not str
        or not decision_id.strip()
        or decision_id != decision_id.strip()
        or len(decision_id) > MAX_DECISION_ID_CHARS
        or type(text) is not str
        or not text.strip()
        or text != text.strip()
        or len(text) > directive_limit(contract)
    ):
        return None
    context = application.as_model_context()
    if len(context) > MAX_RENDERED_DIRECTIVE_CHARS + directive_limit(contract) - MAX_DIRECTIVE_CHARS:
        return None
    return decision_id, context


def decision_advisory_for_execution(
    application: object, *, execution_ref: str, decision_id: str, contract=None,
) -> DecisionAdvisory | None:
    """Host routing: validate the full recipient before projecting its local ID.

    The caller obtains both recipient identities from the receiving Execution
    owner. Mind's durable APPLIED record keeps the full run/Root/decision target.
    """
    if type(application) is not DirectiveApplication:
        return None
    if any(type(value) is not str or not value or value != value.strip()
           or len(value) > MAX_DECISION_ID_CHARS for value in (execution_ref, decision_id)):
        return None
    if application.decision_id != f"{execution_ref}:root:{decision_id}":
        return None
    if decision_advisory_from(application, contract=contract) is None:
        return None
    return decision_advisory_from(replace(application, decision_id=decision_id), contract=contract)
