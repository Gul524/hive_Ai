"""Hive command line interface."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import timedelta
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from hive import __version__
from hive.agents.registry import Agent, AgentRegistry
from hive.cli.grid import render_grid
from hive.cli.chat import chat_loop, chat_once
from hive.cli.render import approval_panel, approval_table, task_panel
from hive.config import load_config
from hive.config.schema import PathConfig
from hive.core.approvals import ApprovalStore
from hive.core.checkpoint import TaskStore
from hive.core.diffs import unified_diff
from hive.core.errors import HiveError
from hive.core.models import ApprovalRequest, RiskLevel, TaskPhase, utc_now
from hive.core.state import open_database
from hive.core.orchestrator import prepare_task_for_approval
from hive.execution.command_plan import PlanStore, plan_file_write
from hive.execution.executor import execute_plan
from hive.failures.store import FailureStore
from hive.observability.logger import configure_logger
from hive.observability.doctor import render_doctor
from hive.os_adapters.fedora import FedoraAdapter
from hive.resources.manager import ResourceManager
from hive.observability.logger import redact
from hive.paths import HivePaths, resolve_paths


def _configured_paths(base: HivePaths, config_paths: PathConfig) -> HivePaths:
    return HivePaths(
        config_dir=(base.config_dir if os.environ.get("HIVE_CONFIG_DIR") else
                    Path(config_paths.config_dir).expanduser().resolve() if config_paths.config_dir else base.config_dir),
        data_dir=(base.data_dir if os.environ.get("HIVE_DATA_DIR") else
                  Path(config_paths.data_dir).expanduser().resolve() if config_paths.data_dir else base.data_dir),
        state_dir=(base.state_dir if os.environ.get("HIVE_STATE_DIR") else
                   Path(config_paths.state_dir).expanduser().resolve() if config_paths.state_dir else base.state_dir),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hive", description="Hive AI local operator")
    parser.add_argument("--version", action="version", version=f"hive {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("init", help="Create local state, logs, and SQLite database")
    commands.add_parser("config-check", help="Validate the Hive configuration")
    approvals = commands.add_parser("approval", help="Manage approval requests")
    approval_commands = approvals.add_subparsers(dest="approval_command", required=True)
    add = approval_commands.add_parser("add", help="Add a reviewable approval request")
    add.add_argument("--task-id", required=True)
    add.add_argument("--agent-id", required=True)
    add.add_argument("--plan-id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--description", required=True)
    add.add_argument("--risk", choices=[r.value for r in RiskLevel], default="low")
    add.add_argument("--command", nargs="+", help="Command argv for display")
    add.add_argument("--verification", required=True)
    add.add_argument("--rollback", required=True)
    add.add_argument("--plan-digest", required=True)
    add.add_argument("--expires-minutes", type=int, default=30)
    listed = approval_commands.add_parser("list", help="List approval history")
    listed.add_argument("--task-id")
    listed.add_argument("--agent-id")
    pending = approval_commands.add_parser("pending", help="List pending approvals")
    pending.add_argument("--task-id")
    pending.add_argument("--agent-id")
    for action in ("show", "approve", "deny", "history"):
        command = approval_commands.add_parser(action)
        command.add_argument("approval_id")
        if action in ("approve", "deny"):
            command.add_argument("--note")
    demo = commands.add_parser("demo", help="Display sample UI elements")
    demos = demo.add_subparsers(dest="demo_command", required=True)
    demos.add_parser("diff")
    demos.add_parser("rich")
    tasks = commands.add_parser("task", help="Manage persistent tasks")
    task_commands = tasks.add_subparsers(dest="task_command", required=True)
    task_commands.add_parser("list")
    task_commands.add_parser("interrupted")
    create = task_commands.add_parser("create")
    create.add_argument("objective")
    create.add_argument("--agent-id", default="core")
    for action in ("show", "events", "trace", "logs", "resources", "pause", "resume", "cancel"):
        command = task_commands.add_parser(action)
        command.add_argument("task_id")
    plans = commands.add_parser("plan", help="Create and review deterministic plans")
    plan_commands = plans.add_subparsers(dest="plan_command", required=True)
    write = plan_commands.add_parser("write-file")
    write.add_argument("task_id")
    write.add_argument("path", type=Path)
    write.add_argument("--content", required=True)
    write.add_argument("--workspace", type=Path, default=Path.cwd())
    install = plan_commands.add_parser("install-package")
    install.add_argument("task_id")
    install.add_argument("package")
    install.add_argument("--start-service", action="store_true")
    show_plan = plan_commands.add_parser("show")
    show_plan.add_argument("plan_id")
    run = commands.add_parser("run", help="Execute an approved stored plan")
    run.add_argument("plan_id")
    agents = commands.add_parser("agents", help="List or create worker agents")
    agents_commands = agents.add_subparsers(dest="agents_command")
    add_agent = agents_commands.add_parser("create")
    add_agent.add_argument("objective")
    add_agent.add_argument("--workspace", type=Path, default=Path.cwd())
    agents_commands.add_parser("list")
    commands.add_parser("grid", help="Show a terminal grid of worker agents")
    failed = commands.add_parser("failed", help="Inspect or prepare repair of failed tasks")
    failed_commands = failed.add_subparsers(dest="failed_command", required=True)
    failed_commands.add_parser("list")
    for action in ("show", "logs", "repair"):
        failed_commands.add_parser(action).add_argument("failed_id")
    commands.add_parser("doctor", help="Check local dependencies and state")
    chat = commands.add_parser("chat", help="Chat with the configured local model")
    chat.add_argument("prompt", nargs="?")
    chat.add_argument("--allow-cloud", action="store_true")
    resources = commands.add_parser("resources", help="Inspect tracked resources")
    resource_commands = resources.add_subparsers(dest="resources_command", required=True)
    resource_commands.add_parser("list")
    resource_commands.add_parser("leaks")
    configuration = commands.add_parser("config", help="Inspect configuration")
    config_commands = configuration.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("show")
    config_commands.add_parser("edit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    console = Console()
    if args.command is None:
        parser.print_help()
        return 0
    try:
        base_paths = resolve_paths()
        config = load_config(paths=base_paths)
        paths = _configured_paths(base_paths, config.paths)
        if args.command == "config-check":
            console.print("[green]Configuration valid[/green]")
            return 0
        if args.command == "doctor":
            render_doctor(console, config, paths)
            return 0
        if args.command == "config":
            if args.config_command == "show":
                console.print(config.model_dump_json(indent=2))
            else:
                console.print(f"Edit {base_paths.config_file} (copy config.example.yaml there first).")
            return 0
        if args.command == "chat":
            if args.prompt:
                console.print(asyncio.run(chat_once(args.prompt, config, allow_cloud=args.allow_cloud,
                                                    database_file=paths.database_file)))
            else:
                asyncio.run(chat_loop(console, config, allow_cloud=args.allow_cloud,
                                      database_file=paths.database_file))
            return 0
        if args.command == "demo":
            if args.demo_command == "diff":
                console.print(Syntax(unified_diff("mode=old\n", "mode=new\n", path="example.conf"), "diff"))
            else:
                console.print(Panel("Approval is required before changes.", title="Hive AI"))
            return 0
        if args.command == "init":
            logger = configure_logger(paths.logs_dir)
            with open_database(paths.database_file) as connection:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            logger.info("Hive local state initialized")
            console.print(f"[green]Hive initialized[/green] (SQLite {journal_mode})")
            return 0
        if args.command == "approval":
            store = ApprovalStore(paths.database_file, audit_file=paths.state_dir / "audit.jsonl")
            action = args.approval_command
            if action == "add":
                if args.expires_minutes <= 0:
                    parser.error("--expires-minutes must be positive")
                request = ApprovalRequest(
                    task_id=args.task_id, agent_id=args.agent_id, plan_id=args.plan_id,
                    title=args.title, description=args.description,
                    risk_level=RiskLevel(args.risk), command_argv=args.command,
                    verification_summary=args.verification,
                    rollback_summary=args.rollback, plan_digest=args.plan_digest,
                    expires_at=utc_now() + timedelta(minutes=args.expires_minutes),
                )
                store.add(request)
                approval_panel(console, request)
            elif action in ("list", "pending"):
                approval_table(console, store.list(
                    pending_only=action == "pending", task_id=args.task_id,
                    agent_id=args.agent_id,
                ))
            elif action == "show":
                approval_panel(console, store.get(args.approval_id))
            elif action == "history":
                for event in store.history(args.approval_id):
                    console.print(event)
            else:
                request = store.decide(args.approval_id, approve=action == "approve", note=args.note)
                approval_panel(console, request)
            return 0
        if args.command == "task":
            store = TaskStore(paths.database_file)
            action = args.task_command
            if action == "create":
                task_panel(console, store.create(args.objective, agent_id=args.agent_id))
            elif action in ("list", "interrupted"):
                for task in store.list(interrupted_only=action == "interrupted"):
                    task_panel(console, task)
            elif action == "show":
                task_panel(console, store.get(args.task_id))
            elif action in ("events", "trace"):
                for event in store.events(args.task_id):
                    console.print(event)
            elif action == "resources":
                for item in ResourceManager(paths.database_file).list():
                    if item.task_id == args.task_id:
                        console.print(item.model_dump_json())
            elif action == "logs":
                log_file = paths.logs_dir / "hive.jsonl"
                if log_file.exists():
                    for line in log_file.read_text(encoding="utf-8").splitlines():
                        if f'"task_id": "{args.task_id}"' in line:
                            console.print(line)
            elif action == "pause":
                task_panel(console, store.pause(args.task_id))
            elif action == "resume":
                task_panel(console, store.resume(args.task_id))
            else:
                task_panel(console, store.transition(args.task_id, TaskPhase.CANCELED))
            return 0
        if args.command == "plan":
            store = PlanStore(paths.database_file, audit_file=paths.state_dir / "audit.jsonl")
            if args.plan_command == "show":
                plan = store.get(args.plan_id)
                console.print(Panel(plan.model_dump_json(indent=2), title=f"Plan {plan.plan_id}"))
                return 0
            task = TaskStore(paths.database_file).get(args.task_id)
            if task.phase != TaskPhase.INTAKE:
                raise HiveError("Task already has a plan or has advanced; create a new task")
            if args.plan_command == "write-file":
                plan = plan_file_write(task_id=task.task_id, agent_id=task.agent_id,
                                       workspace_root=args.workspace, target_path=args.path,
                                       content=args.content)
            else:
                from hive.os_adapters.detector import detect_distro
                if detect_distro() != "fedora":
                    raise HiveError("Package plans currently support Fedora only")
                plan = FedoraAdapter().install_package(
                    args.package, task_id=task.task_id, agent_id=task.agent_id,
                    workspace_root=Path.cwd(), start_service=args.start_service,
                )
            approvals = store.add(plan, approval_minutes=config.approval.approval_expiry_minutes)
            prepare_task_for_approval(paths.database_file, plan,
                                      [item.approval_id for item in approvals])
            console.print(f"Plan ID: {plan.plan_id}")
            for item in approvals:
                approval_panel(console, item)
            return 0
        if args.command == "run":
            results = asyncio.run(execute_plan(args.plan_id, database_file=paths.database_file,
                                               audit_file=paths.state_dir / "audit.jsonl"))
            for result in results:
                console.print(result)
            console.print("[green]Plan verification passed[/green]")
            return 0
        if args.command == "agents":
            registry = AgentRegistry(paths.database_file)
            if args.agents_command == "create":
                agent = Agent(objective=args.objective, workspace=args.workspace.resolve())
                task = TaskStore(paths.database_file).create(args.objective, agent_id=agent.agent_id)
                agent.task_id = task.task_id
                registry.add(agent)
                console.print(f"Agent {agent.agent_id} created with task {task.task_id}")
            else:
                for agent in registry.list():
                    console.print(f"{agent.agent_id}  {agent.status.value}  {agent.objective}")
            return 0
        if args.command == "grid":
            render_grid(console, paths.database_file)
            return 0
        if args.command == "resources":
            manager = ResourceManager(paths.database_file)
            for item in manager.list(leaks_only=args.resources_command == "leaks"):
                console.print(item.model_dump_json())
            return 0
        if args.command == "failed":
            store = FailureStore(paths.database_file, paths.data_dir / "failed_tasks")
            if args.failed_command == "list":
                for record in store.list():
                    console.print(f"{record.failed_id}  {record.failure_class.value}  {record.objective}")
            else:
                record = store.get(args.failed_id)
                if args.failed_command == "show":
                    console.print(Panel(record.model_dump_json(indent=2), title=record.failed_id))
                elif args.failed_command == "logs":
                    console.print((paths.data_dir / "failed_tasks" / record.failed_id / "error.log").read_text())
                else:
                    task = TaskStore(paths.database_file).create(
                        f"Repair: {record.objective}", agent_id=record.agent_id,
                        parent_task_id=record.task_id,
                    )
                    task_panel(console, task)
                    console.print("Create a new plan and approve it before execution.")
            return 0
    except HiveError as exc:
        Console(stderr=True).print(f"[red]Error:[/red] {exc}")
        return 1
    except Exception as exc:
        Console(stderr=True).print(f"[red]Error:[/red] {redact(str(exc))}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
