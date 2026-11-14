"""Event triggers with durable one-shot delivery and explicit action approval."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import timedelta
from pathlib import Path

from hive.core.approvals import ApprovalStore
from hive.config import load_config
from hive.core.checkpoint import TaskStore
from hive.core.models import ApprovalRequest, ApprovalStatus, RiskLevel, utc_now
from hive.core.orchestrator import prepare_task_for_approval
from hive.core.state import open_database
from hive.execution.command_plan import PlanStore, VerificationCheck, plan_command_checks
from hive.memory.models import (HIGH_RISK_ACTIONS, MemoryError, TriggerAction,
                                TriggerEvent, TriggerRule)
from hive.memory.store import MemoryStore
from hive.policy.risk import classify_command


POWER_COMMANDS = {
    TriggerAction.SHUTDOWN_LAPTOP: ["systemctl", "poweroff"],
    TriggerAction.REBOOT_LAPTOP: ["systemctl", "reboot"],
    TriggerAction.SUSPEND_LAPTOP: ["systemctl", "suspend"],
}


def _argv(rule: TriggerRule) -> list[str] | None:
    if rule.action in POWER_COMMANDS:
        return POWER_COMMANDS[rule.action]
    if rule.action == TriggerAction.RUN_COMMAND:
        argv = rule.action_payload.get("argv")
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            raise MemoryError("run_command requires argv list")
        decision = classify_command(argv)
        if not decision.allowed:
            raise MemoryError("Trigger command is outside the command policy")
        verify = rule.action_payload.get("verify_argv")
        if not isinstance(verify, list) or not all(isinstance(x, str) for x in verify):
            raise MemoryError("run_command requires a read-only verify_argv list")
        check = classify_command(verify)
        if not check.allowed or check.risk_level != RiskLevel.LOW:
            raise MemoryError("Trigger verification must be an allowed read-only command")
        return argv
    return None


def _digest(rule: TriggerRule, event_key: str) -> str:
    stable_rule = rule.model_dump(mode="json", exclude={"last_triggered_at"})
    return hashlib.sha256((json.dumps(stable_rule, sort_keys=True) + event_key).encode()).hexdigest()


class TriggerEngine:
    def __init__(self, database_file: Path):
        self.database_file = database_file
        self.store = MemoryStore(database_file)

    def add(self, rule: TriggerRule) -> TriggerRule:
        _argv(rule)  # Validate commands before persisting the rule.
        return self.store.add_trigger(rule)

    def on_event(self, event: TriggerEvent, *, event_key: str,
                 context: dict[str, str]) -> list[ApprovalRequest]:
        if not load_config().triggers.enabled:
            return []
        approvals = []
        for rule in self.store.list_triggers():
            if not rule.enabled or rule.event != event:
                continue
            if any(context.get(key) != value for key, value in rule.condition.items()):
                continue
            if not self.store.claim_trigger_event(rule.trigger_id, event_key):
                continue
            if rule.action == TriggerAction.RUN_COMMAND:
                argv = _argv(rule)
                assert argv is not None
                task = TaskStore(self.database_file).create(f"Trigger: {rule.name}")
                TaskStore(self.database_file).patch_state(
                    task.task_id, {"trigger_origin": rule.trigger_id,
                                   "trigger_event_key": event_key},
                    event_type="trigger_task_created")
                plan = plan_command_checks(
                    task_id=task.task_id, agent_id=task.agent_id, workspace_root=Path.cwd(),
                    commands=[(rule.name, argv, VerificationCheck(
                        kind="command_success", command_argv=rule.action_payload["verify_argv"]))])
                requests = PlanStore(self.database_file).add(plan)
                prepare_task_for_approval(self.database_file, plan,
                                          [request.approval_id for request in requests])
                approvals.extend(requests)
                rule.last_triggered_at = utc_now()
                self.store.update_trigger(rule)
                continue
            if rule.action in HIGH_RISK_ACTIONS or rule.requires_approval:
                argv = _argv(rule)
                confirmation = (f"{event.value.replace('_', ' ').capitalize()}. "
                                f"{rule.action.value.replace('_', ' ').capitalize()} now?")
                request = ApprovalRequest(
                    task_id=context.get("task_id", event_key), agent_id=context.get("agent_id", "core"),
                    plan_id=rule.trigger_id, title=confirmation,
                    description=f"{rule.event.value}: {rule.action.value}",
                    risk_level=RiskLevel.HIGH if rule.action in HIGH_RISK_ACTIONS else rule.risk_level,
                    command_argv=argv,
                    verification_summary="Confirm trigger action result where possible",
                    rollback_summary="Power actions cannot be rolled back; command actions need manual review",
                    plan_digest=_digest(rule, event_key),
                    expires_at=utc_now() + timedelta(minutes=30),
                )
                ApprovalStore(self.database_file).add(request)
                approvals.append(request)
            else:
                self._execute_low_risk(rule, context)
                self.store.set_trigger_event_status(rule.trigger_id, event_key, "completed")
            rule.last_triggered_at = utc_now()
            self.store.update_trigger(rule)
        return approvals

    def _execute_low_risk(self, rule: TriggerRule, context: dict[str, str]) -> None:
        if rule.action == TriggerAction.NOTIFY_USER:
            # Notification is inspectable via trigger event and task trace.
            if "task_id" in context:
                TaskStore(self.database_file).checkpoint(
                    context["task_id"], "trigger_notification", detail=rule.name)
        elif rule.action == TriggerAction.START_TASK:
            objective = rule.action_payload.get("objective")
            if not isinstance(objective, str) or not objective.strip():
                raise MemoryError("start_task trigger needs an objective")
            TaskStore(self.database_file).create(objective)
        elif rule.action == TriggerAction.PAUSE_AGENT:
            task_id = context.get("task_id")
            agent_id = context.get("agent_id")
            if not agent_id or agent_id == "core":
                raise MemoryError("pause_agent trigger needs a worker agent")
            from hive.agents.registry import AgentRegistry, AgentStatus
            registry = AgentRegistry(self.database_file)
            agent = registry.get(agent_id)
            agent.status = AgentStatus.PAUSED
            registry.update(agent)
            if task_id:
                from hive.core.models import TaskPhase, TaskStatus
                task = TaskStore(self.database_file).get(task_id)
                if task.phase in {TaskPhase.INTAKE, TaskPhase.CLARIFY, TaskPhase.RESEARCH,
                                  TaskPhase.SUMMARIZE, TaskPhase.OPTIONS,
                                  TaskPhase.RECOMMEND, TaskPhase.DISCUSS,
                                  TaskPhase.WAIT_APPROVAL} and task.status != TaskStatus.PAUSED:
                    TaskStore(self.database_file).pause(task_id)
        else:
            raise MemoryError("Unsupported low-risk trigger action")

    def execute_approved(self, trigger_id: str, event_key: str, approval_id: str) -> None:
        if not load_config().triggers.enabled:
            raise MemoryError("Triggers are disabled")
        rule = self.store.get_trigger(trigger_id)
        if not rule.enabled:
            raise MemoryError("Trigger is disabled")
        if rule.action not in POWER_COMMANDS:
            raise MemoryError("This trigger uses a normal plan; run its approved plan")
        approval = ApprovalStore(self.database_file).get(approval_id)
        if (approval.status != ApprovalStatus.APPROVED or approval.plan_id != trigger_id
                or approval.plan_digest != _digest(rule, event_key)):
            raise MemoryError("Trigger has no matching current approval")
        argv = _argv(rule)
        if argv is None:
            raise MemoryError("Trigger action has no executable command")
        # Recheck command policy at execution time; power operations have a fixed argv.
        if rule.action == TriggerAction.RUN_COMMAND and not classify_command(argv).allowed:
            raise MemoryError("Trigger command is no longer allowed")
        with open_database(self.database_file) as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE trigger_events SET status='running' WHERE trigger_id=? "
                                "AND event_key=? AND status='pending'", (trigger_id, event_key))
            if result.rowcount != 1:
                raise MemoryError("Trigger event already executed or unknown")
        try:
            completed = subprocess.run(argv, check=False, timeout=30, capture_output=True, text=True)
            if completed.returncode != 0:
                raise MemoryError(f"Trigger command exited {completed.returncode}")
        except Exception:
            with open_database(self.database_file) as db:
                db.execute("UPDATE trigger_events SET status='failed' WHERE trigger_id=? AND event_key=?",
                           (trigger_id, event_key))
            raise
        with open_database(self.database_file) as db:
            db.execute("UPDATE trigger_events SET status='completed' WHERE trigger_id=? AND event_key=?",
                       (trigger_id, event_key))
