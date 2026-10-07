"""Execute stored plans only after matching, current approvals."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import pty
import re
import signal
import shutil
import stat
import tempfile
import time
from pathlib import Path

from hive.agents.scheduler import LockStore
from hive.core.approvals import ApprovalStore
from hive.core.models import ApprovalStatus, RiskLevel, utc_now
from hive.core.orchestrator import start_execution, finish_execution
from hive.execution.command_plan import CommandPlan, ExecutionStep, PlanError, PlanStore, VerificationCheck, MAX_FILE_BYTES
from hive.execution.backups import BackupStore
from hive.failures.store import FailedTaskRecord, FailureClass, FailureStore
from hive.observability.audit import AuditLog
from hive.policy.risk import classify_command, classify_file_write
from hive.resources.manager import ResourceHandle, ResourceManager, ResourceType
from hive.observability.logger import redact
from hive.observability.metrics import MetricsStore


class ExecutionError(PlanError):
    pass


async def run_command(argv: list[str], *, timeout: int = 300,
                      resources: ResourceManager | None = None,
                      task_id: str = "standalone", agent_id: str = "core",
                      working_directory: Path | None = None,
                      environment: dict[str, str] | None = None,
                      use_pty: bool = False) -> tuple[int, str, str]:
    decision = classify_command(argv)
    if not decision.allowed:
        raise ExecutionError(decision.reason)
    if working_directory is not None and not working_directory.is_dir():
        raise ExecutionError("Working directory does not exist")
    env = os.environ.copy()
    for key, value in (environment or {}).items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key.startswith(("LD_", "PYTHON", "SUDO_")) \
                or key in {"PATH", "HOME", "SHELL", "BASH_ENV", "ENV"} or "\x00" in value:
            raise ExecutionError("Unsafe environment override")
        env[key] = value
    safe_argv = ["sudo", "-n", *argv[1:]] if argv[0] == "sudo" else argv
    master_fd: int | None = None
    slave_fd: int | None = None
    if use_pty:
        master_fd, slave_fd = pty.openpty()
        os.set_blocking(master_fd, False)
    try:
        process = await asyncio.create_subprocess_exec(
            *safe_argv, stdin=slave_fd if use_pty else asyncio.subprocess.DEVNULL,
            stdout=slave_fd if use_pty else asyncio.subprocess.PIPE,
            stderr=slave_fd if use_pty else asyncio.subprocess.PIPE,
            cwd=working_directory, env=env, start_new_session=True,
        )
    except BaseException:
        if master_fd is not None:
            os.close(master_fd)
        if slave_fd is not None:
            os.close(slave_fd)
        raise
    if slave_fd is not None:
        os.close(slave_fd)
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
        if master_fd is None:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        else:
            reader = _pty_reader(master_fd)
            await asyncio.wait_for(asyncio.gather(process.wait(), reader), timeout)
            stdout = reader.result()
            stderr = b""
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await cleanup()
        raise
    finally:
        try:
            if handle:
                await resources.release(handle.resource_id)
            else:
                await cleanup()
        finally:
            if master_fd is not None:
                asyncio.get_running_loop().remove_reader(master_fd)
                os.close(master_fd)
    return process.returncode, stdout.decode(errors="replace")[:20000], stderr.decode(errors="replace")[:20000]


def _pty_reader(fd: int) -> asyncio.Future[bytes]:
    loop = asyncio.get_running_loop()
    result: asyncio.Future[bytes] = loop.create_future()
    output = bytearray()

    def ready() -> None:
        try:
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    finish()
                    return
                if len(output) < 20000:
                    output.extend(chunk[:20000 - len(output)])
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno in {errno.EIO, errno.EBADF}:
                finish()
            elif not result.done():
                result.set_exception(exc)
            loop.remove_reader(fd)

    def finish() -> None:
        loop.remove_reader(fd)
        if not result.done():
            result.set_result(bytes(output))

    loop.add_reader(fd, ready)
    return result


async def verify(check: VerificationCheck, *, resources: ResourceManager | None = None,
                 task_id: str = "standalone", agent_id: str = "core") -> bool:
    if check.kind == "file_content":
        return bool(check.path and check.path.exists() and
                    check.path.read_text(encoding="utf-8") == check.expected_content)
    if check.kind in {"command_success", "command_exit", "command_output"} and check.command_argv:
        code, stdout, _stderr = await run_command(check.command_argv, timeout=30, resources=resources,
                                                  task_id=task_id, agent_id=agent_id)
        if check.kind == "command_success":
            return code == 0
        if check.kind == "command_exit":
            return code in check.expected_exit_codes
        return (code in check.expected_exit_codes and check.expected_content is not None
                and check.expected_content in stdout)
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
                       audit_file: Path | None = None,
                       data_dir: Path | None = None,
                       max_retries: int = 2,
                       initial_delay_seconds: float = 2,
                       backoff_multiplier: float = 2,
                       command_timeout_seconds: int = 300) -> list[str]:
    if not 0 <= max_retries <= 2 or initial_delay_seconds < 0 \
            or backoff_multiplier < 1 or command_timeout_seconds < 1:
        raise ExecutionError("Invalid execution retry or timeout settings")
    plan_store = PlanStore(database_file)
    plan = plan_store.get(plan_id)
    approvals = ApprovalStore(database_file, audit_file=audit_file)
    audit = AuditLog(audit_file or database_file.parent / "audit.jsonl")
    locks = LockStore(database_file)
    resources = ResourceManager(database_file)
    backups = BackupStore(database_file, (data_dir or database_file.parent) / "backups")
    claimed = False
    try:
        _approval_guard(plan, approvals)
        plan_store.claim_run(plan_id)
        claimed = True
        start_execution(database_file, plan.task_id)
        results = await _execute_approved_plan(plan, approvals, locks, audit, resources,
                                               backups, max_retries=max_retries,
                                               initial_delay_seconds=initial_delay_seconds,
                                               backoff_multiplier=backoff_multiplier,
                                               command_timeout_seconds=command_timeout_seconds)
        leaks = await resources.cleanup_all(task_id=plan.task_id)
        if leaks:
            raise ExecutionError(f"Resource cleanup failed: {leaks}")
        plan_store.finish_run(plan_id, success=True)
        finish_execution(database_file, plan.task_id, success=True)
        audit.append("task_completed", task_id=plan.task_id, agent_id=plan.agent_id,
                     plan_id=plan.plan_id)
        return results
    except BaseException as exc:
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
                                 resources: ResourceManager,
                                 backups: BackupStore, *, max_retries: int,
                                 initial_delay_seconds: float,
                                 backoff_multiplier: float,
                                 command_timeout_seconds: int) -> list[str]:
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
                attempts = 1 + max_retries if step.idempotent and step.risk_level == RiskLevel.LOW else 1
                for attempt in range(attempts):
                    started = time.monotonic()
                    code, stdout, stderr = await run_command(
                        step.command_argv,
                        timeout=min(step.timeout_seconds, command_timeout_seconds), resources=resources,
                        task_id=plan.task_id, agent_id=plan.agent_id,
                        working_directory=step.working_directory,
                        environment=step.environment, use_pty=step.pty,
                    )
                    MetricsStore(resources.database_file).add(
                        "command_duration_seconds", time.monotonic() - started
                    )
                    if code == 0:
                        break
                    transient = any(word in stderr.lower() for word in
                                    ("temporarily unavailable", "timed out", "connection reset"))
                    if attempt == attempts - 1 or not transient:
                        raise ExecutionError(f"Step failed with exit {code}: {redact(stderr[:300])}")
                    MetricsStore(resources.database_file).add("retry_count")
                    await asyncio.sleep(initial_delay_seconds * backoff_multiplier ** attempt)
                results.append(stdout)
                audit.append("command_executed", task_id=plan.task_id, agent_id=plan.agent_id,
                             step_id=step.step_id, command_argv=step.command_argv)
                for check in step.verification:
                    if not await verify(check, resources=resources,
                                        task_id=plan.task_id, agent_id=plan.agent_id):
                        raise ExecutionError(f"Verification failed for step {step.step_id}")
            else:
                await _write_file_step(step, plan, backups)
                results.append(f"Wrote {step.target_path}")
                audit.append("file_written", task_id=plan.task_id, agent_id=plan.agent_id,
                             step_id=step.step_id, path=str(step.target_path))
    return results


async def _write_file_step(step: ExecutionStep, plan: CommandPlan,
                           backups: BackupStore) -> None:
    assert step.target_path is not None and step.content is not None
    target = step.target_path
    decision = classify_file_write(target, allowed_root=plan.workspace_root)
    if not decision.allowed or decision.risk_level != step.risk_level:
        raise ExecutionError("File path is outside approved workspace")
    if target.exists() and target.stat().st_size > MAX_FILE_BYTES:
        raise ExecutionError("File grew beyond the safe-edit limit")
    current = target.read_bytes() if target.exists() else None
    original_mode = stat.S_IMODE(target.stat().st_mode) if current is not None else None
    digest = hashlib.sha256(current).hexdigest() if current is not None else None
    if digest != step.before_sha256:
        raise ExecutionError("File changed after plan review; create a new plan")
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_record = (backups.create(task_id=plan.task_id, agent_id=plan.agent_id,
                                    original_path=target) if current is not None else None)
    if backup_record and backup_record.sha256 != digest:
        raise ExecutionError("File changed while creating its backup")
    backup_path = backups.path(backup_record) if backup_record else None
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=".hive-", delete=False) as stream:
            stream.write(step.content)
            stream.flush()
            os.fsync(stream.fileno())
            temp_path = Path(stream.name)
        if original_mode is not None:
            os.chmod(temp_path, original_mode)
        os.replace(temp_path, target)
        if not all([await verify(check) for check in step.verification]):
            raise ExecutionError("File verification failed")
    except BaseException:
        if backup_path and backup_path.exists():
            restore_temp = target.with_name(f".{target.name}.{step.step_id}.hive-restore")
            try:
                shutil.copy2(backup_path, restore_temp)
                os.replace(restore_temp, target)
            finally:
                restore_temp.unlink(missing_ok=True)
        elif current is None:
            target.unlink(missing_ok=True)
        raise
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
