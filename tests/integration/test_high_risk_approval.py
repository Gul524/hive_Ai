import asyncio
from pathlib import Path

import pytest

from hive.core.approvals import ApprovalStore
from hive.execution.command_plan import PlanStore
from hive.execution.executor import ExecutionError, execute_plan
from hive.os_adapters.fedora import FedoraAdapter


def test_high_risk_step_needs_separate_approval(tmp_path: Path) -> None:
    database = tmp_path / "hive.db"
    plan = FedoraAdapter().install_package("nginx", task_id="t", agent_id="a",
                                           workspace_root=tmp_path)
    approvals = PlanStore(database).add(plan)
    assert len(approvals) == 2
    ApprovalStore(database).decide(approvals[0].approval_id, approve=True)
    with pytest.raises(ExecutionError, match="High-risk step approval"):
        asyncio.run(execute_plan(plan.plan_id, database_file=database))
