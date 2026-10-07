"""Immutable reviewed plans stored in SQLite."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from hive.core.approvals import ApprovalStore
from hive.core.diffs import file_diff
from hive.core.errors import HiveError
from hive.core.models import ApprovalRequest, RiskLevel, new_id, utc_now
from hive.core.state import open_database
from hive.policy.risk import classify_command, classify_file_write


class PlanError(HiveError):
    pass


MAX_FILE_BYTES = 1_000_000


class VerificationCheck(BaseModel):
    kind: Literal["file_content", "command_success", "command_exit", "command_output"]
    path: Path | None = None
    expected_content: str | None = None
    command_argv: list[str] | None = None
    expected_exit_codes: list[int] = Field(default_factory=lambda: [0])


class ExecutionStep(BaseModel):
    step_id: str = Field(default_factory=new_id)
    task_id: str
    agent_id: str
    description: str
    tool: Literal["write_file", "command"]
    command_argv: list[str] | None = None
    target_path: Path | None = None
    content: str | None = None
    before_sha256: str | None = None
    risk_level: RiskLevel
    idempotent: bool
    requires_sudo: bool = False
    requires_approval: bool = True
    backup_required: bool = False
    sandboxable: bool = True
    timeout_seconds: int = Field(default=300, gt=0)
    working_directory: Path | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    pty: bool = False
    verification: list[VerificationCheck] = Field(default_factory=list)
    rollback_summary: str | None = None

    @model_validator(mode="after")
    def valid_action(self) -> "ExecutionStep":
        if self.tool == "command" and (not self.command_argv or self.target_path is not None):
            raise ValueError("Command step requires argv and no target path")
        if self.tool == "write_file" and (self.target_path is None or self.content is None or self.command_argv):
            raise ValueError("File step requires a target and content")
        return self


class CommandPlan(BaseModel):
    plan_id: str = Field(default_factory=new_id)
    task_id: str
    agent_id: str
    summary: str
    workspace_root: Path
    steps: list[ExecutionStep]
    risk_level: RiskLevel
    expected_changes: list[str]
    verification_summary: str
    rollback_summary: str

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def plan_file_write(*, task_id: str, agent_id: str, workspace_root: Path,
                    target_path: Path, content: str) -> CommandPlan:
    root = workspace_root.resolve()
    target = target_path.resolve()
    decision = classify_file_write(target, allowed_root=root)
    if not decision.allowed:
        raise PlanError(decision.reason)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise PlanError("File content exceeds the 1 MB safe-edit limit")
    if target.exists() and target.stat().st_size > MAX_FILE_BYTES:
        raise PlanError("Existing file exceeds the 1 MB safe-edit limit")
    before = target.read_bytes() if target.exists() else None
    if before is not None:
        try:
            before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PlanError("Binary files cannot be edited with write-file") from exc
    step = ExecutionStep(
        task_id=task_id, agent_id=agent_id, description=f"Write {target}", tool="write_file",
        target_path=target, content=content,
        before_sha256=hashlib.sha256(before).hexdigest() if before is not None else None,
        risk_level=decision.risk_level, idempotent=True, backup_required=before is not None,
        verification=[VerificationCheck(kind="file_content", path=target, expected_content=content)],
        rollback_summary="Restore backup or remove newly created file",
    )
    return CommandPlan(task_id=task_id, agent_id=agent_id, summary=f"Write {target.name}",
                       workspace_root=root, steps=[step], risk_level=decision.risk_level,
                       expected_changes=[str(target)], verification_summary="Exact file content matches",
                       rollback_summary="Restore original file on failure")


def plan_commands(*, task_id: str, agent_id: str, workspace_root: Path,
                  commands: list[tuple[str, list[str], list[str]]]) -> CommandPlan:
    return plan_command_checks(
        task_id=task_id, agent_id=agent_id, workspace_root=workspace_root,
        commands=[(description, argv, VerificationCheck(kind="command_success", command_argv=verify_argv))
                  for description, argv, verify_argv in commands],
    )


def plan_command_checks(*, task_id: str, agent_id: str, workspace_root: Path,
                        commands: list[tuple[str, list[str], VerificationCheck]]) -> CommandPlan:
    steps: list[ExecutionStep] = []
    for description, argv, check in commands:
        decision = classify_command(argv)
        verification = classify_command(check.command_argv or [])
        if not decision.allowed or not verification.allowed or verification.risk_level != RiskLevel.LOW:
            raise PlanError("Command or verification is outside the allowlist")
        steps.append(ExecutionStep(
            task_id=task_id, agent_id=agent_id, description=description, tool="command",
            command_argv=argv, risk_level=decision.risk_level,
            idempotent=decision.risk_level == RiskLevel.LOW,
            requires_sudo=argv[0] == "sudo", sandboxable=False,
            verification=[check],
            rollback_summary="Manual review required for system changes",
        ))
    if not steps:
        raise PlanError("Plan requires at least one step")
    risk = (RiskLevel.HIGH if any(s.risk_level == RiskLevel.HIGH for s in steps)
            else RiskLevel.MEDIUM if any(s.risk_level == RiskLevel.MEDIUM for s in steps)
            else RiskLevel.LOW)
    return CommandPlan(task_id=task_id, agent_id=agent_id, summary="; ".join(s.description for s in steps),
                       workspace_root=workspace_root.resolve(), steps=steps, risk_level=risk,
                       expected_changes=[s.description for s in steps],
                       verification_summary="Run read-only verification commands",
                       rollback_summary="Manual review required for system changes")


class PlanStore:
    def __init__(self, database_file: Path, *, audit_file: Path | None = None):
        self.database_file = database_file
        self.audit_file = audit_file
        with open_database(database_file) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS plans (plan_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
                "digest TEXT NOT NULL, record_json TEXT NOT NULL, run_status TEXT NOT NULL DEFAULT 'ready')"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(plans)")}
            if "run_status" not in columns:
                connection.execute("ALTER TABLE plans ADD COLUMN run_status TEXT NOT NULL DEFAULT 'ready'")

    def add(self, plan: CommandPlan, *, approval_minutes: int = 30) -> list[ApprovalRequest]:
        if not plan.steps or any(s.task_id != plan.task_id or s.agent_id != plan.agent_id for s in plan.steps):
            raise PlanError("Plan steps must belong to the task and agent")
        digest = plan.digest()
        with open_database(self.database_file) as connection:
            connection.execute("INSERT INTO plans (plan_id, task_id, digest, record_json) VALUES (?, ?, ?, ?)",
                               (plan.plan_id, plan.task_id, digest, plan.model_dump_json()))
        expiry = utc_now() + timedelta(minutes=approval_minutes)
        first = plan.steps[0]
        diff = file_diff(first.target_path, first.content) if first.tool == "write_file" else None
        requests = [ApprovalRequest(
            task_id=plan.task_id, agent_id=plan.agent_id, plan_id=plan.plan_id,
            title=f"Approve plan: {plan.summary}", description="; ".join(plan.expected_changes),
            risk_level=plan.risk_level, command_argv=first.command_argv, diff=diff,
            verification_summary=plan.verification_summary, rollback_summary=plan.rollback_summary,
            plan_digest=digest, expires_at=expiry,
        )]
        for step in plan.steps:
            if step.risk_level == RiskLevel.HIGH:
                requests.append(ApprovalRequest(
                    task_id=plan.task_id, agent_id=plan.agent_id, plan_id=plan.plan_id,
                    step_id=step.step_id, title=f"Approve step: {step.description}",
                    description=step.description, risk_level=step.risk_level,
                    command_argv=step.command_argv, verification_summary=plan.verification_summary,
                    rollback_summary=step.rollback_summary or plan.rollback_summary,
                    plan_digest=digest, expires_at=expiry,
                ))
        approval_store = ApprovalStore(self.database_file, audit_file=self.audit_file)
        for request in requests:
            approval_store.add(request)
        return requests

    def get(self, plan_id: str) -> CommandPlan:
        with open_database(self.database_file) as connection:
            row = connection.execute("SELECT digest, record_json FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise PlanError(f"Unknown plan: {plan_id}")
        plan = CommandPlan.model_validate_json(row[1])
        if plan.digest() != row[0]:
            raise PlanError("Stored plan integrity check failed")
        return plan

    def claim_run(self, plan_id: str) -> None:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE plans SET run_status='running' WHERE plan_id=? AND run_status='ready'",
                (plan_id,),
            )
            if result.rowcount != 1:
                raise PlanError("Plan was already run or interrupted; create a new plan")

    def finish_run(self, plan_id: str, *, success: bool) -> None:
        with open_database(self.database_file) as connection:
            connection.execute("UPDATE plans SET run_status=? WHERE plan_id=? AND run_status='running'",
                               ("completed" if success else "failed", plan_id))
