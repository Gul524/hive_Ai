from pathlib import Path

import pytest

from hive.core.models import RiskLevel
from hive.os_adapters.fedora import FedoraAdapter
from hive.policy.risk import classify_command


@pytest.mark.parametrize("builder", [
    lambda a, p: a.remove_package("nginx", task_id="t", agent_id="a", workspace_root=p),
    lambda a, p: a.update_package_index(task_id="t", agent_id="a", workspace_root=p),
    lambda a, p: a.upgrade_system(task_id="t", agent_id="a", workspace_root=p),
    lambda a, p: a.service("stop", "nginx.service", task_id="t", agent_id="a", workspace_root=p),
    lambda a, p: a.service("disable", "nginx.service", task_id="t", agent_id="a", workspace_root=p),
    lambda a, p: a.open_firewall_port(8080, task_id="t", agent_id="a", workspace_root=p),
])
def test_fedora_operations_are_policy_checked(builder, tmp_path: Path) -> None:
    plan = builder(FedoraAdapter(), tmp_path)
    assert plan.steps
    for step in plan.steps:
        assert classify_command(step.command_argv).allowed
        for check in step.verification:
            assert classify_command(check.command_argv).risk_level == RiskLevel.LOW


def test_firewall_port_rejects_invalid_range(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FedoraAdapter().open_firewall_port(99999, task_id="t", agent_id="a", workspace_root=tmp_path)
