"""Validated, inspectable records for task memory and triggers."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hive.core.models import RiskLevel, new_id, utc_now


class MemoryError(ValueError):
    pass


SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|cookie|authorization|session|credential|private[_-]?key)", re.I)
SECRET_VALUE = re.compile(r"(?:-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|\bBearer\s+\S+|\b(?:sk|ghp|gho|github_pat)_[A-Za-z0-9_-]{12,})", re.I)


def reject_secrets(value: Any) -> None:
    """Reject likely credentials rather than persisting a redacted but unusable recipe."""
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY.search(str(key)):
                raise MemoryError(f"Secret-like field cannot be stored: {key}")
            reject_secrets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_secrets(item)
    elif isinstance(value, str):
        if (SECRET_VALUE.search(value)
                or re.search(r"\b(?:password|passwd|secret|access token|api key|session token)\b", value, re.I)
                or re.search(r"[?&](?:token|api_key|key|secret|password|auth|session|signature)=", value, re.I)
                or re.search(r"--(?:password|token|api-key|secret|cookie|authorization)(?:=|$)", value, re.I)
                or re.search(r"\b(?:password|passwd|secret|token|api[_-]?key|cookie)\s*[:=]\s*\S+", value, re.I)
                or re.search(r"https?://[^/\s:@]+:[^/\s@]+@", value, re.I)):
            raise MemoryError("Secret-like value cannot be stored in memory")


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class SkillParameter(MemoryModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = ""
    required: bool = True
    default: str | None = None
    entity_type: str | None = None

    @model_validator(mode="after")
    def no_secret_parameter(self) -> "SkillParameter":
        if SECRET_KEY.search(self.name) or (self.entity_type and SECRET_KEY.search(self.entity_type)):
            raise ValueError("Secret parameters cannot be stored in skills")
        return self


class SkillStep(MemoryModel):
    kind: Literal["operator_action", "file_write", "browser", "gui"]
    arguments: dict[str, Any]
    description: str = ""


class Skill(MemoryModel):
    skill_id: str = Field(default_factory=new_id)
    name: str
    description: str
    intent: str
    trigger_phrases: list[str]
    parameters: list[SkillParameter] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_modules: list[str] = Field(default_factory=list)
    steps: list[SkillStep]
    verification: str
    cleanup: str
    risk_level: RiskLevel
    status: SkillStatus = SkillStatus.DRAFT
    version: int = Field(default=1, ge=1)
    created_from_task_id: str | None = None
    last_used_at: datetime | None = None
    success_count: int = 0
    failure_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def safe_recipe(self) -> "Skill":
        if not self.name.strip() or not self.steps:
            raise ValueError("Skill requires a name and steps")
        if len({p.name for p in self.parameters}) != len(self.parameters):
            raise ValueError("Duplicate skill parameter")
        reject_secrets(self.model_dump(mode="json"))
        return self


class SkillVersion(MemoryModel):
    skill_id: str
    version: int = Field(ge=1)
    snapshot: Skill
    created_at: datetime = Field(default_factory=utc_now)


class EntityMemory(MemoryModel):
    entity_id: str = Field(default_factory=new_id)
    entity_type: str
    name: str
    value: str
    description: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def safe_entity(self) -> "EntityMemory":
        if not self.name.strip() or not self.value.strip():
            raise ValueError("Entity needs name and value")
        if SECRET_KEY.search(self.entity_type) or SECRET_KEY.search(self.name):
            raise ValueError("Secret entities are not allowed")
        reject_secrets(self.model_dump(mode="json"))
        return self


class TriggerEvent(StrEnum):
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    AGENT_COMPLETED = "agent_completed"
    ALL_AGENTS_COMPLETED = "all_agents_completed"
    APPROVAL_PENDING = "approval_pending"
    RESOURCE_CLEANED = "resource_cleaned"


class TriggerAction(StrEnum):
    NOTIFY_USER = "notify_user"
    RUN_COMMAND = "run_command"
    START_TASK = "start_task"
    PAUSE_AGENT = "pause_agent"
    SHUTDOWN_LAPTOP = "shutdown_laptop"
    REBOOT_LAPTOP = "reboot_laptop"
    SUSPEND_LAPTOP = "suspend_laptop"


HIGH_RISK_ACTIONS = {TriggerAction.RUN_COMMAND, TriggerAction.SHUTDOWN_LAPTOP,
                     TriggerAction.REBOOT_LAPTOP, TriggerAction.SUSPEND_LAPTOP}


class TriggerRule(MemoryModel):
    trigger_id: str = Field(default_factory=new_id)
    name: str
    event: TriggerEvent
    condition: dict[str, str] = Field(default_factory=dict)
    action: TriggerAction
    action_payload: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    last_triggered_at: datetime | None = None

    @model_validator(mode="after")
    def enforce_risk(self) -> "TriggerRule":
        reject_secrets(self.model_dump(mode="json"))
        if self.action in HIGH_RISK_ACTIONS:
            self.risk_level = RiskLevel.HIGH
            self.requires_approval = True
        return self


class ImprovementStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    APPLIED = "applied"


class ImprovementProposal(MemoryModel):
    proposal_id: str = Field(default_factory=new_id)
    skill_id: str
    reason: str
    evidence: str
    proposed_changes: dict[str, Any]
    old_version: int = Field(ge=1)
    proposed_version: int = Field(ge=2)
    status: ImprovementStatus = ImprovementStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def safe_proposal(self) -> "ImprovementProposal":
        reject_secrets(self.model_dump(mode="json"))
        return self
