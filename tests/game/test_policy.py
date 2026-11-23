import collections
import random

from game import match, policy, simulate, state as st


def _free_reds(s):
    for r in st.RED_IDS:
        s.actors[r].busy_ticks = 0
        s.actors[r].busy = None


def test_every_agent_returns_action_with_reason_and_facts(serving):
    s = st.new_match(serving, 4, "m-pol")
    _free_reds(s)
    rng = random.Random(4)
    for aid in st.BLUE_AGENT_IDS + st.RED_IDS:
        a = policy.choose(s, aid, rng)
        assert a.actor_id == aid and a.kind
        assert isinstance(a.reason, str) and len(a.reason) > 10
        assert isinstance(a.facts, dict) and a.facts


def test_agents_behave_differently(serving, next_serving):
    mix = collections.defaultdict(collections.Counter)
    for seed in range(1, 6):
        r = simulate.run_match(serving, seed, 150, drop_tick=60, next_serving=next_serving)
        for k, n in r["actions"].items():
            aid, act = k.split(":")
            mix[aid][act] += n
    # reds: the Patient scans far more than the Opportunist and is the only one that pivots
    assert mix["red2"]["SCAN"] > 2 * mix["red1"]["SCAN"]
    assert mix["red1"]["PIVOT"] == 0
    # blues: Triage and Patcher patch, the Hunter monitors and never patches
    assert mix["blue3"]["MONITOR"] > 0 and mix["blue3"]["PATCH"] == 0
    assert mix["blue1"]["PATCH"] > 0 and mix["blue2"]["PATCH"] > 0
    assert mix["blue1"]["MONITOR"] == 0 and mix["blue2"]["MONITOR"] == 0


def test_red_view_hides_unscanned_cards_patch_state_and_monitoring(serving):
    s = st.new_match(serving, 9, "m-view")
    v = policy.red_view(s, "red1")
    visible = {kc.card_id for kc in v.cards}
    new_ids = {c.card_id for n in s.nodes for c in n.cards if c.is_new}
    assert visible == new_ids                          # nothing scanned yet: only public advisories
    assert all(kc.patch_seen is None for kc in v.cards)
    assert not hasattr(v.nodes[0], "monitored") and not hasattr(v, "blue_positions")
    # after a scan the red sees that node's cards, as they were at scan time
    from game.rules import record_intel
    node = s.nodes[s.actors["red1"].node_id]
    record_intel(s, "red1", node)
    node.cards[0].patch_state = "patched"              # patched after the scan: the red does not know
    v = policy.red_view(s, "red1")
    seen = {kc.card_id: kc for kc in v.cards}
    assert {c.card_id for c in node.cards} <= set(seen)
    assert seen[node.cards[0].card_id].patch_seen == "unpatched"
    # red2 has its own intel and learns nothing from red1's scan
    v2 = policy.red_view(s, "red2")
    assert {kc.card_id for kc in v2.cards} == new_ids


def test_blue_view_sees_reds_only_when_revealed(serving):
    s = st.new_match(serving, 9, "m-view")
    assert policy.blue_view(s, "blue3").revealed_reds == []
    s.actors["red1"].revealed_ticks = 2
    assert policy.blue_view(s, "blue3").revealed_reds == [("red1", s.actors["red1"].node_id)]


def test_memory_changes_later_decisions(serving):
    """The Opportunist drops a card after failing on it; the Patient will not retry without a new scan."""
    s = st.new_match(serving, 13, "m-mem")
    _free_reds(s)
    rng = random.Random(1)
    here = s.actors["red1"].node_id
    node = s.nodes[here]
    c = node.cards[0]
    c.score, c.is_new, c.patch_state = 0.6, True, "unpatched"
    a1 = policy.choose(s, "red1", random.Random(5))
    assert a1.kind == "EXPLOIT" and a1.card_id == c.card_id
    s.memory["red1"]["failed"][c.card_id] = s.tuning.opportunist_give_up
    a2 = policy.choose(s, "red1", random.Random(5))
    assert not (a2.kind == "EXPLOIT" and a2.card_id == c.card_id)

    # Patient: scanned card, one failure at a tick after the scan -> no retry until it scans again
    s2 = st.new_match(serving, 13, "m-mem2")
    _free_reds(s2)
    here = s2.actors["red2"].node_id
    node = s2.nodes[here]
    for cc in node.cards:
        cc.score, cc.patch_state = 0.0, "unpatched"
    c = node.cards[0]
    c.score = 0.8
    from game.rules import record_intel
    record_intel(s2, "red2", node)
    a = policy.choose(s2, "red2", random.Random(2))
    assert a.kind == "EXPLOIT" and a.card_id == c.card_id
    s2.memory["red2"]["failed"][c.card_id] = 1
    s2.memory["red2"]["fail_tick"] = {c.card_id: s2.tick}
    a = policy.choose(s2, "red2", random.Random(2))
    assert not (a.kind == "EXPLOIT" and a.card_id == c.card_id)


def test_learn_records_visits_and_scans(serving):
    s = st.new_match(serving, 2, "m-learn")
    rng = random.Random(2)
    for _ in range(60):
        match.run_tick(s, {}, rng)
    assert len(s.memory["blue3"]["visited"]) > 3
    assert s.memory["red2"]["scanned"]
    assert all(isinstance(k, int) for k in s.memory["red2"]["scanned"])


def test_triage_follows_priority_rank(serving):
    s = st.new_match(serving, 3, "m-tri")
    s.tuning = s.tuning.with_overrides(eps_triage=0.0)
    a = policy.choose(s, "blue1", random.Random(0))
    best = min(((c.priority_rank is None, c.priority_rank or 0, -c.score, c.card_id), c.cve_id)
               for n in s.nodes for c in n.unpatched())
    assert a.facts["cve"] == best[1]
