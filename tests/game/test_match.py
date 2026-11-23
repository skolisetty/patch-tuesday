import json
import math
import random

import duckdb

from game import match, policy, simulate, state as st
from game.ledger import Ledger


def _strip(rows):
    return [{k: v for k, v in r.items() if k != "ts"} for r in rows]


def test_same_seed_replays_exactly(serving, next_serving):
    a = simulate.run_match(serving, 11, 150, drop_tick=50, next_serving=next_serving)
    b = simulate.run_match(serving, 11, 150, drop_tick=50, next_serving=next_serving)
    assert a["score"] == b["score"] and a["actions"] == b["actions"] and a["events_log"] == b["events_log"]
    c = simulate.run_match(serving, 12, 150, drop_tick=50, next_serving=next_serving)
    assert (c["actions"], c["score"]) != (a["actions"], a["score"])


def test_rows_replay_tick_by_tick(serving):
    rows = []
    for _ in range(2):
        s = st.new_match(serving, 3, "m-det")
        rng = random.Random(3)
        out = []
        while s.phase == "running":
            out.extend(_strip(match.run_tick(s, {}, rng).rows))
        rows.append(out)
    assert rows[0] == rows[1]


def test_full_headless_match_with_ledger(serving, next_serving, tmp_path):
    led = Ledger(tmp_path / "game.duckdb", tmp_path / "ledger.jsonl", tmp_path / "reasons.jsonl")
    r = simulate.run_match(serving, 7, 150, drop_tick=60, next_serving=next_serving, ledger=led,
                           match_id="m-full")
    assert r["ticks"] == 150 and r["state"].phase == "ended"
    assert led.count("m-full") == 150 * 7
    per_tick = led.con.execute("SELECT tick, count(*) FROM agent_ledger GROUP BY tick").fetchall()
    assert len(per_tick) == 150 and all(n == 7 for _, n in per_tick)
    per_actor = dict(led.con.execute("SELECT actor_id, count(*) FROM agent_ledger GROUP BY 1").fetchall())
    assert per_actor == {a: 150 for a in st.ACTOR_IDS}
    led.close()
    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1050
    assert all(set(json.loads(x)) == set(st.LEDGER_FIELDS) for x in lines[:20])
    s = r["score"]
    assert math.isfinite(s["red"]) and math.isfinite(s["blue"]) and s["red"] >= 0
    assert s["healthy"] + s["compromised"] + s["isolated"] == 20
    assert 0 <= s["breaches_in_top50"] <= s["breaches_total"] < 60
    assert r["cards_patched"] > 10
    assert r["events"].get("drop") == 1 and r["events"].get("campaign") == 1
    # humans sent nothing, so they waited every tick
    assert r["actions"]["p1:WAIT"] == 150 and r["actions"]["p2:WAIT"] == 150
    with duckdb.connect(str(tmp_path / "game.duckdb"), read_only=True) as con:
        assert con.execute("SELECT count(DISTINCT ledger_id) FROM agent_ledger").fetchone()[0] == 1050


def test_drop_and_campaign_without_human_input(serving, next_serving):
    s = st.new_match(serving, 21, "m-drop")
    rng = random.Random(21)
    for _ in range(30):
        match.run_tick(s, {}, rng)
    before = sum(len(n.cards) for n in s.nodes)
    res = match.run_tick(s, {}, rng, new_world=next_serving)
    kinds = [e["kind"] for e in res.events]
    assert "drop" in kinds and "campaign" in kinds and "gate" in kinds
    assert s.world["asof_date"] == next_serving.world["asof_date"]
    after = sum(len(n.cards) for n in s.nodes)
    assert after > before
    assert s.campaign and any(s.campaign["targets"][r] for r in st.RED_IDS)
    new_cards = {c.card_id for n in s.nodes for c in n.cards if c.dropped_tick is not None}
    targeted = {cid for r in st.RED_IDS for _, cid in s.campaign["targets"][r]}
    assert targeted <= new_cards
    # the campaign shows up in the ledger as red rows with policy "campaign"
    seen = []
    for _ in range(25):
        rows = match.run_tick(s, {}, rng).rows
        seen += [r for r in rows if r["policy"] == "campaign"]
    assert seen and all(r["team"] == "red" for r in seen)


def test_late_world_change_waits_for_next_match(serving, next_serving):
    s = st.new_match(serving, 2, "m-late")
    rng = random.Random(2)
    while s.tick < 130:
        match.run_tick(s, {}, rng)
    res = match.run_tick(s, {}, rng, new_world=next_serving)
    assert not any(e["kind"] == "drop" for e in res.events)
    assert s.world["asof_date"] == serving.world["asof_date"]


def test_reveal_text(serving):
    s = st.new_match(serving, 1, "m-rev")
    txt = match.reveal_text(s, serving.reveal)
    assert txt and txt.startswith("Reveal:")
    assert match.reveal_text(s, {}) is None
