"""Terminal overview of worker nodes."""

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel

from hive.agents.registry import AgentRegistry
from hive.core.approvals import ApprovalStore
from hive.resources.manager import ResourceManager
from hive.screens.manager import LocalScreenManager


def render_grid(console: Console, database_file) -> None:
    agents = AgentRegistry(database_file).list()
    approvals = ApprovalStore(database_file)
    resources = ResourceManager(database_file)
    screens = LocalScreenManager(database_file, database_file.parent).list(active_only=True)
    panels = []
    for agent in agents:
        pending = approvals.list(pending_only=True, agent_id=agent.agent_id)
        owned = resources.list(agent_id=agent.agent_id, leaks_only=True)
        display = next((screen.display for screen in screens if screen.agent_id == agent.agent_id), "-")
        panels.append(Panel(
            f"Task: {agent.task_id or '-'}\nStatus: {agent.status.value}"
            f"\nAction: {agent.latest_action}\nApprovals: {len(pending)}"
            f"\nResources: {len(owned)}\nDisplay: {display}"
            f"\nScreen: {agent.screen_path or '-'}",
            title=agent.agent_id,
        ))
    console.print(Columns(panels, equal=True, expand=True) if panels else Panel("No agents yet", title="Hive Grid"))
