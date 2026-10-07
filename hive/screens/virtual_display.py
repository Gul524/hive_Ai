"""Launch and verify isolated Xvfb displays."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path


def process_start_ticks(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        return fields[21]
    except (OSError, IndexError):
        return None


def is_our_xvfb(pid: int, start_ticks: str, display: str) -> bool:
    if process_start_ticks(pid) != start_ticks:
        return False
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return False
    return "Xvfb" in command and display in command


def is_our_firefox(pid: int, start_ticks: str, profile: Path) -> bool:
    if process_start_ticks(pid) != start_ticks:
        return False
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return False
    return "firefox" in command.lower() and str(profile) in command


class XvfbBackend:
    async def launch_firefox(self, display: str, profile: Path, url: str) -> tuple[int, str]:
        env = os.environ.copy()
        env["DISPLAY"] = display
        process = await asyncio.create_subprocess_exec(
            "firefox", "--no-remote", "--profile", str(profile), "--new-window", url,
            env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        await asyncio.sleep(1)
        if process.returncode is not None:
            raise RuntimeError(f"Firefox exited with code {process.returncode}")
        ticks = process_start_ticks(process.pid)
        if not ticks:
            process.kill()
            await process.wait()
            raise RuntimeError("Could not identify Firefox process for safe cleanup")
        return process.pid, ticks

    async def stop_firefox(self, pid: int, start_ticks: str, profile: Path) -> bool:
        if not is_our_firefox(pid, start_ticks, profile):
            return process_start_ticks(pid) is None
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        for _ in range(30):
            if not is_our_firefox(pid, start_ticks, profile):
                return True
            await asyncio.sleep(0.1)
        if is_our_firefox(pid, start_ticks, profile):
            os.killpg(pid, signal.SIGKILL)
        for _ in range(20):
            if not is_our_firefox(pid, start_ticks, profile):
                return True
            await asyncio.sleep(0.1)
        return False

    async def start(self, display: str, *, width: int = 1280,
                    height: int = 800) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            "Xvfb", display, "-screen", "0", f"{width}x{height}x24",
            "-nolisten", "tcp", "-noreset", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        socket = Path(f"/tmp/.X11-unix/X{display.removeprefix(':')}")
        for _ in range(50):
            if socket.exists():
                ticks = process_start_ticks(process.pid)
                if not ticks:
                    process.kill()
                    await process.wait()
                    raise RuntimeError("Could not identify Xvfb process for safe cleanup")
                return process.pid, ticks
            if process.returncode is not None:
                raise RuntimeError(f"Xvfb exited with code {process.returncode}")
            await asyncio.sleep(0.1)
        process.kill()
        await process.wait()
        raise TimeoutError("Xvfb did not create its display socket")

    async def stop(self, pid: int, start_ticks: str, display: str) -> bool:
        if not is_our_xvfb(pid, start_ticks, display):
            return process_start_ticks(pid) is None
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        for _ in range(30):
            if not is_our_xvfb(pid, start_ticks, display):
                return True
            await asyncio.sleep(0.1)
        if is_our_xvfb(pid, start_ticks, display):
            os.killpg(pid, signal.SIGKILL)
        for _ in range(20):
            if not is_our_xvfb(pid, start_ticks, display):
                return True
            await asyncio.sleep(0.1)
        return False

    async def screenshot(self, display: str, path: Path) -> None:
        env = os.environ.copy()
        env["DISPLAY"] = display
        process = await asyncio.create_subprocess_exec(
            "import", "-window", "root", "-silent", str(path), env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.communicate()
            raise
        if process.returncode or not path.is_file():
            raise RuntimeError(f"Screen capture failed: {stderr.decode(errors='replace')[:300]}")
