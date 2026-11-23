"""The "explain itself" additions to the state message (docs/CONTRACTS.md, "Explain-itself
additions"): world extras and live advance progress, score.rate, score.breakdown, score_deltas,
player_feedback, reveal, and the plain gate sentence."""
import asyncio
import importlib.util
import json
import os
import random
import sys
import threading
import time
from pathlib import Path

import pytest

from game import config, feedback, match, progress, rules, world
from game import state as st
from test_contract import check_message

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------ score_deltas, breakdown, rate
def _scripted_humans(s, tick):
    """p1 isolates now and then, p2 restores what it stands on, both otherwise patch or walk."""
    acts = {}
    for player, aid in ((1, "p1"), (2, "p2")):
        a = s.actors[aid]
        if a.busy_ticks > 0:
            continue
        node = s.nodes[a.node_id]
        if node.is_compromised:
            kind, d = "RESTORE", None
        elif player == 1 and tick % 23 == 5:
            kind, d = "ISOLATE", None
        elif node.unpatched() and tick % 3 == 0:
            kind, d = "PATCH", None
        else:
            kind, d = "MOVE", "NESW"[(tick // 2 + player) % 4]
        acts[aid] = rules.human_action(s, player, kind, d)
    return acts


def _play(serving, seed, next_serving=None, humans=True):
    s = st.new_match(serving, seed, f"m-explain-{seed}")
    rng = random.Random(seed)
    deltas, fb, msgs = [], [], []
    while s.phase == "running":
        t = s.tick
        res = match.run_tick(s, _scripted_humans(s, t) if humans else {}, rng,
                             new_world=next_serving if (next_serving is not None and t == 40) else None)
        msg = st.snapshot(s, res.rows, res.events, deltas=res.deltas, feedback=res.feedback)
        check_message(msg)
        deltas += msg["score_deltas"]
        fb += msg["player_feedback"]
        msgs.append(msg)
    return s, deltas, fb, msgs


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11])
def test_sum_of_score_deltas_equals_the_final_scores(serving, next_serving, seed):
    s, deltas, _, msgs = _play(serving, seed, next_serving)
    assert s.phase == "ended" and deltas
    red = sum(d["amount"] for d in deltas if d["team"] == "red")
    blue = sum(d["amount"] for d in deltas if d["team"] == "blue")
    assert red == pytest.approx(s.score.red, abs=0.01)
    assert blue == pytest.approx(s.score.blue, abs=0.01)
    final = msgs[-1]["score"]
    assert red == pytest.approx(final["red"], abs=0.01) and blue == pytest.approx(final["blue"], abs=0.01)


def test_deltas_cover_every_cause_and_match_the_breakdown(serving, next_serving):
    seen = set()
    for seed in (1, 2, 3, 7, 11):
        s, deltas, _, msgs = _play(serving, seed, next_serving)
        b = msgs[-1]["score"]["breakdown"]
        by_cause = {}
        for d in deltas:
            by_cause[d["cause"]] = by_cause.get(d["cause"], 0.0) + d["amount"]
            seen.add(d["cause"])
        for team in ("blue", "red"):
            for cause, total in b[team].items():
                assert by_cause.get(cause, 0.0) == pytest.approx(total, abs=0.01), (seed, cause)
        # the breakdown adds up to the score, team by team
        assert sum(b["blue"].values()) == pytest.approx(msgs[-1]["score"]["blue"], abs=0.02)
        assert sum(b["red"].values()) == pytest.approx(msgs[-1]["score"]["red"], abs=0.02)
        assert b["blue"]["isolate_cost"] <= 0 and b["blue"]["patch"] >= 0 and b["red"]["held"] >= 0
    assert seen == {"patch", "restore", "isolate_cost", "first_breach", "held"}


# Agent-only matches on the generated mock data, played by the code as it was BEFORE the
# explain-itself additions (git HEAD of the ux-overhaul branch point): final red score, final blue
# score, breaches, breaches through top-50 cards, and a hash of every ledger row's rule fields.
# Seed 11 takes the next week's drop at tick 40. These are constants on purpose: a change to the
# rules, the scoring, a policy or the order of rng calls must fail here.
_GOLDEN_KEYS = ("tick", "actor_id", "action", "node_id", "cve_id", "score_used", "score_source",
                "p_breach", "roll", "outcome", "policy")
GOLDEN = {(5, False): (49.0, 29.8581, 4, 0, "fd2768b5f8d20081"),
          (11, True): (53.0, 31.9351, 4, 1, "df5513a029c67d3c")}


@pytest.mark.parametrize("seed,drop", sorted(GOLDEN))
def test_rules_and_scores_are_exactly_what_they_were_before_the_additions(serving, next_serving, seed, drop):
    import hashlib
    s = st.new_match(serving, seed, f"m-golden-{seed}")
    rng = random.Random(seed)
    h = hashlib.sha256()
    while s.phase == "running":
        res = match.run_tick(s, {}, rng, new_world=next_serving if (drop and s.tick == 40) else None)
        for r in res.rows:
            h.update(json.dumps([r[k] for k in _GOLDEN_KEYS], separators=(",", ":")).encode())
    got = (round(s.score.red, 4), round(s.score.blue, 4), s.score.breaches_total,
           s.score.breaches_in_top50, h.hexdigest()[:16])
    assert got == GOLDEN[(seed, drop)]


def test_breakdown_is_zero_in_the_lobby_and_a_seed_replays_exactly(serving):
    lobby = st.snapshot(st.new_match(serving, 1, "m-lobby", phase="lobby"))
    assert lobby["score"]["breakdown"] == {"blue": {"patch": 0, "restore": 0, "isolate_cost": 0},
                                           "red": {"first_breach": 0, "held": 0}}
    assert lobby["score"]["rate"] == {"red": 0, "blue": 0}
    assert lobby["score_deltas"] == [] and lobby["player_feedback"] == [] and lobby["reveal"] is None
    # same seed, same match (the comparison with the old code is the golden test above)
    a, _, _, _ = _play(serving, 5, humans=False)
    b, _, _, _ = _play(serving, 5, humans=False)
    assert (a.score.red, a.score.blue, a.score.breaches_total) == (b.score.red, b.score.blue, b.score.breaches_total)


def test_rate_is_the_points_per_tick_right_now(serving):
    s = st.new_match(serving, 1, "m-rate")
    assert st.score_rate(s) == {"red": 0, "blue": 0}
    s.nodes[3].state = "compromised"       # VPN-01, value 3
    s.nodes[0].state = "compromised"       # MAIL-01, value 2
    s.nodes[6].state = "isolated"
    s.nodes[7].state, s.nodes[7].prior_state = "isolated", "compromised"   # earns red nothing while isolated
    assert st.score_rate(s) == {"red": 5, "blue": -2}
    before = (s.score.red, s.score.blue)
    res = rules.step(s, {}, random.Random(1))
    assert s.score.red - before[0] == 5 and s.score.blue - before[1] == -2      # the rate was true
    held = [d for d in res.deltas if d["cause"] == "held"]
    assert sorted((d["node_id"], d["amount"]) for d in held) == [(0, 2), (3, 3)]
    assert all(d["actor_id"] is None and d["cve_id"] is None for d in held)
    iso = [d for d in res.deltas if d["cause"] == "isolate_cost"]
    assert sorted(d["node_id"] for d in iso) == [6, 7] and all(d["amount"] == -1 for d in iso)


def test_patch_restore_and_first_breach_deltas(serving):
    s = st.new_match(serving, 4, "m-deltas", tuning=config.TUNING.with_overrides(red_entry_delay=0))
    rng = random.Random(4)
    p1 = s.actors["p1"]
    node = s.nodes[p1.node_id]
    card = rules.best_patch_card(node)
    card.score = 0.46
    rules.step(s, {"p1": rules.human_action(s, 1, "PATCH")}, rng)
    res = rules.step(s, {}, rng)
    d = [x for x in res.deltas if x["cause"] == "patch"]
    assert d == [{"team": "blue", "amount": round(node.value * 0.46, 4), "cause": "patch", "node_id": node.id,
                  "actor_id": "p1", "cve_id": card.cve_id}]
    assert card.patched_by == "Player 1" and card.patched_tick == 1

    # a red breaches the node p2 stands on; p2 restores it
    p2 = s.actors["p2"]
    n2 = s.nodes[p2.node_id]
    c2 = n2.unpatched()[0]
    c2.score, c2.in_kev = 1.0, True
    red = s.actors["red1"]
    red.node_id, red.busy_ticks, red.busy = n2.id, 0, None
    res = rules.step(s, {"red1": rules.Action("red1", "EXPLOIT", card_id=c2.card_id)}, _AlwaysLow())
    fbd = [x for x in res.deltas if x["cause"] == "first_breach"]
    assert fbd == [{"team": "red", "amount": 5.0, "cause": "first_breach", "node_id": n2.id,
                    "actor_id": "red1", "cve_id": c2.cve_id}]
    assert [x for x in res.deltas if x["cause"] == "held"][0]["amount"] == n2.value
    assert c2.breached_by == "Opportunist" and c2.breached_tick == 2
    out = []
    for _ in range(s.tuning.restore_ticks):
        res = rules.step(s, {"p2": rules.human_action(s, 2, "RESTORE")}, rng)
        out += [x for x in res.deltas if x["cause"] == "restore"]
    assert out == [{"team": "blue", "amount": 3.0, "cause": "restore", "node_id": n2.id, "actor_id": "p2",
                    "cve_id": None}]


class _AlwaysLow(random.Random):
    def random(self):
        return 0.0


# ------------------------------------------------------------------ fog of war
def test_additions_never_name_a_hidden_red_while_a_match_runs(serving, next_serving):
    for seed in (2, 7):
        s = st.new_match(serving, seed, f"m-fogx-{seed}")
        rng = random.Random(seed)
        while s.phase == "running":
            res = match.run_tick(s, _scripted_humans(s, s.tick), rng,
                                 new_world=next_serving if s.tick == 40 else None)
            msg = st.snapshot(s, res.rows, res.events, deltas=res.deltas, feedback=res.feedback,
                              reveal={"n_cards": 1, "n_real": 0, "horizon_days": 91, "items": []})
            hidden = {a["id"] for a in msg["actors"] if a["team"] == "red" and not a["revealed"]}
            if msg["phase"] == "running":
                assert msg["reveal"] is None                  # never before the match ends
                for d in msg["score_deltas"]:
                    assert d["actor_id"] not in hidden
                    if d["team"] == "red":
                        # red points only ever sit on a machine the board already shows as breached
                        assert msg["nodes"][d["node_id"]]["state"] == "compromised"
            assert all(f["player"] in (1, 2) for f in msg["player_feedback"])
            blob = json.dumps(msg["player_feedback"])
            assert "Opportunist" not in blob and "Patient" not in blob


def test_a_delta_of_an_unrevealed_red_is_blanked(serving):
    s = st.new_match(serving, 1, "m-blank")
    d = [st.delta("red", 5, "first_breach", 3, "red1", "CVE-2099-0001")]
    s.actors["red1"].revealed_ticks = 0
    out = st.snapshot(s, deltas=d)["score_deltas"][0]
    assert out["actor_id"] is None and out["cve_id"] is None and out["node_id"] == 3 and out["amount"] == 5
    s.actors["red1"].revealed_ticks = 2
    assert st.snapshot(s, deltas=d)["score_deltas"][0]["actor_id"] == "red1"
    s.actors["red1"].revealed_ticks = 0
    s.phase = "ended"
    assert st.snapshot(s, deltas=d)["score_deltas"][0]["cve_id"] == "CVE-2099-0001"
    assert d[0]["actor_id"] == "red1"                         # the caller's list is not changed


# ------------------------------------------------------------------ player_feedback
def _fb(res, player=1):
    return [f for f in res.feedback if f["player"] == player]


def test_feedback_sentence_for_every_human_outcome(serving):
    s = st.new_match(serving, 6, "m-fb")
    rng = random.Random(6)
    t = s.tuning
    p1 = s.actors["p1"]
    seen = {}

    def do(kind, d=None, cve=None, player=1):
        res = rules.step(s, {f"p{player}": rules.human_action(s, player, kind, d, cve)}, rng)
        for f in res.feedback:
            seen[(f["action"], f["outcome"])] = f
        return _fb(res, player)

    def tick():                                        # no key pressed this tick
        res = rules.step(s, {}, rng)
        for f in res.feedback:
            seen[(f["action"], f["outcome"])] = f
        return _fb(res)

    # WAIT: nothing
    assert rules.step(s, {}, rng).feedback == []
    # MOVE moved / blocked
    p1.node_id = 0
    f = do("MOVE", "E")[0]
    assert f == {"player": 1, "action": "MOVE", "outcome": "moved", "ok": True, "text": "Moved to WEB-01."}
    p1.node_id = 0
    f = do("MOVE", "N")[0]
    assert f["outcome"] == "blocked" and f["ok"] is False
    assert f["text"] == "Can't move: that is the edge of the network."
    # PATCH patching -> (working: no entry) -> patched with points
    p1.node_id = 11                                   # DC-01, value 3
    node = s.nodes[11]
    card = rules.best_patch_card(node)
    card.score = 0.46
    f = do("PATCH")[0]
    assert f["outcome"] == "patching" and f["ok"] and f["text"] == f"Patching {card.cve_id} (2 ticks)."
    f = tick()[0]
    assert f["outcome"] == "patched" and f["ok"]
    assert f["text"] == f"Patched {card.cve_id}: +1.38 (machine value 3 x danger 46%)."
    # PATCH on a card that was already attacked: no points
    c2 = rules.best_patch_card(node)
    c2.attempted = True
    do("PATCH")
    f = tick()[0]
    assert f["text"] == f"Patched {c2.cve_id}: already attacked, no points." and f["ok"]
    # PATCH named card that is already patched; then nothing left to patch at all
    f = do("PATCH", cve=card.cve_id)[0]
    assert f["outcome"] == "invalid" and not f["ok"] and f["text"] == f"{card.cve_id} is already patched."
    for c in node.cards:
        c.patch_state = "patched"
    f = do("PATCH")[0]
    assert f["outcome"] == "invalid" and f["text"] == "Nothing left to patch on this machine."
    # PATCH that someone else closes meanwhile -> noop
    node.cards[0].patch_state = "unpatched"
    do("PATCH")
    node.cards[0].patch_state = "patched"
    f = tick()[0]
    assert f["outcome"] == "noop" and not f["ok"] and "Nothing left to patch" in f["text"]
    # MONITOR
    f = do("MONITOR")[0]
    assert f["outcome"] == "monitoring" and f["ok"] and f["text"].startswith("Monitoring DC-01 for 3 ticks")
    # ISOLATE isolated / invalid
    f = do("ISOLATE")[0]
    assert f["outcome"] == "isolated" and f["ok"] and f["text"].startswith("Isolated DC-01 for 5 ticks")
    f = do("ISOLATE")[0]
    assert f["outcome"] == "invalid" and not f["ok"] and f["text"] == "Can't isolate: this machine is already isolated."
    # RESTORE invalid / restoring -> restored / noop
    p1.node_id = 12
    f = do("RESTORE")[0]
    assert f["outcome"] == "invalid" and not f["ok"]
    assert f["text"] == "Nothing to restore: this machine is not breached."
    s.nodes[12].state = "compromised"
    f = do("RESTORE")[0]
    assert f["outcome"] == "restoring" and f["text"] == "Restoring ERP-01 (3 ticks)."
    assert tick() == []          # the middle tick ("working") says nothing
    f = tick()[0]
    assert f["outcome"] == "restored" and f["ok"] and f["text"].startswith("Restored ERP-01: +3.")
    s.nodes[12].state = "compromised"
    do("RESTORE")
    s.nodes[12].state = "healthy"                      # an agent got there first
    rules.step(s, {}, rng)
    f = tick()[0]
    assert f["outcome"] == "noop" and not f["ok"] and f["text"] == "Nothing to restore: ERP-01 was already clean."
    # player 2 gets its own entries
    f = do("MONITOR", player=2)[0]
    assert f["player"] == 2

    assert set(seen) == {("MOVE", "moved"), ("MOVE", "blocked"), ("PATCH", "patching"), ("PATCH", "patched"),
                         ("PATCH", "invalid"), ("PATCH", "noop"), ("MONITOR", "monitoring"),
                         ("ISOLATE", "isolated"), ("ISOLATE", "invalid"), ("RESTORE", "restoring"),
                         ("RESTORE", "restored"), ("RESTORE", "invalid"), ("RESTORE", "noop")}
    for f in seen.values():
        assert f["text"].endswith(".") and f["ok"] == (f["outcome"] in feedback.OK_OUTCOMES)
    assert t.patch_ticks == 2 and t.restore_ticks == 3     # the numbers quoted above


def test_feedback_for_a_named_card_that_is_not_here_or_is_being_patched(serving):
    s = st.new_match(serving, 6, "m-fb2")
    rng = random.Random(6)
    node = s.nodes[s.actors["p1"].node_id]
    res = rules.step(s, {"p1": rules.human_action(s, 1, "PATCH", cve="CVE-1999-0001")}, rng)
    assert res.feedback[0]["text"] == "Can't patch: that card is not on this machine."
    card = node.unpatched()[0]
    card.patch_state = "patching"
    res = rules.step(s, {"p1": rules.human_action(s, 1, "PATCH", cve=card.cve_id)}, rng)
    assert res.feedback[0]["text"] == f"{card.cve_id} is already being patched."


def test_agents_get_no_feedback_entries(serving):
    _, _, fb, _ = _play(serving, 3, humans=False)
    assert fb == []


# ------------------------------------------------------------------ reveal
def _ended_board(serving):
    s = st.new_match(serving, 8, "m-reveal")
    cards = [(n, c) for n in s.nodes for c in n.cards]
    (n1, patched), (n2, breached), (n3, open_), (n4, restored), (_, not_real) = cards[:5]
    patched.patch_state, patched.patched_by, patched.patched_tick = "patched", "Patcher", 44
    breached.breached, breached.breached_by, breached.breached_tick = True, "Patient", 61
    restored.breached, restored.breached_by, restored.breached_tick = True, "Opportunist", 30
    restored.patch_state = "patched"                    # closed by a RESTORE: still "breached"
    rv = {
        patched.cve_id: {"entered_kev_within_91d": True, "kev_date_added": "2026-05-14"},
        breached.cve_id: {"entered_kev_within_91d": True, "kev_date_added": "2026-04-01"},
        open_.cve_id: {"entered_kev_within_91d": True, "kev_date_added": "2026-06-20"},
        restored.cve_id: {"entered_kev_within_91d": True, "kev_date_added": "2026-03-30", "asof_date": "2026-03-29"},
        not_real.cve_id: {"entered_kev_within_91d": False, "kev_date_added": None},
    }
    return s, rv, (n1, patched), (n2, breached), (n3, open_), (n4, restored)


def test_reveal_fates_patched_breached_open(serving):
    s, rv, (n1, patched), (n2, breached), (n3, open_), (n4, restored) = _ended_board(serving)
    assert match.reveal_data(s, rv) is None             # still running: the future stays hidden
    s.phase = "ended"
    r = match.reveal_data(s, rv)
    assert r["n_cards"] == 5 and r["n_real"] == 4 and r["horizon_days"] == 91
    assert [i["kev_date_added"] for i in r["items"]] == ["2026-03-30", "2026-04-01", "2026-05-14", "2026-06-20"]
    by = {i["cve_id"]: i for i in r["items"]}
    assert (by[patched.cve_id]["fate"], by[patched.cve_id]["by"], by[patched.cve_id]["tick"]) == ("patched", "Patcher", 44)
    assert (by[breached.cve_id]["fate"], by[breached.cve_id]["by"], by[breached.cve_id]["tick"]) == ("breached", "Patient", 61)
    assert (by[open_.cve_id]["fate"], by[open_.cve_id]["by"], by[open_.cve_id]["tick"]) == ("open", None, None)
    assert by[restored.cve_id]["fate"] == "breached"
    i = by[patched.cve_id]
    assert i["node_id"] == n1.id and i["node_name"] == n1.name and i["title"] == patched.title
    assert i["days_after_asof"] == 53                   # 2026-03-22 -> 2026-05-14
    assert by[restored.cve_id]["days_after_asof"] == 1  # its own asof_date stamp wins
    assert i["priority_rank"] == patched.priority_rank and i["score"] == round(patched.score, 4)
    assert i["score_source"] == patched.score_source and i["in_kev_at_asof"] == patched.in_kev
    msg = st.snapshot(s, reveal=r)
    check_message(msg)
    assert msg["reveal"] == r


def test_reveal_is_null_without_a_reveal_file_and_empty_when_nothing_was_real(serving):
    s = st.new_match(serving, 8, "m-reveal0")
    s.phase = "ended"
    assert match.reveal_data(s, {}) is None
    assert st.snapshot(s)["reveal"] is None
    cve = s.nodes[0].cards[0].cve_id
    r = match.reveal_data(s, {cve: {"entered_kev_within_91d": False, "kev_date_added": None}})
    assert r == {"n_cards": 1, "n_real": 0, "horizon_days": 91, "items": []}
    r = match.reveal_data(s, {cve: {"entered_kev_within_91d": True, "kev_date_added": "not a date"}})
    assert r["items"][0]["days_after_asof"] is None     # a bad date never raises


def test_reveal_after_a_simulated_match_agrees_with_the_info_sentence(serving):
    s, _, _, _ = _play(serving, 3)
    rv = dict(serving.reveal)
    r = match.reveal_data(s, rv)
    text = match.reveal_text(s, rv)
    assert r is not None and text.startswith(f"Reveal: {r['n_real']} of {r['n_cards']} cards")
    for i in r["items"]:
        if i["fate"] == "patched" and i["by"] is not None:
            assert isinstance(i["tick"], int)


# ------------------------------------------------------------------ gate sentence
def test_gate_text_is_plain_and_falls_back_to_the_old_wording():
    w = {"asof_date": "2026-03-29", "score_source": "model", "model_version": "hgb-2026-03-29",
         "challenger_p_at_50": 0.04, "epss_p_at_50": 0.0, "challenger_hits_at_50": 2, "epss_hits_at_50": 0,
         "n_positives": 32}
    assert match.gate_text(w) == ("Gate passed: our model put 2 of the 32 soon-to-be-exploited bugs in its top 50; "
                                  "the EPSS baseline put 0. Our model (hgb-2026-03-29) now sets the danger scores.")
    b = dict(w, score_source="epss", model_version=None, challenger_hits_at_50=0, epss_hits_at_50=1, n_positives=41)
    assert match.gate_text(b) == "Gate blocked: our model 0, EPSS baseline 1 (of 41). EPSS stays."
    old = dict(w, challenger_hits_at_50=None)
    assert match.gate_text(old) == ("Gate passed for 2026-03-29: our model (hgb-2026-03-29) now sets the danger "
                                    "scores (hits in top 50: our model 4%, EPSS baseline 0%).")
    assert match.gate_text({"asof_date": "2026-03-29", "score_source": "epss"}) == (
        "Gate blocked for 2026-03-29: the EPSS baseline sets the danger scores (hit counts not available).")


# ------------------------------------------------------------------ world extras
def test_world_extra_fields_with_mock_data(serving, next_serving):
    w = st.snapshot(st.new_match(serving, 1, "m-w", phase="lobby"))["world"]
    assert w["pipeline_run_id"] == "mock-2026-03-22" and w["gate_passed"] is False
    assert (w["challenger_hits_at_50"], w["epss_hits_at_50"], w["n_positives"]) == (6, 8, 41)
    assert w["n_candidates"] == 250000 and w["n_cves_visible"] == 251000 and w["n_new_cves_this_week"] == 900
    assert w["advance"] is None
    w2 = st.snapshot(st.new_match(next_serving, 1, "m-w2", phase="lobby"))["world"]
    assert w2["gate_passed"] is True and w2["score_source"] == "model"


def test_world_extra_fields_are_null_safe(tmp_path):
    (tmp_path / "world.json").write_text(json.dumps([{"asof_date": "2026-03-22"}]), encoding="utf-8")
    (tmp_path / "attack_cards.json").write_text(json.dumps([{"card_id": 1, "cve_id": "CVE-2099-0001"}]),
                                                encoding="utf-8")
    d = world.load_serving(tmp_path)
    msg = st.snapshot(st.new_match(d, 1, "m-null", phase="lobby"), advancing=True,
                      advance=progress.empty_progress())
    check_message(msg)
    w = msg["world"]
    for k in ("pipeline_run_id", "challenger_hits_at_50", "epss_hits_at_50", "n_positives", "n_candidates",
              "n_cves_visible", "n_new_cves_this_week", "model_version", "challenger_p_at_50"):
        assert w[k] is None, k
    assert w["gate_passed"] is False and w["score_source"] == "epss"
    assert w["advance"]["stage"] is None and w["advance"]["assets_total"] is None
    json.dumps(msg)
    # a world dict with nothing in it at all
    assert st.world_public({}, False)["gate_passed"] is None


# ------------------------------------------------------------------ advance progress: the parser
def test_stage_of():
    assert progress.stage_of("landing.kev") == "landing"
    assert progress.stage_of("raw.epss") == "raw" and progress.stage_of("stg.cve_technique") == "stg"
    assert progress.stage_of("feat.snapshot_features") == "feat"
    assert progress.stage_of("ml.challenger_scores") == "ml"
    assert progress.stage_of("ml.eval_metrics") == "gate"
    assert progress.stage_of("serve.export_world") == "serve" and progress.stage_of("sim.reveal") == "serve"
    assert progress.stage_of("other.thing") is None


def test_parser_on_a_real_week_log():
    p = progress.parse_log(_fixture("advance_week.log"))
    assert p["stage"] == "serve" and p["stage_index"] == 6
    assert p["assets_done"] == 26 and p["assets_total"] == 26
    assert p["current"] == "serve.export_world"
    assert set(p) == {"stage", "stage_index", "assets_done", "assets_total", "current", "seconds", "message"}


def test_parser_follows_a_real_log_line_by_line():
    """Feed the real log one line at a time: the stage only ever moves forward, the three
    bookkeeping assets Bruin runs first do not light "ml"/"feat" early, and ml.eval_metrics is
    the gate step."""
    p = progress.AdvanceProgress()
    assert p.snapshot()["stage"] is None                 # nothing read yet
    stages, at = [], {}
    for line in _fixture("advance_week.log").splitlines(keepends=True):
        p.feed(line)
        snap = p.snapshot()
        if not stages or stages[-1] != snap["stage"]:
            stages.append(snap["stage"])
        if snap["current"] and snap["current"] not in at:
            at[snap["current"]] = (snap["stage"], snap["assets_done"])
    assert stages == ["landing", "raw", "feat", "ml", "gate", "serve"]
    assert at["landing.attack_techniques"] == ("landing", 0)
    assert at["ml.promotions"] == ("landing", 6)         # runs 7th, before raw.*: stage not moved
    assert at["raw.world_clock"] == ("raw", 7)
    assert at["feat.snapshots"][0] == "raw" and at["ml.retract"][0] == "raw"
    assert at["feat.snapshot_features"] == ("feat", 13)
    assert at["stg.cve_technique"][0] == "feat"          # never goes back
    assert at["ml.challenger_scores"][0] == "ml"
    assert at["ml.eval_metrics"] == ("gate", 17)
    assert at["ml.sensitivity"][0] == "gate" and at["ml.promote"][0] == "gate"
    assert at["serve.patch_priority"][0] == "serve" and at["sim.reveal"][0] == "serve"
    assert p.completed == "successfully" and p.exit_code == 0 and p.failed == [] and p.commands == 1
    assert "serve.export_world" not in p.snapshot()["message"] or "finished" in p.snapshot()["message"]


def test_parser_is_the_same_for_any_chunking():
    text = _fixture("advance_week.log")
    whole = progress.parse_log(text)
    for size in (1, 7, 100, 4096):
        p = progress.AdvanceProgress()
        for i in range(0, len(text), size):
            p.feed(text[i:i + size])
        assert p.snapshot() == whole, size
    # a line cut in half is not acted on until its newline arrives
    p = progress.AdvanceProgress()
    p.feed("[13:24:43] Running:  landing.att")
    assert p.snapshot()["current"] is None
    p.feed("ack_techniques\r\n")
    assert p.snapshot()["current"] == "landing.attack_techniques"


def test_parser_on_a_real_gate_blocked_log_with_the_epss_rebuild():
    text = _fixture("advance_gate_blocked.log")
    main, _, rebuild = text.partition("\n$ ")
    p = progress.AdvanceProgress()
    p.feed(main + "\n")
    assert p.gate_failed and p.completed == "with failures" and p.exit_code == 1
    assert p.failed == ["ml.eval_metrics:custom-check:challenger_beats_epss_on_precision@50"]
    snap = p.snapshot()
    assert snap["stage"] == "gate" and snap["assets_total"] == 26 and snap["assets_done"] == 19
    p.feed("$ " + rebuild)
    snap = p.snapshot()
    assert p.commands == 2
    assert snap["stage"] == "serve" and snap["assets_total"] == 6 and snap["assets_done"] == 6
    assert snap["current"] == "serve.export_world" and "EPSS baseline" in snap["message"]
    assert progress.parse_log(text) == snap


def test_parser_on_a_real_log_where_an_upstream_check_failed():
    p = progress.AdvanceProgress()
    p.feed(_fixture("advance_check_failed.log"))
    assert p.failed == ["feat.snapshots:custom-check:every_snapshot_is_a_sunday"] and not p.gate_failed
    snap = p.snapshot()
    assert snap["stage"] in ("raw", "stg") and snap["assets_done"] == 14 and p.exit_code == 1
    f = progress.final_progress(snap, False)
    assert f["stage"] == "failed" and f["stage_index"] == snap["stage_index"]
    d = progress.final_progress(snap, True, "ok")
    assert d["stage"] == "done" and d["stage_index"] == 6 and d["current"] is None


def test_parser_never_forwards_the_command_line_or_odd_names():
    p = progress.AdvanceProgress()
    p.feed("$ cd /mnt/c/Users/someone/secret-path && bruin run x\n"
           "[01:00:00] Running:  ../../etc/passwd\n"
           "[01:00:00] Running:  <script>alert(1)</script>\n"
           "garbage \x00 line\n[01:00:01] Running:  raw.cves\n")
    snap = p.snapshot()
    assert snap["current"] == "raw.cves" and snap["stage"] == "raw"
    assert "someone" not in json.dumps(snap)


def test_log_tail_reads_a_growing_file_and_survives_a_missing_folder(tmp_path):
    lines = _fixture("advance_week.log").splitlines(keepends=True)
    tail = progress.LogTail(tmp_path / "nope")
    assert tail.poll()["stage"] is None                  # no folder: stage null, no exception
    d = tmp_path / "logs" / "advance"
    d.mkdir(parents=True)
    old = d / "20200101-000000-week-old.log"
    old.write_text("[01:00:00] Running:  serve.export_world\n", encoding="utf-8")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    tail = progress.LogTail(d)
    assert tail.poll()["stage"] is None                  # an older run's log is not this advance
    f = d / "20261123-132440-week-2026-03-29.log"
    with open(f, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines[:20]))
        fh.flush()
        first = tail.poll()
        assert first["stage"] == "landing" and first["current"] == "landing.cwe_attack_map"
        fh.write("".join(lines[20:300]))
        fh.flush()
        mid = tail.poll()
        assert mid["assets_done"] > first["assets_done"] and mid["stage_index"] >= first["stage_index"]
        fh.write("".join(lines[300:]))
    assert tail.poll() == progress.parse_log("".join(lines))


def test_log_tail_ignores_a_log_that_ended_a_moment_ago_and_follows_the_newest(tmp_path):
    """Advance week clicked again right after a run ended: the earlier log was written within
    the last second, but it is not the log of this advance."""
    lines = _fixture("advance_week.log").splitlines(keepends=True)
    d = tmp_path / "logs" / "advance"
    d.mkdir(parents=True)
    prev = d / "20261123-132440-week-2026-03-29.log"
    prev.write_text("".join(lines), encoding="utf-8", newline="")       # just written: mtime is now
    tail = progress.LogTail(d)
    assert tail.poll()["stage"] is None and tail.path is None            # not latched onto the old run
    assert tail.poll()["stage"] is None
    new = d / "20261123-132545-week-2026-04-05.log"
    new.write_text("".join(lines[:20]), encoding="utf-8", newline="")
    first = tail.poll()
    assert tail.path == new and first["stage"] == "landing" and first["assets_done"] < 26
    # a still newer log (the tail was made before advance.py created its own file) takes over
    newer = d / "20261123-132546-week-2026-04-05.log"
    newer.write_text("[01:00:01] Running:  raw.cves\n", encoding="utf-8", newline="")
    os.utime(newer, (time.time() + 5, time.time() + 5))
    snap = tail.poll()
    assert tail.path == newer and snap["current"] == "raw.cves" and snap["assets_done"] == 0


# ------------------------------------------------------------------ advance.py streams its log
def _load_advance():
    spec = importlib.util.spec_from_file_location("advance_under_test", ROOT / "scripts" / "advance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_advance_py_streams_the_log_while_the_command_runs(tmp_path):
    adv = _load_advance()
    log = tmp_path / "logs" / "advance" / "x.log"
    child = ("import sys, time\n"
             "print('[01:00:00] Running:  landing.kev', flush=True)\n"
             "time.sleep(1.5)\n"
             "print('[01:00:02] Finished: landing.kev (1.5s)', flush=True)\n"
             "sys.exit(3)\n")
    seen = {}

    def watch():
        t0 = time.time()
        while time.time() - t0 < 1.2:
            if log.is_file() and "Running:  landing.kev" in log.read_text(encoding="utf-8"):
                seen["live"] = "Finished" not in log.read_text(encoding="utf-8")
                return
            time.sleep(0.05)
    th = threading.Thread(target=watch)
    th.start()
    rc, out = adv.stream([sys.executable, "-c", child], log, "$ fake command\n")
    th.join()
    assert seen.get("live") is True                      # the first line was on disk before the end
    assert rc == 3 and "Running:  landing.kev" in out and "Finished: landing.kev" in out
    text = log.read_text(encoding="utf-8")
    assert text.startswith("$ fake command\n") and text.rstrip().endswith("[exit 3]")
    # byte for byte the file the old all-at-once writer produced (text mode, as before)
    old = f"$ fake command\n{out}\n[exit 3]\n"
    assert log.read_bytes() == old.replace("\n", os.linesep).encode("utf-8")
    p = progress.parse_log(text)
    assert p["assets_done"] == 1 and p["stage"] == "landing"
    # without a log file it still returns the output
    rc, out = adv.stream([sys.executable, "-c", "print('hi')"], None)
    assert rc == 0 and out.strip() == "hi"


def test_advance_py_last_line_contract_is_unchanged():
    adv = _load_advance()
    r = adv.result(True, {"asof_date": "2026-03-29", "score_source": "model", "challenger_p_at_50": 0.04,
                          "epss_p_at_50": 0.0}, "m")
    assert list(r) == ["ok", "asof_date", "score_source", "challenger_p_at_50", "epss_p_at_50", "gate_passed",
                       "message"]
    assert r["gate_passed"] is True


# ------------------------------------------------------------------ WorldManager publishes progress
def _fake_advance(tmp_path, serving_dir, ok=True, with_log=True):
    """A stand-in advance.py that streams part of a real Bruin log the way the real one does."""
    script = tmp_path / "scripts" / "advance.py"
    script.parent.mkdir(exist_ok=True)
    src = (
        "import json, pathlib, time, sys\n"
        f"lines = pathlib.Path({str(FIX / 'advance_week.log')!r}).read_text(encoding='utf-8').splitlines(keepends=True)\n"
        f"root = pathlib.Path({str(tmp_path)!r})\n"
        f"with_log = {with_log!r}\n"
        "if with_log:\n"
        "    (root / 'logs' / 'advance').mkdir(parents=True, exist_ok=True)\n"
        "    fh = open(root / 'logs' / 'advance' / 'now-week.log', 'a', encoding='utf-8', newline='')\n"
        "    for i in range(0, len(lines), 60):\n"
        "        fh.write(''.join(lines[i:i + 60])); fh.flush(); time.sleep(0.12)\n"
        "    fh.close()\n"
        "else:\n"
        "    time.sleep(0.6)\n"
        f"d = pathlib.Path({str(serving_dir)!r})\n"
        f"ok = {ok!r}\n"
        "if ok:\n"
        "    (d / 'world.json').write_text(json.dumps([{'asof_date': '2026-03-29', 'score_source': 'model'}]))\n"
        "print(json.dumps({'ok': ok, 'asof_date': '2026-03-29' if ok else '2026-03-22',\n"
        "                  'score_source': 'model', 'gate_passed': ok, 'message': 'done' if ok else 'boom'}))\n"
        "sys.exit(0 if ok else 1)\n")
    script.write_text(src, encoding="utf-8")
    return script


def _serving(tmp_path):
    d = tmp_path / "serving"
    d.mkdir()
    (d / "world.json").write_text(json.dumps([{"asof_date": "2026-03-22", "score_source": "epss"}]), encoding="utf-8")
    (d / "attack_cards.json").write_text(json.dumps([{"card_id": 1, "cve_id": "CVE-2099-0001"}]), encoding="utf-8")
    return d


@pytest.mark.parametrize("ok", [True, False])
def test_world_manager_reports_live_progress_then_done_or_failed(tmp_path, mock_dir, ok):
    serving_dir = _serving(tmp_path)
    script = _fake_advance(tmp_path, serving_dir, ok=ok)

    async def go():
        wm = world.WorldManager(serving_dir, mock_dir, advance_script=script, progress_seconds=0.03)
        assert wm.advance_progress() is None
        wm.start_advance()
        seen = []
        while wm.advancing:
            p = wm.advance_progress()
            if p is not None:
                seen.append(p)
            await asyncio.sleep(0.03)
        await wm.wait_advance()
        return wm, seen
    wm, seen = asyncio.run(go())
    assert seen[0]["stage"] is None and seen[0]["assets_done"] == 0          # before the log exists
    stages = [p["stage"] for p in seen if p["stage"] is not None]
    assert "landing" in stages and stages[-1] == "serve"
    idx = [p["stage_index"] for p in seen if p["stage_index"] is not None]
    assert idx == sorted(idx)                                                # only forward
    assert max(p["assets_done"] for p in seen) >= 20 and seen[-1]["assets_total"] == 26
    assert seen[-1]["seconds"] > seen[0]["seconds"] >= 0
    assert wm.advance_progress() is None                                     # over: null again
    f = wm.final_progress
    assert f["stage"] == ("done" if ok else "failed") and f["assets_done"] == 26 and f["seconds"] > 0


def test_world_manager_progress_when_the_log_never_appears(tmp_path, mock_dir):
    serving_dir = _serving(tmp_path)
    script = _fake_advance(tmp_path, serving_dir, with_log=False)

    async def go():
        wm = world.WorldManager(serving_dir, mock_dir, advance_script=script, progress_seconds=0.03)
        wm.start_advance()
        seen = []
        while wm.advancing:
            seen.append(wm.advance_progress())
            await asyncio.sleep(0.05)
        return wm, [p for p in seen if p is not None], await wm.wait_advance()
    wm, seen, result = asyncio.run(go())
    assert result["ok"] and seen
    assert all(p["stage"] is None and p["stage_index"] is None and p["current"] is None for p in seen)
    assert seen[-1]["seconds"] > 0 and wm.final_progress["stage"] == "done"


# ------------------------------------------------------------------ the server puts it all on the wire
@pytest.fixture
def client(tmp_path, mock_dir, monkeypatch):
    from fastapi.testclient import TestClient
    serving_dir = tmp_path / "serving"                    # absent at start: the mock data is used
    monkeypatch.setattr(config, "SERVING_DIR", serving_dir)
    monkeypatch.setattr(config, "MOCK_DIR", mock_dir)
    monkeypatch.setattr(config, "GAME_DB", tmp_path / "game.duckdb")
    monkeypatch.setattr(config, "LEDGER_JSONL", tmp_path / "ledger" / "ledger.jsonl")
    monkeypatch.setattr(config, "REASONS_JSONL", tmp_path / "ledger" / "reasons.jsonl")
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(config, "ADVANCE_SCRIPT", tmp_path / "scripts" / "advance.py")
    monkeypatch.setenv("TICK_SECONDS", "0.02")
    from game import server
    monkeypatch.setattr(server, "ADVANCE_HEARTBEAT_SECONDS", 0.05)
    with TestClient(server.app) as c:
        yield c


def _states(ws, until, limit=2000):
    out = []
    for _ in range(limit):
        m = json.loads(ws.receive_text())
        if m.get("t") != "state":
            continue
        out.append(m)
        if until(m):
            return out
    raise AssertionError("the expected state message never came")


def test_server_match_carries_deltas_feedback_and_keeps_the_reveal(client):
    with client.websocket_connect("/ws") as ws:
        first = _states(ws, lambda m: True)[0]
        check_message(first)
        assert first["reveal"] is None and first["world"]["advance"] is None
        assert client.post("/api/match/start?seed=3").status_code == 200
        ws.send_text(json.dumps({"t": "act", "player": 1, "action": "MOVE", "dir": "N"}))
        ws.send_text(json.dumps({"t": "act", "player": 2, "action": "PATCH"}))
        msgs = _states(ws, lambda m: m["phase"] == "ended")
    for m in msgs:
        check_message(m)
    end = msgs[-1]
    fb = [f for m in msgs for f in m["player_feedback"]]
    assert any(f["player"] == 1 and f["action"] == "MOVE" for f in fb)
    assert any(f["player"] == 2 and f["action"] == "PATCH" for f in fb)
    deltas = [d for m in msgs for d in m["score_deltas"]]
    assert sum(d["amount"] for d in deltas if d["team"] == "red") == pytest.approx(end["score"]["red"], abs=0.01)
    assert sum(d["amount"] for d in deltas if d["team"] == "blue") == pytest.approx(end["score"]["blue"], abs=0.01)
    assert all(m["reveal"] is None for m in msgs[:-1])
    assert end["reveal"] is not None and end["reveal"]["horizon_days"] == 91
    assert any(e["text"].startswith("Reveal:") for e in end["events"])      # the INFO sentence stays
    # /api/state keeps the reveal after the match has ended, also after a later publish
    assert client.get("/api/state").json()["reveal"] == end["reveal"]
    assert client.post("/api/world/advance").status_code == 503   # no script: publishes an info event
    time.sleep(0.2)
    again = client.get("/api/state").json()
    assert again["phase"] == "ended" and again["reveal"] == end["reveal"]
    assert again["score_deltas"] == [] and again["player_feedback"] == []
    assert client.post("/api/match/reset").json()["phase"] == "lobby"
    assert client.get("/api/state").json()["reveal"] is None


def test_server_forgets_the_closing_progress_of_an_advance_once_delivered(client):
    """A failed advance leaves stage "failed" behind. A later world change this server did not
    start (advance.py run from a terminal) must not be published with that old object."""
    from game import server
    g = server.game
    sent = []

    async def fake_broadcast(text):
        sent.append(json.loads(text))
    g.manager.broadcast = fake_broadcast
    failed = progress.final_progress(progress.empty_progress(), False)

    async def result(res):
        g._on_advance_result(res)
        await asyncio.sleep(0.01)

    g.world.final_progress = dict(failed)
    asyncio.run(result({"ok": False, "message": "boom"}))
    assert sent[-1]["world"]["advance"]["stage"] == "failed"            # delivered once
    assert g.world.final_progress is None
    g.pending_world = g.world.current                                   # an out-of-band new week waits
    assert g.advance_public() is None and g.snapshot()["world"]["advance"] is None
    g.pending_world = None

    # a finished advance whose week still waits for the next tick keeps "done" until it is dealt
    done = progress.final_progress(progress.empty_progress(), True)
    g.world.final_progress = dict(done)
    g.pending_world = g.world.current
    asyncio.run(result({"ok": True, "asof_date": "2026-03-29", "score_source": "model"}))
    assert sent[-1]["world"]["advance"]["stage"] == "done"
    assert "data as of 2026-03-29, danger set by our model" in sent[-1]["events"][0]["text"]
    assert g.advance_public()["stage"] == "done"
    g.pending_world = None
    g._advance_delivered()
    assert g.world.final_progress is None and g.advance_public() is None


def test_event_texts_use_the_screen_words(serving, next_serving):
    """Banner, flash and events strip: machine, danger, breach chance; never node, score, odds."""
    import re
    texts = []
    for seed in (1, 2, 3, 11):
        s = st.new_match(serving, seed, f"m-words-{seed}")
        rng = random.Random(seed)
        while s.phase == "running":
            res = match.run_tick(s, {}, rng, new_world=next_serving if s.tick == 40 else None)
            texts += [(e["kind"], e["text"]) for e in res.events]
    kinds = {k for k, _ in texts}
    assert {"gate", "drop", "campaign", "breach", "patched"} <= kinds
    banned = re.compile(r"\b(nodes?|odds|scored|score \d|rank \d|P@50|precision)\b", re.I)
    for kind, text in texts:
        assert not banned.search(text), (kind, text)
    drop = next(t for k, t in texts if k == "drop")
    assert re.search(r"new cards? dropped on \d+ machines?; danger set by (our model|the EPSS baseline)\.$", drop)
    camp = next(t for k, t in texts if k == "campaign")
    assert re.search(r"\(top: CVE-[\d-]+, danger \d+(\.\d)?%, on [A-Z0-9-]+\)", camp)
    breach = next(t for k, t in texts if k == "breach")
    assert re.search(r"\(breach chance \d+%, roll 0\.\d\d\)", breach)


def test_server_publishes_advance_progress_in_the_lobby(client, tmp_path):
    serving_dir = _serving(tmp_path)                        # the real-serving folder appears
    _fake_advance(tmp_path, serving_dir)
    time.sleep(1.3)                                         # the watcher (1 s) loads it
    assert client.get("/api/world").json()["asof_date"] == "2026-03-22"
    with client.websocket_connect("/ws") as ws:
        _states(ws, lambda m: True)
        assert client.post("/api/world/advance").status_code == 202
        msgs = _states(ws, lambda m: (m["world"]["advance"] or {}).get("stage") in ("done", "failed"))
    for m in msgs:
        check_message(m)
    adv = [m["world"]["advance"] for m in msgs if m["world"]["advance"] is not None]
    live = [a for a in adv if a["stage"] not in (None, "done", "failed")]
    assert live, "no live progress reached the socket"
    assert all(m["world"]["advancing"] for m in msgs if (m["world"]["advance"] or {}).get("stage") in
               ("landing", "raw", "stg", "feat", "ml", "gate", "serve"))
    assert adv[-1]["stage"] == "done" and adv[-1]["stage_index"] == 6 and adv[-1]["assets_done"] == 26
    assert any("Advance finished" in e["text"] for e in msgs[-1]["events"])
    idx = [a["stage_index"] for a in adv if a["stage_index"] is not None]
    assert idx == sorted(idx)
    time.sleep(0.3)
    w = client.get("/api/world").json()
    assert w["advance"] is None and w["advancing"] is False and w["asof_date"] == "2026-03-29"
