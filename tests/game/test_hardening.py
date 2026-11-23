"""Review fixes: slow sockets, cross-site requests, oversized frames, strict input, busy input,
fog of war for undetected reds, no runtime flip to the mock data, BAND budget."""
import asyncio
import json
import random
import shutil

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from game import band, config, match, rules, server, state as st
from game import world as gworld
from test_band import AGENT_KEY, FakeBand
from test_contract import check_message


# ------------------------------------------------------------------ a slow socket never stalls the tick
class FakeWS:
    def __init__(self, block=False):
        self.block = block
        self.sent = []
        self.closed = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        if self.block:
            await asyncio.sleep(3600)
        self.sent.append(text)

    async def close(self, code=1000):
        self.closed = code


def test_slow_client_is_closed_and_does_not_stall_broadcast():
    async def run():
        m = server.ConnectionManager()
        m.SEND_TIMEOUT = 0.1
        good, slow = FakeWS(), FakeWS(block=True)
        assert await m.connect(good) and await m.connect(slow)
        loop = asyncio.get_running_loop()
        gaps = []
        for i in range(10):
            t0 = loop.time()
            await m.broadcast(f"msg{i}")
            gaps.append(loop.time() - t0)
            await asyncio.sleep(0.03)
        await asyncio.sleep(0.2)
        return m, good, slow, gaps

    m, good, slow, gaps = asyncio.run(run())
    assert max(gaps) < 0.05                        # broadcast only queues; it never waits on a socket
    assert good.sent == [f"msg{i}" for i in range(10)]
    assert slow.closed == 1011                     # closed, so the browser's onclose fires and it reconnects
    assert m.active == [good] and m.dropped == 1


def test_client_that_stops_reading_is_closed_when_its_queue_overflows():
    async def run():
        m = server.ConnectionManager()
        m.SEND_TIMEOUT = 60.0
        stuck = FakeWS(block=True)
        await m.connect(stuck)
        for i in range(m.QUEUE_MAX + 3):
            await m.broadcast(f"m{i}")
        await asyncio.sleep(0.05)
        return m, stuck

    m, stuck = asyncio.run(run())
    assert stuck.closed == 1011 and m.active == []


def test_connection_cap():
    async def run():
        m = server.ConnectionManager(max_clients=2)
        a, b, c = FakeWS(), FakeWS(), FakeWS()
        res = [await m.connect(a), await m.connect(b), await m.connect(c)]
        return res, c

    res, c = asyncio.run(run())
    assert res == [True, True, False] and c.closed == 1013 and not c.accepted


# ------------------------------------------------------------------ server fixtures
@pytest.fixture
def client(tmp_path, mock_dir, monkeypatch):
    monkeypatch.setattr(config, "SERVING_DIR", tmp_path / "serving")
    monkeypatch.setattr(config, "MOCK_DIR", mock_dir)
    monkeypatch.setattr(config, "GAME_DB", tmp_path / "game.duckdb")
    monkeypatch.setattr(config, "LEDGER_JSONL", tmp_path / "ledger" / "ledger.jsonl")
    monkeypatch.setattr(config, "REASONS_JSONL", tmp_path / "ledger" / "reasons.jsonl")
    monkeypatch.setattr(config, "ADVANCE_SCRIPT", tmp_path / "scripts" / "advance.py")
    monkeypatch.setenv("TICK_SECONDS", "0.02")
    with TestClient(server.app) as c:
        yield c


def _state(ws, phase=None, limit=400):
    for _ in range(limit):
        m = json.loads(ws.receive_text())
        if m.get("t") == "state" and (phase is None or m["phase"] == phase):
            return m
    raise AssertionError("no matching state message")


# ------------------------------------------------------------------ cross-site requests
def test_websocket_from_another_site_is_refused(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_text()
    assert exc.value.code == 1008
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000"):
        with client.websocket_connect("/ws", headers={"origin": origin}) as ws:
            assert _state(ws)["phase"] == "lobby"
    with client.websocket_connect("/ws") as ws:              # no Origin (scripts, tests): allowed
        assert _state(ws)["phase"] == "lobby"


def test_cross_site_posts_are_refused(client):
    form = {"origin": "http://evil.example", "content-type": "application/x-www-form-urlencoded"}
    for path in ("/api/match/start", "/api/world/advance", "/api/match/reset"):
        r = client.post(path, headers=form, content=b"x=1")
        assert r.status_code == 403, path
    assert client.get("/api/state").json()["phase"] == "lobby"     # nothing started
    assert client.post("/api/match/reset", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/match/reset", headers={"origin": "null"}).status_code == 403
    assert client.post("/api/match/reset", headers={"origin": "http://localhost:5173",
                                                    "sec-fetch-site": "same-origin"}).status_code == 200
    assert client.post("/api/match/reset").status_code == 200       # no Origin: curl, start.ps1
    assert client.get("/api/world", headers={"origin": "http://evil.example"}).status_code == 200


def test_extra_origins_from_env(monkeypatch):
    monkeypatch.setenv("PT_ALLOWED_ORIGINS", "http://192.168.1.5:5173/, http://box:5173")
    assert server.origin_ok({"origin": "http://192.168.1.5:5173"})
    assert server.origin_ok({"origin": "http://box:5173"})
    assert not server.origin_ok({"origin": "http://evil.example"})


# ------------------------------------------------------------------ oversized frames, strict input
def test_oversized_frame_is_ignored_and_connection_survives(client):
    with client.websocket_connect("/ws") as ws:
        _state(ws)
        ws.send_text(json.dumps({"t": "act", "player": 1, "action": "MOVE", "dir": "N" * 100000}))
        ws.send_text(json.dumps({"t": "control", "cmd": "start"}))
        assert _state(ws, "running")["phase"] == "running"
        assert server.game.human_inputs == {}
        client.post("/api/match/reset")


@pytest.mark.parametrize("player,ok", [(1, True), (2, True), ("1", True), ("2", True), (True, False),
                                       (False, False), (1.9, False), (1.0, False), (3, False), (None, False),
                                       ("01", False), ([1], False)])
def test_player_id_must_be_exactly_1_or_2(client, player, ok):
    g = server.game
    g.human_inputs = {}
    assert g.human_input({"t": "act", "player": player, "action": "MOVE", "dir": "N"}) is ok
    assert bool(g.human_inputs) is ok


def test_dir_and_action_are_bounded(client):
    g = server.game
    g.human_inputs = {}
    assert g.human_input({"player": 1, "action": "move", "dir": "w"})
    assert g.human_inputs["p1"]["dir"] == "W" and g.human_inputs["p1"]["action"] == "MOVE"
    assert g.human_input({"player": 1, "action": "MOVE", "dir": "N" * 50})
    assert g.human_inputs["p1"]["dir"] == "NN"            # truncated before .upper(); resolves as no move
    assert not g.human_input({"player": 1, "action": "PATCH" * 100})
    assert not g.human_input({"player": 1, "action": 5})


# ------------------------------------------------------------------ input during a busy tick is kept
def test_input_during_a_busy_tick_is_kept_for_the_first_free_tick(serving):
    g = server.Game.__new__(server.Game)     # only the input queue is exercised
    g.human_inputs = {}
    s = st.new_match(serving, 2, "m-busy")
    p1 = s.actors["p1"]
    p1.busy_ticks, p1.busy = 2, {"action": "PATCH", "node": p1.node_id, "card": None}
    assert g.human_input({"player": 1, "action": "MONITOR"})
    assert g.take_human_actions(s) == {}              # busy: not applied, not thrown away
    assert "p1" in g.human_inputs
    p1.busy_ticks = 0
    acts = g.take_human_actions(s)
    assert acts["p1"].kind == "MONITOR" and g.human_inputs == {}
    # a newer key press while busy replaces the kept one
    p1.busy_ticks = 1
    g.human_input({"player": 1, "action": "MONITOR"})
    g.take_human_actions(s)
    g.human_input({"player": 1, "action": "RESTORE"})
    p1.busy_ticks = 0
    assert g.take_human_actions(s)["p1"].kind == "RESTORE"


def test_every_human_input_is_applied_in_a_match(serving):
    """A PATCH (2 ticks) followed at once by MONITOR: before the fix the MONITOR was lost."""
    g = server.Game.__new__(server.Game)
    g.human_inputs = {}
    s = st.new_match(serving, 9, "m-apply")
    rng = random.Random(9)
    node = s.nodes[s.actors["p1"].node_id]
    node.cards[0].patch_state = "unpatched"
    g.human_input({"player": 1, "action": "PATCH"})
    res = match.run_tick(s, g.take_human_actions(s), rng)
    assert next(r for r in res.rows if r["actor_id"] == "p1")["action"] == "PATCH"
    g.human_input({"player": 1, "action": "MONITOR"})       # pressed while the patch runs
    applied = []
    for _ in range(6):
        res = match.run_tick(s, g.take_human_actions(s), rng)
        applied += [r["action"] for r in res.rows if r["actor_id"] == "p1"]
    assert "MONITOR" in applied


# ------------------------------------------------------------------ fog of war
def _hide(s, rid):
    a = s.actors[rid]
    a.revealed_ticks = 0
    return a


def test_unrevealed_red_is_fogged_in_the_state_message(serving):
    s = st.new_match(serving, 3, "m-fog")
    red = _hide(s, "red1")
    red.last_seen_node, red.node_id, red.action, red.target_node = 4, 12, "EXPLOIT", 12
    row = {"ledger_id": "m-fog-001-red1", "match_id": "m-fog", "world_asof": "x", "pipeline_run_id": "r",
           "model_version": None, "tick": 1, "ts": "t", "actor_id": "red1", "team": "red", "kind": "agent",
           "action": "EXPLOIT", "node_id": 12, "cve_id": "CVE-2099-0001", "score_used": 0.3,
           "score_source": "epss", "p_breach": 0.4, "roll": 0.9, "outcome": "failed", "policy": "opportunist",
           "reason_text": "Opportunist tries CVE-2099-0001 on DB-01.", "llm_model": None, "llm_latency_ms": None}
    msg = st.snapshot(s, [row])
    check_message(msg)
    a = next(x for x in msg["actors"] if x["id"] == "red1")
    assert a == {**a, "node_id": 4, "action": "WAIT", "target_node": None, "revealed": False, "busy_ticks": 0}
    r = msg["ledger"][0]
    assert r["action"] == "UNSEEN" and r["node_id"] is None and r["cve_id"] is None and r["p_breach"] is None
    assert "CVE" not in r["reason_text"] and set(r) == set(row)
    assert row["node_id"] == 12                       # the stored row is untouched
    red.revealed_ticks = 2                             # detected: shown as it is
    msg = st.snapshot(s, [row])
    a = next(x for x in msg["actors"] if x["id"] == "red1")
    assert a["node_id"] == 12 and a["action"] == "EXPLOIT" and msg["ledger"][0]["node_id"] == 12
    s.phase = "ended"                                  # after the match nothing is hidden
    red.revealed_ticks = 0
    assert st.snapshot(s, [row])["ledger"][0]["action"] == "EXPLOIT"


def test_fog_never_shows_a_hidden_red_position_during_a_whole_match(serving):
    s = st.new_match(serving, 11, "m-fog2")
    rng = random.Random(11)
    hidden_seen = 0
    while s.phase == "running":
        res = match.run_tick(s, {}, rng)
        msg = st.snapshot(s, res.rows, res.events)
        for a in msg["actors"]:
            real = s.actors[a["id"]]
            if a["team"] == "red" and not real.revealed and s.phase == "running":
                hidden_seen += 1
                assert a["node_id"] == real.last_seen_node and a["action"] == "WAIT"
                assert all(r["action"] == "UNSEEN" for r in msg["ledger"] if r["actor_id"] == a["id"])
            else:
                assert a["node_id"] == real.node_id
    assert hidden_seen > 0


def test_server_withholds_late_reasons_and_ledger_rows_of_unseen_reds(client):
    g = server.game
    g.unseen_ids = {"m-x-001-red1"}
    sent = []

    async def fake_broadcast(text):
        sent.append(text)
    g.manager.broadcast = fake_broadcast
    g.ledger.update_reason = lambda *a, **k: None
    asyncio.run(g._on_reason("m-x-001-red1", "Opportunist exploits CVE-2099-0001", None, None))
    asyncio.run(g._on_reason("m-x-001-blue1", "Triage patches", None, None))
    assert len(sent) == 1 and "blue1" in sent[0]
    g.ledger.recent = lambda limit: [{"ledger_id": "m-x-001-red1", "action": "EXPLOIT", "node_id": 3,
                                      "cve_id": "CVE-2099-0001"},
                                     {"ledger_id": "m-x-001-blue1", "action": "PATCH", "node_id": 2,
                                      "cve_id": "CVE-2099-0002"}]
    rows = client.get("/api/ledger?limit=5").json()
    assert rows[0]["action"] == "UNSEEN" and rows[0]["cve_id"] is None and rows[1]["cve_id"] == "CVE-2099-0002"
    g.unseen_ids = set()
    assert client.get("/api/ledger?limit=5").json()[0]["cve_id"] == "CVE-2099-0001"


# ------------------------------------------------------------------ no runtime flip to serving_mock
def _copy_serving(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("attack_cards.json", "patch_priority.json", "reveal.json", "world.json"):
        shutil.copy(src / name, dst / name)


def test_deleted_serving_files_never_switch_a_running_server_to_the_mock(tmp_path, mock_dir):
    serving = tmp_path / "serving"
    _copy_serving(mock_dir / "next", serving)             # stands in for the real day-0 world
    wm = gworld.WorldManager(serving_dir=serving, mock_dir=mock_dir)
    assert wm.current.source == "serving" and wm.current.asof == "2026-03-29"
    seen = []
    wm.on_change(seen.append)
    for name in ("world.json", "attack_cards.json", "patch_priority.json", "reveal.json"):   # reset.sh
        (serving / name).unlink()
    assert asyncio.run(wm.poll_once()) is None
    assert wm.current.source == "serving" and seen == []
    _copy_serving(mock_dir, serving)                       # the rebuilt day-0 world lands
    data = asyncio.run(wm.poll_once())
    assert data is not None and data.source == "serving" and data.asof == "2026-03-22" and len(seen) == 1


def test_an_older_world_mid_match_drops_nothing(client, tmp_path, mock_dir):
    g = server.game
    with client.websocket_connect("/ws") as ws:
        _state(ws)
        g.tick_seconds = 0.05
        assert client.post("/api/match/start?seed=4").status_code == 200
        _state(ws, "running")
        board = g.state.world["asof_date"]
        older = gworld.load_serving(mock_dir, "serving")
        older.world = {**older.world, "asof_date": "2026-03-15"}
        g._on_world_change(older)
        assert g.pending_world is None
        texts = []
        for _ in range(10):
            m = _state(ws)
            texts += [e["text"] for e in m["events"]]
            assert m["world"]["asof_date"] == board
        assert any("World data replaced" in t for t in texts)
        assert not any(e for e in texts if e.startswith("Patch Tuesday"))
        client.post("/api/match/reset")


# ------------------------------------------------------------------ BAND budget
def _band(fake, **kw):
    return band.BandMirror(api_key=AGENT_KEY, flag=True, interval=0.0,
                           transport=httpx.MockTransport(fake.handler), **kw)


def test_band_process_budget_switches_the_mirror_off():
    fake = FakeBand()
    m = _band(fake, max_events=3, max_queue=20)

    async def run():
        await m.start()
        for i in range(8):
            m.submit("breach", f"b{i}")
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        await m.stop()
    asyncio.run(run())
    assert len(fake.posts) == 3 and not m.active and "BAND_MAX_EVENTS" in m.disabled_reason


def test_band_hourly_cap_drops_events():
    fake = FakeBand()
    m = _band(fake, max_per_hour=2, max_queue=20)

    async def run():
        await m.start()
        for i in range(6):
            m.submit("breach", f"b{i}")
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        await m.stop()
    asyncio.run(run())
    assert len(fake.posts) == 2 and m.active and m.stats["dropped_hourly_cap"] == 4


def test_band_budget_defaults(monkeypatch):
    m = band.BandMirror(api_key=AGENT_KEY, flag=True)
    assert m.max_events == 300 and m.max_per_hour == 120
    monkeypatch.setenv("BAND_MAX_EVENTS", "7")
    monkeypatch.setenv("BAND_MAX_PER_HOUR", "5")
    m = band.BandMirror(api_key=AGENT_KEY, flag=True)
    assert m.max_events == 7 and m.max_per_hour == 5
