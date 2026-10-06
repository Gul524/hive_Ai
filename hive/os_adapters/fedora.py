"""Fedora plans use fixed package and service command templates."""

from __future__ import annotations

from pathlib import Path

from hive.execution.command_plan import CommandPlan, plan_commands
from hive.policy.templates import package_check, package_install, service_check, service_start


class FedoraAdapter:
    def install_package(self, package: str, *, task_id: str, agent_id: str,
                        workspace_root: Path, start_service: bool = False) -> CommandPlan:
        commands = [(f"Install {package}", package_install(package), package_check(package))]
        if start_service:
            service = f"{package}.service"
            commands.append((f"Start {service}", service_start(service), service_check(service)))
        return plan_commands(task_id=task_id, agent_id=agent_id,
                             workspace_root=workspace_root, commands=commands)

    def start_service(self, service: str, *, task_id: str, agent_id: str,
                      workspace_root: Path) -> CommandPlan:
        return plan_commands(task_id=task_id, agent_id=agent_id, workspace_root=workspace_root,
                             commands=[(f"Start {service}", service_start(service), service_check(service))])
