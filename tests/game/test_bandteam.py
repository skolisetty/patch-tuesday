""""Call for backup", the BAND side: BandTeam against a fake BAND room (httpx.MockTransport and an
injected clock), the server wiring with a scripted flow, and scripts/band_setup.py against a mock
HTTP server on a free local port. Nothing here talks to the real BAND API."""
import asyncio
import datetime as dt
import importlib.util
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from game import bandteam

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://band.test/api/v1"
CHAT = "11111111-0000-0000-0000-000000000000"
KEYS = {a: f"band_a_test_{a}" for a in bandteam.AGENTS}
IDS = {a: f"a{i:07d}-0000-0000-0000-000000000000" for i, a in enumerate(bandteam.AGENTS)}
AGENT_OF_KEY = {v: k for k, v in KEYS.items()}
WALL0 = 1_800_000_000.0


def human(n: int) -> dict:
    return {"id": f"b{n:07d}-0000-0000-0000-000000000000", "handle": f"player{n}", "name": f"Player {n}",
            "type": "User", "role": "member"}


def agent_row(a: str) -> dict:
    return {"id": IDS[a], "handle": f"owner/{a}", "name": a.capitalize(), "type": "Agent",
            "role": "owner" if a == "coach" else "member"}


@dataclass
class Say:
    speaker: str
    text: str
    mention: str = "team"
    kind: str = "report"
    role: str | None = None


class Clock:
    """Monotonic time that only moves when someone sleeps on it."""

    def __init__(self):
        self.t = 5000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def wall(self) -> float:
        return WALL0 + (self.t - 5000.0)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(0.0, seconds)
        await asyncio.sleep(0)


def err(status: int, code: str, message: str = "", details: dict | None = None, headers=None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {},
                          json={"error": {"code": code, "message": message, "request_id": "r", "details": details or {}}})


class Room:
    """A small BAND: one room, Coach's mention queue, and the validation BAND applies to posts."""

    def __init__(self, clock: Clock, humans: int = 1):
        self.clock = clock
        self.people = [agent_row(a) for a in ("coach",) + bandteam.BLUE] + [human(i + 1) for i in range(humans)]
        self.requests: list[tuple] = []          # (method, path, agent, json body, clock time)
        self.queue: list[dict] = []              # rows that mention Coach
        self.processing: set[str] = set()
        self.processed: list[str] = []
        self.hooks: list = []                    # f(method, path, agent, body) -> Response | None

    # -- helpers for the tests
    def ids(self) -> set[str]:
        return {p["id"] for p in self.people}

    def row(self, mid: str, text: str, sender: dict | None = None, age: float = 0.0, mentions=None,
            sender_type: str | None = None) -> dict:
        sender = sender or self.people[-1]
        when = dt.datetime.fromtimestamp(self.clock.wall() - age, dt.timezone.utc)
        if mentions is None:
            mentions = [{"id": IDS["coach"], "handle": "owner/coach", "name": "Coach", "type": "agent",
                         "kind": "mention"}]
        r = {"id": mid, "content": text, "message_type": "text", "sender_id": sender["id"],
             "sender_type": sender_type or sender["type"], "sender_name": sender["name"], "chat_room_id": CHAT,
             "metadata": {"mentions": mentions}, "attachments": [],
             "inserted_at": when.isoformat().replace("+00:00", "Z")}
        self.queue.append(r)
        return r

    def calls(self, method: str, tail: str) -> list[tuple]:
        return [r for r in self.requests if r[0] == method and r[1].endswith(tail)]

    def messages(self) -> list[tuple]:
        return self.calls("POST", f"/chats/{CHAT}/messages")

    # -- the transport
    def handler(self, request: httpx.Request) -> httpx.Response:
        agent = AGENT_OF_KEY.get(request.headers.get("x-api-key", ""))
        body = json.loads(request.content) if request.content else None
        path, method = request.url.path, request.method
        assert "band_a_" not in str(request.url)                 # a key never travels in a URL
        self.requests.append((method, path, agent, body, self.clock()))
        for hook in self.hooks:
            r = hook(method, path, agent, body)
            if r is not None:
                return r
        if agent is None:
            return err(401, "unauthorized")
        room = f"/api/v1/agent/chats/{CHAT}"
        if path == "/api/v1/agent/me":
            return httpx.Response(200, json={"data": agent_row(agent)})
        if not path.startswith(room):
            return err(404, "not_found")
        tail = path[len(room):]
        if tail == "/participants" and method == "GET":
            return httpx.Response(200, json={"data": list(self.people)})
        if tail == "/participants" and method == "POST":
            pid = body["participant"]["participant_id"]
            if pid in self.ids():
                return err(422, "validation_error", "has already been taken")
            who = next(a for a in bandteam.AGENTS if IDS[a] == pid)
            self.people.append(agent_row(who))
            return httpx.Response(201, json={"data": agent_row(who)})
        if tail.startswith("/participants/") and method == "DELETE":
            pid = tail.rsplit("/", 1)[1]
            if pid not in self.ids():
                return err(404, "not_found")
            self.people = [p for p in self.people if p["id"] != pid]
            return httpx.Response(200, json={"data": {"id": pid}})
        if tail == "/messages" and method == "GET":
            assert request.url.params.get("limit") == "100"
            waiting = [r for r in self.queue if r["id"] not in self.processed]
            return httpx.Response(200, json={"data": waiting[:100], "metadata": {"has_more": False}})
        if tail.endswith("/processing") and method == "POST":
            mid = tail.split("/")[2]
            self.processing.add(mid)
            return httpx.Response(200, json={"data": {"id": mid, "status": "processing", "success": True}})
        if tail.endswith("/processed") and method == "POST":
            mid = tail.split("/")[2]
            if mid not in self.processing:
                return err(422, "validation_error", "not processing")
            self.processed.append(mid)
            return httpx.Response(200, json={"data": {"id": mid, "status": "processed", "success": True}})
        if tail == "/messages" and method == "POST":
            if IDS[agent] not in self.ids():
                return err(403, "forbidden", "not a participant")
            mentions = body["message"].get("mentions") or []
            got = [m.get("id") for m in mentions]
            if not got:
                return err(422, "mentions_required")
            if IDS[agent] in got:
                return err(422, "cannot_mention_self")
            if len(set(got)) != len(got):
                return err(422, "duplicate_mentions")
            if not set(got) <= self.ids():
                return err(422, "mentioned_participant_not_in_room")
            return httpx.Response(201, json={"data": {"id": f"m{len(self.requests)}", "success": True}})
        return err(404, "not_found")


def make(room: Room, clock: Clock, **kw) -> bandteam.BandTeam:
    kw.setdefault("gap", 3.0)
    return bandteam.BandTeam(keys=KEYS, ids=dict(IDS), chat_id=CHAT, base_url=BASE, flag=True,
                             transport=httpx.MockTransport(room.handler), clock=clock, wall=clock.wall,
                             sleep=clock.sleep, **kw)


def drive(room: Room, clock: Clock, steps, **kw):
    """Run `steps(team)` (a coroutine function) on a BandTeam with no background tasks."""
    team = make(room, clock, **kw)

    async def go():
        await team.start(run_tasks=False)
        team.match_started()
        team.set_listening(True)
        try:
            return await steps(team)
        finally:
            await team.stop()

    return team, asyncio.run(go())


# ------------------------------------------------------------------ outbound
def test_each_say_is_one_post_from_the_speakers_key_with_mention_objects():
    clock = Clock()
    room = Room(clock, humans=5)

    async def steps(team):
        team.submit(Say("coach", "Slow response: Red earned 31 of its 46 points from held machines.", "blue_agents",
                        "analysis"))
        team.submit(Say("triage", "I patched 4 cards worth 1.9.", "coach", "agent_line"))
        team.submit(Say("patcher", "I restored 2 machines.", "coach", "agent_line"))
        team.submit(Say("coach", "I recommend calling in Responder. Reply yes or no.", "humans", "ask"))
        team.submit(Say("hunter", "I revealed 1 attacker.", "blue_agents", "agent_line"))
        await team.flush()

    team, _ = drive(room, clock, steps)
    posts = room.messages()
    assert [p[2] for p in posts] == ["coach", "triage", "patcher", "coach", "hunter"]   # one POST per Say, in order
    analysis = posts[0][3]["message"]
    assert analysis["mentions"] == [{"id": IDS[a], "name": a.capitalize(), "handle": f"owner/{a}"}
                                    for a in bandteam.BLUE]
    assert analysis["content"].endswith("Slow response: Red earned 31 of its 46 points from held machines.")
    assert analysis["content"].startswith("@owner/triage @owner/patcher @owner/hunter ")
    assert posts[1][3]["message"]["mentions"] == [{"id": IDS["coach"], "name": "Coach", "handle": "owner/coach"}]
    ask = posts[3][3]["message"]["mentions"]
    assert [m["handle"] for m in ask] == ["player1", "player2", "player3"]             # humans capped at 3
    hunter = [m["id"] for m in posts[4][3]["message"]["mentions"]]
    assert hunter == [IDS["triage"], IDS["patcher"]]                                   # never itself
    for p in posts:
        got = [m["id"] for m in p[3]["message"]["mentions"]]
        assert got and len(got) == len(set(got)) and IDS[p[2]] not in got
        assert set(p[3]) == {"message"} and set(p[3]["message"]) == {"content", "mentions"}
    assert team.stats["posts"] == 5 and team.stats["post_failed"] == 0


def test_a_room_without_humans_still_gets_a_valid_mention():
    clock = Clock()
    room = Room(clock, humans=0)

    async def steps(team):
        team.submit(Say("coach", "Reply yes or no.", "humans", "ask"))
        team.submit(Say("triage", "Summary line.", "team", "summary"))
        await team.flush()

    drive(room, clock, steps)
    posts = room.messages()
    assert [m["id"] for m in posts[0][3]["message"]["mentions"]] == [IDS[a] for a in bandteam.BLUE]
    assert [m["id"] for m in posts[1][3]["message"]["mentions"]] == [IDS["patcher"], IDS["hunter"]]


def test_join_adds_the_specialist_to_the_room_before_it_speaks():
    clock = Clock()
    room = Room(clock)

    async def steps(team):
        team.submit(Say("coach", "Suman approved: calling in Responder.", "humans", "decision"))
        team.submit(Say("responder", "Responder here. FILE-01 is breached and worth 2: going there now.", "team",
                        "join", role="responder"))
        team.submit(Say("responder", "Restored FILE-01.", "team", "report"))
        await team.flush()

    team, _ = drive(room, clock, steps)
    adds = room.calls("POST", "/participants")
    assert len(adds) == 1 and adds[0][2] == "coach"                    # Coach owns the room and adds once
    assert adds[0][3] == {"participant": {"participant_id": IDS["responder"], "role": "member"}}
    order = [(r[0], r[1].rsplit("/", 1)[1], r[2]) for r in room.requests if r[0] == "POST"]
    assert order.index(("POST", "participants", "coach")) < order.index(("POST", "messages", "responder"))
    assert [p[2] for p in room.messages()] == ["coach", "responder", "responder"]
    assert team.stats["joins"] == 1 and team.status()["specialists_in_room"] == ["responder"]


def test_join_survives_already_a_member():
    clock = Clock()
    room = Room(clock)
    room.people.append(agent_row("sentinel"))                          # left over from an earlier match

    async def steps(team):
        team._participants = [p for p in await team._load_participants(force=True) if p["id"] != IDS["sentinel"]]
        team.submit(Say("sentinel", "Sentinel here.", "team", "join", role="sentinel"))   # stale view: add -> 422
        await team.flush()

    team, _ = drive(room, clock, steps)
    assert len(room.calls("POST", "/participants")) == 1
    assert [p[2] for p in room.messages()] == ["sentinel"]
    assert team.stats["join_failed"] == 0


def test_a_join_that_cannot_happen_drops_the_line_without_raising():
    clock = Clock()
    room = Room(clock)
    room.hooks.append(lambda m, p, a, b: err(403, "forbidden", "not allowed")
                      if m == "POST" and p.endswith("/participants") else None)

    async def steps(team):
        assert team.submit(Say("hardener", "Hardener here.", "team", "join", role="hardener")) is True
        await team.flush()

    team, _ = drive(room, clock, steps)
    assert room.messages() == [] and team.stats["join_failed"] == 1 and team.active


def test_the_specialist_leaves_the_room_at_the_next_match_start():
    clock = Clock()
    room = Room(clock)
    room.people.append(agent_row("responder"))

    async def steps(team):
        await team.flush()                                             # match_started() queued the clean-up
        first = list(room.people)
        team.match_reset()
        await team.flush()
        return first

    team, after_start = drive(room, clock, steps)
    gone = room.calls("DELETE", f"/participants/{IDS['responder']}")
    assert len(gone) == 1 and gone[0][2] == "coach"
    assert IDS["responder"] not in {p["id"] for p in after_start}
    assert team.stats["removed"] == 1 and len([r for r in room.requests if r[0] == "DELETE"]) == 1
    assert {p["id"] for p in room.people} >= {IDS[a] for a in ("coach",) + bandteam.BLUE}   # nobody else left


def test_room_gap_between_posts_whoever_speaks():
    clock = Clock()
    room = Room(clock)

    async def steps(team):
        for a in ("coach", "triage", "patcher", "hunter"):
            team.submit(Say(a, f"line from {a}", "humans"))
        await team.flush()

    drive(room, clock, steps)
    times = [p[4] for p in room.messages()]
    assert len(times) == 4
    assert all(b - a >= 3.0 for a, b in zip(times, times[1:]))


def test_hourly_budget_drops_and_process_budget_switches_off():
    clock = Clock()
    room = Room(clock)

    async def steps(team):
        for i in range(4):
            team.submit(Say("coach", f"line {i}", "humans"))
        await team.flush()

    team, _ = drive(room, clock, steps, max_per_hour=2)
    assert len(room.messages()) == 2 and team.stats["dropped_hourly_cap"] == 2 and team.active

    clock, room = Clock(), Room(Clock())
    room.clock = clock
    team, _ = drive(room, clock, steps, max_posts=3)
    assert len(room.messages()) == 3 and not team.active
    assert "BAND_TEAM_MAX_POSTS" in team.disabled_reason
    assert team.submit(Say("coach", "more", "humans")) is False


def test_budget_and_interval_defaults_come_from_the_environment(monkeypatch):
    t = bandteam.BandTeam(keys=KEYS, ids=IDS, chat_id=CHAT, flag=True)
    assert (t.max_per_hour, t.max_posts, t.gap, t.poll_interval) == (200, 1000, 3.0, 2.5)
    monkeypatch.setenv("BAND_TEAM_MAX_PER_HOUR", "9")
    monkeypatch.setenv("BAND_TEAM_MAX_POSTS", "11")
    monkeypatch.setenv("BAND_TEAM_POLL_SECONDS", "0.1")                # never faster than 2.5 s
    monkeypatch.setenv("BAND_TRUSTED_SENDER_IDS", " x1 , x2 ")
    t = bandteam.BandTeam(keys=KEYS, ids=IDS, chat_id=CHAT, flag=True)
    assert (t.max_per_hour, t.max_posts, t.poll_interval, t.trusted) == (9, 11, 2.5, {"x1", "x2"})


def test_off_without_the_flag_keys_or_room(monkeypatch):
    for k in list(bandteam.env_keys()) + ["x"]:
        monkeypatch.delenv(bandteam.key_var(k), raising=False)
    monkeypatch.delenv("BAND_BLUE_CHAT_ID", raising=False)
    monkeypatch.delenv("BAND_BACKUP", raising=False)
    assert bandteam.enabled() is False and bandteam.off_reason() == "BAND_ENABLED is not 1"
    monkeypatch.setenv("BAND_ENABLED", "1")
    reason = bandteam.off_reason()
    assert "BAND_KEY_COACH" in reason and "BAND_KEY_SENTINEL" in reason and "BAND_BLUE_CHAT_ID" in reason
    t = bandteam.BandTeam()
    assert t.active is False and t.submit(Say("coach", "x")) is False and t.drain_inbox() == []
    for a in bandteam.AGENTS:
        monkeypatch.setenv(bandteam.key_var(a), KEYS[a])
    monkeypatch.setenv("BAND_BLUE_CHAT_ID", CHAT)
    assert bandteam.enabled() is True and bandteam.BandTeam().active is True
    monkeypatch.setenv("BAND_BACKUP", "0")
    assert bandteam.enabled() is False and bandteam.BandTeam().disabled_reason == "BAND_BACKUP=0"
    status = json.dumps(bandteam.BandTeam(keys=KEYS, ids=IDS, chat_id=CHAT, flag=True).status())
    assert "band_a_" not in status and CHAT not in status and IDS["coach"] not in status


# ------------------------------------------------------------------ inbound
def test_every_row_is_acknowledged_and_only_fresh_human_mentions_are_accepted():
    clock = Clock()
    room = Room(clock)
    owner = room.people[-1]
    room.row("m-old", "yes", age=61)                                   # typed before the server started
    room.row("m-agent", "I patched 4 cards.", sender=agent_row("triage"))
    room.row("m-other", "yes", mentions=[{"id": IDS["triage"], "kind": "mention"}])
    room.row("m-ref", "yes", mentions=[{"id": IDS["coach"], "kind": "reference"}])
    room.row("m-good", f"@[[{IDS['coach']}]]   yes\tplease\x00\n" + "x" * 500)
    room.row("m-empty", f"@[[{IDS['coach']}]]")

    async def steps(team):
        n = await team.poll_once()
        return n, team.drain_inbox(), team.drain_inbox()

    team, (n, inbox, again) = drive(room, clock, steps)
    assert n == 1 and again == []
    assert len(inbox) == 1
    text, by = inbox[0]
    assert by == owner["name"]
    assert text.startswith("yes please x") and "@[[" not in text and "\x00" not in text
    assert len(text) == bandteam.HUMAN_TEXT_MAX
    # every row: /processing, then /processed, in that order; /failed never
    posts = [r[1].split("/messages/")[1] for r in room.requests if r[0] == "POST" and "/messages/" in r[1]]
    assert posts == [f"{m}/{step}" for m in ("m-old", "m-agent", "m-other", "m-ref", "m-good", "m-empty")
                     for step in ("processing", "processed")]
    assert all(r[2] == "coach" for r in room.requests)                 # one poller, Coach's key only
    assert not any("failed" in r[1] for r in room.requests)
    assert team.stats["stale"] == 1 and team.stats["ignored_agent"] == 1
    assert team.stats["ignored_no_mention"] == 2 and team.stats["ignored_empty"] == 1 and team.stats["acked"] == 6


def test_a_refused_claim_is_not_recorded_as_handled():
    clock = Clock()
    room = Room(clock)
    room.row("m-1", "yes")
    refuse = {"on": True}
    room.hooks.append(lambda m, p, a, b: httpx.Response(204)
                      if refuse["on"] and p.endswith("/m-1/processing") else None)

    async def steps(team):
        first = await team.poll_once()
        calls = [r[1].rsplit("/", 1)[1] for r in room.requests if "/messages/" in r[1]]
        refuse["on"] = False
        second = await team.poll_once()
        third = await team.poll_once()
        return first, calls, second, third, team.drain_inbox()

    team, (first, calls, second, third, inbox) = drive(room, clock, steps)
    assert first == 0 and calls == ["processing"]                      # no /processed after a refused claim
    assert "m-1" not in room.processed[:0] and team.stats["claim_refused"] == 1
    assert second == 1 and third == 0 and inbox == [("yes", "Player 1")]


def test_a_redelivered_message_is_acknowledged_again_but_acted_on_once():
    clock = Clock()
    room = Room(clock)
    room.row("m-1", "hardener")
    room.queue.append(dict(room.queue[0]))                             # the same id twice in one page

    async def steps(team):
        a = await team.poll_once()
        room.processed.clear()                                         # BAND serves it again (at-least-once)
        b = await team.poll_once()
        return a, b, team.drain_inbox()

    team, (a, b, inbox) = drive(room, clock, steps)
    assert (a, b) == (1, 0) and inbox == [("hardener", "Player 1")]
    # twice the second copy in a page (not even acknowledged again), once the redelivery
    assert team.stats["duplicates"] == 3 and team.stats["acked"] == 2
    assert len([r for r in room.requests if r[1].endswith("/processing")]) == 2


def test_only_users_and_the_allow_list_are_heard():
    clock = Clock()
    room = Room(clock)
    bot = {"id": "c0000001-0000-0000-0000-000000000000", "name": "Test Driver", "type": "Agent"}
    room.row("m-bot", "yes", sender=bot)

    async def steps(team):
        await team.poll_once()
        return team.drain_inbox()

    _, inbox = drive(room, clock, steps)
    assert inbox == []
    clock, room2 = Clock(), None
    room2 = Room(clock)
    room2.row("m-bot", "sentinel", sender=bot)
    _, inbox = drive(room2, clock, steps, trusted={bot["id"]})
    assert inbox == [("sentinel", "Test Driver")]


def test_messages_from_before_this_match_are_dropped_even_when_recent():
    clock = Clock()
    room = Room(clock)
    room.row("m-before", "yes", age=20)                                # 20 s old: written before match_started()

    async def steps(team):
        await team.poll_once()
        room.row("m-now", "no")
        await team.poll_once()
        return team.drain_inbox()

    team, inbox = drive(room, clock, steps)
    assert inbox == [("no", "Player 1")] and team.stats["stale"] == 1 and len(room.processed) == 2


def test_message_age_uses_bands_clock_when_ours_is_wrong():
    clock = Clock()
    room = Room(clock)
    ahead = 600.0                                                      # our clock runs 10 minutes fast
    real_now = dt.datetime.fromtimestamp(clock.wall(), dt.timezone.utc)
    date = real_now.strftime("%a, %d %b %Y %H:%M:%S GMT")

    def with_date(m, p, a, b):
        if m == "GET" and p.endswith("/messages"):
            return httpx.Response(200, headers={"Date": date}, json={"data": list(room.queue)})
        return None

    room.hooks.append(with_date)
    team = bandteam.BandTeam(keys=KEYS, ids=dict(IDS), chat_id=CHAT, base_url=BASE, flag=True,
                             transport=httpx.MockTransport(room.handler), clock=clock,
                             wall=lambda: clock.wall() + ahead, sleep=clock.sleep)

    async def go():
        await team.start(run_tasks=False)
        room.row("m-1", "yes")
        await team.poll_once()
        return team.drain_inbox()

    assert asyncio.run(go()) == [("yes", "Player 1")]


def test_inject_puts_a_cleaned_reply_in_the_inbox():
    team = bandteam.BandTeam(keys=KEYS, ids=IDS, chat_id=CHAT, flag=True)
    team.inject(f"@[[{IDS['coach']}]] <b>YES</b> " + "y" * 400, "Suman\nK")
    (text, by), = team.drain_inbox()
    assert text.startswith("<b>YES</b> y") and len(text) == 200 and by == "Suman K"


# ------------------------------------------------------------------ errors
def test_429_waits_retry_after_and_then_posts():
    clock = Clock()
    room = Room(clock)
    left = {"n": 1}

    def limit(m, p, a, b):
        if m == "POST" and p.endswith("/messages") and left["n"]:
            left["n"] -= 1
            return err(429, "rate_limited", headers={"Retry-After": "7"})
        return None

    room.hooks.append(limit)

    async def steps(team):
        team.submit(Say("coach", "analysis", "humans"))
        await team.flush()
        return await team.poll_once()

    team, _ = drive(room, clock, steps)
    times = [p[4] for p in room.messages()]
    assert len(times) == 2 and times[1] - times[0] >= 7.0
    assert team.stats["posts"] == 1 and team.stats["rate_limited"] == 1 and team.active


def test_429_without_retry_after_backs_off_and_pauses_the_poller():
    clock = Clock()
    room = Room(clock)
    room.hooks.append(lambda m, p, a, b: err(429, "rate_limited") if m == "GET" and p.endswith("/messages") else None)

    async def steps(team):
        await team.poll_once()
        before = len(room.requests)
        await team.poll_once()                                         # still paused: no request at all
        paused = len(room.requests) == before
        await clock.sleep(1.0)
        await team.poll_once()
        return paused, team._pause_until - clock()

    team, (paused, second_pause) = drive(room, clock, steps)
    assert paused and second_pause == 2.0                              # 1 s, then 2 s
    assert team.stats["rate_limited"] == 2 and team.active


def test_503_is_retried():
    clock = Clock()
    room = Room(clock)
    left = {"n": 2}

    def shed(m, p, a, b):
        if m == "POST" and p.endswith("/messages") and left["n"]:
            left["n"] -= 1
            return err(503, "service_unavailable")
        return None

    room.hooks.append(shed)

    async def steps(team):
        team.submit(Say("patcher", "line", "coach"))
        await team.flush()

    team, _ = drive(room, clock, steps)
    assert len(room.messages()) == 3 and team.stats["posts"] == 1 and team.stats["retried_503"] == 2


def test_403_limit_reached_stops_the_feature_and_names_the_limit():
    clock = Clock()
    room = Room(clock)
    room.hooks.append(lambda m, p, a, b: err(403, "limit_reached", "Too many agents running. Wait or upgrade.",
                                             {"key": "max_concurrent_agents", "current": 6, "limit": 5})
                      if m == "POST" and p.endswith("/messages") else None)

    async def steps(team):
        team.submit(Say("triage", "one", "coach"))
        team.submit(Say("coach", "two", "humans"))
        await team.flush()
        before = len(room.requests)
        assert await team.poll_once() == 0
        return len(room.requests) - before

    team, later = drive(room, clock, steps)
    assert len(room.messages()) == 1 and later == 0
    assert team.active is False and "max_concurrent_agents" in team.disabled_reason
    assert team.submit(Say("coach", "three", "humans")) is False
    assert team.status()["active"] is False


def test_execution_stopped_stops_only_that_agent():
    clock = Clock()
    room = Room(clock)
    room.hooks.append(lambda m, p, a, b: err(403, "forbidden", "Agent execution is stopped; cannot post messages")
                      if m == "POST" and p.endswith("/messages") and a == "hunter" else None)

    async def steps(team):
        for speaker in ("hunter", "triage", "hunter", "coach"):
            team.submit(Say(speaker, f"line from {speaker}", "humans"))
        await team.flush()

    team, _ = drive(room, clock, steps)
    assert [p[2] for p in room.messages()] == ["hunter", "triage", "coach"]
    assert team.active and team.status()["stopped_agents"] == ["hunter"]


def test_coach_stopped_means_feature_off():
    clock = Clock()
    room = Room(clock)
    room.hooks.append(lambda m, p, a, b: err(403, "forbidden", "Agent execution is stopped; cannot post messages")
                      if m == "POST" and p.endswith("/messages") else None)

    async def steps(team):
        team.submit(Say("coach", "analysis", "humans"))
        await team.flush()

    team, _ = drive(room, clock, steps)
    assert team.active is False and "Coach" in team.disabled_reason


def test_transport_errors_never_propagate_and_switch_off_after_five():
    clock = Clock()

    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    team = bandteam.BandTeam(keys=KEYS, ids=dict(IDS), chat_id=CHAT, base_url=BASE, flag=True,
                             transport=httpx.MockTransport(boom), clock=clock, wall=clock.wall, sleep=clock.sleep)

    async def go():
        await team.start(run_tasks=False)
        team.match_started()
        team.set_listening(True)
        for i in range(4):
            team.submit(Say("coach", f"line {i}", "humans", "join", role="responder"))
        await team.flush()
        for _ in range(6):
            await team.poll_once()
        await team.stop()

    asyncio.run(go())                                                  # no exception reaches the caller
    assert team.active is False and "failed requests" in team.disabled_reason
    assert team.stats["errors"] >= 5 and team.stats["posts"] == 0


def test_tick_side_calls_never_block_or_raise_whatever_the_state():
    team = bandteam.BandTeam(keys=KEYS, ids=IDS, chat_id=CHAT, flag=True)
    assert team.submit(Say("coach", "before start")) is False          # no queue yet
    assert team.submit(object()) is False and team.submit(None) is False
    team.match_started()
    team.match_reset()
    team.set_listening(True)
    assert team.drain_inbox() == []

    async def go():
        t = bandteam.BandTeam(keys=KEYS, ids=IDS, chat_id=CHAT, flag=True, max_queue=3,
                              transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        await t.start(run_tasks=False)
        results = [t.submit(Say("coach", f"l{i}")) for i in range(6)]
        await t.stop()
        return results, t.stats["dropped"]

    results, dropped = asyncio.run(go())
    assert results == [True, True, True, False, False, False] and dropped == 3


def test_nothing_is_polled_unless_a_match_runs_and_the_poller_runs_in_the_background():
    clock = Clock()
    room = Room(clock)
    team = bandteam.BandTeam(keys=KEYS, ids=dict(IDS), chat_id=CHAT, base_url=BASE, flag=True, gap=0.0,
                             poll_interval=0.02, transport=httpx.MockTransport(room.handler), wall=clock.wall)

    async def go():
        await team.start()                                             # real tasks, real (short) sleeps
        await asyncio.sleep(0.15)
        idle = len(room.requests)
        team.match_started()
        team.set_listening(True)
        room.row("m-1", "yes")
        team.submit(Say("coach", "analysis", "humans"))
        await asyncio.sleep(0.25)
        busy = len(room.calls("GET", "/messages"))
        inbox = team.drain_inbox()
        team.set_listening(False)
        await asyncio.sleep(0.1)
        mark = len(room.calls("GET", "/messages"))
        await asyncio.sleep(0.15)
        quiet = len(room.calls("GET", "/messages")) - mark
        await team.stop()
        return idle, busy, inbox, quiet

    idle, busy, inbox, quiet = asyncio.run(go())
    assert idle == 0                                                   # lobby: not one request
    assert busy >= 2 and inbox == [("yes", "Player 1")] and len(room.messages()) == 1
    assert quiet == 0                                                  # match over: polling stops


def test_missing_ids_are_looked_up_once_with_each_agents_own_key():
    clock = Clock()
    room = Room(clock)
    team = bandteam.BandTeam(keys=KEYS, ids={}, chat_id=CHAT, base_url=BASE, flag=True, gap=0.0,
                             transport=httpx.MockTransport(room.handler), clock=clock, wall=clock.wall,
                             sleep=clock.sleep)

    async def go():
        await team.start(run_tasks=False)
        team.submit(Say("hardener", "Hardener here.", "team", "join", role="hardener"))
        team.submit(Say("coach", "again", "blue_agents"))
        await team.flush()
        await team.stop()

    asyncio.run(go())
    me = room.calls("GET", "/agent/me")
    assert sorted(r[2] for r in me) == sorted(bandteam.AGENTS)
    assert [p[2] for p in room.messages()] == ["hardener", "coach"]


# ------------------------------------------------------------------ server wiring (scripted flow)
class ScriptedFlow:
    """Stands in for game.backup.BackupFlow: says fixed lines at fixed ticks, records its calls."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.status = "off"
        self.chosen = None

    def reset(self, state):
        self.calls.append(("reset", state.match_id))
        self.status, self.chosen = "watching", None

    def set_unavailable(self, reason=""):
        self.calls.append(("unavailable", reason))
        self.status = "unavailable"

    def on_tick(self, state, rows, all_rows):
        self.calls.append(("tick", state.tick, len(rows), len(all_rows)))
        if state.tick == 3:
            self.status = "asked"
            return [Say("coach", "Slow response. I recommend Responder. Reply yes or no.", "humans", "ask")]
        return []

    def on_human(self, state, text, by_name):
        self.calls.append(("human", state.tick, text, by_name))
        if self.status == "asked" and "yes" in text.lower().split():
            self.status, self.chosen = "joined", "responder"
            return [Say("responder", "Responder here.", "team", "join", role="responder")]
        return []

    def on_match_end(self, state, all_rows):
        self.calls.append(("end", state.tick, len(all_rows)))
        return [Say("coach", "Summary.", "humans", "summary")]

    def public(self, state):
        return {"status": self.status, "chosen": self.chosen, "room": bandteam.ROOM_TITLE}


@pytest.fixture
def game_env(tmp_path, mock_dir, monkeypatch):
    from game import config
    monkeypatch.setattr(config, "SERVING_DIR", tmp_path / "serving")
    monkeypatch.setattr(config, "MOCK_DIR", mock_dir)
    monkeypatch.setattr(config, "GAME_DB", tmp_path / "game.duckdb")
    monkeypatch.setattr(config, "LEDGER_JSONL", tmp_path / "ledger" / "ledger.jsonl")
    monkeypatch.setattr(config, "REASONS_JSONL", tmp_path / "ledger" / "reasons.jsonl")
    monkeypatch.setattr(config, "ADVANCE_SCRIPT", tmp_path / "scripts" / "advance.py")
    for name in ["BAND_BLUE_CHAT_ID", "BAND_BACKUP", "PT_BACKUP_OFFLINE"] + [bandteam.key_var(a) for a in bandteam.AGENTS]:
        monkeypatch.delenv(name, raising=False)


def test_server_drives_the_flow_and_hands_its_lines_to_the_team(game_env):
    """A whole match offline: the flow asks at tick 3, a scripted human replies in the fake room,
    the reply reaches flow.on_human through the tick loop, and the lines go out through BAND."""
    from game import server

    clock = Clock()
    room = Room(clock)
    flow = ScriptedFlow()

    async def go():
        g = server.Game(tick_seconds=0.01)
        g.team = bandteam.BandTeam(keys=KEYS, ids=dict(IDS), chat_id=CHAT, base_url=BASE, flag=True, gap=0.0,
                                   poll_interval=0.01, transport=httpx.MockTransport(room.handler),
                                   wall=clock.wall)
        g._new_flow = lambda: flow
        await g.team.start()
        assert g.snapshot().get("backup") is None                      # lobby: nothing to show
        g.start_match(seed=5)
        replied = False
        for _ in range(3000):
            await asyncio.sleep(0.005)
            if flow.status == "asked" and not replied and room.messages():
                room.row("m-yes", f"@[[{IDS['coach']}]] yes")           # the human answers in BAND
                replied = True
            if g.state.phase == "ended":
                break
        listening_at_end = g.team.listening
        for _ in range(200):
            if len(room.messages()) >= 3:
                break
            await asyncio.sleep(0.005)
        msg = g.last_message
        status = g.backup_status()
        await g.team.stop()
        g.ledger.close()
        return msg, status, listening_at_end

    msg, status, listening_at_end = asyncio.run(go())
    assert msg["phase"] == "ended" and msg["backup"] == {"status": "joined", "chosen": "responder",
                                                         "room": bandteam.ROOM_TITLE}
    kinds = [c[0] for c in flow.calls]
    assert kinds[0] == "reset" and kinds.count("tick") == 150 and kinds[-1] == "end"
    humans = [c for c in flow.calls if c[0] == "human"]
    assert len(humans) == 1 and humans[0][2:] == ("yes", "Player 1")
    ticks = [c for c in flow.calls if c[0] == "tick"]
    assert ticks[0][1] == 1 and ticks[-1][3] == sum(c[2] for c in ticks)      # all_rows = every row so far
    assert [p[2] for p in room.messages()] == ["coach", "responder", "coach"]
    assert len(room.calls("POST", "/participants")) == 1
    assert listening_at_end is False                                   # match over: the poller goes quiet
    assert status["active"] is True and status["posts"] == 3 and "band_a_" not in json.dumps(status)


def test_a_flow_that_raises_never_stops_the_match(game_env, monkeypatch):
    from game import server
    monkeypatch.setenv("PT_BACKUP_OFFLINE", "1")

    class Broken(ScriptedFlow):
        def on_tick(self, state, rows, all_rows):
            raise RuntimeError("boom")

    async def go():
        g = server.Game(tick_seconds=0.001)
        g._new_flow = Broken
        g.start_match(seed=5)
        for _ in range(3000):
            await asyncio.sleep(0.005)
            if g.state.phase == "ended":
                break
        msg = g.last_message
        g.ledger.close()
        return msg, g.flow

    msg, flow = asyncio.run(go())
    assert msg["phase"] == "ended" and msg["tick"] == 150 and flow is None
    assert not any("server error" in e["text"] for e in msg["events"])


def _play(g, room=None, reply=None, limit=4000):
    """Run the started match to its end; post `reply` in the fake room once Coach has asked."""
    async def go():
        replied = False
        for _ in range(limit):
            await asyncio.sleep(0.004)
            b = g.state.backup or {}
            if reply and not replied and b.get("status") == "asked" and len(room.messages()) >= 5:
                room.row("m-reply", f"@[[{IDS['coach']}]] {reply}")
                replied = True
            if g.state.phase == "ended":
                return
        raise AssertionError("the match did not end")
    return go()


def _real_game(server, room, clock, **cfg):
    from game import backup
    g = server.Game(tick_seconds=0.01)
    if room is not None:
        g.team = bandteam.BandTeam(keys=KEYS, ids=dict(IDS), chat_id=CHAT, base_url=BASE, flag=True, gap=0.0,
                                   poll_interval=0.01, transport=httpx.MockTransport(room.handler),
                                   wall=clock.wall)
    g._new_flow = lambda: backup.BackupFlow(backup.BackupConfig(gap_ticks=2, report_gap_ticks=8, **cfg))
    return g


def test_whole_match_with_the_real_flow_and_a_human_who_picks_another_specialist(game_env):
    """game/backup.py end to end through the server and a fake BAND room: analysis, the three
    agents' lines, the question, a human naming Hardener, the join, reports, the summary."""
    from game import server

    clock = Clock()
    room = Room(clock, humans=2)

    async def go():
        g = _real_game(server, room, clock)
        await g.team.start()
        seen = []
        orig = g.publish

        async def publish(rows=None, events=None, *a, **kw):
            seen.extend(events or [])
            await orig(rows, events, *a, **kw)

        g.publish = publish
        g.start_match(seed=11)
        assert g.snapshot()["backup"]["status"] == "watching"
        await _play(g, room, "hardener please")
        for _ in range(300):
            if g.team.status()["queued"] == 0 and any(p[2] == "coach" and "Match over" in p[3]["message"]["content"]
                                                      for p in room.messages()):
                break
            await asyncio.sleep(0.005)
        out = (g.last_message, g.backup_status(), seen, dict(g.state.actors))
        await g.reset()
        for _ in range(200):
            if g.team.stats["removed"]:
                break
            await asyncio.sleep(0.005)
        await g.team.stop()
        g.ledger.close()
        return out + (g.last_message,)

    msg, status, events, actors, lobby = asyncio.run(go())
    b = msg["backup"]
    assert b["status"] == "joined" and b["chosen"] == "hardener" and b["by"] == "Player 2"
    assert b["actor_id"] == "blue4" and b["before"] and b["after"] and b["room"] == bandteam.ROOM_TITLE
    assert len(msg["actors"]) == 8 and msg["actors"][-1]["id"] == "blue4" and actors["blue4"].role == "hardener"
    posts = room.messages()
    speakers = [p[2] for p in posts]
    assert speakers[:5] == ["coach", "triage", "patcher", "hunter", "coach"]          # analysis, lines, question
    assert speakers[5:7] == ["coach", "hardener"] and speakers[-1] == "coach"         # decision, intro ... summary
    assert set(speakers) <= {"coach", "triage", "patcher", "hunter", "hardener"}
    assert 8 <= len(posts) <= 30
    ask = posts[4][3]["message"]
    assert [m["handle"] for m in ask["mentions"]] == ["player1", "player2"] and "Reply" in ask["content"]
    assert "Hardener here" in posts[6][3]["message"]["content"]
    adds = room.calls("POST", "/participants")
    assert len(adds) == 1 and adds[0][3]["participant"]["participant_id"] == IDS["hardener"]
    kinds = [e["kind"] for e in events]
    assert kinds.count("coach") == 2 and kinds.count("backup") == 1
    assert "via BAND" in next(e["text"] for e in events if e["kind"] == "backup")
    assert status["flow"] == "joined" and status["posts"] == len(posts) and status["errors"] == 0
    # every line Coach was sent (the agents' lines and reports mention it) was acknowledged
    assert len(room.processed) == len(room.queue) and status["accepted"] == 1
    assert not any("/failed" in r[1] for r in room.requests)
    # reset: back to seven actors, no backup object, the specialist is taken out of the room
    assert "backup" not in lobby and len(lobby["actors"]) == 7
    assert IDS["hardener"] not in room.ids() and len(room.calls("DELETE", f"/participants/{IDS['hardener']}")) == 1


def test_no_reply_means_coach_decides_and_no_means_no(game_env):
    from game import server

    async def go(reply, **cfg):
        clock = Clock()
        room = Room(clock)
        g = _real_game(server, room, clock, **cfg)
        await g.team.start()
        g.start_match(seed=11)
        await _play(g, room, reply)
        await asyncio.sleep(0.1)
        out = (g.last_message["backup"], len(g.state.actors), [p[2] for p in room.messages()],
               len(room.calls("POST", "/participants")))
        await g.team.stop()
        g.ledger.close()
        return out

    b, n, speakers, adds = asyncio.run(go(None))
    assert b["status"] == "joined" and b["by"] == "Coach (no reply)" and b["chosen"] == b["recommended"]
    assert n == 8 and adds == 1 and b["chosen"] in speakers

    b, n, speakers, adds = asyncio.run(go(None, auto=False))
    assert b["status"] == "declined" and n == 7 and adds == 0

    b, n, speakers, adds = asyncio.run(go("no thanks"))
    assert b["status"] == "declined" and b["by"] == "Player 1" and n == 7 and adds == 0
    assert set(speakers) == {"coach", "triage", "patcher", "hunter"}


def test_band_going_away_mid_match_makes_the_flow_unavailable_and_the_match_goes_on(game_env):
    from game import server

    clock = Clock()
    room = Room(clock)
    room.hooks.append(lambda m, p, a, b: err(403, "limit_reached", "Monthly message limit reached.",
                                             {"key": "max_messages_monthly"})
                      if m == "POST" and p.endswith(f"/chats/{CHAT}/messages") else None)

    async def go():
        g = _real_game(server, room, clock)
        await g.team.start()
        g.start_match(seed=11)
        await _play(g, room)
        out = (g.last_message, g.backup_status(), len(g.state.actors))
        await g.team.stop()
        g.ledger.close()
        return out

    msg, status, n = asyncio.run(go())
    assert msg["phase"] == "ended" and msg["tick"] == 150 and n == 7
    assert msg["backup"]["status"] == "unavailable" and "max_messages_monthly" in msg["backup"]["reason"]
    assert status["active"] is False and "max_messages_monthly" in status["disabled_reason"]
    assert len(room.messages()) == 1                                    # the first refused post was the last


def test_offline_rehearsal_runs_the_flow_without_band(game_env, monkeypatch):
    import time as _t

    from fastapi.testclient import TestClient

    from game import server
    monkeypatch.setenv("PT_BACKUP_OFFLINE", "1")
    with TestClient(server.app) as c:
        s = c.get("/api/status").json()["band_team"]
        assert s["active"] is False and s["offline"] is True
        server.game.tick_seconds = 2.0                                  # the flow's tick maths: 3 s = 2 ticks
        flow = server.game._new_flow()
        assert flow is not None and flow.cfg.gap_ticks == 2 and flow.cfg.report_gap_ticks == 8
        server.game._new_flow = lambda: flow
        server.game.tick_seconds = 0.005
        assert c.post("/api/match/start?seed=4").json()["ok"] is True
        assert c.get("/api/state").json()["backup"]["status"] == "watching"
        for _ in range(4000):
            if server.game.state.phase == "ended":
                break
            _t.sleep(0.005)
        end = c.get("/api/state").json()
        assert end["phase"] == "ended" and end["backup"]["status"] == "joined"
        assert end["backup"]["by"] == "Coach (no reply)" and len(end["actors"]) == 8
        assert c.get("/api/status").json()["band_team"]["posts"] == 0


def test_feature_off_leaves_the_server_as_it_was(game_env):
    from fastapi.testclient import TestClient

    from game import server
    with TestClient(server.app) as c:
        assert server.game.flow is None and server.game.team.active is False
        s = c.get("/api/status").json()
        assert s["band_team"]["active"] is False and s["band_team"]["feature"] == "Call for backup"
        assert c.get("/api/state").json().get("backup") is None
        assert c.post("/api/match/start?seed=1").json()["ok"] is True
        assert server.game.flow is None and len(server.game.state.actors) == 7
        c.post("/api/match/reset")


# ------------------------------------------------------------------ scripts/band_setup.py
def load_setup():
    spec = importlib.util.spec_from_file_location("band_setup", ROOT / "scripts" / "band_setup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


USER_KEY = "band_u_test_user_key_never_printed"
OWNER = {"id": "d0000000-0000-0000-0000-000000000001", "handle": "owner", "first_name": "Olive",
         "last_name": "Owner", "email": "o@example.test", "role": "user"}


class SetupServer:
    """A mock BAND for the setup script: /me/* with the user key, /agent/* with agent keys."""

    def __init__(self):
        self.agents: dict[str, dict] = {}          # id -> {"id","name","key"}
        self.rooms: dict[str, list[str]] = {}      # chat id -> participant ids
        self.log: list[tuple] = []                 # (method, path, body)
        self.n = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, obj):
                raw = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                status, obj = outer.route(self.command, self.path, self.headers.get("X-API-Key", ""), body)
                self._send(status, obj)

            do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _do

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def add_agent(self, name: str) -> dict:
        self.n += 1
        a = {"id": f"e{self.n:07d}-0000-0000-0000-000000000000", "name": name, "key": f"band_a_secret_{self.n:03d}"}
        self.agents[a["id"]] = a
        return a

    def posts(self) -> list[tuple]:
        return [x for x in self.log if x[0] != "GET"]

    def route(self, method, full, key, body):
        path, _, query = full.partition("?")
        assert "band_" not in full                                      # no key in a URL
        self.log.append((method, path, body))
        e404 = (404, {"error": {"code": "not_found", "message": "Not found", "request_id": "r", "details": {}}})
        e401 = (401, {"error": {"code": "unauthorized", "message": "Invalid key", "request_id": "r", "details": {}}})
        path = path.removeprefix("/api/v1")
        if path.startswith("/me/"):
            if key != USER_KEY:
                return e401
            if path == "/me/profile":
                return 200, {"data": OWNER}
            if path == "/me/agents" and method == "GET":
                from urllib.parse import parse_qs
                want = (parse_qs(query).get("name") or [""])[0].lower()
                rows = [{"id": a["id"], "name": a["name"], "slug": a["name"].lower()} for a in self.agents.values()
                        if want in a["name"].lower()]                    # substring, like BAND
                return 200, {"data": rows}
            if path == "/me/agents/register" and method == "POST":
                a = self.add_agent(body["agent"]["name"])
                assert 10 <= len(body["agent"]["description"]) <= 500
                return 201, {"data": {"agent": {"id": a["id"], "name": a["name"]},
                                      "credentials": {"api_key": a["key"]}}}
            return e404
        me = next((a for a in self.agents.values() if a["key"] == key), None)
        if me is None:
            return e401
        if path == "/agent/me":
            return 200, {"data": {"id": me["id"], "name": me["name"], "handle": f"owner/{me['name'].lower()}"}}
        if path == "/agent/chats" and method == "POST":
            self.n += 1
            cid = f"f{self.n:07d}-0000-0000-0000-000000000000"
            assert body == {"chat": {"title": "Patch Tuesday - Blue team"}}
            self.rooms[cid] = [me["id"]]
            return 201, {"data": {"id": cid, "title": body["chat"]["title"]}}
        parts = path.split("/")
        if len(parts) >= 5 and parts[1:3] == ["agent", "chats"] and parts[3] in self.rooms:
            members = self.rooms[parts[3]]
            if me["id"] not in members:
                return e404
            if parts[4] == "participants" and method == "GET":
                rows = []
                for pid in members:
                    if pid == OWNER["id"]:
                        rows.append({"id": pid, "handle": "owner", "name": "Olive Owner", "type": "User"})
                    else:
                        a = self.agents[pid]
                        rows.append({"id": pid, "handle": f"owner/{a['name'].lower()}", "name": a["name"],
                                     "type": "Agent"})
                return 200, {"data": rows}
            if parts[4] == "participants" and method == "POST":
                members.append(body["participant"]["participant_id"])
                assert body["participant"]["role"] == "member"
                return 201, {"data": {"id": members[-1]}}
            if parts[4] == "messages" and method == "POST":
                assert body["message"]["mentions"] and body["message"]["mentions"][0]["id"] in members
                return 201, {"data": {"id": "m1", "success": True}}
        return e404


@pytest.fixture
def setup_server():
    s = SetupServer()
    yield s
    s.close()


OTHER_LINES = ("# my settings\n"
               "LLM_API_KEY=sk-not-a-band-thing\n"
               "\n"
               "BAND_ENABLED=0\n"
               "BAND_API_KEY=band_a_announcer_key\n"
               "TELEM_API_KEY=telem-secret   \n")


def write_envfile(tmp_path, server, extra=""):
    p = tmp_path / ".env"
    p.write_bytes((OTHER_LINES + f"BAND_BASE_URL={server.url}\nBAND_USER_API_KEY={USER_KEY}\n" + extra).encode())
    return p


def assert_no_key(text: str, server: SetupServer):
    assert "band_a_" not in text and "band_u_" not in text and USER_KEY not in text
    for a in server.agents.values():
        assert a["key"] not in text


def test_setup_registers_agents_creates_the_room_and_is_idempotent(tmp_path, setup_server, capsys):
    mod = load_setup()
    env = write_envfile(tmp_path, setup_server)
    assert mod.main(["--env", str(env)]) == 0
    out = capsys.readouterr().out
    assert_no_key(out, setup_server)
    names = sorted(a["name"] for a in setup_server.agents.values())
    assert names == ["Coach", "Hardener", "Hunter", "Patcher", "Responder", "Sentinel", "Triage"]
    values = mod.read_env(env)
    by_name = {a["name"]: a for a in setup_server.agents.values()}
    for var, title, _ in mod.AGENTS:
        assert values[f"BAND_KEY_{var}"] == by_name[title]["key"]
        assert values[f"BAND_ID_{var}"] == by_name[title]["id"]
        assert by_name[title]["id"] in out                              # ids are printed, keys are not
    chat = values["BAND_BLUE_CHAT_ID"]
    members = setup_server.rooms[chat]
    assert set(members) == {by_name[n]["id"] for n in ("Coach", "Triage", "Patcher", "Hunter")} | {OWNER["id"]}
    hello = [x for x in setup_server.log if x[0] == "POST" and x[1].endswith("/messages")]
    assert len(hello) == 1 and hello[0][2]["message"]["mentions"][0]["id"] == OWNER["id"]
    # only BAND_* lines changed: every earlier line is still there, byte for byte, in order
    text = env.read_bytes().decode()
    assert text.startswith(OTHER_LINES)
    added = [ln.split("=")[0] for ln in text[len(OTHER_LINES):].splitlines() if "=" in ln]
    assert all(k.startswith("BAND_") for k in added) and len(added) == 2 + 14 + 1
    assert not list(tmp_path.glob(".env.*"))                            # no temp file left behind

    # second run: reads only
    before, n_posts = env.read_bytes(), len(setup_server.posts())
    assert mod.main(["--env", str(env)]) == 0
    out2 = capsys.readouterr().out
    assert len(setup_server.posts()) == n_posts and env.read_bytes() == before
    assert "nothing was created or changed" in out2
    assert_no_key(out2, setup_server)
    assert len(setup_server.agents) == 7 and len(setup_server.rooms) == 1


def test_setup_stops_when_an_agent_exists_with_an_unknown_key(tmp_path, setup_server, capsys):
    mod = load_setup()
    setup_server.add_agent("Hunter")                                    # registered earlier; key lost
    setup_server.add_agent("Coach Assistant")                           # a substring match is not a clash
    env = write_envfile(tmp_path, setup_server, "BAND_KEY_PATCHER=band_a_wrong_key\n")
    before = env.read_bytes()
    assert mod.main(["--env", str(env)]) == 3
    out = capsys.readouterr().out
    assert "STOPPED" in out and "Hunter" in out and "--suffix" in out and "regenerate" in out
    assert setup_server.posts() == [] and env.read_bytes() == before    # nothing registered, written or deleted
    assert not any(x[0] == "DELETE" for x in setup_server.log) and len(setup_server.agents) == 2
    assert_no_key(out, setup_server)

    # --suffix registers a separate set and leaves the existing agent alone
    assert mod.main(["--env", str(env), "--suffix", " PT2"]) == 0
    out = capsys.readouterr().out
    names = sorted(a["name"] for a in setup_server.agents.values())
    assert "Hunter" in names and "Hunter PT2" in names and "Coach PT2" in names and len(names) == 9
    assert mod.read_env(env)["BAND_KEY_PATCHER"] != "band_a_wrong_key"  # the refused key was replaced
    assert_no_key(out, setup_server)


def test_setup_dry_run_posts_nothing_and_writes_nothing(tmp_path, setup_server, capsys):
    mod = load_setup()
    env = write_envfile(tmp_path, setup_server)
    before = env.read_bytes()
    assert mod.main(["--env", str(env), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert setup_server.posts() == [] and env.read_bytes() == before and setup_server.agents == {}
    assert out.count("would register") == 7 and "would create the room" in out
    # after a real run, a dry run with a missing member says what it would add
    assert mod.main(["--env", str(env)]) == 0
    chat = mod.read_env(env)["BAND_BLUE_CHAT_ID"]
    setup_server.rooms[chat].remove(OWNER["id"])
    capsys.readouterr()
    before, n = env.read_bytes(), len(setup_server.posts())
    assert mod.main(["--env", str(env), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would add Olive Owner (you)" in out and len(setup_server.posts()) == n and env.read_bytes() == before
    assert_no_key(out, setup_server)


def test_setup_recreates_an_unreadable_room_and_adds_missing_members(tmp_path, setup_server, capsys):
    mod = load_setup()
    env = write_envfile(tmp_path, setup_server, "BAND_BLUE_CHAT_ID=99999999-0000-0000-0000-000000000000\n")
    assert mod.main(["--env", str(env)]) == 0
    out = capsys.readouterr().out
    values = mod.read_env(env)
    assert values["BAND_BLUE_CHAT_ID"] in setup_server.rooms and "cannot read" in out
    assert env.read_text().count("BAND_BLUE_CHAT_ID=") == 1             # replaced in place, not duplicated
    chat = values["BAND_BLUE_CHAT_ID"]
    hunter = values["BAND_ID_HUNTER"]
    setup_server.rooms[chat].remove(hunter)
    assert mod.main(["--env", str(env)]) == 0
    assert hunter in setup_server.rooms[chat] and len(setup_server.rooms) == 1
    specialists = {values[f"BAND_ID_{v}"] for v in ("RESPONDER", "HARDENER", "SENTINEL")}
    assert not specialists & set(setup_server.rooms[chat])              # specialists are never added here


def test_setup_refuses_bad_configuration_without_any_request(tmp_path, setup_server, capsys):
    mod = load_setup()
    assert mod.main(["--env", str(tmp_path / "missing.env")]) == 2
    env = tmp_path / ".env"
    env.write_text(f"BAND_BASE_URL={setup_server.url}\nBAND_API_KEY=band_a_agent_key\n")
    assert mod.main(["--env", str(env)]) == 2                           # an agent key cannot register agents
    env.write_text("BAND_BASE_URL=http://band.example.test/api/v1\nBAND_USER_API_KEY=" + USER_KEY + "\n")
    assert mod.main(["--env", str(env)]) == 2                           # a key is never sent over plain http
    out = capsys.readouterr().out
    assert setup_server.log == [] and "band_u_" not in out.replace("(band_u_...)", "")
    env.write_text(f"BAND_BASE_URL={setup_server.url}\nBAND_API_KEY={USER_KEY}\n")   # user key in BAND_API_KEY
    assert mod.main(["--env", str(env), "--dry-run"]) == 0


def test_write_env_touches_only_band_lines(tmp_path):
    mod = load_setup()
    p = tmp_path / ".env"
    original = b"\xef\xbb\xbfA=1\r\n# BAND_KEY_COACH=commented\r\nexport BAND_KEY_COACH=old\r\nB = two words \r\n"
    p.write_bytes(original)
    mod.write_env(p, {"BAND_KEY_COACH": "new", "BAND_ID_COACH": "id-1"})
    got = p.read_bytes()
    assert got.startswith(b"\xef\xbb\xbfA=1\r\n# BAND_KEY_COACH=commented\r\nBAND_KEY_COACH=new\r\nB = two words \r\n")
    assert got.endswith(b"BAND_ID_COACH=id-1\r\n") and got.count(b"BAND_KEY_COACH=new") == 1
    with pytest.raises(ValueError):
        mod.write_env(p, {"LLM_API_KEY": "x"})
    assert p.read_bytes() == got
