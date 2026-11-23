"""BAND event mirror (design.md section 7, docs.band.ai Agent API).

The game server posts ledger highlights (breach, patch completed, restore, gate result, Patch
Tuesday drop, red campaign, match result) into one BAND room as *events* from a single remote
agent. Events need no mention and are delivered to people only:

    POST https://app.band.ai/api/v1/agent/chats/{chat_id}/events
    {"event": {"content": "...", "message_type": "thought"}}

Rules:
  * Off unless BAND_ENABLED=1 and BAND_API_KEY holds an *agent* key (band_a_...). A user key
    (band_u_...) is refused by the Agent API, so the mirror switches itself off at start.
  * The room is BAND_CHAT_ID, else the agent's chat whose title contains "Patch Tuesday". It never
    posts into any other room and never creates rooms, agents or accounts.
  * submit() is synchronous and never blocks: it drops the event when the queue is full.
  * One worker task posts at most one event every BAND_MIN_INTERVAL seconds (default 3).
  * Budget: at most BAND_MAX_PER_HOUR posts in any rolling hour (default 120; events over it are
    dropped) and BAND_MAX_EVENTS posts for the whole process (default 300; then the mirror
    switches itself off).
  * HTTP 401, 403 or 429 switches the mirror off for the rest of the process.
  * Highlights go out as ordinary chat *messages* that mention the room's human (BAND_POST_AS=message,
    the default), because Band Desktop's Chat view does not show agent "thought" events. Waiting
    highlights are grouped into one digest message per interval (default 8 s). If the room has no
    human to mention, or BAND_POST_AS=event, each highlight is posted as an event as before.

    POST https://app.band.ai/api/v1/agent/chats/{chat_id}/messages
    {"message": {"content": "@handle ...", "mentions": [{"id": "...", "handle": "...", "name": "..."}]}}
The key is read from the environment and never logged.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque

from . import config

log = logging.getLogger("game.band")

BASE_URL = "https://app.band.ai/api/v1"
HIGHLIGHT_KINDS = {"breach", "patched", "restored", "drop", "campaign", "gate", "intel"}
MESSAGE_TYPES = {"breach": "attention", "campaign": "attention", "drop": "task"}
ROOM_TITLE = "patch tuesday"
CONTENT_MAX = 600
DIGEST_MAX_ITEMS = 8
DIGEST_MAX_CHARS = 1800
STALE_S = 90.0
HOUR_S = 3600.0


def enabled() -> bool:
    return config.env_flag("BAND_ENABLED")


def is_highlight(kind: str, text: str) -> bool:
    if kind in HIGHLIGHT_KINDS:
        return True
    return kind == "info" and (text.startswith("Match over") or text.startswith("Reveal:"))


class BandMirror:
    def __init__(self, api_key: str | None = None, chat_id: str | None = None, base_url: str | None = None,
                 interval: float | None = None, max_queue: int = 10, transport=None, flag: bool | None = None,
                 max_events: int | None = None, max_per_hour: int | None = None, post_as: str | None = None):
        self.flag = enabled() if flag is None else flag
        self.api_key = api_key if api_key is not None else config.env("BAND_API_KEY")
        self.chat_id = chat_id if chat_id is not None else (config.env("BAND_CHAT_ID") or None)
        self.base_url = (base_url or config.env("BAND_BASE_URL") or BASE_URL).rstrip("/")
        self.post_as = (post_as or config.env("BAND_POST_AS") or "message").strip().lower()
        if self.post_as not in ("message", "event"):
            self.post_as = "message"
        default_gap = 8.0 if self.post_as == "message" else 3.0
        self.interval = interval if interval is not None else config.env_float("BAND_MIN_INTERVAL", default_gap)
        self._mention: dict | None = None      # the human to mention in message mode
        self._mention_tried = False
        self.max_queue = max_queue
        self.max_events = int(max_events if max_events is not None else config.env_float("BAND_MAX_EVENTS", 300))
        self.max_per_hour = int(max_per_hour if max_per_hour is not None
                                else config.env_float("BAND_MAX_PER_HOUR", 120))
        self._posts = 0                     # POST attempts in this process
        self._recent: deque[float] = deque()   # monotonic times of the posts in the last hour
        self._transport = transport
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._client = None
        self._last_post = 0.0
        self.disabled_reason: str | None = None
        self.stats = Counter()
        if self.flag and not self.api_key:
            self.disabled_reason = "BAND_API_KEY is empty"
        elif self.flag and self.api_key.startswith("band_u_"):
            self.disabled_reason = ("BAND_API_KEY is a user key (band_u_); the Agent API needs a remote agent "
                                    "key (band_a_)")

    # ------------------------------------------------------------------ state
    @property
    def active(self) -> bool:
        return self.flag and self.disabled_reason is None

    def status(self) -> dict:
        return {"enabled_flag": self.flag, "active": self.active, "disabled_reason": self.disabled_reason,
                "chat_resolved": self.chat_id is not None, "queued": self._queue.qsize() if self._queue else 0,
                **{k: v for k, v in self.stats.items()}}

    def _switch_off(self, reason: str) -> None:
        if self.disabled_reason is None:
            self.disabled_reason = reason
            log.warning("BAND mirror switched off: %s", reason)
        if self._queue is not None:
            while not self._queue.empty():
                self._queue.get_nowait()
                self.stats["dropped"] += 1

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if not self.flag:
            return
        if self.disabled_reason:
            log.warning("BAND mirror off: %s", self.disabled_reason)
            return
        self._queue = asyncio.Queue(maxsize=self.max_queue)
        self._task = asyncio.get_running_loop().create_task(self._worker())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------------ producer side
    def submit(self, kind: str, text: str) -> bool:
        """Queue one event. Never blocks, never raises. False when off, filtered or dropped."""
        if not self.active or self._queue is None or not is_highlight(kind, text or ""):
            return False
        content = f"[Patch Tuesday] {kind.upper()}: {text}"[:CONTENT_MAX]
        try:
            self._queue.put_nowait((time.monotonic(), kind, content))
        except asyncio.QueueFull:
            self.stats["dropped"] += 1
            return False
        self.stats["queued"] += 1
        return True

    # ------------------------------------------------------------------ worker
    def _http(self):
        if self._client is None:
            import httpx
            kw = {"timeout": 5.0, "headers": {"X-API-Key": self.api_key, "Accept": "application/json"}}
            if self._transport is not None:
                kw["transport"] = self._transport
            self._client = httpx.AsyncClient(**kw)
        return self._client

    def _check(self, resp, what: str) -> bool:
        code = resp.status_code
        if code in (401, 403, 429):
            err = ""
            try:
                err = (resp.json().get("error") or {}).get("code") or ""
            except Exception:
                pass
            self._switch_off(f"{what} answered HTTP {code}{' ' + err if err else ''}")
            return False
        if code >= 400:
            self.stats["failed"] += 1
            log.info("BAND %s answered HTTP %s", what, code)
            return False
        return True

    async def _resolve_chat(self) -> bool:
        if self.chat_id:
            return True
        resp = await self._http().get(f"{self.base_url}/agent/chats")
        if not self._check(resp, "GET agent/chats"):
            if self.disabled_reason is None:
                self._switch_off("could not list the agent's chats")
            return False
        try:
            body = resp.json()
        except ValueError:
            self._switch_off("GET agent/chats returned no JSON")
            return False
        chats = body.get("data", body) if isinstance(body, dict) else body
        if isinstance(chats, dict):
            chats = chats.get("chats") or chats.get("items") or []
        for c in chats if isinstance(chats, list) else []:
            if isinstance(c, dict) and ROOM_TITLE in str(c.get("title") or "").lower() and c.get("id"):
                self.chat_id = str(c["id"])
                log.info("BAND mirror posts to the room titled %r", c.get("title"))
                return True
        self._switch_off(f"the agent is in {len(chats) if isinstance(chats, list) else 0} chat(s) and none is "
                         f"titled 'Patch Tuesday'; set BAND_CHAT_ID or create that room")
        return False

    async def _resolve_mention(self) -> dict | None:
        """The room's human participant, looked up once. None means: post events instead."""
        if self._mention_tried:
            return self._mention
        self._mention_tried = True
        try:
            resp = await self._http().get(f"{self.base_url}/agent/chats/{self.chat_id}/participants")
            if resp.status_code != 200:
                return None
            body = resp.json()
            people = body.get("data", body) if isinstance(body, dict) else body
            for p in people if isinstance(people, list) else []:
                if isinstance(p, dict) and str(p.get("type") or "").lower() == "user" and p.get("id") and p.get("handle"):
                    self._mention = {"id": str(p["id"]), "handle": str(p["handle"]), "name": str(p.get("name") or p["handle"])}
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("BAND participants lookup failed: %s", type(exc).__name__)
        if self._mention is None:
            log.info("BAND room has no human to mention; posting events instead of messages")
        return self._mention

    async def _post_digest(self, lines: list[str], who: dict) -> bool:
        lines = [ln.removeprefix("[Patch Tuesday] ") for ln in lines]   # the room is already named
        text = f"@{who['handle']} " + "\n".join(lines)
        body = {"message": {"content": text[:DIGEST_MAX_CHARS], "mentions": [who]}}
        resp = await self._http().post(f"{self.base_url}/agent/chats/{self.chat_id}/messages", json=body)
        if self._check(resp, "POST messages"):
            self.stats["sent"] += len(lines)
            self.stats["messages"] += 1
            return True
        return False

    async def _post(self, kind: str, content: str) -> bool:
        body = {"event": {"content": content, "message_type": MESSAGE_TYPES.get(kind, "thought")}}
        resp = await self._http().post(f"{self.base_url}/agent/chats/{self.chat_id}/events", json=body)
        if self._check(resp, "POST events"):
            self.stats["sent"] += 1
            return True
        return False

    def _budget_ok(self) -> bool:
        """False (and the event dropped) when a post would go over the hourly or process budget."""
        if self._posts >= self.max_events:
            self._switch_off(f"BAND_MAX_EVENTS ({self.max_events}) posts reached for this process")
            return False
        now = time.monotonic()
        while self._recent and now - self._recent[0] > HOUR_S:
            self._recent.popleft()
        if len(self._recent) >= self.max_per_hour:
            self.stats["dropped_hourly_cap"] += 1
            return False
        return True

    async def _worker(self) -> None:
        fails = 0
        while self.active:
            queued_at, kind, content = await self._queue.get()
            if not self.active:
                break
            if time.monotonic() - queued_at > STALE_S:
                self.stats["dropped"] += 1
                continue
            if not self._budget_ok():
                continue
            wait = self.interval - (time.monotonic() - self._last_post)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_post = time.monotonic()
            self._posts += 1
            self._recent.append(self._last_post)
            try:
                if await self._resolve_chat():
                    who = await self._resolve_mention() if self.post_as == "message" else None
                    if who:
                        lines = [content]
                        while len(lines) < DIGEST_MAX_ITEMS and not self._queue.empty():
                            q_at, _k, more = self._queue.get_nowait()
                            if time.monotonic() - q_at <= STALE_S:
                                lines.append(more)
                        ok = await self._post_digest(lines, who)
                    else:
                        ok = await self._post(kind, content)
                    fails = 0 if ok else fails + 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:          # network trouble: count it, give up after 5 in a row
                fails += 1
                self.stats["failed"] += 1
                log.info("BAND post failed: %s", type(exc).__name__)
            if fails >= 5:
                self._switch_off("5 failed posts in a row")


# Module-level helper kept for callers that only want a fire-and-forget call.
_default: BandMirror | None = None


def set_default(mirror: BandMirror | None) -> None:
    global _default
    _default = mirror


async def post_event(kind: str, text: str) -> bool:
    """Queue one event on the server's mirror. True only if it was queued."""
    return _default.submit(kind, text) if _default is not None else False
