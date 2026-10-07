"""Wayland host input requires a user-granted desktop portal session."""

from __future__ import annotations

from pathlib import Path

from hive.gui.adapter import GuiAdapter


class WaylandAdapter(GuiAdapter):
    async def screenshot(self, path: Path) -> Path:
        raise PermissionError("Wayland screenshots require a desktop portal grant")

    async def click(self, x: int, y: int, *, approval_id: str) -> None:
        raise PermissionError("Wayland input requires a RemoteDesktop portal grant")

    async def close(self) -> bool:
        return True
