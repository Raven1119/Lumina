"""One authoritative task, projected for cognition and action."""
from __future__ import annotations
from Nervous.storage import fingerprint

COGNITIVE_CONTRACT_VERSION = "mind-cognition-v1"
TASK_VIEW_VERSION = "owner-task-views-v1"
EXPRESSION_OUTPUT_CHARS = 6000
OWNER_TASK_GOAL_CHARS = 4000


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


def output_limit(context=None):
    return EXPRESSION_OUTPUT_CHARS


def directive_limit(contract=None):
    return EXPRESSION_OUTPUT_CHARS


def evidence_read_limits(contract=None):
    return 8000, 9000


def analysis_question_limit(contract=None):
    return EXPRESSION_OUTPUT_CHARS


def execution_view_limits(context=None):
    return OWNER_TASK_GOAL_CHARS, 6000


def context_limit(context=None):
    return 64000
