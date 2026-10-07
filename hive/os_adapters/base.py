from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from hive.execution.command_plan import CommandPlan


class OSAdapter(ABC):
    @abstractmethod
    def install_package(self, package: str, *, task_id: str, agent_id: str,
                        workspace_root: Path, start_service: bool = False) -> CommandPlan: ...

    @abstractmethod
    def remove_package(self, package: str, *, task_id: str, agent_id: str,
                       workspace_root: Path) -> CommandPlan: ...

    @abstractmethod
    def service(self, action: str, service: str, *, task_id: str, agent_id: str,
                workspace_root: Path) -> CommandPlan: ...
