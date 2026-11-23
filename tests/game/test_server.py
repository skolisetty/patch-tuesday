import json

import pytest
from fastapi.testclient import TestClient

from game import config
from test_contract import check_message


@pytest.fixture
def client(tmp_path, mock_dir, monkeypatch):
    monkeypatch.setattr(config, "SERVING_DIR", tmp_path / "serving")     # absent: mock data is used
    monkeypatch.setattr(config, "MOCK_DIR", mock_dir)
    monkeypatch.setattr(config, "GAME_DB", tmp_path / "game.duckdb")
    monkeypatch.setattr(config, "LEDGER_JSONL", tmp_path / "ledger" / "ledger.jsonl")
    monkeypatch.setattr(config, "REASONS_JSONL", tmp_path / "ledger" / "reasons.jsonl")
    monkeypatch.setattr(config, "ADVANCE_SCRIPT", tmp_path / "scripts" / "advance.py")
    monkeypatch.setenv("TICK_SECONDS", "0.02")
    from game import server
    with TestClient(server.app) as c:
        yield c


def _next_state(ws, want_phase=None, limit=400):
    for _ in range(limit):
        m = json.loads(ws.receive_text())
        if m.get("t") == "state" and (want_phase is None or m["phase"] == want_phase):
            return m
    raise AssertionError("no matching state message")


def test_http_routes(client):
    s = client.get("/api/state").json()
    check_message(s)
    assert s["phase"] == "lobby"
    w = client.get("/api/world").json()
    assert w["asof_date"] == "2026-03-22" and w["advancing"] is False
    assert client.get("/api/ledger?limit=5").json() == []
    r = client.post("/api/world/advance")
    assert r.status_code == 503 and "advance.py not found" in r.json()["message"]


def test_mid_match_world_change_drops_cards_and_starts_campaign(client, tmp_path, mock_dir, monkeypatch):
    import shutil
    from game import server
    server.game.tick_seconds = 0.05
    with client.websocket_connect("/ws") as ws:
        _next_state(ws)
        assert client.post("/api/match/start?seed=3").json()["seed"] == 3
        _next_state(ws, "running")
        serving = tmp_path / "serving"
        serving.mkdir()
        for name in ("attack_cards.json", "patch_priority.json", "reveal.json", "world.json"):  # world last
            shutil.copy(mock_dir / "next" / name, serving / name)
        kinds, asof = [], None
        while True:
            m = _next_state(ws)
            kinds += [e["kind"] for e in m["events"]]
            asof = m["world"]["asof_date"]
            if m["phase"] == "ended":
                break
        assert "drop" in kinds and "campaign" in kinds and "gate" in kinds
        assert asof == "2026-03-29" and m["world"]["score_source"] == "model"
    assert client.get("/api/world").json()["data_source"] == "serving"


def test_ws_match_flow(client, tmp_path):
    with client.websocket_connect("/ws") as ws:
        first = _next_state(ws)
        check_message(first)
        assert first["phase"] == "lobby"
        ws.send_text(json.dumps({"t": "control", "cmd": "start"}))
        m = _next_state(ws, "running")
        check_message(m)
        assert m["match_id"].startswith("m-")
        ws.send_text(json.dumps({"t": "act", "player": 1, "action": "MOVE", "dir": "W"}))
        ws.send_text(json.dumps({"t": "act", "player": 2, "action": "PATCH"}))
        ws.send_text("not json")
        ws.send_text(json.dumps({"t": "act", "player": 9, "action": "EXPLOIT"}))
        human_rows = []
        msgs = 0
        while True:
            m = _next_state(ws)
            check_message(m)
            msgs += 1
            human_rows += [r for r in m["ledger"] if r["kind"] == "human" and r["action"] != "WAIT"]
            if m["phase"] == "ended":
                break
        assert m["tick"] == 150 and msgs >= 100
        assert any(r["actor_id"] == "p1" and r["action"] == "MOVE" for r in human_rows)
        assert any(r["actor_id"] == "p2" and r["action"] == "PATCH" for r in human_rows)
    rows = client.get("/api/ledger?limit=2000").json()
    assert len(rows) == 1050
    assert client.post("/api/match/start").status_code == 200        # ended -> new match allowed
    assert client.post("/api/match/start").status_code == 409        # already running
    assert client.post("/api/match/reset").json()["phase"] == "lobby"
    assert client.get("/api/state").json()["phase"] == "lobby"
    lines = (tmp_path / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1050


def test_advancing_stays_true_until_the_new_week_is_on_the_board(client):
    from game import server
    assert client.get("/api/world").json()["advancing"] is False
    server.game.pending_world = server.game.world.current      # loaded between ticks, not applied yet
    try:
        assert server.game.snapshot()["world"]["advancing"] is True
        assert client.get("/api/world").json()["advancing"] is True
    finally:
        server.game.pending_world = None
    assert server.game.snapshot()["world"]["advancing"] is False
