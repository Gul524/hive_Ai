from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
import asyncio
import os

from hive.browser.safety import PAYMENT, url_is_public_https, validate_public_url
from hive.core.models import utc_now


class BrowserManager(ABC):
    @abstractmethod
    async def open(self, *, agent_id: str, task_id: str, isolated: bool = True) -> None: ...

    @abstractmethod
    async def screenshot(self, path: Path) -> Path: ...

    @abstractmethod
    async def close(self) -> bool: ...


class PlaywrightBrowserManager(BrowserManager):
    """One non-persistent browser context owned by one task."""

    def __init__(self, *, screenshot_root: Path, browser_name: str = "chromium",
                 visible: bool = True, max_tabs: int = 5, max_download_mb: int = 25,
                 allow_downloads: bool = False):
        self.screenshot_root = screenshot_root
        self.browser_name = browser_name
        self.visible = visible
        self.max_tabs = max_tabs
        self.max_download_bytes = max_download_mb * 1_000_000
        self.allow_downloads = allow_downloads
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._agent_id: str | None = None
        self._task_id: str | None = None
        self._checked_hosts: set[str] = set()

    async def open(self, *, agent_id: str, task_id: str, isolated: bool = True) -> None:
        if not isolated:
            raise ValueError("Browser must use an isolated context")
        if self._browser is not None:
            raise RuntimeError("Browser is already open")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Install the browser extra and Playwright Chromium first") from exc
        self._agent_id = agent_id
        self._task_id = task_id
        try:
            self._playwright = await async_playwright().start()
            browser_type = getattr(self._playwright, self.browser_name)
            self._browser = await browser_type.launch(headless=not self.visible)
            self._context = await self._browser.new_context(
                accept_downloads=self.allow_downloads, service_workers="block",
                permissions=[],
            )
            await self._context.route("**/*", self._route)
            self._page = await self._context.new_page()
        except BaseException:
            await self.close()
            raise

    async def _route(self, route: Any) -> None:
        url = route.request.url
        if not url_is_public_https(url):
            await route.abort()
            return
        host = (urlsplit(url).hostname or "").lower()
        if host not in self._checked_hosts:
            try:
                await validate_public_url(url)
            except (ValueError, OSError):
                await route.abort()
                return
            self._checked_hosts.add(host)
        await route.continue_()

    def _require_page(self) -> Any:
        if self._page is None:
            raise RuntimeError("Browser is closed")
        return self._page

    async def navigate(self, url: str, *, timeout_ms: int = 30000) -> str:
        await validate_public_url(url)
        page = self._require_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if len(self._context.pages) > self.max_tabs:
            for extra in self._context.pages[self.max_tabs:]:
                await extra.close()
        return page.url

    async def read(self) -> dict[str, str]:
        page = self._require_page()
        return {"url": page.url, "title": await page.title(),
                "text": (await page.locator("body").inner_text(timeout=10000))[:12000]}

    async def click_link(self, selector: str) -> str:
        page = self._require_page()
        locator = page.locator(selector).first
        if await locator.evaluate("el => el.tagName.toLowerCase()") != "a":
            raise ValueError("Navigation click must target a link")
        if await locator.get_attribute("onclick"):
            raise ValueError("Links with script handlers require a separate high-risk flow")
        href = await locator.get_attribute("href")
        if not href:
            raise ValueError("Link has no destination")
        destination = urljoin(page.url, href)
        if PAYMENT.search(destination):
            raise PermissionError("Payment navigation is blocked")
        await validate_public_url(destination)
        await locator.click(timeout=10000)
        return page.url

    async def fill(self, selector: str, value: str) -> None:
        page = self._require_page()
        await page.locator(selector).first.fill(value, timeout=10000)

    async def submit(self, selector: str) -> None:
        page = self._require_page()
        locator = page.locator(selector).first
        form_action = await locator.evaluate(
            "el => (el.form && el.form.action) || (el.closest('form') && el.closest('form').action) || ''")
        if PAYMENT.search(form_action):
            raise PermissionError("Payment submission is blocked")
        await locator.click(timeout=10000)

    async def upload(self, selector: str, path: Path, *, workspace: Path) -> None:
        source = path.resolve()
        if not source.is_file() or not source.is_relative_to(workspace.resolve()):
            raise ValueError("Upload file must be inside the approved workspace")
        if source.stat().st_size > self.max_download_bytes:
            raise ValueError("Upload file is too large")
        await self._require_page().locator(selector).first.set_input_files(str(source))

    async def download(self, selector: str, destination: Path, *, workspace: Path) -> Path:
        if not self.allow_downloads:
            raise PermissionError("Downloads were not enabled for this plan")
        target = destination.resolve()
        if not target.is_relative_to(workspace.resolve()):
            raise ValueError("Download destination is outside the approved workspace")
        if target.exists():
            raise FileExistsError("Download destination already exists")
        page = self._require_page()
        async with page.expect_download(timeout=30000) as info:
            await page.locator(selector).first.click(timeout=10000)
        download = await info.value
        temp = await download.path()
        if temp is None or Path(temp).stat().st_size > self.max_download_bytes:
            await download.delete()
            raise ValueError("Download is unavailable or too large")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            await download.delete()
            raise FileExistsError("Download destination appeared during download")
        await download.save_as(str(target))
        os.chmod(target, 0o600)
        await download.delete()
        return target

    async def screenshot(self, path: Path) -> Path:
        destination = path.resolve()
        if not destination.is_relative_to(self.screenshot_root.resolve()):
            raise ValueError("Screenshot path is outside the agent directory")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        await self._require_page().screenshot(path=str(destination), full_page=False)
        os.chmod(destination, 0o600)
        return destination

    def screenshot_path(self, step_id: str) -> Path:
        stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        return self.screenshot_root / f"{stamp}_{step_id}.png"

    async def close(self) -> bool:
        errors: list[Exception] = []
        if self._context is not None:
            try:
                for page in list(self._context.pages):
                    await page.close()
                await self._context.close()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._context = None
                self._page = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._playwright = None
        return not errors and self._context is None and self._browser is None and self._playwright is None
