"""Reviewable, single-action GUI plans tied to a virtual display."""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hive.core.approvals import ApprovalStore
from hive.core.errors import HiveError
from hive.core.models import ApprovalRequest, RiskLevel, new_id, utc_now
from hive.core.state import open_database
from hive.screens.manager import LocalScreenManager
from hive.browser.safety import url_is_public_https


class GuiPlanError(HiveError):
    pass


class GuiPlan(BaseModel):
    plan_id: str = Field(default_factory=new_id)
    step_id: str = Field(default_factory=new_id)
    task_id: str
    agent_id: str
    screen_id: str
    display: str
    summary: str
    kind: Literal["click", "type", "key", "launch"]
    x: int | None = None
    y: int | None = None
    reference_screenshot: Path | None = None
    reference_sha256: str | None = None
    value_env: str | None = None
    value_sha256: str | None = None
    key_name: str | None = None
    url: str | None = None
    confidence_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    risk_level: RiskLevel = RiskLevel.HIGH

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def create_gui_plan(*, task_id: str, agent_id: str, screen_id: str,
                    kind: str, screens: LocalScreenManager,
                    x: int | None = None, y: int | None = None,
                    reference_screenshot: Path | None = None,
                    value_env: str | None = None, key_name: str | None = None,
                    url: str | None = None,
                    confidence_threshold: float = 0.9) -> GuiPlan:
    record = screens.get(screen_id)
    if record.status != "active" or record.agent_id != agent_id:
        raise GuiPlanError("Screen is not active for this agent")
    reference_hash = None
    value_hash = None
    if kind == "click":
        root = screens.data_dir / "agents" / agent_id / "screenshots"
        if x is None or y is None or x < 0 or y < 0 or reference_screenshot is None:
            raise GuiPlanError("Click requires coordinates and a reference screenshot")
        reference = reference_screenshot.resolve()
        if not reference.is_file() or not reference.is_relative_to(root.resolve()):
            raise GuiPlanError("Reference screenshot is outside the agent screen directory")
        reference_hash = hashlib.sha256(reference.read_bytes()).hexdigest()
    elif kind == "type":
        if not value_env or not value_env.isidentifier() or value_env not in os.environ:
            raise GuiPlanError("Typing needs an existing environment variable")
        value_hash = hashlib.sha256(os.environ[value_env].encode()).hexdigest()
    elif kind == "key":
        if key_name not in {"Return", "Escape", "Tab", "BackSpace", "Left", "Right", "Up", "Down"}:
            raise GuiPlanError("Key is outside the allowlist")
    elif kind == "launch":
        if record.app_pid:
            raise GuiPlanError("Screen already has an application")
        if not url or not url_is_public_https(url):
            raise GuiPlanError("Launch requires a public HTTPS URL")
    else:
        raise GuiPlanError("Unsupported GUI action")
    return GuiPlan(
        task_id=task_id, agent_id=agent_id, screen_id=screen_id,
        display=record.display, summary=f"GUI {kind} on {record.display}",
        kind=kind, x=x, y=y,
        reference_screenshot=reference_screenshot.resolve() if reference_screenshot else None,
        reference_sha256=reference_hash, value_env=value_env,
        value_sha256=value_hash, key_name=key_name, url=url,
        confidence_threshold=confidence_threshold,
    )


class GuiPlanStore:
    def __init__(self, database_file: Path, *, audit_file: Path | None = None):
        self.database_file = database_file
        self.audit_file = audit_file
        with open_database(database_file) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gui_plans (plan_id TEXT PRIMARY KEY, digest TEXT NOT NULL, "
                "run_status TEXT NOT NULL DEFAULT 'ready', record_json TEXT NOT NULL)"
            )

    def add(self, plan: GuiPlan, *, expiry_minutes: int = 30) -> list[ApprovalRequest]:
        with open_database(self.database_file) as connection:
            connection.execute("INSERT INTO gui_plans VALUES (?, ?, 'ready', ?)",
                               (plan.plan_id, plan.digest(), plan.model_dump_json()))
        expiry = utc_now() + timedelta(minutes=expiry_minutes)
        description = (f"{plan.kind} on {plan.display}; coordinates {plan.x},{plan.y}; "
                       f"reference {plan.reference_screenshot or '-'}; env {plan.value_env or '-'}; "
                       f"key {plan.key_name or '-'}; url {plan.url or '-'}")
        requests = [ApprovalRequest(
            task_id=plan.task_id, agent_id=plan.agent_id, plan_id=plan.plan_id,
            title="Approve GUI plan", description=description,
            risk_level=RiskLevel.HIGH,
            verification_summary="Screenshot before and after, with target confidence check",
            rollback_summary="Close virtual display; GUI effects may need manual reversal",
            plan_digest=plan.digest(), expires_at=expiry,
        ), ApprovalRequest(
            task_id=plan.task_id, agent_id=plan.agent_id, plan_id=plan.plan_id,
            step_id=plan.step_id, title=f"Approve GUI {plan.kind}", description=description,
            risk_level=RiskLevel.HIGH,
            verification_summary="Compare target screenshot, perform action, capture result",
            rollback_summary="Close virtual display; manual reversal may be needed",
            plan_digest=plan.digest(), expires_at=expiry,
        )]
        approvals = ApprovalStore(self.database_file, audit_file=self.audit_file)
        for request in requests:
            approvals.add(request)
        return requests

    def get(self, plan_id: str) -> GuiPlan:
        with open_database(self.database_file) as connection:
            row = connection.execute("SELECT digest, record_json FROM gui_plans WHERE plan_id=?",
                                     (plan_id,)).fetchone()
        if row is None:
            raise GuiPlanError(f"Unknown GUI plan: {plan_id}")
        plan = GuiPlan.model_validate_json(row[1])
        if plan.digest() != row[0]:
            raise GuiPlanError("GUI plan integrity check failed")
        return plan

    def claim(self, plan_id: str) -> None:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE gui_plans SET run_status='running' WHERE plan_id=? AND run_status='ready'",
                (plan_id,),
            )
            if result.rowcount != 1:
                raise GuiPlanError("GUI plan was already run or interrupted")

    def finish(self, plan_id: str, *, success: bool) -> None:
        with open_database(self.database_file) as connection:
            connection.execute("UPDATE gui_plans SET run_status=? WHERE plan_id=? AND run_status='running'",
                               ("completed" if success else "failed", plan_id))
