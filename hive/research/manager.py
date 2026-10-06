"""Research helpers. External text is evidence, never executable instructions."""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit
from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel, Field


class ClarificationQuestion(BaseModel):
    question: str
    reason: str
    options: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    title: str
    source: str
    excerpt: str
    untrusted: bool = True


class ResearchSummary(BaseModel):
    summary: str
    citations: list[Citation]
    unanswered_questions: list[str] = Field(default_factory=list)


class WebSearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str) -> list[Citation]: ...


async def _read_command(argv: list[str], *, timeout: float = 15) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[:500])
    return stdout.decode(errors="replace")


async def man_page(topic: str) -> Citation:
    if not topic or not all(c.isalnum() or c in "._+-" for c in topic):
        raise ValueError("Invalid manual topic")
    text = await _read_command(["man", "--pager=cat", topic])
    return Citation(title=f"man {topic}", source=f"man:{topic}", excerpt=text[:12000])


async def package_metadata(package: str) -> Citation:
    if not package or not all(c.isalnum() or c in "._+-" for c in package):
        raise ValueError("Invalid package name")
    text = await _read_command(["rpm", "-qi", package])
    return Citation(title=package, source=f"rpm:{package}", excerpt=text[:12000])


async def read_page(url: str, *, timeout: float = 30) -> Citation:
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS research pages are supported")
    host = urlsplit(url).hostname
    if not host or host.lower() == "localhost" or host.endswith(".localhost"):
        raise ValueError("Local research addresses are blocked")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private research addresses are blocked")
    addresses = await asyncio.get_running_loop().getaddrinfo(host, 443)
    if any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError("Research address resolves to a private network")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("Research page is too large")
        return Citation(title=url, source=url, excerpt=response.text[:12000])


def evidence_context(citations: list[Citation]) -> str:
    return "\n".join(
        f"<untrusted_source source={item.source!r}>\n{item.excerpt}\n</untrusted_source>"
        for item in citations
    )
