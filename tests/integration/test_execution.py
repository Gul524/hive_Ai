import asyncio
from pathlib import Path

import pytest

from hive.core.approvals import ApprovalStore
from hive.execution.command_plan import PlanStore, plan_file_write
from hive.execution.executor import ExecutionError, execute_plan


def test_file_write_requires_approved_matching_plan(tmp_path: Path) -> None:
    database = tmp_path / "hive.db"
    target = tmp_path / "work/result.txt"
    target.parent.mkdir()
    plan = plan_file_write(task_id="t1", agent_id="a1", workspace_root=tmp_path / "work",
                           target_path=target, content="hello\n")
    approvals = PlanStore(database).add(plan)
    with pytest.raises(ExecutionError):
        asyncio.run(execute_plan(plan.plan_id, database_file=database))
    assert not target.exists()
    ApprovalStore(database).decide(approvals[0].approval_id, approve=True)
    asyncio.run(execute_plan(plan.plan_id, database_file=database))
    assert target.read_text() == "hello\n"
    assert not list(target.parent.glob("*.hive-backup"))
    with pytest.raises(Exception, match="already run"):
        asyncio.run(execute_plan(plan.plan_id, database_file=database))


def test_changed_file_invalidates_approved_plan(tmp_path: Path) -> None:
    database = tmp_path / "hive.db"
    target = tmp_path / "work/result.txt"
    target.parent.mkdir()
    target.write_text("old\n")
    plan = plan_file_write(task_id="t1", agent_id="a1", workspace_root=target.parent,
                           target_path=target, content="new\n")
    approval = PlanStore(database).add(plan)[0]
    ApprovalStore(database).decide(approval.approval_id, approve=True)
    target.write_text("someone else's change\n")
    with pytest.raises(ExecutionError, match="changed after plan review"):
        asyncio.run(execute_plan(plan.plan_id, database_file=database))
    assert target.read_text() == "someone else's change\n"


def test_failed_file_verification_restores_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "hive.db"
    target = tmp_path / "work/result.txt"
    target.parent.mkdir()
    target.write_text("original\n")
    plan = plan_file_write(task_id="t1", agent_id="a1", workspace_root=target.parent,
                           target_path=target, content="replacement\n")
    approval = PlanStore(database).add(plan)[0]
    ApprovalStore(database).decide(approval.approval_id, approve=True)

    async def verification_fails(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr("hive.execution.executor.verify", verification_fails)
    with pytest.raises(ExecutionError, match="verification failed"):
        asyncio.run(execute_plan(plan.plan_id, database_file=database))
    assert target.read_text() == "original\n"
    assert not list(target.parent.glob(".*hive-backup"))
