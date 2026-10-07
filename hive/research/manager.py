"""Research helpers. External text is evidence, never executable instructions."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit
from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel, Field
from hive.browser.safety import url_is_public_https


class ClarificationQuestion(BaseModel):
    question: str
    reason: str
    options: list[str] = Field(default_factory=list)


class ResearchRequest(BaseModel):
    question: str
    urls: list[str] = Field(default_factory=list)
    man_topics: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    search_query: str | None = None


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


class BraveSearchProvider(WebSearchProvider):
    """Optional search provider; its snippets are untrusted evidence."""

    def __init__(self, api_key: str, *, timeout: float = 30,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise ValueError("Brave API key is required")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport
        self._failures = 0
        self._disabled_until = 0.0

    async def search(self, query: str) -> list[Citation]:
        if not query.strip() or len(query) > 600:
            raise ValueError("Search query must contain 1 to 600 characters")
        if time.monotonic() < self._disabled_until:
            raise RuntimeError("Search provider is temporarily unavailable")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": 5},
                    headers={"X-Subscription-Token": self.api_key,
                             "Accept": "application/json"},
                )
                response.raise_for_status()
                results = (response.json().get("web") or {}).get("results", [])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self._failures += 1
            if self._failures >= 3:
                self._disabled_until = time.monotonic() + 60
            raise RuntimeError("Search provider request failed") from exc
        self._failures = 0
        return [Citation(title=str(item.get("title", ""))[:200],
                         source=str(item["url"]),
                         excerpt=str(item.get("description") or "")[:2000])
                for item in results[:5] if isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and url_is_public_https(item["url"])]


class _PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        clean = " ".join(data.split())
        if clean:
            (self.title_parts if self.in_title else self.parts).append(clean)


def page_text(html: str) -> tuple[str, str]:
    parser = _PageText()
    parser.feed(html)
    return " ".join(parser.title_parts)[:200], " ".join(parser.parts)[:12000]


async def _read_command(argv: list[str], *, timeout: float = 15) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
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
        title, body = page_text(response.text)
        return Citation(title=title or url, source=url, excerpt=body)


async def research(request: ResearchRequest,
                   *, search_provider: WebSearchProvider | None = None) -> ResearchSummary:
    if not request.question.strip():
        raise ValueError("Research question cannot be empty")
    if len(request.urls) + len(request.man_topics) + len(request.packages) > 8:
        raise ValueError("Research is limited to eight explicit sources")
    citations: list[Citation] = []
    unanswered: list[str] = []
    if request.search_query:
        if search_provider is None:
            unanswered.append("Web search needs HIVE_BRAVE_API_KEY")
        else:
            try:
                citations.extend(await search_provider.search(request.search_query))
            except (RuntimeError, ValueError) as exc:
                unanswered.append(f"Web search: {exc}")
    for topic in request.man_topics:
        try:
            citations.append(await man_page(topic))
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            unanswered.append(f"man {topic}: {exc}")
    for package in request.packages:
        try:
            citations.append(await package_metadata(package))
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            unanswered.append(f"rpm {package}: {exc}")
    for url in request.urls:
        try:
            citations.append(await read_page(url))
        except (OSError, httpx.HTTPError, RuntimeError, TimeoutError, ValueError) as exc:
            unanswered.append(f"{url}: {exc}")
    summaries = []
    for citation in citations:
        sentences = re.split(r"(?<=[.!?])\s+", citation.excerpt)
        summaries.append(f"{citation.title}: {' '.join(sentences[:2])[:450]}")
    return ResearchSummary(
        summary="\n".join(summaries) if summaries else "No sources were available.",
        citations=citations, unanswered_questions=unanswered,
    )


def evidence_context(citations: list[Citation]) -> str:
    return "\n".join(
        f"<untrusted_source source={item.source!r}>\n{item.excerpt}\n</untrusted_source>"
        for item in citations
    )
