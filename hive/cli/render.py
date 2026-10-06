"""Rich renderers for plans, approvals, and task state."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from hive.core.models import ApprovalRequest, Task


def approval_table(console: Console, requests: list[ApprovalRequest]) -> None:
    table = Table(title="Approvals")
    for column in ("ID", "Task", "Agent", "Risk", "Status", "Expires"):
        table.add_column(column)
    for request in requests:
        table.add_row(request.approval_id, request.task_id, request.agent_id,
                      request.risk_level.value, request.status.value,
                      request.expires_at.isoformat())
    console.print(table)


def approval_panel(console: Console, request: ApprovalRequest) -> None:
    lines = [
        f"Task: {request.task_id}  Agent: {request.agent_id}",
        f"Plan: {request.plan_id}  Step: {request.step_id or 'whole plan'}",
        f"Risk: {request.risk_level.value}  Status: {request.status.value}",
        f"Description: {request.description}",
        f"Command: {request.command_argv or 'none'}",
        f"Verification: {request.verification_summary}",
        f"Rollback: {request.rollback_summary}",
        f"Expires: {request.expires_at.isoformat()}",
    ]
    if request.notes:
        lines.append(f"Note: {request.notes}")
    console.print(Panel("\n".join(lines), title=request.title))
    if request.diff:
        console.print(Syntax(request.diff, "diff", theme="ansi_dark"))


def task_panel(console: Console, task: Task) -> None:
    console.print(Panel(
        f"Task: {task.task_id}\nAgent: {task.agent_id}\nObjective: {task.objective}"
        f"\nStatus: {task.status.value}\nPhase: {task.phase.value}",
        title="Hive task",
    ))
