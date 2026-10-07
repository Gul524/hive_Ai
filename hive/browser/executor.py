"""Run approved browser plans in a fresh isolated context and verify closure."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from hive.browser.manager import PlaywrightBrowserManager
from hive.browser.plan import BrowserAction, BrowserPlan, BrowserPlanError, BrowserPlanStore
from hive.browser.safety import browser_risk
from hive.core.approvals import ApprovalStore
from hive.core.models import ApprovalStatus, RiskLevel, utc_now
from hive.core.orchestrator import finish_execution, start_execution
from hive.failures.store import FailedTaskRecord, FailureClass, FailureStore
from hive.observability.audit import AuditLog
from hive.resources.manager import ResourceHandle, ResourceManager, ResourceType


def _guard(plan: BrowserPlan, approvals: ApprovalStore) -> None:
    records = approvals.list(task_id=plan.task_id, agent_id=plan.agent_id)
    digest = plan.digest()
    def approved(step_id: str | None) -> bool:
        return any(record.plan_id == plan.plan_id and record.step_id == step_id
                   and record.plan_digest == digest and record.status == ApprovalStatus.APPROVED
                   and record.expires_at > utc_now() for record in records)
    if not approved(None):
        raise BrowserPlanError("Browser plan approval is missing, denied, or expired")
    for action in plan.actions:
        if browser_risk(action.kind, url=plan.url, selector=action.selector) == RiskLevel.HIGH \
                and not approved(action.step_id):
            raise BrowserPlanError(f"High-risk browser step approval is missing: {action.step_id}")


async def _perform(action: BrowserAction, manager: PlaywrightBrowserManager,
                   plan: BrowserPlan) -> str | None:
    if action.kind in {"visit", "read"}:
        return None
    assert action.selector is not None
    if action.kind == "click":
        return await manager.click_link(action.selector)
    if action.kind == "fill":
        value = os.environ.get(action.value_env or "")
        if value is None or hashlib.sha256(value.encode()).hexdigest() != action.value_sha256:
            raise BrowserPlanError("Fill value changed since approval")
        await manager.fill(action.selector, value)
    elif action.kind == "submit":
        await manager.submit(action.selector)
    elif action.kind == "upload":
        assert action.file_path is not None
        source = action.file_path.resolve()
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != action.file_sha256:
            raise BrowserPlanError("Upload file changed since approval")
        await manager.upload(action.selector, source, workspace=plan.workspace_root)
    elif action.kind == "download":
        assert action.output_path is not None
        if action.output_path.exists():
            raise BrowserPlanError("Download destination changed since approval")
        result = await manager.download(action.selector, action.output_path,
                                        workspace=plan.workspace_root)
        return str(result)
    return None


async def execute_browser_plan(plan_id: str, *, database_file: Path,
                               audit_file: Path | None = None,
                               visible: bool = True, browser_name: str = "chromium",
                               max_tabs: int = 5, max_download_mb: int = 25,
                               manager_factory: Callable[..., PlaywrightBrowserManager] = PlaywrightBrowserManager) -> dict:
    store = BrowserPlanStore(database_file)
    plan = store.get(plan_id)
    approvals = ApprovalStore(database_file, audit_file=audit_file)
    audit = AuditLog(audit_file or database_file.parent / "audit.jsonl")
    resources = ResourceManager(database_file)
    screenshots = database_file.parent / "agents" / plan.agent_id / "screenshots"
    manager = manager_factory(screenshot_root=screenshots, visible=visible,
                              browser_name=browser_name, max_tabs=max_tabs,
                              max_download_mb=max_download_mb,
                              allow_downloads=any(a.kind == "download" for a in plan.actions))
    handle: ResourceHandle | None = None
    claimed = False
    try:
        _guard(plan, approvals)
        store.claim(plan_id)
        claimed = True
        start_execution(database_file, plan.task_id)
        await manager.open(agent_id=plan.agent_id, task_id=plan.task_id, isolated=True)
        handle = await resources.acquire(ResourceHandle(
            agent_id=plan.agent_id, task_id=plan.task_id, resource_type=ResourceType.BROWSER,
            metadata={"plan_id": plan.plan_id, "isolated": True}, timeout_seconds=300,
        ), manager.close)
        await manager.navigate(plan.url)
        action_results: list[str] = []
        for action in plan.actions:
            _guard(plan, approvals)
            current = await manager.read()
            current_risk = browser_risk(action.kind, url=current["url"], selector=action.selector)
            if current_risk == RiskLevel.FORBIDDEN:
                raise BrowserPlanError("Payment or unsupported browser action is blocked")
            if current_risk == RiskLevel.HIGH:
                await manager.screenshot(manager.screenshot_path(f"{action.step_id}_before"))
            result = await _perform(action, manager, plan)
            if result:
                action_results.append(result)
            if current_risk == RiskLevel.HIGH:
                await manager.screenshot(manager.screenshot_path(f"{action.step_id}_after"))
            audit.append("browser_action", task_id=plan.task_id, agent_id=plan.agent_id,
                         step_id=action.step_id, action=action.kind, url=current["url"])
        page = await manager.read()
        await manager.screenshot(manager.screenshot_path("final"))
        if not await resources.release(handle.resource_id):
            raise BrowserPlanError("Browser cleanup could not be verified")
        handle = None
        leaks = await resources.cleanup_all(task_id=plan.task_id)
        if leaks:
            raise BrowserPlanError(f"Browser resource leaks: {leaks}")
        store.finish(plan_id, success=True)
        finish_execution(database_file, plan.task_id, success=True)
        audit.append("task_completed", task_id=plan.task_id, agent_id=plan.agent_id,
                     plan_id=plan.plan_id)
        return {"page": page, "actions": action_results, "screenshots": str(screenshots)}
    except BaseException as exc:
        cleanup_errors: list[str] = []
        if handle is not None:
            try:
                await resources.release(handle.resource_id)
            except BaseException as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
        else:
            try:
                await manager.close()
            except BaseException as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
        if claimed:
            store.finish(plan_id, success=False)
            finish_execution(database_file, plan.task_id, success=False)
        try:
            leaks = await resources.cleanup_all(task_id=plan.task_id)
        except BaseException as cleanup_exc:
            leaks = []
            cleanup_errors.append(str(cleanup_exc))
        FailureStore(database_file, database_file.parent / "failed_tasks").add(FailedTaskRecord(
            task_id=plan.task_id, agent_id=plan.agent_id, objective=plan.summary,
            plan_id=plan.plan_id, failure_class=FailureClass.COMMAND,
            error_message=str(exc), logs_path=str((audit_file.parent if audit_file else database_file.parent) / "logs"),
            screenshots_path=str(screenshots), cleanup_report={"resource_leaks": leaks,
                                                               "errors": cleanup_errors},
            suggested_fixes=["Inspect the page and approvals, then create a new browser plan"],
        ))
        audit.append("task_failed", task_id=plan.task_id, plan_id=plan.plan_id,
                     agent_id=plan.agent_id, error=str(exc))
        raise
