"""End to end: a real uvicorn process on the REAL serving/*.json, two WebSocket clients as p1 and p2.

    .venv\\Scripts\\python -m pytest tests/e2e -q          (or)    .venv\\Scripts\\python tests/e2e/test_e2e.py

What it checks:
  * the server serves serving/ (not the mock) and every state message has the CONTRACTS.md shape;
  * a match started by p1's {"t":"control","cmd":"start"} runs 150 ticks and ends;
  * all seven actors produce one ledger row per tick and each does something other than WAIT;
  * the human actions sent over the sockets are applied (MOVE changes the node, MONITOR watches
    the node, PATCH with a cve patches exactly that card);
  * every ledger row reaches data/game.duckdb and ledger/ledger.jsonl (temporary copies here).

The tick is shortened with PT_TICK_SECONDS; LLM reasons, BAND and Telem are switched off so the
test makes no outside call, and advance.py points at a file that does not exist.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "game"))

import duckdb  # noqa: E402
import httpx  # noqa: E402
import websockets  # noqa: E402

from test_contract import check_message  # noqa: E402  (the field-by-field contract checker)

TICK = float(os.environ.get("E2E_TICK_SECONDS", "0.15"))
ACTORS = ["red1", "red2", "blue1", "blue2", "blue3", "p1", "p2"]
DIRS = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(tmp: Path, port: int, tick: float = TICK) -> subprocess.Popen:
    env = dict(os.environ)
    env.update({
        "PT_TICK_SECONDS": str(tick),
        "PT_LEDGER_DIR": str(tmp / "ledger"),
        "PT_GAME_DB": str(tmp / "game.duckdb"),
        "PT_ADVANCE_SCRIPT": str(tmp / "no-advance.py"),
        "LLM_REASONS": "0", "BAND_ENABLED": "0", "TELEM_ENABLED": "0",
        "PYTHONUNBUFFERED": "1",
    })
    env.pop("PT_SERVING_DIR", None)
    with open(tmp / "server.log", "w", encoding="utf-8") as log:    # the child keeps its own handle
        return subprocess.Popen([sys.executable, "-m", "uvicorn", "game.server:app", "--port", str(port),
                                 "--host", "127.0.0.1", "--ws-max-size", "4096"],
                                cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)


def wait_up(base: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError("server exited early")
        try:
            if httpx.get(base + "/api/state", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError("server did not come up")


def nxt(node_id: int, d: str) -> int | None:
    x, y = node_id % 5, node_id // 5
    dx, dy = DIRS[d]
    if 0 <= x + dx < 5 and 0 <= y + dy < 4:
        return (y + dy) * 5 + x + dx
    return None


def plan_human(player: int, state: dict, me: dict) -> dict | None:
    """A simple, varied human: patch the best card here (naming it), else monitor, else move."""
    node = state["nodes"][me["node_id"]]
    if me["busy_ticks"] > 0:
        return None
    open_cards = sorted((c for c in node["cards"] if c["patch_state"] == "unpatched"),
                        key=lambda c: (c["priority_rank"] is None, c["priority_rank"] or 0, -c["score"]))
    t = state["tick"]
    if open_cards and node["state"] == "healthy" and t % 3 == 0:
        return {"t": "act", "player": player, "action": "PATCH", "cve": open_cards[0]["cve_id"]}
    if player == 2 and t % 5 == 1 and not node["monitored"]:
        return {"t": "act", "player": player, "action": "MONITOR"}
    order = ["E", "S", "W", "N"] if player == 1 else ["W", "N", "E", "S"]
    d = order[(t // 2) % 4]
    if nxt(me["node_id"], d) is None:
        d = next(x for x in order if nxt(me["node_id"], x) is not None)
    return {"t": "act", "player": player, "action": "MOVE", "dir": d}


async def client(url: str, player: int, inbox: list, sent: list, start: bool, done: asyncio.Event) -> None:
    async with websockets.connect(url, max_size=2**22) as ws:
        first = json.loads(await ws.recv())
        inbox.append(first)
        if start:
            await ws.send(json.dumps({"t": "control", "cmd": "start"}))
        last_tick = None
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 20))
            inbox.append(msg)
            if msg.get("t") != "state":
                continue
            if msg["phase"] == "ended":
                break
            if msg["phase"] != "running" or msg["tick"] == last_tick:
                continue
            last_tick = msg["tick"]
            me = next(a for a in msg["actors"] if a["id"] == f"p{player}")
            act = plan_human(player, msg, me)
            if act:
                await ws.send(json.dumps(act))
                sent.append((msg["tick"], me["node_id"], act))
    done.set()


def run_e2e() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="pt-e2e-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = start_server(tmp, port)
    out: dict = {"tmp": str(tmp)}
    try:
        wait_up(base, proc)
        world = httpx.get(base + "/api/world").json()
        assert world["data_source"] == "serving", f"server is not on the real serving/ data: {world['data_source']}"
        check_message(httpx.get(base + "/api/state").json())
        out["world"] = {k: world[k] for k in ("asof_date", "score_source", "n_cards")}

        inbox1, inbox2, sent1, sent2 = [], [], [], []
        d1, d2 = asyncio.Event(), asyncio.Event()

        async def both():
            url = f"ws://127.0.0.1:{port}/ws"
            await asyncio.gather(client(url, 1, inbox1, sent1, True, d1),
                                 client(url, 2, inbox2, sent2, False, d2))
        t0 = time.time()
        asyncio.run(both())
        out["match_seconds"] = round(time.time() - t0, 1)
        api_rows = httpx.get(base + "/api/ledger?limit=5000").json()
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ---- every message has the contract shape, on both sockets
    states1 = [m for m in inbox1 if m.get("t") == "state"]
    states2 = [m for m in inbox2 if m.get("t") == "state"]
    for m in states1 + states2:
        check_message(m)
    for m in inbox1 + inbox2:
        assert m.get("t") in ("state", "reason"), m.get("t")
        if m["t"] == "reason":
            assert set(m) == {"t", "ledger_id", "reason_text"}
    running = [m for m in states1 if m["phase"] in ("running", "ended") and m["match_id"] != "m-lobby"]
    mid = running[-1]["match_id"]
    match_states = [m for m in running if m["match_id"] == mid]
    assert match_states[-1]["phase"] == "ended" and match_states[-1]["tick"] == 150
    assert states2[-1]["phase"] == "ended" and states2[-1]["match_id"] == mid
    out["state_messages"] = {"p1_socket": len(states1), "p2_socket": len(states2)}

    # ---- seven actors, one row each per tick, each does something
    rows = [r for m in match_states for r in m["ledger"]]
    assert len(rows) == 150 * 7, len(rows)
    by_tick: dict[int, list] = {}
    for r in rows:
        by_tick.setdefault(r["tick"], []).append(r["actor_id"])
    assert sorted(by_tick) == list(range(150))
    assert all(sorted(v) == sorted(ACTORS) for v in by_tick.values())
    acted = {a: sorted({r["action"] for r in rows if r["actor_id"] == a} - {"WAIT"}) for a in ACTORS}
    assert all(acted[a] for a in ACTORS), acted
    out["actions_per_actor"] = acted

    # ---- human actions applied
    # The state message with tick T is the board before tick T resolves; the rows of tick T ride on
    # the message with tick T + 1. An input sent on seeing state T resolves as tick T, or one tick
    # later when it reaches the server after that tick began (a loaded machine), or on the first
    # free tick after a PATCH. So each fresh human row is matched to an input sent in the last few
    # ticks and checked against the board the SERVER had, never against the client's clock.
    row_at = {(r["tick"], r["actor_id"]): r for r in rows}
    state_at = {m["tick"]: m for m in match_states}
    CONTINUES = ("working", "patched", "restored", "noop")      # later ticks of a multi-tick action
    checked = {"MOVE": 0, "MONITOR": 0, "PATCH": 0}
    applied = 0
    for player, sent in ((1, sent1), (2, sent2)):
        aid = f"p{player}"
        for r in rows:
            if r["actor_id"] != aid or r["action"] == "WAIT" or r["outcome"] in CONTINUES:
                continue
            tick = r["tick"]
            cands = [a for t, _, a in sent if tick - 3 <= t <= tick and a["action"] == r["action"]]
            assert cands, f"{aid} did {r['action']} at tick {tick} but no such input was sent"
            before = next(a for a in state_at[tick]["actors"] if a["id"] == aid)["node_id"]
            if r["action"] == "MOVE":
                assert r["outcome"] == "moved", r
                assert r["node_id"] in [nxt(before, a["dir"]) for a in cands], (before, cands, r)
            elif r["action"] == "MONITOR":
                assert r["outcome"] == "monitoring" and r["node_id"] == before, r
                assert state_at[tick + 1]["nodes"][before]["monitored"] is True
            elif r["action"] == "PATCH":
                assert r["cve_id"] in [a["cve"] for a in cands], (cands, r)
                after = next((c for c in state_at[tick + 1]["nodes"][before]["cards"]
                              if c["cve_id"] == r["cve_id"]), None)
                if r["outcome"] in ("patching", "patched"):                # exactly that card
                    assert after is not None and after["patch_state"] in ("patching", "patched"), (r, after)
                else:
                    # refused, never misapplied: the player had walked on, or another defender took
                    # that card first (the other human resolves earlier in the same tick)
                    assert r["outcome"] == "invalid", r
                    assert after is None or after["patch_state"] != "unpatched", (r, after)
            applied += 1
            checked[r["action"]] += 1
    total_sent = len(sent1) + len(sent2)
    # a late input can be replaced by the next key press (the server keeps the latest per player)
    assert total_sent > 40 and applied >= 0.75 * total_sent, (applied, total_sent)
    assert all(checked[k] > 0 for k in checked), checked
    out["human_actions"] = {"sent": total_sent, "applied": applied, "checked": checked}

    # ---- the "explain itself" additions, end to end on the real data
    fb_seen = 0
    for m in match_states:
        for f in m["player_feedback"]:
            r = row_at[(m["tick"] - 1, f"p{f['player']}")]
            assert (r["action"], r["outcome"]) == (f["action"], f["outcome"]), (f, r)
            fb_seen += 1
    assert fb_seen >= applied                      # every fresh action got a sentence (plus completions)
    deltas = [d for m in match_states for d in m["score_deltas"]]
    final = match_states[-1]["score"]
    for team in ("red", "blue"):
        assert abs(sum(d["amount"] for d in deltas if d["team"] == team) - final[team]) < 0.01, team
        assert abs(sum(final["breakdown"][team].values()) - final[team]) < 0.02, team
    assert all(m["reveal"] is None for m in match_states[:-1])
    reveal = match_states[-1]["reveal"]
    assert reveal is not None and reveal["n_cards"] > 0, "serving/reveal.json should give a reveal"
    w = match_states[-1]["world"]
    assert w["pipeline_run_id"] and w["n_cves_visible"] and w["advance"] is None
    out["explain"] = {"feedback": fb_seen, "deltas": len(deltas), "breakdown": final["breakdown"],
                      "reveal": {k: reveal[k] for k in ("n_cards", "n_real")},
                      "fates": sorted(i["fate"] for i in reveal["items"])}

    # ---- the ledger reached DuckDB and JSONL
    con = duckdb.connect(str(tmp / "game.duckdb"), read_only=True)
    n_db = con.execute("SELECT count(*), count(DISTINCT ledger_id) FROM agent_ledger WHERE match_id = ?", [mid]).fetchone()
    db_ids = {x[0] for x in con.execute("SELECT ledger_id FROM agent_ledger WHERE match_id = ?", [mid]).fetchall()}
    con.close()
    jl = [json.loads(line) for line in (tmp / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    jl = [r for r in jl if r["match_id"] == mid]
    ws_ids = {r["ledger_id"] for r in rows}
    assert n_db == (1050, 1050) and db_ids == ws_ids
    assert len(jl) == 1050 and {r["ledger_id"] for r in jl} == ws_ids
    assert all(set(r) == set(rows[0]) for r in jl)
    assert len([r for r in api_rows if r["match_id"] == mid]) == 1050
    out["ledger"] = {"game.duckdb": n_db[0], "ledger.jsonl": len(jl), "api": len(api_rows)}
    out["final_score"] = match_states[-1]["score"]
    return out


def test_e2e_two_players_full_match():
    out = run_e2e()
    print(json.dumps(out, indent=1))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _hardening(base: str, url: str) -> dict:
    out: dict = {}
    # 1. another site's page cannot open the socket or post
    try:
        async with websockets.connect(url, origin="http://evil.example") as ws:
            await ws.recv()
        out["evil_ws"] = "accepted"
    except websockets.exceptions.InvalidStatus as exc:
        out["evil_ws"] = exc.response.status_code
    async with httpx.AsyncClient() as h:
        r = await h.post(base + "/api/world/advance", content=b"x=1",
                         headers={"Origin": "http://evil.example",
                                  "Content-Type": "application/x-www-form-urlencoded"})
        out["evil_post"] = r.status_code
        out["phase_after_evil"] = (await h.get(base + "/api/state")).json()["phase"]
    # 2. an oversized frame closes only that connection (uvicorn --ws-max-size 4096)
    async with websockets.connect(url, max_size=2**22) as ws:
        await ws.recv()
        await ws.send(json.dumps({"t": "act", "player": 1, "action": "MOVE", "dir": "N" * 8_000_000}))
        try:
            while True:
                await asyncio.wait_for(ws.recv(), 5)
        except websockets.exceptions.ConnectionClosed as exc:
            out["big_frame_close"] = exc.rcvd.code if exc.rcvd else None
    # 3. one client stops reading for 5 s: the other keeps its tick rate, the slow one is not left frozen
    good_times: list[float] = []
    async with websockets.connect(url, max_size=2**22) as good, \
            websockets.connect(url, max_size=2**22, max_queue=1) as slow:
        await good.recv()
        await slow.recv()
        await good.send(json.dumps({"t": "control", "cmd": "start"}))
        loop = asyncio.get_running_loop()
        t_end = loop.time() + 5.0
        good_tick = None
        while loop.time() < t_end:
            m = json.loads(await asyncio.wait_for(good.recv(), 5))
            if m.get("t") == "state":
                good_times.append(loop.time())
                good_tick = m["tick"]
        gaps = [b - a for a, b in zip(good_times, good_times[1:])]
        out["good_msgs_5s"] = len(good_times)
        out["good_max_gap_s"] = round(max(gaps), 3) if gaps else None
        # the slow client reads again: it either catches up to the live tick or sees a close frame
        slow_tick, slow_closed = None, None
        try:
            t2 = loop.time() + 8.0
            while loop.time() < t2:
                m = json.loads(await asyncio.wait_for(slow.recv(), 3))
                if m.get("t") == "state":
                    slow_tick = m["tick"]
                    if good_tick is not None and slow_tick >= good_tick:
                        break
        except websockets.exceptions.ConnectionClosed as exc:
            slow_closed = exc.rcvd.code if exc.rcvd else "closed"
        except asyncio.TimeoutError:
            slow_closed = "timeout (frozen)"
        out.update(slow_last_tick=slow_tick, good_tick=good_tick, slow_closed=slow_closed)
    async with httpx.AsyncClient() as h:
        await h.post(base + "/api/match/reset")
    return out


def run_hardening() -> dict:
    port = free_port()
    # sys.executable in a venv is a launcher: the real interpreter can hold server.log for a moment
    # after terminate(), so a failed delete of the temp folder must not fail the test (Windows).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        tmp = Path(d)
        proc = start_server(tmp, port, tick=0.05)
        try:
            wait_up(f"http://127.0.0.1:{port}", proc)
            return asyncio.run(_hardening(f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}/ws"))
        finally:
            proc.terminate()
            proc.wait(10)


def test_e2e_hardening_origin_big_frame_and_slow_client():
    out = run_hardening()
    print(json.dumps(out, indent=1))
    assert out["evil_ws"] == 403 and out["evil_post"] == 403 and out["phase_after_evil"] == "lobby"
    assert out["big_frame_close"] == 1009                      # message too big
    assert out["good_msgs_5s"] >= 60 and out["good_max_gap_s"] < 0.5   # 0.05 s ticks never stall
    # never "open but frozen": the slow reader is either live again or was closed (1011) and reconnects
    assert out["slow_closed"] in (1011, None), out
    if out["slow_closed"] is None:
        assert out["slow_last_tick"] >= out["good_tick"]


if __name__ == "__main__":
    print(json.dumps(run_e2e(), indent=1))
    print(json.dumps(run_hardening(), indent=1))
