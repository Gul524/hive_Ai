"""Connect reviewed plans to the persistent task workflow."""

from __future__ import annotations

from pathlib import Path

from hive.core.checkpoint import TaskStore, TransitionError
from hive.core.models import TaskPhase
from hive.execution.command_plan import CommandPlan
from hive.observability.metrics import MetricsStore


def prepare_task_for_approval(database_file: Path, plan: CommandPlan,
                              approval_ids: list[str]) -> None:
    store = TaskStore(database_file)
    task = store.get(plan.task_id)
    if task.phase != TaskPhase.INTAKE:
        raise TransitionError("Task must be new before creating an executable plan")
    store.transition(task.task_id, TaskPhase.SUMMARIZE,
                     state_patch={"summary": plan.summary})
    store.transition(task.task_id, TaskPhase.OPTIONS,
                     state_patch={"options": [plan.summary]})
    store.transition(task.task_id, TaskPhase.RECOMMEND,
                     state_patch={"recommendation": plan.summary})
    store.transition(task.task_id, TaskPhase.DISCUSS,
                     state_patch={"plan_id": plan.plan_id})
    store.transition(task.task_id, TaskPhase.WAIT_APPROVAL,
                     state_patch={"approval_ids": approval_ids})


def start_execution(database_file: Path, task_id: str) -> None:
    store = TaskStore(database_file)
    try:
        task = store.get(task_id)
    except TransitionError:
        return  # Programmatic plans may have no task record.
    if task.phase != TaskPhase.WAIT_APPROVAL:
        raise TransitionError("Task is not waiting for plan approval")
    for phase in (TaskPhase.PLAN, TaskPhase.PREFLIGHT, TaskPhase.EXECUTE):
        store.transition(task_id, phase)


def finish_execution(database_file: Path, task_id: str, *, success: bool) -> None:
    store = TaskStore(database_file)
    try:
        task = store.get(task_id)
    except TransitionError:
        return
    if task.phase != TaskPhase.EXECUTE:
        return
    if success:
        for phase in (TaskPhase.VERIFY, TaskPhase.REPORT, TaskPhase.DONE):
            store.transition(task_id, phase)
    else:
        store.transition(task_id, TaskPhase.FAILED)
    MetricsStore(database_file).add("task_success" if success else "task_failure")
