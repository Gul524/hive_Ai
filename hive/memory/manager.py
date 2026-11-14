"""Skill matching, parameter binding, and reviewable plan compilation."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from hive.browser.plan import BrowserAction, BrowserPlanStore, create_browser_plan
from hive.core.checkpoint import TaskStore
from hive.core.models import RiskLevel, TaskPhase, utc_now
from hive.core.orchestrator import prepare_task_for_approval
from hive.execution.command_plan import PlanStore, plan_file_write
from hive.memory.models import (EntityMemory, ImprovementProposal, ImprovementStatus,
                                MemoryError, Skill, SkillStatus, reject_secrets)
from hive.memory.store import MemoryStore
from hive.core.operator import ProposedAction, _action_plan
from hive.gui.plan import GuiPlanStore, create_gui_plan
from hive.screens.manager import LocalScreenManager


def _recipe_from_command(plan) -> dict[str, Any] | None:
    commands = [step.command_argv for step in plan.steps]
    if not commands or any(argv is None for argv in commands):
        return None
    first = commands[0]
    if first[:4] == ["sudo", "dnf", "install", "-y"] and len(first) == 5:
        starts_service = len(commands) == 2 and commands[1] == [
            "sudo", "systemctl", "start", f"{first[4]}.service"]
        if len(commands) != 1 and not starts_service:
            return None
        return {"kind": "install_package", "package": first[4],
                "start_service": starts_service}
    if first[:4] == ["sudo", "dnf", "remove", "-y"] and len(first) == 5:
        return {"kind": "remove_package", "package": first[4]}
    if first == ["sudo", "dnf", "makecache"] and len(commands) == 1:
        return {"kind": "refresh_packages"}
    if first == ["sudo", "dnf", "upgrade", "-y"] and len(commands) == 1:
        return {"kind": "upgrade_system"}
    if len(first) == 4 and first[:2] == ["sudo", "systemctl"] and len(commands) == 1:
        return {"kind": "service", "service_action": first[2], "service": first[3]}
    if len(first) == 4 and first[:3] == ["sudo", "firewall-cmd", "--permanent"] \
            and first[3].startswith("--add-port=") and len(commands) == 2 \
            and commands[1] == ["sudo", "firewall-cmd", "--reload"]:
        port, protocol = first[3].removeprefix("--add-port=").split("/")
        return {"kind": "open_firewall_port", "port": int(port), "protocol": protocol}
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[\w]+", text.casefold()))


def match_skills(store: MemoryStore, request: str) -> list[tuple[Skill, float]]:
    """Local intent and phrase matching; a future embedding provider can add scores here."""
    query = request.casefold().strip()
    words = _words(query)
    matches = []
    for skill in store.list_skills():
        if skill.status != SkillStatus.ACTIVE:
            continue
        score = 0.0
        phrases = [skill.name, skill.intent, *skill.trigger_phrases]
        for phrase in phrases:
            candidate = phrase.casefold().strip()
            candidate_words = _words(candidate)
            if candidate and (candidate == query or candidate in query):
                score = max(score, 0.92 if candidate != query else 1.0)
            elif candidate_words:
                score = max(score, 0.72 * len(words & candidate_words) / len(candidate_words))
        if score:
            score = min(1.0, score + min(skill.success_count, 5) * 0.01)
            matches.append((skill, score))
    return sorted(matches, key=lambda row: row[1], reverse=True)


def match_decision(score: float) -> str:
    return "propose" if score >= 0.85 else "ask" if score >= 0.45 else "normal"


def resolve_parameters(skill: Skill, supplied: dict[str, str],
                       entities: list[EntityMemory]) -> tuple[dict[str, str], list[str]]:
    allowed = {parameter.name for parameter in skill.parameters}
    if set(supplied) - allowed:
        raise MemoryError("Unknown skill parameter")
    values = dict(supplied)
    for parameter in skill.parameters:
        if parameter.name not in values and parameter.default is not None:
            values[parameter.name] = parameter.default
        if parameter.name not in values and parameter.entity_type:
            candidates = [entity for entity in entities if entity.entity_type == parameter.entity_type]
            if len(candidates) == 1:
                values[parameter.name] = candidates[0].value
        if parameter.name in values and parameter.entity_type:
            for entity in entities:
                if entity.entity_type == parameter.entity_type and entity.name.casefold() == values[parameter.name].casefold():
                    values[parameter.name] = entity.value
                    break
    missing = [parameter.name for parameter in skill.parameters
               if parameter.required and not values.get(parameter.name)]
    reject_secrets(values)
    return values, missing


PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def _bind(value: Any, parameters: dict[str, str]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match) -> str:
            name = match.group(1)
            if name not in parameters:
                raise MemoryError(f"Missing parameter: {name}")
            return parameters[name]
        return PLACEHOLDER.sub(replace, value)
    if isinstance(value, list):
        return [_bind(item, parameters) for item in value]
    if isinstance(value, dict):
        return {key: _bind(item, parameters) for key, item in value.items()}
    return value


class SkillManager:
    def __init__(self, database_file: Path):
        self.database_file = database_file
        self.store = MemoryStore(database_file)

    def recipe_from_task(self, task_id: str):
        task = TaskStore(self.database_file).get(task_id)
        if task.phase != TaskPhase.DONE:
            raise MemoryError("Only a successfully completed task can become a skill")
        proposal = task.state.get("proposal") or {}
        action = proposal.get("action")
        from hive.memory.models import SkillStep
        recipe = None
        risk = RiskLevel.HIGH
        tools = ["command_plan"]
        if action:
            recipe = SkillStep(kind="operator_action", arguments=action)
        elif task.state.get("plan_id"):
            plan_id = task.state["plan_id"]
            try:
                plan = PlanStore(self.database_file).get(plan_id)
                risk = plan.risk_level
                if len(plan.steps) == 1 and plan.steps[0].tool == "write_file":
                    step = plan.steps[0]
                    recipe = SkillStep(kind="file_write", arguments={
                        "path": str(step.target_path), "content": step.content})
                else:
                    derived = _recipe_from_command(plan)
                    if derived:
                        recipe = SkillStep(kind="operator_action", arguments=derived)
            except Exception:
                pass
            if recipe is None:
                try:
                    browser = BrowserPlanStore(self.database_file).get(plan_id)
                    risk = browser.risk_level
                    tools = ["browser"]
                    actions = []
                    for item in browser.actions:
                        data = item.model_dump(mode="json", exclude_none=True)
                        for transient in ("step_id", "value_sha256", "file_sha256"):
                            data.pop(transient, None)
                        actions.append(data)
                    recipe = SkillStep(kind="browser", arguments={"url": browser.url,
                                                                  "actions": actions})
                except Exception:
                    pass
            if recipe is None:
                try:
                    gui = GuiPlanStore(self.database_file).get(plan_id)
                    risk = gui.risk_level
                    tools = ["gui"]
                    recipe = SkillStep(kind="gui", arguments={
                        "screen_id": gui.screen_id, "kind": gui.kind,
                        "x": gui.x, "y": gui.y,
                        "reference_screenshot": str(gui.reference_screenshot) if gui.reference_screenshot else None,
                        "value_env": gui.value_env, "key_name": gui.key_name,
                        "url": gui.url, "confidence_threshold": gui.confidence_threshold})
                except Exception:
                    pass
        if recipe is None:
            raise MemoryError("Task has no reusable supported plan; supply an explicit skill recipe")
        return recipe, risk, tools

    def draft_from_task(self, task_id: str, *, name: str, trigger_phrases: list[str],
                        parameters: list | None = None) -> Skill:
        from hive.memory.models import SkillParameter
        task = TaskStore(self.database_file).get(task_id)
        recipe, risk, tools = self.recipe_from_task(task_id)
        skill = Skill(name=name, description=task.objective, intent=name,
                      trigger_phrases=trigger_phrases,
                      parameters=[SkillParameter.model_validate(p) for p in (parameters or [])],
                      required_tools=tools, required_modules=["execution"],
                      steps=[recipe],
                      verification="Use the generated plan's read-only checks",
                      cleanup="Use normal resource manager cleanup",
                      risk_level=risk,
                      created_from_task_id=task_id)
        return self.store.add_skill(skill)

    def propose_from_repair(self, repaired_task_id: str):
        repaired = TaskStore(self.database_file).get(repaired_task_id)
        if repaired.phase != TaskPhase.DONE or not repaired.parent_task_id:
            return None
        original = TaskStore(self.database_file).get(repaired.parent_task_id)
        if original.phase != TaskPhase.FAILED:
            return None
        skill_id = original.state.get("skill_id")
        if not skill_id:
            return None
        skill = self.store.get_skill(skill_id)
        if original.state.get("skill_version") != skill.version:
            return None  # A newer version needs a fresh review of this repair.
        recipe, risk, tools = self.recipe_from_task(repaired_task_id)
        if skill.steps == [recipe] and skill.risk_level == risk:
            return None
        return self.propose_improvement(
            skill_id, reason="Failed skill was repaired by a successful linked task",
            evidence=f"Failed task {original.task_id}; repair task {repaired_task_id}",
            changes={"steps": [recipe.model_dump(mode="json")],
                     "risk_level": risk.value, "required_tools": tools})

    def create_plan(self, skill_id: str, parameters: dict[str, str], *, workspace_root: Path,
                    approval_minutes: int = 30, browser_enabled: bool = False,
                    gui_enabled: bool = False, data_dir: Path | None = None,
                    entity_memory_enabled: bool = True):
        skill = self.store.get_skill(skill_id)
        if skill.status != SkillStatus.ACTIVE:
            raise MemoryError("Skill is not active")
        values, missing = resolve_parameters(
            skill, parameters, self.store.list_entities() if entity_memory_enabled else [])
        if missing:
            raise MemoryError("Missing parameters: " + ", ".join(missing))
        # A recipe creates a fresh plan and approvals each time. Only one deterministic
        # plan step is supported; compound procedures require a new reviewed plan type.
        if len(skill.steps) != 1:
            raise MemoryError("Compound skill recipes are not executable yet")
        step = skill.steps[0]
        args = _bind(step.arguments, values)
        reject_secrets(args)
        screens = None
        agent_id = "core"
        if step.kind == "gui":
            if not gui_enabled or data_dir is None:
                raise MemoryError("GUI is disabled")
            screens = LocalScreenManager(self.database_file, data_dir)
            agent_id = screens.get(args["screen_id"]).agent_id
        task = TaskStore(self.database_file).create(skill.name, agent_id=agent_id)
        TaskStore(self.database_file).patch_state(
            task.task_id, {"skill_id": skill.skill_id, "skill_version": skill.version},
            event_type="skill_selected")
        audit_file = self.database_file.parent / "audit.jsonl"
        try:
            if step.kind == "operator_action":
                action = ProposedAction.model_validate(args)
                plan = _action_plan(action, task_id=task.task_id, agent_id=task.agent_id)
                approvals = PlanStore(self.database_file, audit_file=audit_file).add(
                    plan, approval_minutes=approval_minutes)
            elif step.kind == "file_write":
                target_path = Path(args["path"])
                if not target_path.is_absolute():
                    target_path = workspace_root / target_path
                plan = plan_file_write(task_id=task.task_id, agent_id=task.agent_id,
                                       workspace_root=workspace_root,
                                       target_path=target_path, content=args["content"])
                approvals = PlanStore(self.database_file, audit_file=audit_file).add(
                    plan, approval_minutes=approval_minutes)
            elif step.kind == "browser":
                if not browser_enabled:
                    raise MemoryError("Browser is disabled")
                actions = [BrowserAction.model_validate(item) for item in args["actions"]]
                plan = create_browser_plan(task_id=task.task_id, agent_id=task.agent_id,
                                           workspace_root=workspace_root, url=args["url"],
                                           actions=actions)
                approvals = BrowserPlanStore(self.database_file, audit_file=audit_file).add(
                    plan, expiry_minutes=approval_minutes)
            elif step.kind == "gui":
                assert screens is not None
                gui_args = {key: value for key, value in args.items() if value is not None}
                if "reference_screenshot" in gui_args:
                    gui_args["reference_screenshot"] = Path(gui_args["reference_screenshot"])
                plan = create_gui_plan(task_id=task.task_id, agent_id=task.agent_id,
                                       screens=screens, **gui_args)
                approvals = GuiPlanStore(self.database_file, audit_file=audit_file).add(
                    plan, expiry_minutes=approval_minutes)
            else:
                raise MemoryError("Unsupported skill step")
            prepare_task_for_approval(self.database_file, plan,
                                      [item.approval_id for item in approvals])
            TaskStore(self.database_file).checkpoint(task.task_id, "skill_plan_created",
                                                    detail=skill.skill_id)
            return task, plan, approvals
        except Exception:
            TaskStore(self.database_file).transition(task.task_id, TaskPhase.FAILED)
            raise

    def propose_improvement(self, skill_id: str, *, reason: str, evidence: str,
                            changes: dict[str, Any]) -> ImprovementProposal:
        skill = self.store.get_skill(skill_id)
        if skill.status != SkillStatus.ACTIVE:
            raise MemoryError("Only active skills can be improved")
        allowed = {"name", "description", "intent", "trigger_phrases", "parameters",
                   "required_tools", "required_modules", "steps", "verification", "cleanup", "risk_level"}
        if not changes or set(changes) - allowed:
            raise MemoryError("Improvement contains unsupported fields")
        reject_secrets(changes)
        candidate = skill.model_dump(mode="python")
        candidate.update(changes)
        Skill.model_validate(candidate)
        return self.store.add_improvement(ImprovementProposal(
            skill_id=skill_id, reason=reason, evidence=evidence, proposed_changes=changes,
            old_version=skill.version, proposed_version=skill.version + 1))

    def propose_failure_review(self, skill_id: str, *, evidence: str) -> ImprovementProposal:
        skill = self.store.get_skill(skill_id)
        return self.store.add_improvement(ImprovementProposal(
            skill_id=skill_id, reason="Browser or GUI skill failed; review selectors and verification",
            evidence=evidence, proposed_changes={}, old_version=skill.version,
            proposed_version=skill.version + 1))

    def edit_improvement(self, proposal_id: str, changes: dict[str, Any]) -> ImprovementProposal:
        proposal = self.store.get_improvement(proposal_id)
        if proposal.status != ImprovementStatus.PENDING:
            raise MemoryError("Only a pending improvement can be edited")
        # Reuse the candidate validation without creating a second proposal.
        skill = self.store.get_skill(proposal.skill_id)
        allowed = {"name", "description", "intent", "trigger_phrases", "parameters",
                   "required_tools", "required_modules", "steps", "verification", "cleanup", "risk_level"}
        if not changes or set(changes) - allowed:
            raise MemoryError("Improvement contains unsupported fields")
        reject_secrets(changes)
        candidate = skill.model_dump(mode="python")
        candidate.update(changes)
        Skill.model_validate(candidate)
        proposal.proposed_changes = changes
        self.store.update_improvement(proposal)
        return proposal

    def approve_improvement(self, proposal_id: str, *, max_versions: int = 20) -> Skill:
        proposal = self.store.get_improvement(proposal_id)
        if proposal.status != ImprovementStatus.PENDING:
            raise MemoryError("Improvement is no longer pending")
        if not proposal.proposed_changes:
            raise MemoryError("Review proposal needs concrete changes before approval")
        current = self.store.get_skill(proposal.skill_id)
        if current.version != proposal.old_version:
            raise MemoryError("Skill changed since proposal; review a new proposal")
        data = current.model_dump(mode="python")
        data.update(deepcopy(proposal.proposed_changes))
        data.update(version=proposal.proposed_version, status=SkillStatus.ACTIVE,
                    updated_at=utc_now())
        updated = Skill.model_validate(data)
        self.store.apply_version(updated, max_versions=max_versions)
        proposal.status = ImprovementStatus.APPLIED
        self.store.update_improvement(proposal)
        return updated

    def restore_version(self, skill_id: str, version: int, *, max_versions: int = 20) -> Skill:
        current = self.store.get_skill(skill_id)
        snapshot = self.store.get_version(skill_id, version).snapshot
        data = snapshot.model_dump(mode="python")
        data.update(version=current.version + 1, status=SkillStatus.ACTIVE,
                    success_count=current.success_count, failure_count=current.failure_count,
                    last_used_at=current.last_used_at, updated_at=utc_now())
        restored = Skill.model_validate(data)
        return self.store.apply_version(restored, max_versions=max_versions)
