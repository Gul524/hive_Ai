"""Hive command line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
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
from hive.cli.chat import build_router
from hive.cli.voice import listen_command, speak_command, download_stt_model, download_tts_voices
from hive.cli.browser import install_chromium
from hive.browser.plan import BrowserAction, BrowserPlanStore, create_browser_plan
from hive.browser.executor import execute_browser_plan
from hive.gui.plan import GuiPlanStore, create_gui_plan
from hive.gui.executor import execute_gui_plan
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
from hive.core.operator import answer_clarification, discuss, make_reviewable_plan, propose
from hive.execution.command_plan import PlanStore, plan_file_write
from hive.execution.backups import BackupStore
from hive.execution.executor import execute_plan
from hive.failures.store import FailureStore
from hive.observability.logger import configure_logger
from hive.observability.doctor import render_doctor
from hive.observability.metrics import MetricsStore
from hive.os_adapters.fedora import FedoraAdapter
from hive.resources.manager import ResourceManager
from hive.resources.watchdog import cleanup as watchdog_cleanup, scan as watchdog_scan, run_periodic
from hive.research.manager import BraveSearchProvider, ResearchRequest, research
from hive.screens.manager import LocalScreenManager
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
    remove = plan_commands.add_parser("remove-package")
    remove.add_argument("task_id")
    remove.add_argument("package")
    refresh = plan_commands.add_parser("refresh-packages")
    refresh.add_argument("task_id")
    upgrade = plan_commands.add_parser("upgrade-system")
    upgrade.add_argument("task_id")
    service = plan_commands.add_parser("service")
    service.add_argument("task_id")
    service.add_argument("action", choices=["start", "stop", "enable", "disable"])
    service.add_argument("service")
    firewall = plan_commands.add_parser("open-firewall-port")
    firewall.add_argument("task_id")
    firewall.add_argument("port", type=int)
    firewall.add_argument("--protocol", choices=["tcp", "udp"], default="tcp")
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
    listen = commands.add_parser("listen", help="Record and transcribe a voice instruction")
    listen.add_argument("--seconds", type=int)
    listen.add_argument("--approval-id")
    talk = commands.add_parser("talk", help="Speak with the configured local model")
    talk.add_argument("--seconds", type=int)
    speak = commands.add_parser("speak", help="Synthesize speech to a WAV file")
    speak.add_argument("text")
    speak.add_argument("--output", type=Path, required=True)
    speak.add_argument("--play", action="store_true")
    voice = commands.add_parser("voice", help="Manage local speech models")
    voice_commands = voice.add_subparsers(dest="voice_command", required=True)
    voice_commands.add_parser("download-stt-model")
    voice_commands.add_parser("download-tts-voices")
    browser = commands.add_parser("browser", help="Plan and run isolated browser actions")
    browser_commands = browser.add_subparsers(dest="browser_command", required=True)
    browser_plan = browser_commands.add_parser("plan")
    browser_plan.add_argument("task_id")
    browser_plan.add_argument("url")
    browser_plan.add_argument("--workspace", type=Path, default=Path.cwd())
    browser_plan.add_argument("--actions-file", type=Path,
                              help="JSON array of browser actions; defaults to a visit")
    browser_show = browser_commands.add_parser("show")
    browser_show.add_argument("plan_id")
    browser_run = browser_commands.add_parser("run")
    browser_run.add_argument("plan_id")
    browser_commands.add_parser("install-chromium")
    screens = commands.add_parser("screen", help="Manage per-agent virtual displays")
    screen_commands = screens.add_subparsers(dest="screen_command", required=True)
    create_screen = screen_commands.add_parser("create")
    create_screen.add_argument("agent_id")
    screen_commands.add_parser("list")
    for action in ("capture", "close"):
        screen_commands.add_parser(action).add_argument("screen_id")
    screen_commands.add_parser("cleanup-expired")
    gui = commands.add_parser("gui", help="Plan and run approved input on a virtual display")
    gui_commands = gui.add_subparsers(dest="gui_command", required=True)
    click = gui_commands.add_parser("plan-click")
    click.add_argument("screen_id")
    click.add_argument("x", type=int)
    click.add_argument("y", type=int)
    click.add_argument("reference_screenshot", type=Path)
    typing = gui_commands.add_parser("plan-type")
    typing.add_argument("screen_id")
    typing.add_argument("value_env", help="Environment variable containing the text")
    key = gui_commands.add_parser("plan-key")
    key.add_argument("screen_id")
    key.add_argument("key_name")
    launch = gui_commands.add_parser("plan-launch")
    launch.add_argument("screen_id")
    launch.add_argument("url")
    for action_parser in (click, typing, key, launch):
        action_parser.add_argument("--task-id", help="New task ID for a later action in this screen")
    gui_commands.add_parser("show").add_argument("plan_id")
    gui_commands.add_parser("run").add_argument("plan_id")
    resources = commands.add_parser("resources", help="Inspect tracked resources")
    resource_commands = resources.add_subparsers(dest="resources_command", required=True)
    resource_commands.add_parser("list")
    resource_commands.add_parser("leaks")
    watchdog = commands.add_parser("watchdog", help="Inspect and clean recoverable stale resources")
    watchdog_commands = watchdog.add_subparsers(dest="watchdog_command", required=True)
    watchdog_commands.add_parser("scan")
    watchdog_commands.add_parser("cleanup")
    watchdog_commands.add_parser("run")
    commands.add_parser("metrics", help="Show local aggregate metrics")
    trace = commands.add_parser("trace", help="Show task checkpoint trace")
    trace.add_argument("task_id")
    research_command = commands.add_parser("research", help="Summarize explicit, untrusted sources")
    research_command.add_argument("question")
    research_command.add_argument("--url", action="append", default=[])
    research_command.add_argument("--man", action="append", default=[])
    research_command.add_argument("--package", action="append", default=[])
    research_command.add_argument("--search", help="Brave Search query; needs HIVE_BRAVE_API_KEY")
    operator = commands.add_parser("operator", help="Guided model proposal and safe plan creation")
    operator_commands = operator.add_subparsers(dest="operator_command", required=True)
    proposal = operator_commands.add_parser("propose")
    proposal.add_argument("task_id")
    proposal.add_argument("--url", action="append", default=[])
    proposal.add_argument("--man", action="append", default=[])
    proposal.add_argument("--package", action="append", default=[])
    proposal.add_argument("--search", help="Brave Search query; needs HIVE_BRAVE_API_KEY")
    proposal.add_argument("--allow-cloud", action="store_true")
    operator_commands.add_parser("show").add_argument("task_id")
    answer = operator_commands.add_parser("answer")
    answer.add_argument("task_id")
    answer.add_argument("answer")
    note = operator_commands.add_parser("discuss")
    note.add_argument("task_id")
    note.add_argument("note")
    operator_commands.add_parser("plan").add_argument("task_id")
    backup = commands.add_parser("backup", help="Inspect backups and plan an approved restore")
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    backup_list = backup_commands.add_parser("list")
    backup_list.add_argument("--task-id")
    backup_commands.add_parser("show").add_argument("backup_id")
    restore = backup_commands.add_parser("plan-restore")
    restore.add_argument("backup_id")
    restore.add_argument("task_id")
    restore.add_argument("--workspace", type=Path, default=Path.cwd())
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
        if args.command in {"listen", "talk"}:
            asyncio.run(listen_command(
                console, config, paths, seconds=args.seconds,
                approval_id=getattr(args, "approval_id", None), talk=args.command == "talk",
            ))
            return 0
        if args.command == "speak":
            asyncio.run(speak_command(console, config, args.text, args.output, play=args.play))
            return 0
        if args.command == "voice":
            if args.voice_command == "download-stt-model":
                asyncio.run(download_stt_model(config))
                console.print("STT model ready")
            else:
                english, urdu = asyncio.run(download_tts_voices(paths.data_dir))
                console.print(f"English voice: {english}\nUrdu voice: {urdu}")
            return 0
        if args.command == "browser":
            if args.browser_command == "install-chromium":
                return asyncio.run(install_chromium())
            store = BrowserPlanStore(paths.database_file,
                                     audit_file=paths.state_dir / "audit.jsonl")
            if args.browser_command == "show":
                plan = store.get(args.plan_id)
                console.print(Panel(plan.model_dump_json(indent=2), title=plan.plan_id))
                return 0
            if args.browser_command == "plan":
                if not config.browser.enabled:
                    raise HiveError("Browser is disabled in config")
                task = TaskStore(paths.database_file).get(args.task_id)
                if task.phase != TaskPhase.INTAKE:
                    raise HiveError("Task already has a plan; create a new task")
                raw_actions = (json.loads(args.actions_file.read_text(encoding="utf-8"))
                               if args.actions_file else [{"kind": "visit"}])
                if not isinstance(raw_actions, list):
                    raise HiveError("Browser actions file must be a JSON array")
                actions = [BrowserAction.model_validate(item) for item in raw_actions]
                plan = create_browser_plan(task_id=task.task_id, agent_id=task.agent_id,
                                           url=args.url, workspace_root=args.workspace,
                                           actions=actions)
                approvals = store.add(plan, expiry_minutes=config.approval.approval_expiry_minutes)
                prepare_task_for_approval(paths.database_file, plan,
                                          [item.approval_id for item in approvals])
                console.print(f"Browser plan ID: {plan.plan_id}")
                for item in approvals:
                    approval_panel(console, item)
                return 0
            if not config.browser.enabled:
                raise HiveError("Browser is disabled in config")
            result = asyncio.run(execute_browser_plan(
                args.plan_id, database_file=paths.database_file,
                audit_file=paths.state_dir / "audit.jsonl", visible=config.browser.visible,
                browser_name=config.browser.browser_name,
                max_tabs=config.browser.max_tabs_per_agent,
                max_download_mb=config.browser.max_download_mb,
            ))
            console.print(Panel(result["page"]["text"], title=result["page"]["title"]))
            console.print(f"Screenshots: {result['screenshots']}")
            return 0
        if args.command == "screen":
            manager = LocalScreenManager(paths.database_file, paths.data_dir)
            if args.screen_command == "create":
                agent = AgentRegistry(paths.database_file).get(args.agent_id)
                if not agent.task_id:
                    raise HiveError("Agent has no task")
                screen_id = asyncio.run(manager.create(agent_id=agent.agent_id, task_id=agent.task_id))
                record = manager.get(screen_id)
                console.print(f"Screen {screen_id} at {record.display} (PID {record.pid})")
            elif args.screen_command == "list":
                for record in manager.list(active_only=True):
                    console.print(f"{record.screen_id}  {record.display}  {record.agent_id}  {record.status}")
            elif args.screen_command == "capture":
                screenshot = asyncio.run(manager.screenshot(args.screen_id))
                record = manager.get(args.screen_id)
                registry = AgentRegistry(paths.database_file)
                agent = registry.get(record.agent_id)
                agent.screen_path = screenshot
                agent.latest_action = "screen capture"
                registry.update(agent)
                console.print(f"Screenshot: {screenshot}")
            elif args.screen_command == "close":
                if not asyncio.run(manager.close(args.screen_id)):
                    raise HiveError("Display cleanup could not be verified")
                console.print("Screen closed")
            else:
                failed = asyncio.run(manager.cleanup_expired())
                console.print("Expired screens cleaned" if not failed else f"Cleanup failed: {failed}")
            return 0
        if args.command == "gui":
            store = GuiPlanStore(paths.database_file,
                                 audit_file=paths.state_dir / "audit.jsonl")
            if args.gui_command == "show":
                plan = store.get(args.plan_id)
                console.print(Panel(plan.model_dump_json(indent=2), title=plan.plan_id))
                return 0
            if not config.gui.enabled:
                raise HiveError("GUI is disabled in config")
            if args.gui_command == "run":
                result = asyncio.run(execute_gui_plan(
                    args.plan_id, database_file=paths.database_file,
                    data_dir=paths.data_dir, audit_file=paths.state_dir / "audit.jsonl"))
                console.print(f"Before: {result['before']}\nAfter: {result['after']}\n"
                              f"Target confidence: {result['confidence']:.3f}")
                return 0
            screens = LocalScreenManager(paths.database_file, paths.data_dir)
            screen = screens.get(args.screen_id)
            task = TaskStore(paths.database_file).get(args.task_id or screen.task_id)
            if task.agent_id != screen.agent_id:
                raise HiveError("GUI task must belong to the screen's agent")
            if task.phase != TaskPhase.INTAKE:
                raise HiveError("Task already has a plan; create a new task")
            kwargs = {}
            if args.gui_command == "plan-click":
                kind = "click"
                kwargs = {"x": args.x, "y": args.y,
                          "reference_screenshot": args.reference_screenshot}
            elif args.gui_command == "plan-type":
                kind = "type"
                kwargs = {"value_env": args.value_env}
            elif args.gui_command == "plan-launch":
                kind = "launch"
                kwargs = {"url": args.url}
            else:
                kind = "key"
                kwargs = {"key_name": args.key_name}
            plan = create_gui_plan(
                task_id=task.task_id, agent_id=task.agent_id, screen_id=screen.screen_id,
                kind=kind, screens=screens,
                confidence_threshold=config.gui.confidence_threshold, **kwargs)
            approvals = store.add(plan, expiry_minutes=config.approval.approval_expiry_minutes)
            prepare_task_for_approval(paths.database_file, plan,
                                      [item.approval_id for item in approvals])
            console.print(f"GUI plan ID: {plan.plan_id}")
            for item in approvals:
                approval_panel(console, item)
            return 0
        if args.command == "watchdog":
            if args.watchdog_command == "run":
                console.print("Watchdog running. Press Ctrl+C to stop.")
                try:
                    asyncio.run(run_periodic(paths.database_file, paths.data_dir,
                                             interval_seconds=config.resource_management.watchdog_interval_seconds))
                except KeyboardInterrupt:
                    console.print("Watchdog stopped")
                return 0
            report = (asyncio.run(watchdog_cleanup(paths.database_file, paths.data_dir))
                      if args.watchdog_command == "cleanup"
                      else watchdog_scan(paths.database_file, paths.data_dir))
            console.print(report.model_dump_json(indent=2))
            return 0
        if args.command == "metrics":
            for metric in MetricsStore(paths.database_file).list():
                console.print(metric)
            return 0
        if args.command == "trace":
            for event in TaskStore(paths.database_file).events(args.task_id):
                console.print(event)
            return 0
        if args.command == "research":
            search_key = os.environ.get("HIVE_BRAVE_API_KEY")
            search_provider = BraveSearchProvider(search_key) if args.search and search_key else None
            report = asyncio.run(research(ResearchRequest(
                question=args.question, urls=args.url,
                man_topics=args.man, packages=args.package, search_query=args.search,
            ), search_provider=search_provider))
            console.print(Panel(report.summary, title="Research summary"))
            for citation in report.citations:
                console.print(f"{citation.title}: {citation.source}")
            for issue in report.unanswered_questions:
                console.print(f"Unavailable: {issue}")
            return 0
        if args.command == "operator":
            if args.operator_command == "propose":
                sources = ResearchRequest(question=TaskStore(paths.database_file).get(args.task_id).objective,
                                          urls=args.url, man_topics=args.man,
                                          packages=args.package, search_query=args.search)
                search_key = os.environ.get("HIVE_BRAVE_API_KEY")
                search_provider = BraveSearchProvider(search_key) if args.search and search_key else None
                router = build_router(config, allow_cloud=args.allow_cloud,
                                      database_file=paths.database_file)
                result = asyncio.run(propose(
                    args.task_id, database_file=paths.database_file, router=router,
                    model=config.models.local.model, sources=sources,
                    allow_cloud=args.allow_cloud, search_provider=search_provider))
                console.print(Panel(result.model_dump_json(indent=2), title="Operator proposal"))
                task = TaskStore(paths.database_file).get(args.task_id)
                for source in task.state.get("research_sources", []):
                    console.print(f"Source: {source}")
                for issue in task.state.get("research_unavailable", []):
                    console.print(f"Unavailable: {issue}")
                return 0
            if args.operator_command == "answer":
                answer_clarification(args.task_id, args.answer,
                                     database_file=paths.database_file)
                console.print("Clarification saved. Run operator propose again.")
                return 0
            if args.operator_command == "discuss":
                discuss(args.task_id, args.note, database_file=paths.database_file)
                console.print("Discussion note saved. Run operator propose to revise it, or operator plan.")
                return 0
            if args.operator_command == "show":
                task = TaskStore(paths.database_file).get(args.task_id)
                console.print(Panel(json.dumps(task.state, indent=2),
                                    title=f"Proposal for {task.task_id}"))
                return 0
            plan, approvals = make_reviewable_plan(
                args.task_id, database_file=paths.database_file,
                audit_file=paths.state_dir / "audit.jsonl",
                approval_minutes=config.approval.approval_expiry_minutes)
            console.print(f"Plan ID: {plan.plan_id}")
            for item in approvals:
                approval_panel(console, item)
            return 0
        if args.command == "backup":
            backups = BackupStore(paths.database_file, paths.data_dir / "backups")
            if args.backup_command == "list":
                for record in backups.list(task_id=args.task_id):
                    console.print(f"{record.backup_id}  {record.original_path}  {record.created_at}")
                return 0
            if args.backup_command == "show":
                record = backups.get(args.backup_id)
                console.print(Panel(record.model_dump_json(indent=2), title=record.backup_id))
                return 0
            task = TaskStore(paths.database_file).get(args.task_id)
            if task.phase != TaskPhase.INTAKE:
                raise HiveError("Restore needs a new task at intake")
            plan = backups.restore_plan(args.backup_id, task_id=task.task_id,
                                        agent_id=task.agent_id,
                                        workspace_root=args.workspace)
            approvals = PlanStore(paths.database_file,
                                  audit_file=paths.state_dir / "audit.jsonl").add(
                                      plan, approval_minutes=config.approval.approval_expiry_minutes)
            prepare_task_for_approval(paths.database_file, plan,
                                      [request.approval_id for request in approvals])
            console.print(f"Restore plan ID: {plan.plan_id}")
            for request in approvals:
                approval_panel(console, request)
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
                    raise HiveError("System plans currently support Fedora only")
                adapter = FedoraAdapter()
                common = dict(task_id=task.task_id, agent_id=task.agent_id,
                              workspace_root=Path.cwd())
                if args.plan_command == "install-package":
                    plan = adapter.install_package(args.package, start_service=args.start_service, **common)
                elif args.plan_command == "remove-package":
                    plan = adapter.remove_package(args.package, **common)
                elif args.plan_command == "refresh-packages":
                    plan = adapter.update_package_index(**common)
                elif args.plan_command == "upgrade-system":
                    plan = adapter.upgrade_system(**common)
                elif args.plan_command == "service":
                    service_name = args.service if args.service.endswith(".service") else f"{args.service}.service"
                    plan = adapter.service(args.action, service_name, **common)
                elif args.plan_command == "open-firewall-port":
                    plan = adapter.open_firewall_port(args.port, protocol=args.protocol, **common)
                else:
                    raise HiveError(f"Unsupported plan action: {args.plan_command}")
            approvals = store.add(plan, approval_minutes=config.approval.approval_expiry_minutes)
            prepare_task_for_approval(paths.database_file, plan,
                                      [item.approval_id for item in approvals])
            console.print(f"Plan ID: {plan.plan_id}")
            for item in approvals:
                approval_panel(console, item)
            return 0
        if args.command == "run":
            results = asyncio.run(execute_plan(args.plan_id, database_file=paths.database_file,
                                               audit_file=paths.state_dir / "audit.jsonl",
                                               data_dir=paths.data_dir,
                                               max_retries=config.retry.max_retries,
                                               initial_delay_seconds=config.retry.initial_delay_seconds,
                                               backoff_multiplier=config.retry.backoff_multiplier,
                                               command_timeout_seconds=config.timeouts.command_default_seconds))
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
