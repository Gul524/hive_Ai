from pathlib import Path

import pytest
from pydantic import ValidationError

from hive.core.approvals import ApprovalStore
from hive.core.checkpoint import TaskStore
from hive.core.models import RiskLevel, TaskPhase
from hive.core.orchestrator import finish_execution
from hive.execution.command_plan import PlanStore
from hive.memory.manager import SkillManager, match_decision, match_skills, resolve_parameters
from hive.memory.models import (EntityMemory, MemoryError, Skill, SkillParameter,
                                SkillStatus, SkillStep, TriggerAction, TriggerEvent, TriggerRule)
from hive.memory.store import MemoryStore
from hive.memory.triggers import TriggerEngine


def sample_skill(**changes):
    data = dict(name="Create project file", description="Write a project marker",
                intent="create project", trigger_phrases=["create project", "project banao"],
                parameters=[SkillParameter(name="project_name")],
                steps=[SkillStep(kind="file_write", arguments={
                    "path": "{project_name}.txt", "content": "created"})],
                verification="Exact file content", cleanup="Resource manager cleanup",
                risk_level=RiskLevel.MEDIUM)
    data.update(changes)
    return Skill(**data)


def test_skill_approval_matching_parameters_and_plan(tmp_path: Path):
    db = tmp_path / "hive.db"
    store = MemoryStore(db)
    skill = store.add_skill(sample_skill())
    assert match_skills(store, "create project") == []
    assert store.get_skill(skill.skill_id).status == SkillStatus.DRAFT
    store.set_skill_status(skill.skill_id, SkillStatus.ACTIVE)
    matched, score = match_skills(store, "project banao")[0]
    assert matched.skill_id == skill.skill_id and match_decision(score) == "propose"
    values, missing = resolve_parameters(skill, {}, [])
    assert values == {} and missing == ["project_name"]
    with pytest.raises(MemoryError, match="Missing parameters"):
        SkillManager(db).create_plan(skill.skill_id, {}, workspace_root=tmp_path)
    task, plan, approvals = SkillManager(db).create_plan(
        skill.skill_id, {"project_name": str(tmp_path / "demo")}, workspace_root=tmp_path)
    assert task.state == {}  # Returned task predates state patches.
    assert TaskStore(db).get(task.task_id).phase == TaskPhase.WAIT_APPROVAL
    assert plan.steps[0].target_path == tmp_path / "demo.txt"
    assert len(approvals) == 1
    assert ApprovalStore(db).get(approvals[0].approval_id).status.value == "pending"
    assert PlanStore(db).get(plan.plan_id).plan_id == plan.plan_id


def test_entity_crud_and_secret_rejection(tmp_path: Path):
    store = MemoryStore(tmp_path / "hive.db")
    entity = store.add_entity(EntityMemory(entity_type="contact", name="Nani", value="WhatsApp Nani"))
    assert store.get_entity(entity.entity_id).value == "WhatsApp Nani"
    assert len(store.list_entities()) == 1
    assert store.update_entity(entity.entity_id, value="Nani contact").value == "Nani contact"
    store.delete_entity(entity.entity_id)
    assert store.list_entities() == []
    with pytest.raises(ValidationError):
        EntityMemory(entity_type="password", name="mail", value="example")
    with pytest.raises(ValidationError):
        sample_skill(steps=[SkillStep(kind="file_write", arguments={
            "path": "file.txt", "content": "Bearer abc123"})])
    with pytest.raises(ValidationError):
        sample_skill(parameters=[SkillParameter(name="api_key")])


def test_trigger_requires_approval_and_deduplicates(tmp_path: Path, monkeypatch):
    db = tmp_path / "hive.db"
    task = TaskStore(db).create("Finish work")
    engine = TriggerEngine(db)
    rule = engine.add(TriggerRule(name="Shutdown after work", event=TriggerEvent.TASK_COMPLETED,
                                  condition={"task_id": task.task_id},
                                  action=TriggerAction.SHUTDOWN_LAPTOP))
    assert rule.requires_approval and rule.risk_level == RiskLevel.HIGH
    requests = engine.on_event(TriggerEvent.TASK_COMPLETED, event_key=task.task_id,
                               context={"task_id": task.task_id, "agent_id": task.agent_id})
    assert len(requests) == 1
    assert requests[0].command_argv == ["systemctl", "poweroff"]
    assert engine.on_event(TriggerEvent.TASK_COMPLETED, event_key=task.task_id,
                           context={"task_id": task.task_id}) == []
    with pytest.raises(MemoryError, match="approval"):
        engine.execute_approved(rule.trigger_id, task.task_id, requests[0].approval_id)
    ApprovalStore(db).decide(requests[0].approval_id, approve=True)
    calls = []
    class Completed:
        returncode = 0
    monkeypatch.setattr("hive.memory.triggers.subprocess.run",
                        lambda argv, **kwargs: (calls.append(argv), Completed())[1])
    engine.execute_approved(rule.trigger_id, task.task_id, requests[0].approval_id)
    assert calls == [["systemctl", "poweroff"]]
    assert store_event_status(MemoryStore(db), rule.trigger_id, task.task_id) == "completed"
    with pytest.raises(MemoryError, match="already executed"):
        engine.execute_approved(rule.trigger_id, task.task_id, requests[0].approval_id)


def store_event_status(store: MemoryStore, trigger_id: str, event_key: str) -> str:
    return next(event["status"] for event in store.list_trigger_events()
                if event["trigger_id"] == trigger_id and event["event_key"] == event_key)


def test_improvement_approval_and_rollback(tmp_path: Path):
    db = tmp_path / "hive.db"
    store = MemoryStore(db)
    skill = store.add_skill(sample_skill())
    store.set_skill_status(skill.skill_id, SkillStatus.ACTIVE)
    manager = SkillManager(db)
    proposal = manager.propose_improvement(skill.skill_id, reason="User corrected wording",
                                           evidence="Successful revised task",
                                           changes={"description": "Updated procedure"})
    assert store.get_skill(skill.skill_id).description == "Write a project marker"
    updated = manager.approve_improvement(proposal.proposal_id)
    assert updated.version == 2 and updated.description == "Updated procedure"
    restored = manager.restore_version(skill.skill_id, 1)
    assert restored.version == 3 and restored.description == "Write a project marker"
    assert [v.version for v in store.skill_versions(skill.skill_id)] == [3, 2, 1]


def test_trigger_command_uses_normal_approval_policy(tmp_path: Path):
    db = tmp_path / "hive.db"
    engine = TriggerEngine(db)
    with pytest.raises(MemoryError, match="outside"):
        engine.add(TriggerRule(name="Unsafe", event=TriggerEvent.TASK_COMPLETED,
                               action=TriggerAction.RUN_COMMAND,
                               action_payload={"argv": ["sh", "-c", "echo unsafe"],
                                               "verify_argv": ["dnf", "repolist"]}))
    rule = engine.add(TriggerRule(name="Refresh", event=TriggerEvent.TASK_COMPLETED,
                                  action=TriggerAction.RUN_COMMAND,
                                  action_payload={"argv": ["sudo", "dnf", "makecache"],
                                                  "verify_argv": ["dnf", "repolist"]}))
    requests = engine.on_event(TriggerEvent.TASK_COMPLETED, event_key="source-task",
                               context={"task_id": "source-task", "agent_id": "core"})
    assert requests and requests[0].plan_id != rule.trigger_id
    assert PlanStore(db).get(requests[0].plan_id).steps[0].command_argv == ["sudo", "dnf", "makecache"]


def test_skill_completion_count_from_normal_task_flow(tmp_path: Path):
    db = tmp_path / "hive.db"
    store = MemoryStore(db)
    skill = store.add_skill(sample_skill())
    store.set_skill_status(skill.skill_id, SkillStatus.ACTIVE)
    task, _, _ = SkillManager(db).create_plan(
        skill.skill_id, {"project_name": str(tmp_path / "demo")}, workspace_root=tmp_path)
    from hive.core.orchestrator import start_execution
    start_execution(db, task.task_id)
    finish_execution(db, task.task_id, success=True)
    assert store.get_skill(skill.skill_id).success_count == 1


def test_successful_repair_proposes_new_version_without_applying(tmp_path: Path):
    from hive.core.orchestrator import prepare_task_for_approval, start_execution
    from hive.execution.command_plan import plan_file_write

    db = tmp_path / "hive.db"
    store = MemoryStore(db)
    skill = store.add_skill(sample_skill())
    store.set_skill_status(skill.skill_id, SkillStatus.ACTIVE)
    failed, _, _ = SkillManager(db).create_plan(
        skill.skill_id, {"project_name": str(tmp_path / "demo")}, workspace_root=tmp_path)
    start_execution(db, failed.task_id)
    finish_execution(db, failed.task_id, success=False)
    repair = TaskStore(db).create("Repair file", parent_task_id=failed.task_id)
    plan = plan_file_write(task_id=repair.task_id, agent_id=repair.agent_id,
                           workspace_root=tmp_path, target_path=tmp_path / "demo.txt",
                           content="repaired")
    approvals = PlanStore(db).add(plan)
    prepare_task_for_approval(db, plan, [item.approval_id for item in approvals])
    start_execution(db, repair.task_id)
    finish_execution(db, repair.task_id, success=True)
    proposals = store.list_improvements()
    assert len(proposals) == 1
    assert proposals[0].proposed_changes["steps"][0]["arguments"]["content"] == "repaired"
    assert store.get_skill(skill.skill_id).version == 1


def test_skill_cannot_plan_outside_workspace(tmp_path: Path):
    db = tmp_path / "hive.db"
    store = MemoryStore(db)
    skill = store.add_skill(sample_skill())
    store.set_skill_status(skill.skill_id, SkillStatus.ACTIVE)
    with pytest.raises(Exception, match="blocked by default"):
        SkillManager(db).create_plan(skill.skill_id, {"project_name": "/etc/blocked"},
                                     workspace_root=tmp_path)
    assert ApprovalStore(db).list() == []


def test_skill_cannot_bypass_command_policy(tmp_path: Path):
    db = tmp_path / "hive.db"
    store = MemoryStore(db)
    skill = sample_skill(steps=[SkillStep(kind="operator_action", arguments={
        "kind": "service", "service": "bad;command", "service_action": "start"})])
    store.add_skill(skill)
    store.set_skill_status(skill.skill_id, SkillStatus.ACTIVE)
    with pytest.raises(Exception):
        SkillManager(db).create_plan(skill.skill_id, {"project_name": "demo"},
                                     workspace_root=tmp_path)
    assert ApprovalStore(db).list() == []


def test_ui_failure_review_needs_concrete_approved_change(tmp_path: Path):
    db = tmp_path / "hive.db"
    store = MemoryStore(db)
    skill = store.add_skill(sample_skill())
    store.set_skill_status(skill.skill_id, SkillStatus.ACTIVE)
    manager = SkillManager(db)
    proposal = manager.propose_failure_review(skill.skill_id, evidence="Selector timed out")
    with pytest.raises(MemoryError, match="concrete changes"):
        manager.approve_improvement(proposal.proposal_id)
    manager.edit_improvement(proposal.proposal_id, {"verification": "Inspect new selector"})
    updated = manager.approve_improvement(proposal.proposal_id)
    assert updated.version == 2


def test_completed_task_can_be_saved_as_draft(tmp_path: Path):
    from hive.core.orchestrator import prepare_task_for_approval, start_execution
    from hive.execution.command_plan import plan_file_write

    db = tmp_path / "hive.db"
    task = TaskStore(db).create("Create marker")
    plan = plan_file_write(task_id=task.task_id, agent_id=task.agent_id,
                           workspace_root=tmp_path, target_path=tmp_path / "marker.txt",
                           content="ready")
    approvals = PlanStore(db).add(plan)
    prepare_task_for_approval(db, plan, [item.approval_id for item in approvals])
    start_execution(db, task.task_id)
    finish_execution(db, task.task_id, success=True)
    saved = SkillManager(db).draft_from_task(task.task_id, name="Create marker",
                                             trigger_phrases=["marker banao"])
    assert saved.status == SkillStatus.DRAFT
    assert saved.created_from_task_id == task.task_id
    assert saved.steps[0].arguments["content"] == "ready"


def test_remember_request_creates_draft_only_after_success(tmp_path: Path):
    from hive.core.orchestrator import prepare_task_for_approval, start_execution
    from hive.execution.command_plan import plan_file_write

    db = tmp_path / "hive.db"
    task = TaskStore(db).create("Create note")
    TaskStore(db).patch_state(task.task_id, {"remember_skill_after_success": True},
                              event_type="remember_requested")
    plan = plan_file_write(task_id=task.task_id, agent_id=task.agent_id,
                           workspace_root=tmp_path, target_path=tmp_path / "note.txt",
                           content="done")
    approvals = PlanStore(db).add(plan)
    prepare_task_for_approval(db, plan, [item.approval_id for item in approvals])
    assert MemoryStore(db).list_skills() == []
    start_execution(db, task.task_id)
    finish_execution(db, task.task_id, success=True)
    skills = MemoryStore(db).list_skills()
    assert len(skills) == 1 and skills[0].status == SkillStatus.DRAFT


def test_remember_phrase_sets_deferred_learning_flag(tmp_path: Path):
    from hive.core.operator import discuss

    db = tmp_path / "hive.db"
    task = TaskStore(db).create("Create note")
    tasks = TaskStore(db)
    for phase in (TaskPhase.SUMMARIZE, TaskPhase.OPTIONS,
                  TaskPhase.RECOMMEND, TaskPhase.DISCUSS):
        tasks.transition(task.task_id, phase)
    discuss(task.task_id, "Do it like this next time", database_file=db)
    assert tasks.get(task.task_id).state["remember_skill_after_success"] is True
