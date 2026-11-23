"""The {"t":"state"} message must carry exactly the field names of docs/CONTRACTS.md."""
import json
import random
import re

from game import match, state as st

TOP = {"t", "match_id", "phase", "tick", "ticks_total", "world", "nodes", "actors", "score", "ledger", "events",
       "score_deltas", "player_feedback", "reveal"}
WORLD = {"asof_date", "score_source", "model_version", "challenger_p_at_50", "epss_p_at_50", "advancing",
         "pipeline_run_id", "gate_passed", "challenger_hits_at_50", "epss_hits_at_50", "n_positives",
         "n_candidates", "n_cves_visible", "n_new_cves_this_week", "advance"}
ADVANCE = {"stage", "stage_index", "assets_done", "assets_total", "current", "seconds", "message"}
ADVANCE_STAGES = {None, "landing", "raw", "stg", "feat", "ml", "gate", "serve", "done", "failed"}
NODE = {"id", "x", "y", "name", "value", "state", "monitored", "cards"}
CARD = {"card_id", "cve_id", "title", "score", "in_kev", "patch_state", "is_new", "technique_id", "tactic",
        "priority_rank"}
ACTOR = {"id", "name", "team", "kind", "role", "node_id", "action", "target_node", "revealed", "busy_ticks"}
SCORE = {"red", "blue", "healthy", "compromised", "isolated", "breaches_total", "breaches_in_top50",
         "rate", "breakdown"}
DELTA = {"team", "amount", "cause", "node_id", "actor_id", "cve_id"}
DELTA_CAUSES = {"blue": {"patch", "restore", "isolate_cost"}, "red": {"first_breach", "held"}}
FEEDBACK = {"player", "action", "outcome", "ok", "text"}
REVEAL = {"n_cards", "n_real", "horizon_days", "items"}
REVEAL_ITEM = {"cve_id", "title", "node_id", "node_name", "kev_date_added", "days_after_asof", "priority_rank",
               "score", "score_source", "in_kev_at_asof", "fate", "by", "tick"}
LEDGER = {"ledger_id", "match_id", "world_asof", "pipeline_run_id", "model_version", "tick", "ts", "actor_id",
          "team", "kind", "action", "node_id", "cve_id", "score_used", "score_source", "p_breach", "roll",
          "outcome", "policy", "reason_text", "llm_model", "llm_latency_ms"}
EVENT = {"kind", "text"}
EVENT_KINDS = {"breach", "patched", "restored", "drop", "campaign", "gate", "intel", "info"}
ACTIONS = {"MOVE", "SCAN", "EXPLOIT", "PIVOT", "WAIT", "PATCH", "MONITOR", "ISOLATE", "RESTORE"}


def check_message(msg):
    assert set(msg) == TOP
    assert msg["t"] == "state"
    assert msg["phase"] in ("lobby", "running", "ended")
    assert isinstance(msg["tick"], int) and msg["ticks_total"] == 150
    assert set(msg["world"]) == WORLD
    assert isinstance(msg["world"]["advancing"], bool)
    assert msg["world"]["score_source"] in ("model", "epss")
    assert len(msg["nodes"]) == 20
    for n in msg["nodes"]:
        assert set(n) == NODE
        assert n["id"] == n["y"] * 5 + n["x"] and 0 <= n["x"] < 5 and 0 <= n["y"] < 4
        assert n["state"] in ("healthy", "compromised", "isolated")
        assert isinstance(n["monitored"], bool) and n["value"] in (1, 2, 3)
        for c in n["cards"]:
            assert set(c) == CARD
            assert re.fullmatch(r"CVE-\d{4}-\d{4,}", c["cve_id"])
            assert c["patch_state"] in ("unpatched", "patching", "patched")
            assert 0.0 <= c["score"] <= 1.0
            assert isinstance(c["in_kev"], bool) and isinstance(c["is_new"], bool)
    assert [a["id"] for a in msg["actors"]] == ["red1", "red2", "blue1", "blue2", "blue3", "p1", "p2"]
    for a in msg["actors"]:
        assert set(a) == ACTOR
        assert a["team"] in ("red", "blue") and a["kind"] in ("agent", "human")
        assert a["action"] in ACTIONS
        assert isinstance(a["revealed"], bool) and isinstance(a["busy_ticks"], int)
    assert set(msg["score"]) == SCORE
    w = msg["world"]
    assert w["gate_passed"] == (w["score_source"] == "model")
    if w["advance"] is not None:
        assert set(w["advance"]) == ADVANCE and w["advance"]["stage"] in ADVANCE_STAGES
    assert set(msg["score"]["rate"]) == {"red", "blue"}
    assert msg["score"]["rate"]["red"] >= 0 and msg["score"]["rate"]["blue"] <= 0
    assert set(msg["score"]["breakdown"]) == {"blue", "red"}
    assert set(msg["score"]["breakdown"]["blue"]) == {"patch", "restore", "isolate_cost"}
    assert set(msg["score"]["breakdown"]["red"]) == {"first_breach", "held"}
    for d in msg["score_deltas"]:
        assert set(d) == DELTA and d["cause"] in DELTA_CAUSES[d["team"]]
        assert isinstance(d["amount"], (int, float)) and 0 <= d["node_id"] < 20
    for f in msg["player_feedback"]:
        assert set(f) == FEEDBACK and f["player"] in (1, 2)
        assert isinstance(f["ok"], bool) and f["text"] and f["action"] != "WAIT"
    if msg["phase"] != "ended":
        assert msg["reveal"] is None
    elif msg["reveal"] is not None:
        assert set(msg["reveal"]) == REVEAL and msg["reveal"]["n_real"] == len(msg["reveal"]["items"])
        for i in msg["reveal"]["items"]:
            assert set(i) == REVEAL_ITEM and i["fate"] in ("patched", "breached", "open")
    for r in msg["ledger"]:
        assert set(r) == LEDGER
    for e in msg["events"]:
        assert set(e) == EVENT and e["kind"] in EVENT_KINDS
    json.dumps(msg)            # serialisable as is


def test_lobby_message_shape(serving):
    s = st.new_match(serving, 1, "m-lobby", phase="lobby")
    msg = st.snapshot(s)
    check_message(msg)
    assert msg["phase"] == "lobby" and msg["ledger"] == []


def test_running_messages_shape_every_tick(serving, next_serving):
    s = st.new_match(serving, 5, "m-shape")
    rng = random.Random(5)
    kinds = set()
    while s.phase == "running":
        res = match.run_tick(s, {}, rng, new_world=next_serving if s.tick == 40 else None)
        for r in res.rows:
            r["ts"] = "2026-01-01T00:00:00.000+00:00"
        msg = st.snapshot(s, res.rows, res.events)
        check_message(msg)
        assert len(msg["ledger"]) == 7
        kinds |= {e["kind"] for e in res.events}
    assert {"drop", "campaign", "gate"} <= kinds
    assert msg["phase"] == "ended" and msg["tick"] == 150
