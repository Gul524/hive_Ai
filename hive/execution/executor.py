"""Execute stored plans only after matching, current approvals."""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import tempfile
from pathlib import Path

from hive.agents.scheduler import LockStore
from hive.core.approvals import ApprovalStore
from hive.core.models import ApprovalStatus, RiskLevel, utc_now
from hive.core.orchestrator import start_execution, finish_execution
from hive.execution.command_plan import CommandPlan, ExecutionStep, PlanError, PlanStore, VerificationCheck, MAX_FILE_BYTES
from hive.failures.store import FailedTaskRecord, FailureClass, FailureStore
from hive.observability.audit import AuditLog
from hive.policy.risk import classify_command, classify_file_write
from hive.resources.manager import ResourceHandle, ResourceManager, ResourceType
from hive.observability.logger import redact


class ExecutionError(PlanError):
    pass


async def run_command(argv: list[str], *, timeout: int = 300,
                      resources: ResourceManager | None = None,
                      task_id: str = "standalone", agent_id: str = "core") -> tuple[int, str, str]:
    decision = classify_command(argv)
    if not decision.allowed:
        raise ExecutionError(decision.reason)
    safe_argv = ["sudo", "-n", *argv[1:]] if argv[0] == "sudo" else argv
    process = await asyncio.create_subprocess_exec(
        *safe_argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True,
    )
    handle: ResourceHandle | None = None

    async def cleanup() -> bool:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.communicate(), 2)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.communicate()
            except ProcessLookupError:
                await process.communicate()
        return process.returncode is not None

    try:
        if resources:
            handle = await resources.acquire(ResourceHandle(
                task_id=task_id, agent_id=agent_id, resource_type=ResourceType.PROCESS,
                timeout_seconds=timeout, metadata={"pid": process.pid, "argv": argv},
            ), cleanup)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await cleanup()
        raise
    finally:
        if handle:
            await resources.release(handle.resource_id)
        else:
            await cleanup()
    return process.returncode, stdout.decode(errors="replace")[:20000], stderr.decode(errors="replace")[:20000]


async def verify(check: VerificationCheck, *, resources: ResourceManager | None = None,
                 task_id: str = "standalone", agent_id: str = "core") -> bool:
    if check.kind == "file_content":
        return bool(check.path and check.path.exists() and
                    check.path.read_text(encoding="utf-8") == check.expected_content)
    if check.kind == "command_success" and check.command_argv:
        code, _, _ = await run_command(check.command_argv, timeout=30, resources=resources,
                                       task_id=task_id, agent_id=agent_id)
        return code == 0
    return False


def _approval_guard(plan: CommandPlan, store: ApprovalStore) -> None:
    approvals = store.list(task_id=plan.task_id, agent_id=plan.agent_id)
    digest = plan.digest()
    plan_approval = [a for a in approvals if a.plan_id == plan.plan_id and a.step_id is None
                     and a.plan_digest == digest and a.status == ApprovalStatus.APPROVED
                     and a.expires_at > utc_now()]
    if not plan_approval:
        raise ExecutionError("Plan approval is missing, denied, or expired")
    for step in plan.steps:
        if step.risk_level == RiskLevel.HIGH and not any(
            a.plan_id == plan.plan_id and a.step_id == step.step_id and a.plan_digest == digest
            and a.status == ApprovalStatus.APPROVED and a.expires_at > utc_now() for a in approvals
        ):
            raise ExecutionError(f"High-risk step approval is missing: {step.step_id}")


async def execute_plan(plan_id: str, *, database_file: Path,
                       audit_file: Path | None = None) -> list[str]:
    plan_store = PlanStore(database_file)
    plan = plan_store.get(plan_id)
    approvals = ApprovalStore(database_file, audit_file=audit_file)
    audit = AuditLog(audit_file or database_file.parent / "audit.jsonl")
    locks = LockStore(database_file)
    resources = ResourceManager(database_file)
    claimed = False
    try:
        _approval_guard(plan, approvals)
        plan_store.claim_run(plan_id)
        claimed = True
        start_execution(database_file, plan.task_id)
        results = await _execute_approved_plan(plan, approvals, locks, audit, resources)
        leaks = await resources.cleanup_all(task_id=plan.task_id)
        if leaks:
            raise ExecutionError(f"Resource cleanup failed: {leaks}")
        plan_store.finish_run(plan_id, success=True)
        finish_execution(database_file, plan.task_id, success=True)
        audit.append("task_completed", task_id=plan.task_id, agent_id=plan.agent_id,
                     plan_id=plan.plan_id)
        return results
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        plan_store.finish_run(plan_id, success=False)
        if claimed:
            finish_execution(database_file, plan.task_id, success=False)
        leaks = await resources.cleanup_all(task_id=plan.task_id)
        failure_class = (FailureClass.APPROVAL if "approval" in str(exc).lower()
                         else FailureClass.VERIFICATION if "verification" in str(exc).lower()
                         else FailureClass.COMMAND)
        FailureStore(database_file, database_file.parent / "failed_tasks").add(FailedTaskRecord(
            task_id=plan.task_id, agent_id=plan.agent_id, objective=plan.summary,
            plan_id=plan.plan_id, failure_class=failure_class, error_message=str(exc),
            logs_path=str((audit_file.parent if audit_file else database_file.parent) / "logs"),
            resource_report={"handles": [item.model_dump(mode="json") for item in resources.list()
                                         if item.task_id == plan.task_id]},
            cleanup_report={"locks_released": True, "resource_leaks": leaks},
            suggested_fixes=["Inspect the plan and error, then create a new plan for repair"],
        ))
        audit.append("task_failed", task_id=plan.task_id, agent_id=plan.agent_id,
                     plan_id=plan.plan_id, error=str(exc))
        raise


async def _execute_approved_plan(plan: CommandPlan, approvals: ApprovalStore,
                                 locks: LockStore, audit: AuditLog,
                                 resources: ResourceManager) -> list[str]:
    results: list[str] = []
    for step in plan.steps:
        _approval_guard(plan, approvals)
        key = (f"file:{step.target_path.resolve()}" if step.tool == "write_file"
               else "package-manager" if step.command_argv and "dnf" in step.command_argv
               else "service-manager")
        async with locks.hold(key, agent_id=plan.agent_id, task_id=plan.task_id):
            if step.tool == "command":
                assert step.command_argv is not None
                decision = classify_command(step.command_argv)
                if not decision.allowed or decision.risk_level != step.risk_level:
                    raise ExecutionError("Command policy changed since plan creation")
                attempts = 3 if step.idempotent and step.risk_level == RiskLevel.LOW else 1
                for attempt in range(attempts):
                    code, stdout, stderr = await run_command(
                        step.command_argv, timeout=step.timeout_seconds, resources=resources,
                        task_id=plan.task_id, agent_id=plan.agent_id,
                    )
                    if code == 0:
                        break
                    if attempt == attempts - 1:
                        raise ExecutionError(f"Step failed with exit {code}: {redact(stderr[:300])}")
                    await asyncio.sleep(2 ** attempt)
                results.append(stdout)
                audit.append("command_executed", task_id=plan.task_id, agent_id=plan.agent_id,
                             step_id=step.step_id, command_argv=step.command_argv)
                for check in step.verification:
                    if not await verify(check, resources=resources,
                                        task_id=plan.task_id, agent_id=plan.agent_id):
                        raise ExecutionError(f"Verification failed for step {step.step_id}")
            else:
                await _write_file_step(step, plan)
                results.append(f"Wrote {step.target_path}")
                audit.append("file_written", task_id=plan.task_id, agent_id=plan.agent_id,
                             step_id=step.step_id, path=str(step.target_path))
    return results


async def _write_file_step(step: ExecutionStep, plan: CommandPlan) -> None:
    assert step.target_path is not None and step.content is not None
    target = step.target_path
    decision = classify_file_write(target, allowed_root=plan.workspace_root)
    if not decision.allowed or decision.risk_level != step.risk_level:
        raise ExecutionError("File path is outside approved workspace")
    if target.exists() and target.stat().st_size > MAX_FILE_BYTES:
        raise ExecutionError("File grew beyond the safe-edit limit")
    current = target.read_bytes() if target.exists() else None
    digest = hashlib.sha256(current).hexdigest() if current is not None else None
    if digest != step.before_sha256:
        raise ExecutionError("File changed after plan review; create a new plan")
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(f".{target.name}.{step.step_id}.hive-backup") if current is not None else None
    if backup:
        backup.write_bytes(current)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".hive-", delete=False) as stream:
            stream.write(step.content)
            stream.flush()
            os.fsync(stream.fileno())
            temp_path = Path(stream.name)
        os.replace(temp_path, target)
        if not all([await verify(check) for check in step.verification]):
            raise ExecutionError("File verification failed")
    except BaseException:
        if backup and backup.exists():
            os.replace(backup, target)
        elif current is None:
            target.unlink(missing_ok=True)
        raise
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        if backup:
            backup.unlink(missing_ok=True)
