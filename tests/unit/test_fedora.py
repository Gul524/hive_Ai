from pathlib import Path

from hive.os_adapters.detector import detect_distro
from hive.os_adapters.fedora import FedoraAdapter


def test_fedora_plan_is_reviewable(tmp_path: Path) -> None:
    plan = FedoraAdapter().install_package("nginx", task_id="t", agent_id="a",
                                           workspace_root=tmp_path, start_service=True)
    assert plan.steps[0].command_argv == ["sudo", "dnf", "install", "-y", "nginx"]
    assert plan.steps[1].command_argv == ["sudo", "systemctl", "start", "nginx.service"]
    assert all(step.requires_approval for step in plan.steps)
    assert detect_distro(Path("/etc/os-release"))
