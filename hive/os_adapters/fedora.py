"""Fedora plans use fixed package and service command templates."""

from __future__ import annotations

from pathlib import Path

from hive.execution.command_plan import CommandPlan, VerificationCheck, plan_commands, plan_command_checks
from hive.os_adapters.base import OSAdapter
from hive.policy.templates import (
    package_check, package_install, package_remove, package_refresh, service_action,
    service_check, service_start, system_upgrade, firewall_add_port, firewall_query_port,
)


class FedoraAdapter(OSAdapter):
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

    def remove_package(self, package: str, *, task_id: str, agent_id: str,
                       workspace_root: Path) -> CommandPlan:
        return plan_command_checks(
            task_id=task_id, agent_id=agent_id, workspace_root=workspace_root,
            commands=[(f"Remove {package}", package_remove(package),
                       VerificationCheck(kind="command_exit", command_argv=package_check(package),
                                         expected_exit_codes=[1]))],
        )

    def update_package_index(self, *, task_id: str, agent_id: str,
                             workspace_root: Path) -> CommandPlan:
        return plan_commands(task_id=task_id, agent_id=agent_id, workspace_root=workspace_root,
                             commands=[("Refresh package metadata", package_refresh(), ["dnf", "repolist"])])

    def upgrade_system(self, *, task_id: str, agent_id: str, workspace_root: Path) -> CommandPlan:
        return plan_commands(task_id=task_id, agent_id=agent_id, workspace_root=workspace_root,
                             commands=[("Upgrade installed packages", system_upgrade(),
                                        ["dnf", "history", "info", "last"])])

    def service(self, action: str, service: str, *, task_id: str, agent_id: str,
                workspace_root: Path) -> CommandPlan:
        argv = service_action(action, service)
        query = ["systemctl", "is-enabled" if action in {"enable", "disable"} else "is-active", service]
        if action in {"start", "enable"}:
            check = VerificationCheck(kind="command_success", command_argv=query)
        else:
            check = VerificationCheck(kind="command_output", command_argv=query,
                                      expected_content="inactive" if action == "stop" else "disabled",
                                      expected_exit_codes=[1, 3])
        return plan_command_checks(task_id=task_id, agent_id=agent_id,
                                   workspace_root=workspace_root,
                                   commands=[(f"{action.title()} {service}", argv, check)])

    def open_firewall_port(self, port: int, *, protocol: str = "tcp", task_id: str,
                           agent_id: str, workspace_root: Path) -> CommandPlan:
        query = firewall_query_port(port, protocol)
        permanent_query = ["firewall-cmd", "--permanent", query[1]]
        return plan_commands(
            task_id=task_id, agent_id=agent_id, workspace_root=workspace_root,
            commands=[(f"Open {port}/{protocol} permanently", firewall_add_port(port, protocol), permanent_query),
                      ("Reload firewall", ["sudo", "firewall-cmd", "--reload"], query)],
        )
