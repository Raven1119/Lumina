"""Owner task role projection; no model summarization or second Intention."""
from __future__ import annotations

import hashlib
import json

EXPRESSION_CONTRACT_VERSION = "cognitive-submit-d7-v1"
THINKING_CONTRACT_VERSION = "cognitive-submit-d7-v2"
CONTINUITY_CONTRACT_VERSION = "cognitive-submit-d7-v3"
EXPRESSION_CONTRACT_VERSIONS = (EXPRESSION_CONTRACT_VERSION, THINKING_CONTRACT_VERSION,
                              CONTINUITY_CONTRACT_VERSION)
TASK_VIEW_VERSION = "owner-task-views-v1"
EXPRESSION_OUTPUT_CHARS = 6000


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def execution_goal(task):
    """Render one authorized task, keeping business acceptance verbatim."""
    if (type(task) is not dict or set(task) != {"business_goal", "execution_protocol"}
            or any(type(v) is not str or not v.strip() for v in task.values())):
        raise ValueError("invalid_owner_task")
    goal = task["business_goal"] + "\n\nExecution protocol:\n" + task["execution_protocol"]
    if len(goal) > 2000:
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


def context_limit(context):
    # Old 8k state + one 6k submission and bounded event/task overhead.
    # This is capacity, not compaction: overflow still fails atomically.
    return 16000 if context.get("contract_version") == CONTINUITY_CONTRACT_VERSION else 8000
