"""Validated domain records shared by the CLI and runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid4().hex


class TaskStatus(StrEnum):
    CREATED = "created"
    CLARIFYING = "clarifying"
    RESEARCHING = "researching"
    DISCUSSING = "discussing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"


class TaskPhase(StrEnum):
    INTAKE = "intake"
    CLARIFY = "clarify"
    RESEARCH = "research"
    SUMMARIZE = "summarize"
    OPTIONS = "options"
    RECOMMEND = "recommend"
    DISCUSS = "discuss"
    WAIT_APPROVAL = "wait_approval"
    PLAN = "plan"
    PREFLIGHT = "preflight"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECOVER = "recover"
    REPORT = "report"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    FORBIDDEN = "forbidden"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class LanguageCode(StrEnum):
    EN = "en"
    UR_ROMAN = "ur_roman"


class Task(BaseModel):
    task_id: str = Field(default_factory=new_id)
    objective: str
    language: LanguageCode = LanguageCode.EN
    status: TaskStatus = TaskStatus.CREATED
    phase: TaskPhase = TaskPhase.INTAKE
    agent_id: str = "core"
    parent_task_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    state: dict = Field(default_factory=dict)
    checkpoint_id: str | None = None


class ApprovalRequest(BaseModel):
    approval_id: str = Field(default_factory=new_id)
    task_id: str
    agent_id: str
    plan_id: str
    step_id: str | None = None
    title: str
    description: str
    risk_level: RiskLevel
    command_argv: list[str] | None = None
    diff: str | None = None
    verification_summary: str
    rollback_summary: str
    plan_digest: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    priority: int = 0
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    decided_at: datetime | None = None

    @field_validator("created_at", "expires_at", "decided_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Approval timestamps must include a timezone")
        return value
