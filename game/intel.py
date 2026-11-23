"""Telem intel search for the blue Hunter's INTEL move (design.md section 7).

Off unless TELEM_ENABLED=1 (and TELEM_API_KEY is set). One search per INTEL move, bounded to the
current world week (window_start .. asof_date), for one board CVE id. The result is reduced to
domain + date for at most three reports. Headlines are never kept: an advisory title carries
affected versions, weakness names and description-style text, and no CVE description text may
reach the game. No page bodies, no summaries, no URLs. A result whose headline looks like exploit
material, or that comes from an exploit archive or a code host, is dropped. Any failure or a 4 s
timeout means "no intel"; the caller never waits on this inside the tick.

Spend is capped twice: TELEM_MAX_PER_MATCH (the Hunter's budget per match, default 3, applied in
the rules) and TELEM_MAX_PER_PROCESS (searches for the whole server process, default 10, applied
here). Once the process budget is spent, INTEL answers "budget spent" without calling Telem.

Telem is called through the official telem-sdk (AsyncTelem.search); fetch() is never used, so
no page content is ever downloaded.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import config

log = logging.getLogger("game.intel")

TIMEOUT_S = 4.0
MAX_ITEMS = 3
DEFAULT_MAX_PER_PROCESS = 10
# Headlines that point at exploit material rather than news: the result is dropped.
_BLOCK = re.compile(r"\b(poc|proof[- ]of[- ]concept|exploit[- ]?(code|db|kit)|payload|shellcode|metasploit|"
                    r"nuclei|github|gist|writeup|write-up|walkthrough|how to exploit)\b", re.I)
# Exploit archives, exploit-intelligence sites and code hosts: a hit there is exploit material.
_BLOCK_DOMAIN = re.compile(r"(^|\.)(exploit-db\.com|packetstormsecurity\.(com|org|net)|0day\.today|"
                           r"github\.com|githubusercontent\.com|gitlab\.com|sploitus\.com|vulners\.com|"
                           r"rapid7\.com|vulncheck\.com)$|(^|[.-])(exploits?|0days?|pocs?)[.-]", re.I)
_CVE = re.compile(r"^CVE-\d{4}-\d{4,}$")


@dataclass
class IntelResult:
    ok: bool
    cve_id: str
    items: list[dict] = field(default_factory=list)   # [{"domain","date"}], never a headline
    session_id: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    usage: dict | None = None                         # whatever cost/usage the API reported

    def text(self, actor: str = "Hunter") -> str:
        """The one line that goes into the ledger row, the HUD and BAND: a count, domains and dates."""
        if not self.ok:
            return f"{actor} found no intel on {self.cve_id} ({self.error or 'no result'})."
        if not self.items:
            return f"{actor} found no public reporting on {self.cve_id} this world week."
        n = len(self.items)
        parts = [i["domain"] + (f" {i['date']}" if i.get("date") else "") for i in self.items]
        return (f"{actor} found {n} public report{'s' if n != 1 else ''} on {self.cve_id} this world week "
                f"({', '.join(parts)}).")


def enabled() -> bool:
    return config.env_flag("TELEM_ENABLED") and bool(config.env("TELEM_API_KEY"))


def _domain(url: str) -> str | None:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    if not re.fullmatch(r"[a-z0-9.-]{1,80}", host or "-"):
        return None
    return host or None


def _date(value: str | None) -> str | None:
    if not value:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
    return m.group(1) if m else None


def _get(r, key):
    return r.get(key) if isinstance(r, dict) else getattr(r, key, None)


def reduce_results(results, limit: int = MAX_ITEMS) -> list[dict]:
    """SearchResult-like objects (title, url, publish_date) -> [{"domain","date"}]. The title is
    read only to drop exploit material; it is never returned."""
    out, seen = [], set()
    for r in results or []:
        title = str(_get(r, "title") or "")
        domain = _domain(str(_get(r, "url") or ""))
        if not domain or _BLOCK.search(title) or _BLOCK_DOMAIN.search(domain):
            continue
        date = _date(_get(r, "publish_date"))
        if (domain, date) in seen:
            continue
        seen.add((domain, date))
        out.append({"domain": domain, "date": date})
        if len(out) >= limit:
            break
    return out


def _usage(resp) -> dict | None:
    """Collect any cost/usage numbers the router reported (shape not documented)."""
    found: dict = {}
    for run in getattr(resp, "by_provider", []) or []:
        if getattr(run, "usage", None):
            found[getattr(run, "provider", "?")] = run.usage
    raw = getattr(resp, "raw", {}) or {}
    for k in ("usage", "cost", "cost_usd", "billing", "credits"):
        if k in raw:
            found[k] = raw[k]
    return found or None


def week_bounds(world: dict) -> tuple[str | None, str | None]:
    """(published_after, published_before) = (window_start, asof_date). The SDK says a date with
    no time "includes that whole day", so the Sunday of the world week is included."""
    start = world.get("window_start")
    end = world.get("asof_date")
    if not start and end:
        try:
            start = (dt.date.fromisoformat(end) - dt.timedelta(days=6)).isoformat()
        except ValueError:
            start = None
    return start, end


class IntelClient:
    def __init__(self, client=None, timeout: float = TIMEOUT_S, providers: list[str] | None = None,
                 max_calls: int | None = None):
        self.timeout = timeout
        self._client = client
        # One provider keeps each INTEL move to one charge ("A search that runs on three
        # providers is three charges"); brave is $0.005 a search. TELEM_PROVIDERS overrides.
        env_p = config.env("TELEM_PROVIDERS") or "brave"
        self.providers = providers if providers is not None else [p.strip() for p in env_p.split(",") if p.strip()]
        self.calls = 0
        self.max_calls = max(0, int(max_calls if max_calls is not None
                                    else config.env_float("TELEM_MAX_PER_PROCESS", DEFAULT_MAX_PER_PROCESS)))

    @property
    def budget_left(self) -> int:
        return max(0, self.max_calls - self.calls)

    def _get(self):
        if self._client is None:
            from telem import AsyncTelem  # telem-sdk; imported lazily so the game runs without it
            self._client = AsyncTelem(api_key=config.env("TELEM_API_KEY"), timeout=self.timeout)
        return self._client

    async def search(self, cve_id: str, world: dict) -> IntelResult:
        cve_id = str(cve_id or "").strip()
        if not _CVE.match(cve_id):
            return IntelResult(False, cve_id or "?", error="not a CVE id")
        if self.calls >= self.max_calls:
            return IntelResult(False, cve_id, error="Telem budget for this server process is spent")
        start, end = week_bounds(world)
        t0 = time.monotonic()
        try:
            client = self._get()
            self.calls += 1
            kw = dict(num_results=MAX_ITEMS + 2, published_after=start, published_before=end,
                      context="Patch Tuesday game: blue Hunter checks public reporting for one CVE in this world week.",
                      metadata={"app": "patch-tuesday", "world_asof": end})
            if self.providers:
                kw["providers_include"] = self.providers
            resp = await asyncio.wait_for(client.search(cve_id, **kw), self.timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return IntelResult(False, cve_id, error="timeout", latency_ms=int((time.monotonic() - t0) * 1000))
        except Exception as exc:  # SDK errors, network, bad key: "no intel"
            log.info("Telem search failed: %s", type(exc).__name__)
            return IntelResult(False, cve_id, error=type(exc).__name__,
                               latency_ms=int((time.monotonic() - t0) * 1000))
        items = reduce_results(getattr(resp, "results", []))
        raw = getattr(resp, "raw", {}) or {}
        log.info("Telem search %s: status %s, %d result(s), session %s, response keys %s",
                 cve_id, getattr(resp, "status", "?"), len(getattr(resp, "results", []) or []),
                 getattr(resp, "session_id", None), sorted(raw)[:20])
        return IntelResult(True, cve_id, items=items, session_id=getattr(resp, "session_id", None),
                           latency_ms=int((time.monotonic() - t0) * 1000), usage=_usage(resp))

    async def aclose(self) -> None:
        c, self._client = self._client, None
        if c is not None and hasattr(c, "aclose"):
            try:
                await c.aclose()
            except Exception:
                pass


async def search_intel(query: str, start: str | None, end: str | None) -> list[dict]:
    """Back-compatible helper: [] when off or on any failure."""
    if not enabled() or not query:
        return []
    c = IntelClient()
    try:
        res = await c.search(query, {"window_start": start, "asof_date": end})
        return res.items if res.ok else []
    finally:
        await c.aclose()
