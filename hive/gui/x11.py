"""Approved X11 input in an isolated display."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from hive.core.approvals import ApprovalStore
from hive.core.models import ApprovalStatus, utc_now
from hive.gui.adapter import GuiAdapter


class X11Adapter(GuiAdapter):
    def __init__(self, *, display: str, approvals: ApprovalStore, plan_id: str,
                 plan_digest: str, step_id: str):
        self.display = display
        self.approvals = approvals
        self.plan_id = plan_id
        self.plan_digest = plan_digest
        self.step_id = step_id

    def _approved(self, approval_id: str) -> None:
        request = self.approvals.get(approval_id)
        if not (request.status == ApprovalStatus.APPROVED and request.plan_id == self.plan_id
                and request.step_id == self.step_id and request.plan_digest == self.plan_digest
                and request.expires_at > utc_now()):
            raise PermissionError("GUI action needs its matching, current step approval")

    async def _run(self, *argv: str) -> None:
        env = os.environ.copy()
        env["DISPLAY"] = self.display
        process = await asyncio.create_subprocess_exec(
            *argv, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.communicate()
            raise
        if process.returncode:
            raise RuntimeError(f"X11 action failed: {stderr.decode(errors='replace')[:300]}")

    async def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        await self._run("import", "-window", "root", "-silent", str(path))
        os.chmod(path, 0o600)
        return path

    async def click(self, x: int, y: int, *, approval_id: str) -> None:
        self._approved(approval_id)
        await self._run("xdotool", "mousemove", "--sync", str(x), str(y), "click", "1")

    async def type_text(self, value: str, *, approval_id: str) -> None:
        self._approved(approval_id)
        await self._run("xdotool", "type", "--clearmodifiers", "--", value)

    async def key(self, key_name: str, *, approval_id: str) -> None:
        self._approved(approval_id)
        if key_name not in {"Return", "Escape", "Tab", "BackSpace", "Left", "Right", "Up", "Down"}:
            raise ValueError("GUI key is outside the allowlist")
        await self._run("xdotool", "key", "--clearmodifiers", key_name)

    async def close(self) -> bool:
        return True  # The owning ScreenManager closes the Xvfb process.
