"""Stored browser actions with the same immutable approval boundary as commands."""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from hive.browser.safety import browser_risk, url_is_public_https
from hive.core.approvals import ApprovalStore
from hive.core.errors import HiveError
from hive.core.models import ApprovalRequest, RiskLevel, new_id, utc_now
from hive.core.state import open_database


class BrowserPlanError(HiveError):
    pass


class BrowserAction(BaseModel):
    step_id: str = Field(default_factory=new_id)
    kind: Literal["visit", "read", "click", "fill", "submit", "download", "upload"]
    selector: str | None = None
    value_env: str | None = None
    file_path: Path | None = None
    output_path: Path | None = None
    value_sha256: str | None = None
    file_sha256: str | None = None

    @model_validator(mode="after")
    def check_fields(self) -> "BrowserAction":
        if self.kind not in {"visit", "read"} and not self.selector:
            raise ValueError("Browser action needs a selector")
        if self.kind == "fill" and (not self.value_env or not self.value_env.isidentifier()):
            raise ValueError("Fill values must come from an environment variable")
        if self.kind == "upload" and self.file_path is None:
            raise ValueError("Upload needs a file path")
        if self.kind == "download" and self.output_path is None:
            raise ValueError("Download needs a destination path")
        return self


class BrowserPlan(BaseModel):
    plan_id: str = Field(default_factory=new_id)
    task_id: str
    agent_id: str
    summary: str
    url: str
    workspace_root: Path
    actions: list[BrowserAction]
    risk_level: RiskLevel

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def create_browser_plan(*, task_id: str, agent_id: str, url: str,
                        workspace_root: Path, actions: list[BrowserAction]) -> BrowserPlan:
    if not url_is_public_https(url):
        raise BrowserPlanError("Browser only accepts public HTTPS URLs")
    if not actions:
        raise BrowserPlanError("Browser plan requires an action")
    root = workspace_root.resolve()
    risks = [browser_risk(action.kind, url=url, selector=action.selector) for action in actions]
    if RiskLevel.FORBIDDEN in risks:
        raise BrowserPlanError("Payment or unsupported browser action is blocked")
    for action in actions:
        if action.kind == "fill":
            value = os.environ.get(action.value_env or "")
            if value is None:
                raise BrowserPlanError(f"Fill environment variable is missing: {action.value_env}")
            action.value_sha256 = hashlib.sha256(value.encode()).hexdigest()
        if action.kind == "upload":
            assert action.file_path
            source = action.file_path.resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise BrowserPlanError("Upload is outside the approved workspace")
            if source.stat().st_size > 25_000_000:
                raise BrowserPlanError("Upload exceeds 25 MB")
            action.file_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        if action.kind == "download":
            assert action.output_path
            if not action.output_path.resolve().is_relative_to(root):
                raise BrowserPlanError("Download is outside the approved workspace")
            if action.output_path.exists():
                raise BrowserPlanError("Download destination already exists")
    risk = (RiskLevel.HIGH if RiskLevel.HIGH in risks else
            RiskLevel.MEDIUM if RiskLevel.MEDIUM in risks else RiskLevel.LOW)
    return BrowserPlan(task_id=task_id, agent_id=agent_id,
                       summary=f"Browser {', '.join(a.kind for a in actions)} at {url}",
                       url=url, workspace_root=root, actions=actions, risk_level=risk)


class BrowserPlanStore:
    def __init__(self, database_file: Path, *, audit_file: Path | None = None):
        self.database_file = database_file
        self.audit_file = audit_file
        with open_database(database_file) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS browser_plans (plan_id TEXT PRIMARY KEY, "
                "digest TEXT NOT NULL, run_status TEXT NOT NULL DEFAULT 'ready', record_json TEXT NOT NULL)"
            )

    def add(self, plan: BrowserPlan, *, expiry_minutes: int = 30) -> list[ApprovalRequest]:
        with open_database(self.database_file) as connection:
            connection.execute("INSERT INTO browser_plans VALUES (?, ?, 'ready', ?)",
                               (plan.plan_id, plan.digest(), plan.model_dump_json()))
        expiry = utc_now() + timedelta(minutes=expiry_minutes)
        description = "; ".join(
            f"{action.kind}: {action.selector or plan.url}"
            + (f" from env {action.value_env}" if action.value_env else "")
            for action in plan.actions
        )
        requests = [ApprovalRequest(
            task_id=plan.task_id, agent_id=plan.agent_id, plan_id=plan.plan_id,
            title="Approve browser plan", description=description,
            risk_level=plan.risk_level, verification_summary="Screenshots and page state saved",
            rollback_summary="Close isolated browser; submitted actions may need manual reversal",
            plan_digest=plan.digest(), expires_at=expiry,
        )]
        for action in plan.actions:
            if browser_risk(action.kind, url=plan.url, selector=action.selector) == RiskLevel.HIGH:
                requests.append(ApprovalRequest(
                    task_id=plan.task_id, agent_id=plan.agent_id, plan_id=plan.plan_id,
                    step_id=action.step_id, title=f"Approve browser {action.kind}",
                    description=f"{action.kind} {action.selector} on {plan.url}",
                    risk_level=RiskLevel.HIGH,
                    verification_summary="Screenshot before and after, then inspect page",
                    rollback_summary="Close isolated browser; manual reversal may be needed",
                    plan_digest=plan.digest(), expires_at=expiry,
                ))
        approvals = ApprovalStore(self.database_file, audit_file=self.audit_file)
        for request in requests:
            approvals.add(request)
        return requests

    def get(self, plan_id: str) -> BrowserPlan:
        with open_database(self.database_file) as connection:
            row = connection.execute("SELECT digest, record_json FROM browser_plans WHERE plan_id=?",
                                     (plan_id,)).fetchone()
        if not row:
            raise BrowserPlanError(f"Unknown browser plan: {plan_id}")
        plan = BrowserPlan.model_validate_json(row[1])
        if plan.digest() != row[0]:
            raise BrowserPlanError("Stored browser plan integrity check failed")
        return plan

    def claim(self, plan_id: str) -> None:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE browser_plans SET run_status='running' WHERE plan_id=? AND run_status='ready'",
                (plan_id,),
            )
            if result.rowcount != 1:
                raise BrowserPlanError("Browser plan was already run or interrupted")

    def finish(self, plan_id: str, *, success: bool) -> None:
        with open_database(self.database_file) as connection:
            connection.execute("UPDATE browser_plans SET run_status=? WHERE plan_id=? AND run_status='running'",
                               ("completed" if success else "failed", plan_id))
