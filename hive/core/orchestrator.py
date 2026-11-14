"""Connect reviewed plans to the persistent task workflow."""

from __future__ import annotations

from pathlib import Path

from hive.core.checkpoint import TaskStore, TransitionError
from hive.core.models import TaskPhase, TaskStatus
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
    if task.state.get("trigger_origin"):
        return
    try:
        from hive.memory.triggers import TriggerEngine
        from hive.memory.models import TriggerEvent
        TriggerEngine(database_file).on_event(
            TriggerEvent.APPROVAL_PENDING, event_key=plan.plan_id,
            context={"task_id": task.task_id, "agent_id": task.agent_id})
    except Exception as exc:
        from hive.observability.logger import redact
        store.checkpoint(task.task_id, "trigger_error", detail=redact(str(exc))[:200])


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
    from hive.config import load_config
    from hive.memory.manager import SkillManager
    from hive.memory.models import TriggerEvent
    from hive.memory.store import MemoryStore
    from hive.memory.triggers import TriggerEngine
    from hive.observability.logger import redact

    task = store.get(task_id)

    def optional(action):
        try:
            return action()
        except Exception as exc:
            store.checkpoint(task_id, "memory_error", detail=redact(str(exc))[:200])
            return None

    if task.state.get("skill_id"):
        optional(lambda: MemoryStore(database_file).record_skill_result(
            task.state["skill_id"], success=success))
    if success and task.state.get("remember_skill_after_success"):
        def save_requested_skill():
            if load_config().memory.skill_memory:
                SkillManager(database_file).draft_from_task(
                    task_id, name=task.objective[:100], trigger_phrases=[task.objective])
        optional(save_requested_skill)
    if task.state.get("trigger_origin"):
        optional(lambda: MemoryStore(database_file).set_trigger_event_status(
            task.state["trigger_origin"], task.state["trigger_event_key"],
            "completed" if success else "failed"))
    if success and task.parent_task_id:
        optional(lambda: SkillManager(database_file).propose_from_repair(task_id))
    if not task.state.get("trigger_origin"):
        engine = optional(lambda: TriggerEngine(database_file))
        if engine is None:
            return
        optional(lambda: engine.on_event(
            TriggerEvent.TASK_COMPLETED if success else TriggerEvent.TASK_FAILED,
            event_key=task_id, context={"task_id": task_id, "agent_id": task.agent_id}))
        if success and task.agent_id != "core":
            optional(lambda: engine.on_event(TriggerEvent.AGENT_COMPLETED, event_key=task_id,
                                             context={"task_id": task_id, "agent_id": task.agent_id}))
            agent_tasks = optional(lambda: [item for item in store.list() if item.agent_id != "core"])
            if agent_tasks and all(item.status in {TaskStatus.COMPLETED, TaskStatus.FAILED,
                                                    TaskStatus.CANCELED} for item in agent_tasks):
                optional(lambda: engine.on_event(
                    TriggerEvent.ALL_AGENTS_COMPLETED,
                    event_key=":".join(sorted(item.task_id for item in agent_tasks)),
                    context={"task_id": task_id, "agent_id": task.agent_id}))
