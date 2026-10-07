"""Execute one approved GUI action against a verified virtual display."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Awaitable, Callable

from hive.core.approvals import ApprovalStore
from hive.core.models import ApprovalRequest, ApprovalStatus, utc_now
from hive.core.orchestrator import finish_execution, start_execution
from hive.failures.store import FailedTaskRecord, FailureClass, FailureStore
from hive.gui.plan import GuiPlan, GuiPlanError, GuiPlanStore
from hive.gui.vision import target_confidence
from hive.gui.x11 import X11Adapter
from hive.observability.audit import AuditLog
from hive.screens.manager import LocalScreenManager


def _guard(plan: GuiPlan, approvals: ApprovalStore) -> ApprovalRequest:
    records = approvals.list(task_id=plan.task_id, agent_id=plan.agent_id)
    digest = plan.digest()

    def approved(step_id: str | None) -> ApprovalRequest | None:
        return next((item for item in records if item.plan_id == plan.plan_id
                     and item.step_id == step_id and item.plan_digest == digest
                     and item.status == ApprovalStatus.APPROVED
                     and item.expires_at > utc_now()), None)

    if approved(None) is None:
        raise GuiPlanError("GUI plan approval is missing, denied, or expired")
    step = approved(plan.step_id)
    if step is None:
        raise GuiPlanError("High-risk GUI step approval is missing")
    return step


async def execute_gui_plan(
    plan_id: str, *, database_file: Path, data_dir: Path,
    audit_file: Path | None = None,
    screens: LocalScreenManager | None = None,
    adapter_factory: Callable[..., X11Adapter] = X11Adapter,
    confidence_fn: Callable[..., Awaitable[float]] = target_confidence,
) -> dict:
    store = GuiPlanStore(database_file)
    plan = store.get(plan_id)
    approvals = ApprovalStore(database_file, audit_file=audit_file)
    audit = AuditLog(audit_file or database_file.parent / "audit.jsonl")
    screens = screens or LocalScreenManager(database_file, data_dir)
    claimed = False
    adapter = None
    before: Path | None = None
    after: Path | None = None
    try:
        step_approval = _guard(plan, approvals)
        store.claim(plan_id)
        claimed = True
        start_execution(database_file, plan.task_id)
        screen = screens.get(plan.screen_id)
        if screen.status != "active" or screen.display != plan.display \
                or screen.agent_id != plan.agent_id:
            raise GuiPlanError("Virtual display changed since plan approval")
        before = await screens.screenshot(plan.screen_id)
        confidence = 1.0
        if plan.kind == "click":
            assert plan.reference_screenshot is not None
            current_hash = hashlib.sha256(plan.reference_screenshot.read_bytes()).hexdigest()
            if current_hash != plan.reference_sha256:
                raise GuiPlanError("Reference screenshot changed since approval")
            confidence = await confidence_fn(plan.reference_screenshot, before,
                                             x=plan.x, y=plan.y)
            if confidence < plan.confidence_threshold:
                raise GuiPlanError(f"GUI target confidence {confidence:.3f} is below the approved threshold")
        if plan.kind != "launch":
            adapter = adapter_factory(display=plan.display, approvals=approvals,
                                      plan_id=plan.plan_id, plan_digest=plan.digest(),
                                      step_id=plan.step_id)
        if plan.kind == "click":
            assert plan.x is not None and plan.y is not None
            await adapter.click(plan.x, plan.y, approval_id=step_approval.approval_id)
        elif plan.kind == "type":
            value = os.environ.get(plan.value_env or "")
            if value is None or hashlib.sha256(value.encode()).hexdigest() != plan.value_sha256:
                raise GuiPlanError("GUI typing value changed since approval")
            await adapter.type_text(value, approval_id=step_approval.approval_id)
        elif plan.kind == "key":
            assert plan.key_name is not None
            await adapter.key(plan.key_name, approval_id=step_approval.approval_id)
        elif plan.kind == "launch":
            assert plan.url is not None
            await screens.launch_firefox(plan.screen_id, plan.url)
        after = await screens.screenshot(plan.screen_id)
        if adapter is not None:
            await adapter.close()
        store.finish(plan_id, success=True)
        finish_execution(database_file, plan.task_id, success=True)
        audit.append("gui_action", task_id=plan.task_id, agent_id=plan.agent_id,
                     plan_id=plan.plan_id, step_id=plan.step_id, kind=plan.kind,
                     confidence=confidence)
        return {"before": str(before), "after": str(after), "confidence": confidence}
    except BaseException as exc:
        cleanup_errors: list[str] = []
        if adapter is not None:
            try:
                await adapter.close()
            except BaseException as cleanup_exc:
                cleanup_errors.append(f"input adapter: {cleanup_exc}")
        if claimed:
            try:
                closed = await screens.close(plan.screen_id)
            except BaseException as cleanup_exc:
                closed = False
                cleanup_errors.append(f"screen: {cleanup_exc}")
            store.finish(plan_id, success=False)
            finish_execution(database_file, plan.task_id, success=False)
        else:
            closed = False
        FailureStore(database_file, database_file.parent / "failed_tasks").add(FailedTaskRecord(
            task_id=plan.task_id, agent_id=plan.agent_id, objective=plan.summary,
            plan_id=plan.plan_id, failed_step_id=plan.step_id,
            failure_class=FailureClass.VERIFICATION if "confidence" in str(exc) else FailureClass.COMMAND,
            error_message=str(exc), logs_path=str((audit_file.parent if audit_file else database_file.parent) / "logs"),
            screenshots_path=str(before.parent) if before else None,
            cleanup_report={"screen_closed": closed, "errors": cleanup_errors},
            suggested_fixes=["Capture a fresh reference screenshot and create a new GUI plan"],
        ))
        audit.append("task_failed", task_id=plan.task_id, agent_id=plan.agent_id,
                     plan_id=plan.plan_id, error=str(exc))
        raise
