"""BAND transport for "Call for backup" (docs/CONTRACTS.md, "Call for backup").

The game decides everything in game/backup.py (BackupFlow, pure and offline). This module only
carries its words to one BAND room and brings the humans' replies back:

  * Each `Say` the flow emits becomes exactly one chat message, posted with the speaker's own
    agent key:  POST /agent/chats/{chat}/messages  {"message": {"content", "mentions": [...]}}
    Mentions are objects {"id","handle","name"} taken from the room's participant list; never
    the speaker itself, never twice the same id, at most three humans.
  * A "join" Say first adds the specialist's agent to the room (Coach owns the room):
    POST /agent/chats/{chat}/participants  {"participant": {"participant_id", "role": "member"}}
    At the next match start or reset Coach removes it again
    (DELETE /agent/chats/{chat}/participants/{id}), so the next match shows it joining.
  * One poller, with Coach's key only, reads Coach's queue
    (GET /agent/chats/{chat}/messages?limit=100) while a match runs. Every returned row is
    acknowledged (/processing, then /processed; /failed is never called). Only a row from a
    human (sender_type "User", or an id in BAND_TRUSTED_SENDER_IDS, a test-only allow-list) that
    mentions Coach, is younger than 60 s and was written after this match started is handed to
    the tick loop, as plain length-capped text with every @[[uuid]] token removed.

Rules it keeps (BAND_SPEC "Polite client"):
  * at least BAND_TEAM_MIN_INTERVAL seconds (default 3) between two posts in the room;
  * no poll faster than every 2.5 s, and none while no match runs and no question is open;
  * BAND_TEAM_MAX_PER_HOUR posts per rolling hour (default 200; more are dropped) and
    BAND_TEAM_MAX_POSTS per process (default 1000; then the feature switches itself off);
  * HTTP 429: wait Retry-After, else 1, 2, 4, 8, 16 s; HTTP 503: retry with a short back-off;
  * HTTP 403 limit_reached: off for the rest of the process, naming the limit;
  * HTTP 403 "execution is stopped": that one agent stops posting (Coach stopped = feature off);
  * nothing here ever raises into the tick loop: submit(), drain_inbox(), set_listening() and
    match_started() are synchronous, never block and never touch the network.

Keys are read from the environment (BAND_KEY_<NAME>) and are never logged or put in a URL.
There are no game decisions in this file.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import email.utils
import logging
import re
import time
from collections import Counter, OrderedDict, deque

from . import config

log = logging.getLogger("game.bandteam")

BASE_URL = "https://app.band.ai/api/v1"
ROOM_TITLE = "Patch Tuesday - Blue team"
COACH = "coach"
BLUE = ("triage", "patcher", "hunter")
SPECIALISTS = ("responder", "hardener", "sentinel")
AGENTS = (COACH,) + BLUE + SPECIALISTS

MIN_POLL_S = 2.5            # the floor for BAND_TEAM_POLL_SECONDS
STALE_S = 60.0              # a human message older than this is acknowledged and dropped
SAY_STALE_S = 90.0          # a line that waited this long in the send queue is no longer news
PARTICIPANTS_TTL_S = 30.0
CONTENT_MAX = 1800
HUMAN_TEXT_MAX = 200
HUMAN_NAME_MAX = 60
MAX_HUMAN_MENTIONS = 3
INBOX_MAX = 20
SEEN_MAX = 2000
HOUR_S = 3600.0
BACKOFF_429 = (1.0, 2.0, 4.0, 8.0, 16.0)
BACKOFF_503 = (1.0, 2.0, 4.0)
RETRY_AFTER_MAX = 120.0
MAX_FAILS_IN_A_ROW = 5

TOKEN_RE = re.compile(r"@\[\[[0-9a-fA-F-]{36}\]\]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


# ------------------------------------------------------------------ environment
def key_var(agent: str) -> str:
    return f"BAND_KEY_{agent.upper()}"


def id_var(agent: str) -> str:
    return f"BAND_ID_{agent.upper()}"


def env_keys() -> dict[str, str]:
    return {a: config.env(key_var(a)) for a in AGENTS}


def env_ids() -> dict[str, str]:
    return {a: config.env(id_var(a)) for a in AGENTS}


def missing_env() -> list[str]:
    """Names (never values) of the variables the feature still needs."""
    names = [key_var(a) for a in AGENTS if not config.env(key_var(a))]
    if not config.env("BAND_BLUE_CHAT_ID"):
        names.append("BAND_BLUE_CHAT_ID")
    return names


def off_reason() -> str | None:
    """Why "Call for backup" is off, or None when it may run."""
    if not config.env_flag("BAND_ENABLED"):
        return "BAND_ENABLED is not 1"
    if config.env("BAND_BACKUP") in ("0", "false", "FALSE", "no", "off"):
        return "BAND_BACKUP=0"
    miss = missing_env()
    if miss:
        return "not set: " + ", ".join(miss) + " (run scripts/band_setup.py)"
    return None


def enabled() -> bool:
    return off_reason() is None


def clean_text(content, limit: int = HUMAN_TEXT_MAX) -> str:
    """A human's message as the game may read it: mention tokens and control characters removed,
    white space collapsed, length capped. It is still untrusted: the flow matches fixed words only."""
    text = TOKEN_RE.sub(" ", str(content or ""))
    text = _CONTROL_RE.sub(" ", text)
    return " ".join(text.split())[:limit]


def _parse_time(value) -> float | None:
    """An ISO-8601 time as epoch seconds, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def _error_of(resp) -> tuple[str, str, dict]:
    """(code, message, details) of a BAND error body; empty values when there is none."""
    try:
        err = resp.json().get("error") or {}
    except Exception:
        return "", "", {}
    if not isinstance(err, dict):
        return "", "", {}
    details = err.get("details")
    return str(err.get("code") or ""), str(err.get("message") or ""), details if isinstance(details, dict) else {}


class BandTeam:
    """See the module text. Every argument is for tests; the server passes none."""

    def __init__(self, keys: dict[str, str] | None = None, ids: dict[str, str] | None = None,
                 chat_id: str | None = None, base_url: str | None = None, transport=None,
                 flag: bool | None = None, clock=time.monotonic, wall=time.time, sleep=asyncio.sleep,
                 gap: float | None = None, poll_interval: float | None = None,
                 max_per_hour: int | None = None, max_posts: int | None = None,
                 trusted: set[str] | None = None, stale_s: float = STALE_S, max_queue: int = 40):
        self.keys = {a: (v or "").strip() for a, v in (keys if keys is not None else env_keys()).items()}
        self.ids = {a: (v or "").strip() for a, v in (ids if ids is not None else env_ids()).items() if v}
        self.chat_id = (chat_id if chat_id is not None else config.env("BAND_BLUE_CHAT_ID")) or None
        self.base_url = (base_url or config.env("BAND_BASE_URL") or BASE_URL).rstrip("/")
        self.gap = float(gap if gap is not None else max(0.0, config.env_float("BAND_TEAM_MIN_INTERVAL", 3.0)))
        self.poll_interval = float(poll_interval if poll_interval is not None
                                   else max(MIN_POLL_S, config.env_float("BAND_TEAM_POLL_SECONDS", MIN_POLL_S)))
        self.max_per_hour = int(max_per_hour if max_per_hour is not None
                                else config.env_float("BAND_TEAM_MAX_PER_HOUR", 200))
        self.max_posts = int(max_posts if max_posts is not None
                             else config.env_float("BAND_TEAM_MAX_POSTS", 1000))
        if trusted is None:
            trusted = {s.strip() for s in config.env("BAND_TRUSTED_SENDER_IDS").split(",") if s.strip()}
        self.trusted = set(trusted)
        self.stale_s = float(stale_s)
        self.max_queue = max_queue
        self._clock, self._wall, self._sleep = clock, wall, sleep
        self._transport = transport
        self._client = None
        self._out: asyncio.Queue | None = None
        self._tasks: list[asyncio.Task] = []
        self._inbox: deque[tuple[str, str]] = deque(maxlen=INBOX_MAX)   # thread-safe append/popleft
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._participants: list[dict] | None = None      # the room, as last read
        self._participants_at = -1e18
        self._stopped: dict[str, str] = {}                # agent -> why it no longer posts
        self._posts = 0
        self._recent: deque[float] = deque()
        self._last_post = -1e18
        self._pause_until = -1e18
        self._n429 = 0
        self._fails = 0
        self._skew = 0.0                                  # BAND's clock minus ours, from Date headers
        self._not_before = -1e18                          # wall time the current match started
        self._ids_tried = False
        self.listening = False
        self.stats: Counter = Counter()
        if flag is None:
            self.flag = config.env_flag("BAND_ENABLED")
            self.disabled_reason: str | None = off_reason() if self.flag else None
        else:
            self.flag = bool(flag)
            self.disabled_reason = None
        if self.flag and self.disabled_reason is None:
            miss = [key_var(a) for a in AGENTS if not self.keys.get(a)]
            if miss:
                self.disabled_reason = "not set: " + ", ".join(miss)
            elif not self.chat_id:
                self.disabled_reason = "not set: BAND_BLUE_CHAT_ID"
            elif any(k.startswith("band_u_") for k in self.keys.values()):
                self.disabled_reason = "a BAND_KEY_* variable holds a user key (band_u_); agent keys are needed"

    # ------------------------------------------------------------------ state
    @property
    def active(self) -> bool:
        return self.flag and self.disabled_reason is None

    def status(self) -> dict:
        """For /api/status. Counters and names only: no key, no id, no message text."""
        in_room = None
        if self._participants is not None:
            present = {p["id"] for p in self._participants}
            in_room = sorted(a for a in SPECIALISTS if self.ids.get(a) in present)
        return {"feature": "Call for backup", "enabled_flag": self.flag, "active": self.active,
                "disabled_reason": self.disabled_reason, "room_resolved": self._participants is not None,
                "listening": self.listening, "queued": self._out.qsize() if self._out else 0,
                "posts": self.stats["posts"], "polls": self.stats["polls"], "errors": self.stats["errors"],
                "accepted": self.stats["accepted"], "stopped_agents": sorted(self._stopped),
                "specialists_in_room": in_room,
                "counters": {k: v for k, v in sorted(self.stats.items())}}

    def _switch_off(self, reason: str) -> None:
        if self.disabled_reason is None:
            self.disabled_reason = reason
            log.warning('"Call for backup" BAND side switched off: %s', reason)
        if self._out is not None:
            while not self._out.empty():
                self._out.get_nowait()
                self.stats["dropped"] += 1

    def _stop_agent(self, agent: str, why: str) -> None:
        if agent not in self._stopped:
            self._stopped[agent] = why
            log.warning("BAND agent %s no longer posts: %s", agent, why)
        if agent == COACH:
            self._switch_off(f"Coach cannot work: {why}")

    # ------------------------------------------------------------------ lifecycle
    async def start(self, run_tasks: bool = True) -> None:
        """Open the send queue and start the sender and the poller. With run_tasks=False nothing
        runs in the background: the caller drives it with flush() and poll_once() (tests,
        offline rehearsals)."""
        if not self.flag:
            return
        if self.disabled_reason:
            log.warning('"Call for backup" off: %s', self.disabled_reason)
            return
        self._out = asyncio.Queue(maxsize=self.max_queue)
        if run_tasks:
            loop = asyncio.get_running_loop()
            self._tasks = [loop.create_task(self._sender()), loop.create_task(self._poller())]

    async def flush(self) -> None:
        """Send everything queued so far, in the caller's task (only without background tasks)."""
        while self._out is not None and not self._out.empty() and self.active:
            await self._send_step(*self._out.get_nowait())

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------------ tick-loop side (never blocks)
    def submit(self, say) -> bool:
        """Queue one line for the room. False when off or dropped. Never blocks, never raises."""
        try:
            if not self.active or self._out is None:
                return False
            self._out.put_nowait(("say", self._clock(), say))
        except asyncio.QueueFull:
            self.stats["dropped"] += 1
            return False
        except Exception:
            self.stats["errors"] += 1
            return False
        self.stats["queued"] += 1
        return True

    def submit_all(self, says) -> int:
        return sum(1 for s in (says or []) if self.submit(s))

    def drain_inbox(self) -> list[tuple[str, str]]:
        """The accepted human messages since the last call, oldest first: (text, sender name)."""
        out = []
        while True:
            try:
                out.append(self._inbox.popleft())
            except IndexError:
                return out

    def inject(self, text: str, by_name: str = "Player") -> None:
        """Offline rehearsals and tests: put a human reply in the inbox as the poller would."""
        cleaned = clean_text(text)
        if cleaned:
            self._inbox.append((cleaned, clean_text(by_name, HUMAN_NAME_MAX) or "A player"))

    def set_listening(self, on: bool) -> None:
        """True while a match runs or Coach's question is open; the poller is idle otherwise."""
        self.listening = bool(on)

    def match_started(self) -> None:
        """A new match begins: forget older replies and take last match's specialist out of the room."""
        self._not_before = self._wall() + self._skew
        self._inbox.clear()
        self._queue_cleanup()

    def match_reset(self) -> None:
        """Back to the lobby: nothing to listen for; the specialist leaves the room."""
        self.listening = False
        self._inbox.clear()
        self._queue_cleanup()

    def _queue_cleanup(self) -> None:
        if not self.active or self._out is None:
            return
        try:
            self._out.put_nowait(("cleanup", self._clock(), None))
        except asyncio.QueueFull:
            self.stats["dropped"] += 1

    # ------------------------------------------------------------------ HTTP
    def _http(self):
        if self._client is None:
            import httpx
            kw = {"timeout": 5.0, "headers": {"Accept": "application/json"}}
            if self._transport is not None:
                kw["transport"] = self._transport
            self._client = httpx.AsyncClient(**kw)
        return self._client

    def _chat(self, tail: str = "") -> str:
        return f"{self.base_url}/agent/chats/{self.chat_id}{tail}"

    async def _call(self, agent: str, method: str, url: str, json=None, params=None, tries: int = 1,
                    wait: bool = True):
        """One request with `agent`'s key and the shared error rules. Returns the response, or
        None when nothing usable came back (off, agent stopped, paused, transport error)."""
        resp = None
        for attempt in range(max(1, tries)):
            if not self.active or agent in self._stopped or not self.keys.get(agent):
                return None
            pause = self._pause_until - self._clock()
            if pause > 0:
                if not wait:
                    return None
                await self._sleep(pause)
            try:
                resp = await self._http().request(method, url, json=json, params=params,
                                                  headers={"X-API-Key": self.keys[agent]})
            except asyncio.CancelledError:
                raise
            except Exception as exc:                      # network trouble: count it, never raise
                self.stats["errors"] += 1
                self._fails += 1
                log.info("BAND %s failed: %s", method, type(exc).__name__)
                if self._fails >= MAX_FAILS_IN_A_ROW:
                    self._switch_off(f"{MAX_FAILS_IN_A_ROW} failed requests in a row")
                return None
            self._fails = 0
            self._note_date(resp)
            code = resp.status_code
            if code == 429:
                self.stats["rate_limited"] += 1
                delay = self._retry_after(resp)
                if delay is None:
                    delay = BACKOFF_429[min(self._n429, len(BACKOFF_429) - 1)]
                self._n429 += 1
                self._pause_until = self._clock() + delay
                continue
            self._n429 = 0
            if code == 503:
                self.stats["retried_503"] += 1
                if attempt + 1 < tries:
                    await self._sleep(BACKOFF_503[min(attempt, len(BACKOFF_503) - 1)])
                continue
            if code == 401:
                self.stats["errors"] += 1
                self._stop_agent(agent, "its key was refused (HTTP 401)")
            elif code == 403:
                self.stats["errors"] += 1
                err, message, details = _error_of(resp)
                if err == "limit_reached":
                    key = str(details.get("key") or "unknown")[:60]
                    self._switch_off(f"BAND plan limit reached ({key}); off until the server restarts")
                elif "execution is stopped" in message.lower():
                    self._stop_agent(agent, "its execution is stopped in BAND")
            return resp
        return resp

    @staticmethod
    def _retry_after(resp) -> float | None:
        raw = resp.headers.get("retry-after")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        return min(max(v, 0.0), RETRY_AFTER_MAX)

    def _note_date(self, resp) -> None:
        """Message ages are judged on BAND's clock, read from the Date header when there is one."""
        raw = resp.headers.get("date")
        if not raw:
            return
        try:
            self._skew = email.utils.parsedate_to_datetime(raw).timestamp() - self._wall()
        except Exception:
            pass

    # ------------------------------------------------------------------ the room
    async def _load_participants(self, force: bool = False) -> list[dict] | None:
        now = self._clock()
        if not force and self._participants is not None and now - self._participants_at < PARTICIPANTS_TTL_S:
            return self._participants
        resp = await self._call(COACH, "GET", self._chat("/participants"), tries=2)
        if resp is None or resp.status_code != 200:
            self.stats["room_read_failed"] += 1
            return self._participants
        try:
            body = resp.json()
        except ValueError:
            return self._participants
        rows = body.get("data", body) if isinstance(body, dict) else body
        people = []
        for p in rows if isinstance(rows, list) else []:
            if isinstance(p, dict) and p.get("id"):
                people.append({"id": str(p["id"]), "handle": str(p["handle"]) if p.get("handle") else None,
                               "name": str(p.get("name") or p.get("handle") or "")[:100],
                               "type": str(p.get("type") or "").lower()})
        self._participants, self._participants_at = people, now
        return people

    async def _resolve_ids(self) -> None:
        """BAND_ID_<NAME> missing from .env: ask each agent who it is, once."""
        if self._ids_tried:
            return
        self._ids_tried = True
        for a in AGENTS:
            if self.ids.get(a):
                continue
            resp = await self._call(a, "GET", f"{self.base_url}/agent/me")
            if resp is not None and resp.status_code == 200:
                try:
                    self.ids[a] = str(resp.json()["data"]["id"])
                except Exception:
                    pass

    def _person(self, pid: str | None, fallback_name: str) -> dict | None:
        """The mention object for one id, or None when the room is known and it is not in it."""
        if not pid:
            return None
        if self._participants is None:
            return {"id": pid, "name": fallback_name}
        for p in self._participants:
            if p["id"] == pid:
                m = {"id": pid, "name": p["name"] or fallback_name}
                if p["handle"]:
                    m["handle"] = p["handle"]
                return m
        return None

    def _mentions(self, speaker: str, group: str) -> list[dict]:
        """Who a line mentions. BAND needs at least one mention on every message, so an empty
        group falls back: humans, then the blue agents, then Coach."""
        me = self.ids.get(speaker)
        humans = [p for p in (self._participants or []) if p["type"] == "user"][:MAX_HUMAN_MENTIONS]

        def people(kind: str) -> list[dict]:
            if kind == "humans":
                return [m for m in (self._person(p["id"], p["name"]) for p in humans) if m]
            if kind == "blue_agents":
                return [m for m in (self._person(self.ids.get(a), a.capitalize()) for a in BLUE) if m]
            if kind == "coach":
                m = self._person(self.ids.get(COACH), "Coach")
                return [m] if m else []
            return []

        order = {"blue_agents": ("blue_agents", "humans", "coach"),
                 "humans": ("humans", "blue_agents", "coach"),
                 "coach": ("coach", "humans", "blue_agents"),
                 "team": ("humans", "blue_agents", "coach")}.get(group, ("humans", "blue_agents", "coach"))
        for kind in order:
            out, seen = [], set()
            for m in people(kind):
                if m["id"] != me and m["id"] not in seen:
                    seen.add(m["id"])
                    out.append(m)
            if out:
                return out
        return []

    async def _ensure_member(self, agent: str) -> bool:
        """Coach adds `agent` to the room unless it is already there. True when it may speak."""
        await self._resolve_ids()
        pid = self.ids.get(agent)
        if not pid:
            self.stats["join_failed"] += 1
            return False
        people = await self._load_participants()
        if people is not None and any(p["id"] == pid for p in people):
            return True
        resp = await self._call(COACH, "POST", self._chat("/participants"),
                                json={"participant": {"participant_id": pid, "role": "member"}}, tries=3)
        if resp is None:
            self.stats["join_failed"] += 1
            return False
        if 200 <= resp.status_code < 300:
            self.stats["joins"] += 1
            await self._load_participants(force=True)
            return True
        # "already a member" has no documented code: read the room and see.
        people = await self._load_participants(force=True)
        if people is not None and any(p["id"] == pid for p in people):
            return True
        self.stats["join_failed"] += 1
        log.info("BAND: could not add %s to the room (HTTP %s %s)", agent, resp.status_code, _error_of(resp)[0])
        return False

    async def _cleanup(self) -> None:
        """Take every specialist out of the room, so the next match shows one joining."""
        await self._resolve_ids()
        people = await self._load_participants(force=True)
        if people is None:
            return
        present = {p["id"] for p in people}
        gone = False
        for a in SPECIALISTS:
            pid = self.ids.get(a)
            if not pid or pid not in present:
                continue
            resp = await self._call(COACH, "DELETE", self._chat(f"/participants/{pid}"), tries=2)
            if resp is not None and 200 <= resp.status_code < 300:
                self.stats["removed"] += 1
                gone = True
            else:
                self.stats["remove_failed"] += 1
                log.info("BAND: could not remove %s from the room (HTTP %s)", a,
                         resp.status_code if resp is not None else "none")
        if gone:
            await self._load_participants(force=True)

    # ------------------------------------------------------------------ outbound
    def _budget_ok(self) -> bool:
        if self._posts >= self.max_posts:
            self._switch_off(f"BAND_TEAM_MAX_POSTS ({self.max_posts}) posts reached for this process")
            return False
        now = self._clock()
        while self._recent and now - self._recent[0] > HOUR_S:
            self._recent.popleft()
        if len(self._recent) >= self.max_per_hour:
            self.stats["dropped_hourly_cap"] += 1
            return False
        return True

    async def _post_say(self, say, queued_at: float) -> bool:
        if not self.active:
            return False
        if self._clock() - queued_at > SAY_STALE_S:
            self.stats["dropped_stale"] += 1
            return False
        speaker = str(getattr(say, "speaker", "") or "").lower()
        text = " ".join(str(getattr(say, "text", "") or "").split())
        if speaker not in AGENTS or not text or speaker in self._stopped:
            self.stats["dropped"] += 1
            return False
        await self._resolve_ids()
        role = str(getattr(say, "role", "") or "").lower()
        newcomers = [a for a in dict.fromkeys([role if getattr(say, "kind", "") == "join" else "", speaker])
                     if a in SPECIALISTS]
        for a in newcomers:                               # a specialist is in the room before it speaks
            if not await self._ensure_member(a):
                self.stats["dropped"] += 1
                return False
        await self._load_participants()
        mentions = self._mentions(speaker, str(getattr(say, "mention", "") or "team"))
        if not mentions:
            self.stats["dropped_no_mention"] += 1
            return False
        if not self._budget_ok():
            return False
        gap = self.gap - (self._clock() - self._last_post)
        if gap > 0:
            await self._sleep(gap)
        if not self.active:
            return False
        handles = " ".join("@" + m["handle"] for m in mentions if m.get("handle"))
        content = (f"{handles} {text}" if handles else text)[:CONTENT_MAX]
        self._last_post = self._clock()
        self._posts += 1
        self._recent.append(self._last_post)
        resp = await self._call(speaker, "POST", self._chat("/messages"),
                                json={"message": {"content": content, "mentions": mentions}}, tries=3)
        self._last_post = self._clock()
        if resp is not None and 200 <= resp.status_code < 300:
            self.stats["posts"] += 1
            return True
        self.stats["post_failed"] += 1
        if resp is not None:
            code = _error_of(resp)[0]
            log.info("BAND: a line from %s was not posted (HTTP %s %s)", speaker, resp.status_code, code)
            if resp.status_code == 422:
                self._participants_at = -1e18             # the room changed under us: read it again
        return False

    async def _send_step(self, what: str, queued_at: float, item) -> None:
        try:
            if what == "cleanup":
                await self._cleanup()
            else:
                await self._post_say(item, queued_at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats["errors"] += 1
            log.info("BAND send step failed: %s", type(exc).__name__)

    async def _sender(self) -> None:
        while True:
            step = await self._out.get()
            if not self.active:
                return
            await self._send_step(*step)

    # ------------------------------------------------------------------ inbound (Coach's queue)
    async def _poller(self) -> None:
        while self.active:
            await self._sleep(self.poll_interval)
            if not self.active:
                return
            if not self.listening or self._clock() < self._pause_until:
                continue
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats["errors"] += 1
                log.info("BAND poll failed: %s", type(exc).__name__)

    async def poll_once(self) -> int:
        """Read Coach's queue once, acknowledge every row, keep the humans' fresh messages.
        Returns how many messages were accepted."""
        resp = await self._call(COACH, "GET", self._chat("/messages"), params={"limit": 100}, wait=False)
        if resp is None:
            return 0
        self.stats["polls"] += 1
        if resp.status_code != 200:
            return 0
        try:
            body = resp.json()
        except ValueError:
            return 0
        rows = body.get("data") if isinstance(body, dict) else body
        now = self._wall() + self._skew
        accepted, batch = 0, set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            mid = str(row["id"])
            if mid in batch:                               # the same row twice in one page
                self.stats["duplicates"] += 1
                continue
            batch.add(mid)
            self.stats["rows"] += 1
            if not await self._acknowledge(mid):
                continue                                   # not ours yet: it will be served again
            if mid in self._seen:
                self.stats["duplicates"] += 1
                continue
            self._seen[mid] = None
            while len(self._seen) > SEEN_MAX:
                self._seen.popitem(last=False)
            if self._accept(row, now):
                accepted += 1
        return accepted

    async def _acknowledge(self, mid: str) -> bool:
        """/processing then /processed. False when BAND refused the claim (204) or either call
        failed; such a row is not recorded as handled. /failed is never called."""
        base = self._chat(f"/messages/{mid}")
        claim = await self._call(COACH, "POST", base + "/processing", tries=2)
        if claim is None or claim.status_code != 200:
            self.stats["claim_refused"] += 1
            return False
        done = await self._call(COACH, "POST", base + "/processed", tries=2)
        if done is None or not 200 <= done.status_code < 300:
            self.stats["ack_failed"] += 1
            return False
        self.stats["acked"] += 1
        return True

    def _accept(self, row: dict, now: float) -> bool:
        sender = str(row.get("sender_id") or "")
        if row.get("sender_type") != "User" and sender not in self.trusted:
            self.stats["ignored_agent"] += 1               # a sibling agent's line: acknowledged only
            return False
        written = _parse_time(row.get("inserted_at"))
        if written is None or now - written > self.stale_s or written < self._not_before - 2.0:
            self.stats["stale"] += 1
            return False
        coach = self.ids.get(COACH)
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        found = meta.get("mentions") if isinstance(meta.get("mentions"), list) else []
        if coach and not any(isinstance(m, dict) and str(m.get("id") or "") == coach
                             and m.get("kind", "mention") in (None, "mention") for m in found):
            self.stats["ignored_no_mention"] += 1
            return False
        text = clean_text(row.get("content"))
        if not text:
            self.stats["ignored_empty"] += 1
            return False
        self._inbox.append((text, clean_text(row.get("sender_name"), HUMAN_NAME_MAX) or "A player"))
        self.stats["accepted"] += 1
        return True
