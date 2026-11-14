"""Rich CLI for local task memory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hive.cli.render import approval_panel
from hive.config.schema import HiveConfig
from hive.memory.manager import SkillManager, match_decision, match_skills, resolve_parameters
from hive.memory.models import (EntityMemory, ImprovementStatus, MemoryError, Skill,
                                SkillStatus, TriggerAction, TriggerEvent, TriggerRule)
from hive.memory.store import MemoryStore
from hive.memory.triggers import TriggerEngine
from hive.paths import HivePaths


def install_parsers(commands) -> None:
    skills = commands.add_parser("skills", help="Manage local task skills")
    sub = skills.add_subparsers(dest="memory_command", required=True)
    sub.add_parser("list")
    sub.add_parser("match").add_argument("request")
    show = sub.add_parser("show")
    show.add_argument("skill_id")
    for action in ("approve", "disable", "delete", "versions"):
        sub.add_parser(action).add_argument("skill_id")
    sub.add_parser("reject").add_argument("skill_id")
    edit = sub.add_parser("edit")
    edit.add_argument("skill_id")
    edit.add_argument("recipe_file", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("skill_id")
    restore.add_argument("version", type=int)
    save = sub.add_parser("save-task")
    save.add_argument("task_id")
    save.add_argument("--name", required=True)
    save.add_argument("--phrase", action="append", default=[])
    add = sub.add_parser("add")
    add.add_argument("recipe_file", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("skill_id")
    plan.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    plan.add_argument("--workspace", type=Path, default=Path.cwd())

    entities = commands.add_parser("entities", help="Inspect local entity memory")
    sub = entities.add_subparsers(dest="memory_command", required=True)
    sub.add_parser("list")
    sub.add_parser("show").add_argument("entity_id")
    sub.add_parser("delete").add_argument("entity_id")
    update = sub.add_parser("update")
    update.add_argument("entity_id")
    update.add_argument("--name")
    update.add_argument("--value")
    update.add_argument("--description")
    add = sub.add_parser("add")
    add.add_argument("entity_type")
    add.add_argument("name")
    add.add_argument("value")

    triggers = commands.add_parser("triggers", help="Manage event triggers")
    sub = triggers.add_subparsers(dest="memory_command", required=True)
    sub.add_parser("list")
    sub.add_parser("pending")
    add = sub.add_parser("add")
    add.add_argument("--name")
    add.add_argument("--event", choices=[e.value for e in TriggerEvent])
    add.add_argument("--action", choices=[a.value for a in TriggerAction])
    add.add_argument("--task-id")
    add.add_argument("--payload-file", type=Path)
    for action in ("disable", "delete"):
        sub.add_parser(action).add_argument("trigger_id")
    run = sub.add_parser("run", help="Execute a trigger after approving its queued request")
    run.add_argument("trigger_id")
    run.add_argument("event_key")
    run.add_argument("approval_id")

    improvements = commands.add_parser("improvements", help="Review versioned skill changes")
    sub = improvements.add_subparsers(dest="memory_command", required=True)
    sub.add_parser("list")
    sub.add_parser("show").add_argument("proposal_id")
    for action in ("approve", "deny"):
        sub.add_parser(action).add_argument("proposal_id")
    edit = sub.add_parser("edit")
    edit.add_argument("proposal_id")
    edit.add_argument("--changes-file", required=True, type=Path)
    propose = sub.add_parser("propose")
    propose.add_argument("skill_id")
    propose.add_argument("--reason", required=True)
    propose.add_argument("--evidence", required=True)
    propose.add_argument("--changes-file", required=True, type=Path)


def _panel(console: Console, value, title: str) -> None:
    console.print(Panel(value.model_dump_json(indent=2), title=title))


def _params(items: list[str]) -> dict[str, str]:
    result = {}
    for item in items:
        if "=" not in item:
            raise MemoryError("Parameters must use NAME=VALUE")
        name, value = item.split("=", 1)
        if not name or name in result:
            raise MemoryError("Parameter name is missing or repeated")
        result[name] = value
    return result


def handle(args: argparse.Namespace, config: HiveConfig, paths: HivePaths,
           console: Console) -> int:
    store = MemoryStore(paths.database_file)
    manager = SkillManager(paths.database_file)
    action = args.memory_command
    if args.command == "skills":
        if not config.memory.skill_memory:
            raise MemoryError("Skill memory is disabled")
        if action == "list":
            table = Table(title="Skills")
            for heading in ("ID", "Name", "Status", "Version", "Success", "Failure"):
                table.add_column(heading)
            for skill in store.list_skills():
                table.add_row(skill.skill_id, skill.name, skill.status.value, str(skill.version),
                              str(skill.success_count), str(skill.failure_count))
            console.print(table)
        elif action == "show":
            _panel(console, store.get_skill(args.skill_id), "Skill proposal and current version")
        elif action == "versions":
            for version in store.skill_versions(args.skill_id):
                console.print(f"{version.version}: {version.snapshot.name} ({version.created_at})")
        elif action == "approve":
            _panel(console, store.set_skill_status(args.skill_id, SkillStatus.ACTIVE), "Approved skill")
        elif action == "disable":
            _panel(console, store.set_skill_status(args.skill_id, SkillStatus.DISABLED), "Disabled skill")
        elif action in {"delete", "reject"}:
            store.delete_skill(args.skill_id)
            console.print("Skill deleted")
        elif action == "edit":
            skill = Skill.model_validate_json(args.recipe_file.read_text(encoding="utf-8"))
            if skill.skill_id != args.skill_id:
                raise MemoryError("Edited skill ID does not match")
            _panel(console, store.replace_draft(skill), "Edited draft skill")
        elif action == "restore":
            _panel(console, manager.restore_version(args.skill_id, args.version,
                   max_versions=config.memory.max_skill_versions), "Restored as new version")
        elif action == "save-task":
            skill = manager.draft_from_task(args.task_id, name=args.name,
                                            trigger_phrases=args.phrase or [args.name])
            _panel(console, skill, "Draft skill: review, then approve or delete")
        elif action == "add":
            skill = Skill.model_validate_json(args.recipe_file.read_text(encoding="utf-8"))
            _panel(console, store.add_skill(skill), "Draft skill: review, then approve or delete")
        elif action == "match":
            matches = match_skills(store, args.request)
            if matches:
                skill, score = matches[0]
                decision = match_decision(score)
                if decision == "ask":
                    console.print(f"This looks similar to a saved skill: {skill.name}. Use it? "
                                  f"[{skill.skill_id}, confidence {score:.2f}]")
                elif decision == "propose":
                    console.print(f"Suggested saved skill: {skill.name} "
                                  f"[{skill.skill_id}, confidence {score:.2f}]")
                else:
                    console.print("No confident saved skill; continue normal planning")
            else:
                console.print("No saved skill matched; continue normal planning")
        elif action == "plan":
            skill = store.get_skill(args.skill_id)
            entities = store.list_entities() if config.memory.entity_memory else []
            values, missing = resolve_parameters(skill, _params(args.param), entities)
            if missing and console.is_terminal:
                for name in missing:
                    values[name] = console.input(f"{name}: ")
                values, missing = resolve_parameters(skill, values, entities)
            if missing:
                raise MemoryError("Missing parameters: " + ", ".join(missing))
            console.print(Panel(json.dumps(values, indent=2), title="Final parameter summary"))
            task, plan, approvals = manager.create_plan(
                skill.skill_id, values, workspace_root=args.workspace,
                approval_minutes=config.approval.approval_expiry_minutes,
                browser_enabled=config.browser.enabled, gui_enabled=config.gui.enabled,
                data_dir=paths.data_dir, entity_memory_enabled=config.memory.entity_memory)
            console.print(f"Task ID: {task.task_id}\nPlan ID: {plan.plan_id}")
            for request in approvals:
                approval_panel(console, request)
        return 0
    if args.command == "entities":
        if not config.memory.entity_memory:
            raise MemoryError("Entity memory is disabled")
        if action == "list":
            for entity in store.list_entities():
                console.print(f"{entity.entity_id}  {entity.entity_type}  {entity.name}: {entity.value}")
        elif action == "show":
            _panel(console, store.get_entity(args.entity_id), "Entity")
        elif action == "delete":
            store.delete_entity(args.entity_id)
            console.print("Entity deleted")
        elif action == "update":
            _panel(console, store.update_entity(args.entity_id, name=args.name,
                   value=args.value, description=args.description), "Updated entity")
        else:
            _panel(console, store.add_entity(EntityMemory(entity_type=args.entity_type,
                   name=args.name, value=args.value)), "Saved entity")
        return 0
    if args.command == "triggers":
        if not config.triggers.enabled:
            raise MemoryError("Triggers are disabled")
        engine = TriggerEngine(paths.database_file)
        if action == "list":
            for rule in store.list_triggers():
                console.print(f"{rule.trigger_id}  {rule.event.value} → {rule.action.value}  enabled={rule.enabled}")
        elif action == "pending":
            from hive.core.approvals import ApprovalStore
            ids = {rule.trigger_id for rule in store.list_triggers()}
            for event in store.list_trigger_events():
                if event["status"] == "pending":
                    console.print(f"Trigger {event['trigger_id']} event key: {event['event_key']}")
            for request in ApprovalStore(paths.database_file).list(pending_only=True):
                if request.plan_id in ids:
                    approval_panel(console, request)
        elif action == "add":
            name = args.name or (console.input("Trigger name: ") if console.is_terminal else None)
            event = args.event or (console.input("Event: ") if console.is_terminal else None)
            trigger_action = args.action or (console.input("Action: ") if console.is_terminal else None)
            if not name or not event or not trigger_action:
                raise MemoryError("Trigger needs a name, event, and action")
            payload = json.loads(args.payload_file.read_text(encoding="utf-8")) if args.payload_file else {}
            rule = TriggerRule(name=name, event=TriggerEvent(event),
                               condition={"task_id": args.task_id} if args.task_id else {},
                               action=TriggerAction(trigger_action), action_payload=payload)
            _panel(console, engine.add(rule), "Saved trigger")
        elif action == "disable":
            rule = store.get_trigger(args.trigger_id)
            rule.enabled = False
            store.update_trigger(rule)
            _panel(console, rule, "Disabled trigger")
        elif action == "delete":
            store.delete_trigger(args.trigger_id)
            console.print("Trigger deleted")
        else:
            engine.execute_approved(args.trigger_id, args.event_key, args.approval_id)
            console.print("Trigger action completed")
        return 0
    if args.command == "improvements":
        if action == "list":
            for proposal in store.list_improvements():
                console.print(f"{proposal.proposal_id}  {proposal.skill_id}  {proposal.status.value}")
        elif action == "show":
            _panel(console, store.get_improvement(args.proposal_id), "Improvement proposal")
        elif action == "propose":
            changes = json.loads(args.changes_file.read_text(encoding="utf-8"))
            _panel(console, manager.propose_improvement(args.skill_id, reason=args.reason,
                   evidence=args.evidence, changes=changes), "Pending improvement")
        elif action == "approve":
            _panel(console, manager.approve_improvement(args.proposal_id,
                   max_versions=config.memory.max_skill_versions), "Applied new skill version")
        elif action == "edit":
            changes = json.loads(args.changes_file.read_text(encoding="utf-8"))
            _panel(console, manager.edit_improvement(args.proposal_id, changes),
                   "Edited improvement proposal")
        else:
            proposal = store.get_improvement(args.proposal_id)
            if proposal.status != ImprovementStatus.PENDING:
                raise MemoryError("Improvement is no longer pending")
            proposal.status = ImprovementStatus.DENIED
            store.update_improvement(proposal)
            _panel(console, proposal, "Denied improvement")
        return 0
    raise MemoryError("Unknown memory command")
