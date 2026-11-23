import random

import pytest

from game import rules, state as st
from game.config import TUNING
from game.rules import Action, breach_probability


# ---------------------------------------------------------------- breach formula
@pytest.mark.parametrize("s, expected", [(0.02, 0.114), (0.10, 0.469), (0.50, 0.95), (0.0, 0.0), (1.0, 0.95)])
def test_breach_worked_values(s, expected):
    assert breach_probability(s) == pytest.approx(expected, abs=0.0005)


def test_breach_uncapped_value_of_half_is_above_cap():
    assert rules.breach_base(0.5) == pytest.approx(0.984375)


def test_breach_modifiers():
    base = rules.breach_base(0.02)
    assert breach_probability(0.02, in_kev=True) == pytest.approx(base + 0.15)
    assert breach_probability(0.02, scanned=True) == pytest.approx(base + 0.10)
    assert breach_probability(0.02, in_kev=True, scanned=True) == pytest.approx(base + 0.25)
    assert breach_probability(0.02, patch_state="patching") == pytest.approx(base * 0.4)
    assert breach_probability(0.02, patch_state="patched") == 0.0
    assert breach_probability(0.02, monitored=True) == pytest.approx(base * 0.5)
    assert breach_probability(0.10, in_kev=True, scanned=True, patch_state="patching", monitored=True) == \
        pytest.approx((rules.breach_base(0.10) + 0.25) * 0.4 * 0.5)
    assert breach_probability(0.9, isolated=True) == 0.0


def test_breach_clamps_score():
    assert breach_probability(-3) == 0.0
    assert breach_probability(7) == 0.95
    assert breach_probability("0.10") == pytest.approx(0.469, abs=0.0005)


# ---------------------------------------------------------------- helpers
def _state(serving, seed=1):
    s = st.new_match(serving, seed, "m-test", phase="running")
    for r in st.RED_IDS:                 # no entry delay in unit tests
        s.actors[r].busy_ticks = 0
        s.actors[r].busy = None
    return s


def _put(s, aid, node_id):
    s.actors[aid].node_id = node_id


def _waits(s):
    return {a: Action(a, "WAIT") for a in st.ACTOR_IDS}


def _step(s, acts, seed=0):
    full = _waits(s)
    full.update(acts)
    return rules.step(s, full, random.Random(seed))


def _card(node, score=0.3, kev=False, rank=None):
    c = node.cards[0]
    c.score, c.in_kev, c.priority_rank, c.patch_state, c.attempted = score, kev, rank, "unpatched", False
    return c


# ---------------------------------------------------------------- patch
def test_patch_takes_two_ticks_and_scores_value_times_score(serving):
    s = _state(serving)
    node = s.nodes[6]
    c = _card(node, 0.4)
    _put(s, "blue1", 6)
    r = _step(s, {"blue1": Action("blue1", "PATCH", card_id=c.card_id)})
    assert c.patch_state == "patching"
    assert r.rows[[x["actor_id"] for x in r.rows].index("blue1")]["outcome"] == "patching"
    assert s.actors["blue1"].busy_ticks == 1
    before = s.score.blue
    r = _step(s, {})
    assert c.patch_state == "patched"
    assert s.score.blue - before == pytest.approx(node.value * 0.4)
    assert any(e["kind"] == "patched" for e in r.events)


def test_patch_after_an_exploit_attempt_scores_nothing(serving):
    s = _state(serving)
    node = s.nodes[6]
    c = _card(node, 0.4)
    c.attempted = True
    _put(s, "blue1", 6)
    _step(s, {"blue1": Action("blue1", "PATCH", card_id=c.card_id)})
    _step(s, {})
    assert c.patch_state == "patched" and s.score.blue == 0


def test_patch_without_card_takes_best_priority_rank(serving):
    s = _state(serving)
    node = s.nodes[11]
    assert len(node.cards) >= 2
    for i, c in enumerate(node.cards):
        c.patch_state, c.priority_rank = "unpatched", 100 + i
    node.cards[-1].priority_rank = 3
    _put(s, "p1", 11)
    act = rules.human_action(s, 1, "PATCH")
    _step(s, {"p1": act})
    assert node.cards[-1].patch_state == "patching"


def test_patched_card_cannot_breach(serving):
    s = _state(serving)
    node = s.nodes[3]
    c = _card(node, 0.99, kev=True)
    c.patch_state = "patched"
    _put(s, "red1", 3)
    r = _step(s, {"red1": Action("red1", "EXPLOIT", card_id=c.card_id)})
    row = next(x for x in r.rows if x["actor_id"] == "red1")
    assert row["p_breach"] == 0.0 and row["outcome"] == "failed"
    assert node.state == "healthy"


# ---------------------------------------------------------------- monitor / isolate / restore
def test_monitor_halves_odds_for_three_ticks(serving):
    s = _state(serving)
    node = s.nodes[3]
    c = _card(node, 0.02)
    _put(s, "blue3", 3)
    _step(s, {"blue3": Action("blue3", "MONITOR")})
    assert node.public()["monitored"] is True
    _put(s, "red1", 3)
    r = _step(s, {"red1": Action("red1", "EXPLOIT", card_id=c.card_id)}, seed=99)
    row = next(x for x in r.rows if x["actor_id"] == "red1")
    assert row["p_breach"] == pytest.approx(rules.breach_base(0.02) * 0.5, abs=1e-6)
    # three ticks of monitoring in all
    s2 = _state(serving)
    _put(s2, "blue3", 3)
    _step(s2, {"blue3": Action("blue3", "MONITOR")})
    seen = [s2.nodes[3].monitored]
    for _ in range(3):
        _step(s2, {})
        seen.append(s2.nodes[3].monitored)
    assert seen == [True, True, False, False]


def test_isolate_blocks_exploits_ejects_red_and_costs_one_per_tick(serving):
    s = _state(serving)
    node = s.nodes[3]
    c = _card(node, 0.99)
    _put(s, "blue3", 3)
    _put(s, "red1", 3)
    r = _step(s, {"blue3": Action("blue3", "ISOLATE")})
    assert node.state == "isolated"
    assert s.actors["red1"].node_id == s.actors["red1"].home
    assert s.score.blue == -1
    _put(s, "red2", 3)
    s.actors["red2"].busy_ticks = 0
    r = _step(s, {"red2": Action("red2", "EXPLOIT", card_id=c.card_id)})
    row = next(x for x in r.rows if x["actor_id"] == "red2")
    assert row["outcome"] in ("invalid", "failed") and node.state == "isolated"
    for _ in range(3):
        _step(s, {})
    assert node.state == "healthy"            # 5 ticks in all
    assert s.score.blue == -5


def test_restore_takes_three_ticks_and_scores_three(serving):
    s = _state(serving)
    node = s.nodes[6]
    node.state, node.ever_breached = "compromised", True
    _put(s, "blue3", 6)
    _step(s, {"blue3": Action("blue3", "RESTORE")})
    assert node.state == "compromised"
    _step(s, {})
    assert node.state == "compromised"
    _step(s, {})
    assert node.state == "healthy"
    assert s.score.blue == pytest.approx(3.0)


def test_restore_on_healthy_node_is_invalid(serving):
    s = _state(serving)
    _put(s, "blue3", 6)
    r = _step(s, {"blue3": Action("blue3", "RESTORE")})
    assert next(x for x in r.rows if x["actor_id"] == "blue3")["outcome"] == "invalid"


# ---------------------------------------------------------------- scoring
def test_red_scoring_first_breach_and_per_tick(serving):
    s = _state(serving)
    node = s.nodes[6]                       # DB-01, value 3
    c = _card(node, 1.0, rank=10)
    _put(s, "red1", 6)
    r = _step(s, {"red1": Action("red1", "EXPLOIT", card_id=c.card_id)}, seed=1)
    row = next(x for x in r.rows if x["actor_id"] == "red1")
    assert row["p_breach"] == 0.95
    if row["outcome"] != "breach":
        pytest.skip("seeded roll missed the 0.95 chance")
    assert node.state == "compromised"
    assert s.score.red == pytest.approx(5 + node.value)
    assert s.score.breaches_total == 1 and s.score.breaches_in_top50 == 1
    _step(s, {})
    assert s.score.red == pytest.approx(5 + 2 * node.value)


def test_second_breach_of_same_node_has_no_bonus(serving):
    s = _state(serving)
    node = s.nodes[6]
    node.ever_breached = True
    c = _card(node, 1.0)
    _put(s, "red1", 6)
    r = _step(s, {"red1": Action("red1", "EXPLOIT", card_id=c.card_id)}, seed=1)
    if next(x for x in r.rows if x["actor_id"] == "red1")["outcome"] == "breach":
        assert s.score.red == pytest.approx(node.value)


def test_move_and_grid_edges(serving):
    s = _state(serving)
    _put(s, "p1", 0)
    _step(s, {"p1": rules.human_action(s, 1, "MOVE", "N")})
    assert s.actors["p1"].node_id == 0          # off the grid: blocked
    _step(s, {"p1": rules.human_action(s, 1, "MOVE", "S")})
    assert s.actors["p1"].node_id == 5          # N is y-1, S is y+1
    _step(s, {"p1": rules.human_action(s, 1, "MOVE", "E")})
    assert s.actors["p1"].node_id == 6


def test_red_actions_rejected_for_blue_and_vice_versa(serving):
    s = _state(serving)
    r = _step(s, {"blue1": Action("blue1", "EXPLOIT"), "red1": Action("red1", "PATCH")})
    acts = {x["actor_id"]: x["action"] for x in r.rows}
    assert acts["blue1"] == "WAIT" and acts["red1"] == "WAIT"


def test_step_emits_one_row_per_actor(serving):
    s = _state(serving)
    r = _step(s, {})
    assert sorted(x["actor_id"] for x in r.rows) == sorted(st.ACTOR_IDS)
    assert s.tick == 1


def test_contract_constants():
    t = TUNING
    assert (t.breach_k, t.kev_bonus, t.recon_bonus, t.patching_mult, t.monitor_mult, t.p_cap) == \
        (6, 0.15, 0.10, 0.4, 0.5, 0.95)
    assert (t.patch_ticks, t.monitor_ticks, t.isolate_ticks, t.restore_ticks) == (2, 3, 5, 3)
    assert (t.first_breach_points, t.restore_points, t.isolate_cost, t.ticks_total) == (5, 3, 1, 150)
