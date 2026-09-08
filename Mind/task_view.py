"""Owner task role projection; no model summarization or second Intention."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

EXPRESSION_CONTRACT_VERSION = "cognitive-submit-d7-v1"
THINKING_CONTRACT_VERSION = "cognitive-submit-d7-v2"
CONTINUITY_CONTRACT_VERSION = "cognitive-submit-d7-v3"
INTEGRATION_CONTRACT_VERSION = "cognitive-chain-v67"
INTEGRATION_CONTRACT_VERSIONS = ("cognitive-chain-v1", "cognitive-chain-v2", "cognitive-chain-v3", "cognitive-chain-v4", "cognitive-chain-v5", "cognitive-chain-v6", "cognitive-chain-v7", "cognitive-chain-v8", "cognitive-chain-v9", "cognitive-chain-v10", "cognitive-chain-v11", "cognitive-chain-v12", "cognitive-chain-v13", "cognitive-chain-v14", "cognitive-chain-v15", "cognitive-chain-v16", "cognitive-chain-v20", "cognitive-chain-v49", "cognitive-chain-v51", "cognitive-chain-v53", "cognitive-chain-v55", "cognitive-chain-v56", "cognitive-chain-v57", "cognitive-chain-v58", "cognitive-chain-v59", "cognitive-chain-v60", "cognitive-chain-v61", "cognitive-chain-v62", "cognitive-chain-v64", "cognitive-chain-v65", "cognitive-chain-v66", INTEGRATION_CONTRACT_VERSION)
INTEGRATION_RECOVERY_VERSIONS = ("cognitive-chain-v7", "cognitive-chain-v8", "cognitive-chain-v9", "cognitive-chain-v10", "cognitive-chain-v11", "cognitive-chain-v12", "cognitive-chain-v13", "cognitive-chain-v14", "cognitive-chain-v15", "cognitive-chain-v16", "cognitive-chain-v20", "cognitive-chain-v49", "cognitive-chain-v51", "cognitive-chain-v53", "cognitive-chain-v55", "cognitive-chain-v56", "cognitive-chain-v57", "cognitive-chain-v58", "cognitive-chain-v59", "cognitive-chain-v60", "cognitive-chain-v61", "cognitive-chain-v62", "cognitive-chain-v64", "cognitive-chain-v65", "cognitive-chain-v66", INTEGRATION_CONTRACT_VERSION)
EXPRESSION_CONTRACT_VERSIONS = (EXPRESSION_CONTRACT_VERSION, THINKING_CONTRACT_VERSION,
                              CONTINUITY_CONTRACT_VERSION, *INTEGRATION_CONTRACT_VERSIONS)
TASK_VIEW_VERSION = "owner-task-views-v1"
EXPRESSION_OUTPUT_CHARS = 6000
OWNER_TASK_GOAL_CHARS = 4000


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def execution_goal(task):
    """Render one authorized task, keeping business acceptance verbatim."""
    if (type(task) is not dict or set(task) != {"business_goal", "execution_protocol"}
            or any(type(v) is not str or not v.strip() for v in task.values())):
        raise ValueError("invalid_owner_task")
    goal = task["business_goal"] + "\n\nExecution protocol:\n" + task["execution_protocol"]
    if len(goal) > OWNER_TASK_GOAL_CHARS:
        raise ValueError("owner_task_too_large")
    return goal


def mind_task_view(task, actual_goal):
    if execution_goal(task) != actual_goal:
        raise ValueError("owner_task_goal_conflict")
    return {"version": TASK_VIEW_VERSION, "owner_task_sha256": fingerprint(task),
        "execution_goal_sha256": fingerprint(actual_goal), "goal": task["business_goal"]}


def project_observation(observation, task_view=None):
    """Validate owner identity before projecting an Execution observation."""
    if task_view is None or observation["capability"] != "inspect_execution":
        return dict(observation)
    if fingerprint(observation["goal"]) != task_view["execution_goal_sha256"]:
        raise ValueError("execution_task_view_conflict")
    return {**observation, "goal": task_view["goal"]}


def output_limit(context):
    return EXPRESSION_OUTPUT_CHARS if context.get("contract_version") in EXPRESSION_CONTRACT_VERSIONS else 2000


def directive_limit(contract):
    """V67 shares the bounded commit; historical Directive fields retain 1000."""
    return EXPRESSION_OUTPUT_CHARS if contract == 'cognitive-chain-v67' else 1000


def evidence_read_limits(contract):
    """Literal read text and serialized observation limits, fixed by activation."""
    return (8000, 9000) if contract in {"cognitive-chain-v51", "cognitive-chain-v53", "cognitive-chain-v55", "cognitive-chain-v56", "cognitive-chain-v57", "cognitive-chain-v58", "cognitive-chain-v59", "cognitive-chain-v60", "cognitive-chain-v61", "cognitive-chain-v62", "cognitive-chain-v64", "cognitive-chain-v65", "cognitive-chain-v66", "cognitive-chain-v67"} else (2700, 3000)


def analysis_question_limit(contract):
    """V55 shares the existing whole-submission allocation; older fields keep 800."""
    return EXPRESSION_OUTPUT_CHARS if contract in {"cognitive-chain-v55", "cognitive-chain-v56", "cognitive-chain-v57", "cognitive-chain-v58", "cognitive-chain-v59", "cognitive-chain-v60", "cognitive-chain-v61", "cognitive-chain-v62", "cognitive-chain-v64", "cognitive-chain-v65", "cognitive-chain-v66", "cognitive-chain-v67"} else 800


def execution_view_limits(context=None):
    """Bound raw owner goal and inspect envelope by the persisted contract."""
    if (isinstance(context, Mapping) and context.get("contract_version") in {"cognitive-chain-v53", "cognitive-chain-v55", "cognitive-chain-v56", "cognitive-chain-v57", "cognitive-chain-v58", "cognitive-chain-v59", "cognitive-chain-v60", "cognitive-chain-v61", "cognitive-chain-v62", "cognitive-chain-v64", "cognitive-chain-v65", "cognitive-chain-v66", "cognitive-chain-v67"}
            and isinstance(context.get("task_view"), Mapping)):
        return OWNER_TASK_GOAL_CHARS, 6000
    return 2000, 3000


def context_limit(context):
    if context.get("contract_version") in {"cognitive-chain-v49", "cognitive-chain-v51", "cognitive-chain-v53", "cognitive-chain-v55", "cognitive-chain-v56", "cognitive-chain-v57", "cognitive-chain-v58", "cognitive-chain-v59", "cognitive-chain-v60", "cognitive-chain-v61", "cognitive-chain-v62", "cognitive-chain-v64", "cognitive-chain-v65", "cognitive-chain-v66", "cognitive-chain-v67"}:
        # Up to 42 literal owner/evidence records plus existing cognition; no summaries.
        return 64000
    # Old 8k state + one 6k submission and bounded event/task overhead.
    # This is capacity, not compaction: overflow still fails atomically.
    return 16000 if context.get("contract_version") in {CONTINUITY_CONTRACT_VERSION, *INTEGRATION_CONTRACT_VERSIONS} else 8000


def restore_execution_goal_projection(document, owner_task):
    """Restore a verified owner goal after the historical 1024-character field cap.

    This changes the actual wire only for callers supplying the immutable owner
    task. No summary or inferred goal is authorized; historical adapters opt out.
    """
    goal = execution_goal(owner_task)
    projected = document.get("goal")
    if (not isinstance(projected, dict) or projected.get("original_chars") != len(goal)
            or not isinstance(projected.get("text"), str)
            or not goal.startswith(projected["text"])):
        raise ValueError("execution_goal_projection_identity_conflict")
    return {**document, "goal": {"text": goal, "original_chars": len(goal), "truncated": False}}
