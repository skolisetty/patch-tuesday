"""Call for backup (game/backup.py): the analysis, the specialists, the flow. All offline."""
import copy
import hashlib
import json
import random

import pytest

from game import backup as bk, match, policy, rules, simulate, state as st

KEYS = ("tick", "actor_id", "action", "node_id", "cve_id", "score_used", "p_breach", "roll", "outcome", "policy")
RED_NAMES = ("Opportunist", "Patient")


# --------------------------------------------------------------------------- helpers
def fresh(serving, seed=1, clear=True):
    s = st.new_match(serving, seed, f"m-bk-{seed}")
    if clear:
        for n in s.nodes:
            n.cards = []
    return s


def add_card(s, node_id, score, kev=False, rank=None, state="unpatched"):
    c = st.Card(card_id=s.next_card_id, cve_id=f"CVE-T-{s.next_card_id}", title="t", score=score,
                in_kev=kev, priority_rank=rank, patch_state=state)
    s.next_card_id += 1
    s.nodes[node_id].cards.append(c)
    return c


def row(aid, tick, action="WAIT", outcome="waiting", node=0, cve=None):
    team = "red" if aid.startswith("red") else "blue"
    return {"actor_id": aid, "team": team, "kind": "human" if aid.startswith("p") else "agent", "tick": tick,
            "action": action, "outcome": outcome, "node_id": node, "cve_id": cve}


def breach(tick, node, monitored=False, open_ticks=12, untouched=True, rank=None, first=True):
    return {"tick": tick, "node": node, "cve": f"CVE-B-{tick}", "rank": rank, "monitored": monitored,
            "open_ticks": open_ticks, "untouched": untouched, "first": first}


def digest(rows):
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps([r[k] for k in KEYS], separators=(",", ":")).encode())
    return h.hexdigest()


def play(serving, seed, mode="off", **kw):
    rows = []
    r = simulate.run_match(serving, seed, backup=mode, on_tick=lambda s, res: rows.extend(res.rows), **kw)
    return r, rows


def tick_to(flow, s, upto, rows=None):
    """Advance the clock tick by tick (no rules run), collecting what the flow says."""
    out = []
    for t in range(s.tick + 1, upto + 1):
        s.tick = t
        out += [(t, x) for x in flow.on_tick(s, [], rows or [])]
    return out


CFG = bk.BackupConfig(checkpoint_tick=40, wait_ticks=15, gap_ticks=2, report_gap_ticks=8)


# --------------------------------------------------------------------------- feature off
def test_feature_off_leaves_the_state_message_and_the_actors_alone(serving):
    s = st.new_match(serving, 3, "m-off")
    rng = random.Random(3)
    for _ in range(30):
        res = match.run_tick(s, {}, rng)
    msg = st.snapshot(s, res.rows, res.events)
    assert "backup" not in msg and len(msg["actors"]) == 7
    assert all("reinforcement" not in a for a in msg["actors"])
    assert st.actor_ids(s) is st.ACTOR_IDS and st.resolve_order(s) is st.RESOLVE_ORDER
    assert st.blue_agent_ids(s) is st.BLUE_AGENT_IDS
    assert s.backup is None and s.pending_events == []
    assert {"coach", "backup"} <= st.EVENT_KINDS


@pytest.mark.parametrize("seed", [2, 5])
def test_a_flow_that_never_spawns_changes_nothing_in_the_match(serving, seed):
    """Coach analysing and being told "no" must not touch a rule, a policy or the random stream."""
    off, rows_off = play(serving, seed)
    no, rows_no = play(serving, seed, "decline")
    assert digest(rows_off) == digest(rows_no)
    assert off["score"] == no["score"]
    assert no["backup"]["status"] == "declined" and no["backup"]["actor_id"] is None
    assert len(no["state"].actors) == 7


# --------------------------------------------------------------------------- analysis: hand counts
def test_slow_response_numbers_match_a_hand_count(serving):
    s = fresh(serving)
    s.tick = 30
    s.score.red, s.score.held, s.score.first_breach, s.score.blue = 46.0, 31.0, 15.0, 12.0
    s.track["breaches"] = [breach(11, 6), breach(14, 0), breach(20, 11)]
    s.track["restores"] = [{"tick": 19, "node": 6, "actor": "blue3"}]
    s.nodes[0].state = s.nodes[11].state = "compromised"
    s.track["red_ticks"], s.track["hidden_ticks"] = 40, 30
    s.track["detections"] = [{"tick": 16, "node": 6}]
    add_card(s, 3, 0.9)                                     # one hot card: not an exposure problem
    a = bk.analyse(s, [])
    n = a.numbers
    # node 6: breached 11 -> restored 19 = 8 ticks; node 0: 14 -> now(30) = 16; node 11: 20 -> 30 = 10
    assert [e["ticks"] for e in bk.breach_episodes(s)] == [8, 16, 10]
    assert n["avg_breach_ticks"] == 11.3 and n["longest_breach_ticks"] == 16
    assert n["held"] == 31 and n["red"] == 46 and n["held_share"] == pytest.approx(31 / 46, abs=0.001)
    assert n["breached_now"] == 2 and n["breached_now_names"] == ["MAIL-01", "DC-01"]
    assert a.scores["slow_response"] == pytest.approx(0.40 * 1 + 0.35 * 1 + 0.25 * 31 / 46, abs=0.002)
    assert a.weakness_key == "slow_response" and a.title == "Slow response" and a.recommended == "responder"
    assert a.evidence == [
        "Red earned 31 of its 46 points from machines left breached.",
        "A breached machine stayed breached for 11.3 ticks on average (longest 16).",
        "2 machines are breached right now (MAIL-01, DC-01).",
    ]
    assert not a.team_ok
    assert a.text.startswith("Analysis at tick 30: Red 46, Blue 12. Our weakest point is slow response.")
    assert "I recommend calling in Responder" in a.ask_text
    assert "Reply yes, or name another: hardener or sentinel. Reply no to carry on without backup." in a.ask_text


def test_exposure_numbers_match_a_hand_count(serving):
    s = fresh(serving)
    s.tick = 15
    s.score.blue = 4.0
    for node in (3, 6, 11, 12, 0):                          # values 3, 3, 3, 3, 2: five that count
        add_card(s, node, 0.94, kev=True)
    add_card(s, 2, 0.94)                                    # machine worth 1: does not count
    add_card(s, 6, 0.29)                                    # danger below 30%: does not count
    add_card(s, 11, 0.94, state="patched")                  # closed: does not count
    add_card(s, 12, 0.94, state="patching")                 # being closed: does not count
    add_card(s, 5, 0.30)                                    # exactly 30% on a machine worth 2: counts
    a = bk.analyse(s, [])
    assert a.numbers["open_hot"] == 6
    assert a.scores["exposure"] == pytest.approx(0.70) and a.scores["slow_response"] == 0.0
    assert a.weakness_key == "exposure" and a.recommended == "hardener" and not a.team_ok
    assert a.evidence[0] == "6 open cards with danger of 30% or more sit on machines worth 2 or more."
    assert a.evidence[1] == "No breach yet, so no card has been used against us."
    assert "I recommend calling in Hardener" in a.ask_text


def test_exposure_breach_figures_match_a_hand_count(serving):
    s = fresh(serving)
    s.tick = 40
    s.score.red = 20.0
    s.track["breaches"] = [breach(11, 0, open_ticks=12, untouched=True, rank=5),     # long open, top 50
                           breach(13, 1, open_ticks=9, untouched=True, rank=50),     # too short, top 50
                           breach(25, 5, open_ticks=20, untouched=False, rank=51),   # a patch had begun
                           breach(30, 6, open_ticks=10, untouched=True, rank=None)]  # long open, unranked
    s.track["restores"] = [{"tick": t + 3, "node": n, "actor": "blue3"} for t, n in ((11, 0), (13, 1), (25, 5), (30, 6))]
    a = bk.analyse(s, [])
    assert a.numbers["breaches"] == 4 and a.numbers["breaches_long_open"] == 2 and a.numbers["breaches_top50"] == 2
    assert a.all_evidence["exposure"] == [
        "0 open cards with danger of 30% or more sit on machines worth 2 or more.",
        "2 of 4 breaches came through a card that had been open and untouched for 10 ticks or more.",
        "2 of 4 breaches came through a top-50 priority card.",
    ]
    assert a.scores["exposure"] == pytest.approx(0.15 * 0.5 + 0.15 * 0.5)


def test_blind_spot_numbers_match_a_hand_count(serving):
    s = fresh(serving)
    s.tick = 40
    s.score.red, s.score.held, s.score.blue = 24.0, 9.0, 20.0
    s.track["breaches"] = [breach(12, 0, monitored=False, untouched=False), breach(20, 5, monitored=False, untouched=False),
                           breach(30, 6, monitored=True, untouched=False)]
    s.track["restores"] = [{"tick": 15, "node": 0, "actor": "blue3"}, {"tick": 23, "node": 5, "actor": "blue1"},
                           {"tick": 33, "node": 6, "actor": "blue3"}]
    s.track["red_ticks"], s.track["hidden_ticks"] = 60, 57
    a = bk.analyse(s, [])
    n = a.numbers
    assert n["hidden_share"] == 0.95 and n["breaches_unmonitored"] == 2 and n["detections"] == 0
    assert n["avg_breach_ticks"] == 3.0 and n["breached_now"] == 0
    assert a.scores["blind_spots"] == pytest.approx(0.40 * 0.9 + 0.30 * 2 / 3 + 0.30, abs=0.002)
    assert a.weakness_key == "blind_spots" and a.recommended == "sentinel"
    assert a.evidence == [
        "The attackers were out of sight for 95% of the time since they became active.",
        "2 of 3 breaches happened on a machine nobody was monitoring.",
        "An attacker has been detected 0 times.",
    ]
    s.track["detections"] = [{"tick": 5, "node": 1}]
    assert bk.analyse(s, []).evidence[2] == "An attacker has been detected 1 time."


def test_team_ok_when_blue_leads_and_nothing_stands_out(serving):
    s = fresh(serving)
    s.tick = 40
    s.score.red, s.score.held, s.score.blue = 11.0, 6.0, 30.0
    s.track["breaches"] = [breach(12, 0, monitored=True, untouched=False)]
    s.track["restores"] = [{"tick": 15, "node": 0, "actor": "blue3"}]
    s.track["red_ticks"], s.track["hidden_ticks"] = 60, 36
    s.track["detections"] = [{"tick": t, "node": 1} for t in (13, 20, 31)]
    a = bk.analyse(s, [])
    assert all(a.scores[k] <= bk.THRESHOLDS[k] for k in bk.WEAKNESSES)
    assert a.team_ok and a.recommended in bk.OPTIONS          # fine, and still offers the best fit
    assert "The team is holding up" in a.text and "I recommend calling in" in a.ask_text
    s.score.red = 40.0                                          # same board, but Red is ahead
    assert not bk.analyse(s, []).team_ok
    s.score.red = 11.0
    s.nodes[6].state = "compromised"                            # Blue ahead, but a weakness stands out
    s.track["breaches"].append(breach(38, 6))
    b = bk.analyse(s, [])
    assert not b.team_ok and b.weakness_key == "slow_response"


def test_three_contrasting_situations_get_three_different_specialists(serving):
    # 1. valuable high-danger cards wide open, nothing breached yet -> Hardener
    a = fresh(serving)
    a.tick = 12
    for node in (3, 6, 11, 12):
        add_card(a, node, 0.94, kev=True)
    # 2. everything patched, two machines left breached for a long time -> Responder
    b = fresh(serving)
    b.tick = 50
    b.score.red, b.score.held, b.score.blue = 60.0, 50.0, 30.0
    b.track["breaches"] = [breach(20, 6, monitored=True), breach(25, 11, monitored=True)]
    b.nodes[6].state = b.nodes[11].state = "compromised"
    b.track["red_ticks"], b.track["hidden_ticks"] = 80, 44
    b.track["detections"] = [{"tick": t, "node": 1} for t in (13, 20, 31)]
    # 3. everything patched, breaches restored at once, attackers never seen -> Sentinel
    c = fresh(serving)
    c.tick = 50
    c.score.red, c.score.held, c.score.blue = 16.0, 6.0, 30.0
    c.track["breaches"] = [breach(20, 6, untouched=False), breach(30, 0, untouched=False)]
    c.track["restores"] = [{"tick": 23, "node": 6, "actor": "blue3"}, {"tick": 33, "node": 0, "actor": "blue3"}]
    c.track["red_ticks"], c.track["hidden_ticks"] = 80, 80
    got = [bk.analyse(x, []).recommended for x in (a, b, c)]
    assert got == ["hardener", "responder", "sentinel"]


def test_player_lines_are_kind_factual_and_counted_by_hand(serving):
    s = fresh(serving)
    s.tick = 30
    c1, c2 = add_card(s, 6, 0.5), add_card(s, 6, 0.4)
    for c, gain in ((c1, 1.4), (c2, 0.5)):
        c.patch_state, c.patched_by_id, c.patch_gain = "patched", "p1", gain
    s.track["breaches"] = [breach(11, 6), breach(12, 0), breach(22, 12)]
    s.track["restores"] = [{"tick": 19, "node": 6, "actor": "blue3"}, {"tick": 26, "node": 12, "actor": "p1"}]
    rows = [row("p1", t, "WAIT", "waiting", 11) for t in range(0, 4)]                 # 4 waits
    rows += [row("p1", 4, "MOVE", "moved", 6), row("p1", 5, "PATCH", "patching", 6),
             row("p1", 6, "PATCH", "patched", 6),
             row("p1", 13, "MOVE", "moved", 7),         # one step from breached machine 6 (11..19): walked past
             row("p1", 24, "RESTORE", "restoring", 12),  # stood on breached machine 12 and restored it: not counted
             row("p1", 26, "RESTORE", "restored", 12)]
    rows += [row("p2", t, "WAIT", "waiting", 8) for t in range(0, 30)]
    rows += [row("red1", 12, "EXPLOIT", "breach", 0)]                                  # red rows are ignored
    a = bk.analyse(s, rows)
    lines = {p["id"]: p["line"] for p in a.player_lines}
    assert [p["id"] for p in a.player_lines] == ["p1", "p2"]
    assert lines["p1"] == ("Player 1 patched 2 cards worth 1.9 and restored 1 machine, but walked past "
                           "1 breached machine; waited 40% of the ticks.")
    assert lines["p2"] == "Player 2 has not acted yet."
    assert a.numbers["players"]["p1"]["walked_past"] == 1 and a.numbers["players"]["p1"]["wait_share"] == 0.4
    assert lines["p1"] in a.ask_text and lines["p2"] in a.ask_text
    # a player with no rows at all (match just started) has not acted either
    assert {p["id"]: p["line"] for p in bk.analyse(s, []).player_lines}["p1"] == "Player 1 has not acted yet."


def test_agent_lines_quote_each_agents_own_numbers(serving):
    s = fresh(serving)
    s.tick = 30
    for aid, gains in (("blue1", (2.8, 1.4)), ("blue2", (0.9,))):
        for g in gains:
            c = add_card(s, 6, 0.9)
            c.patch_state, c.patched_by_id, c.patch_gain = "patched", aid, g
    s.track["restores"] = [{"tick": 19, "node": 6, "actor": "blue3"}, {"tick": 25, "node": 0, "actor": "blue3"},
                           {"tick": 28, "node": 5, "actor": "blue1"}]
    s.track["detections"] = [{"tick": 16, "node": 6}, {"tick": 18, "node": 6}]
    rows = [row("blue1", 26, "RESTORE", "restoring", 5), row("blue1", 28, "RESTORE", "restored", 5),
            row("blue3", 3, "MONITOR", "monitoring", 7), row("blue3", 6, "MONITOR", "monitoring", 7),
            row("blue3", 9, "MONITOR", "monitoring", 7)]
    a = bk.analyse(s, rows)
    assert set(a.agent_lines) == {"blue1", "blue2", "blue3"}
    assert a.agent_lines["blue1"] == "I patched 2 cards worth 4.2 and was pulled off the priority list 1 time to restore a machine."
    assert a.agent_lines["blue2"] == "I patched 1 card worth 0.9 and was pulled off patching 0 times to restore a machine."
    assert a.agent_lines["blue3"] == "I restored 2 machines and monitored 3 times; an attacker was detected 2 times."


# --------------------------------------------------------------------------- spawn
def test_spawn_adds_exactly_one_actor_at_jump_01_and_refuses_a_second(serving):
    s = st.new_match(serving, 1, "m-spawn")
    before = dict(s.actors)
    a = bk.spawn(s, "hardener")
    assert set(s.actors) - set(before) == {"blue4"} and len(s.actors) == 8
    assert (a.id, a.name, a.team, a.kind, a.role) == ("blue4", "Hardener", "blue", "agent", "hardener")
    assert a.node_id == 19 and s.nodes[a.node_id].name == "JUMP-01" and a.reinforcement and a.busy_ticks == 0
    assert "blue4" in s.memory
    assert st.actor_ids(s)[-1] == "blue4" and st.blue_agent_ids(s) == ["blue1", "blue2", "blue3", "blue4"]
    assert st.resolve_order(s) == ["p1", "p2", "blue1", "blue2", "blue3", "blue4", "red1", "red2"]
    with pytest.raises(ValueError):
        bk.spawn(s, "responder")
    assert len(s.actors) == 8 and s.actors["blue4"].role == "hardener"
    msg = st.snapshot(s)
    assert len(msg["actors"]) == 8
    last = msg["actors"][-1]
    assert last["id"] == "blue4" and last["name"] == "Hardener" and last["team"] == "blue"
    assert last["kind"] == "agent" and last["role"] == "hardener" and last["reinforcement"] is True
    assert all("reinforcement" not in x for x in msg["actors"][:7])


def test_spawn_refuses_an_unknown_role_and_a_match_that_is_not_running(serving):
    s = st.new_match(serving, 1, "m-spawn2")
    with pytest.raises(ValueError):
        bk.spawn(s, "wizard")
    lobby = st.new_match(serving, 1, "m-lobby", phase="lobby")
    with pytest.raises(ValueError):
        bk.spawn(lobby, "responder")
    assert "blue4" not in s.actors and "blue4" not in lobby.actors


# --------------------------------------------------------------------------- policies on small boards
def view_of(s):
    return policy.blue_view(s, "blue4")


def test_responder_goes_to_the_highest_value_breach_restores_it_then_stands_by(serving):
    s = fresh(serving)
    bk.spawn(s, "responder")
    s.nodes[18].state = "compromised"          # LAB-01, value 1, one step away
    s.nodes[12].state = "compromised"          # ERP-01, value 3, three steps away
    act = bk.responder(view_of(s), s.memory["blue4"], random.Random(0))
    assert act.kind == "MOVE" and act.policy == "responder"
    assert st.dist(act.target_node, 12) == st.dist(19, 12) - 1          # toward the value-3 machine
    assert s.memory["blue4"]["target"] == {"node": 12, "card": None, "what": "RESTORE"}
    s.actors["blue4"].node_id = 12
    act = bk.responder(view_of(s), s.memory["blue4"], random.Random(0))
    assert act.kind == "RESTORE"
    # nothing breached: it holds the most valuable exposed machine and keeps it monitored
    s.nodes[12].state = s.nodes[18].state = "healthy"
    add_card(s, 12, 0.9)
    add_card(s, 13, 0.9)                       # WIKI-01 is worth 1: less exposed than ERP-01 (worth 3)
    s.memory["blue4"]["target"] = None
    act = bk.responder(view_of(s), s.memory["blue4"], random.Random(0))
    assert act.kind == "MONITOR" and act.facts["standby"]
    s.nodes[12].monitored_ticks = 3
    assert bk.responder(view_of(s), s.memory["blue4"], random.Random(0)).kind == "WAIT"


def test_responder_leaves_a_breach_to_a_team_mate_who_is_already_on_it(serving):
    s = fresh(serving)
    bk.spawn(s, "responder")
    s.nodes[12].state = "compromised"
    s.actors["blue3"].node_id = 12
    s.memory["blue3"]["target"] = {"node": 12, "card": None, "what": "RESTORE"}
    add_card(s, 14, 0.5)
    act = bk.responder(view_of(s), s.memory["blue4"], random.Random(0))
    assert not (act.kind == "MOVE" and s.memory["blue4"]["target"]["what"] == "RESTORE")
    assert s.memory["blue4"]["target"]["node"] == 14          # stands by elsewhere instead


def test_hardener_takes_the_card_worth_most_and_skips_claimed_ones(serving):
    s = fresh(serving)
    bk.spawn(s, "hardener")
    low = add_card(s, 19, 0.30)                # here, value 2 -> worth 0.60
    top = add_card(s, 14, 0.94, kev=True)      # PRINT-01 value 1, one step -> 0.94 (+ KEV) / 1.25
    far = add_card(s, 3, 0.94, kev=True)       # VPN-01 value 3, four steps -> 3 x 1.09 / 2 = 1.63: the most
    act = bk.hardener(view_of(s), s.memory["blue4"], random.Random(0))
    assert act.kind == "MOVE" and s.memory["blue4"]["target"]["card"] == far.card_id
    assert act.facts["cve"] == far.cve_id and act.facts["worth"] == pytest.approx(3 * 0.94)
    # a team-mate claims that card: the Hardener takes the next best
    s.memory["blue1"]["target"] = {"node": 3, "card": far.card_id, "what": "PATCH"}
    s.memory["blue4"]["target"] = None
    act = bk.hardener(view_of(s), s.memory["blue4"], random.Random(0))
    assert s.memory["blue4"]["target"]["card"] == top.card_id
    # standing on its card, it patches that card
    s.actors["blue4"].node_id = 14
    act = bk.hardener(view_of(s), s.memory["blue4"], random.Random(0))
    assert act.kind == "PATCH" and act.card_id == top.card_id and act.policy == "hardener"
    top.patch_state = far.patch_state = low.patch_state = "patched"
    s.memory["blue4"]["target"] = None
    assert bk.hardener(view_of(s), s.memory["blue4"], random.Random(0)).kind == "WAIT"


def test_hardener_never_looks_at_whether_a_card_was_attacked(serving):
    """card.attempted would give away an attacker nobody has seen."""
    s = fresh(serving)
    bk.spawn(s, "hardener")
    a, b = add_card(s, 3, 0.94), add_card(s, 12, 0.93)
    first = bk.hardener(view_of(s), copy.deepcopy(s.memory["blue4"]), random.Random(0))
    a.attempted = True
    again = bk.hardener(view_of(s), copy.deepcopy(s.memory["blue4"]), random.Random(0))
    assert (first.kind, first.target_node, first.facts["cve"]) == (again.kind, again.target_node, again.facts["cve"])


def test_sentinel_monitors_rotates_and_restores_under_its_feet(serving):
    s = fresh(serving)
    bk.spawn(s, "sentinel")
    add_card(s, 12, 0.9)                       # ERP-01, value 3
    add_card(s, 11, 0.9)                       # DC-01, value 3, next to it
    s.actors["blue4"].node_id = 12
    mem = s.memory["blue4"]
    act = bk.sentinel(view_of(s), mem, random.Random(0))
    assert act.kind == "MONITOR" and sorted(mem["beat"]) == [11, 12]
    s.nodes[12].monitored_ticks = 3            # watched: move on to the other machine of the beat
    act = bk.sentinel(view_of(s), mem, random.Random(0))
    assert act.kind == "MOVE" and act.target_node == 11
    s.actors["blue4"].node_id = 11
    assert bk.sentinel(view_of(s), mem, random.Random(0)).kind == "MONITOR"
    s.nodes[11].state = "compromised"          # breached under its feet
    act = bk.sentinel(view_of(s), mem, random.Random(0))
    assert act.kind == "RESTORE" and act.policy == "sentinel"


def test_sentinel_watches_the_machine_a_visible_attacker_stands_on(serving):
    s = fresh(serving)
    bk.spawn(s, "sentinel")
    add_card(s, 12, 0.9)
    add_card(s, 18, 0.05)                      # LAB-01: little exposure, but ...
    s.actors["red1"].node_id = 18
    mem = s.memory["blue4"]
    bk.sentinel(view_of(s), mem, random.Random(0))
    assert 12 in mem["beat"]                   # ... the attacker there is not revealed: it changes nothing
    s.actors["red1"].revealed_ticks = 2
    mem["beat"] = []
    bk.sentinel(view_of(s), mem, random.Random(0))
    assert mem["beat"] == [18]


# --------------------------------------------------------------------------- whole matches with a specialist
@pytest.mark.parametrize("role", ["responder", "hardener", "sentinel"])
def test_specialist_plays_by_the_rules_and_the_team_stays_legal(serving, role):
    for seed in (1, 4):
        prev = {}
        claims_ok = True

        def check(s, res):
            nonlocal claims_ok
            cards = [(s.memory[a].get("target") or {}).get("card") for a in st.blue_agent_ids(s)]
            cards = [c for c in cards if c is not None]
            claims_ok = claims_ok and len(cards) == len(set(cards))

        rows = []
        r = simulate.run_match(serving, seed, backup=role, backup_config=bk.BackupConfig(checkpoint_tick=12),
                               on_tick=lambda s, res: (rows.extend(res.rows), check(s, res)))
        s = r["state"]
        j = r["backup"]["joined_tick"]
        assert r["backup"]["status"] == "joined" and r["backup"]["chosen"] == role and j is not None
        assert claims_ok, "two blue agents were after the same card"
        mine = [x for x in rows if x["actor_id"] == "blue4"]
        # exactly one row per tick from the tick it joined, never before
        assert [x["tick"] for x in mine] == list(range(j, s.ticks_total))
        assert all(x["team"] == "blue" and x["kind"] == "agent" and x["policy"] == role for x in mine)
        assert all(x["action"] in rules.BLUE_ACTIONS for x in mine)
        assert mine[0]["node_id"] in [19] + st.neighbours(19)            # it walked on at JUMP-01
        for a, b in zip(mine, mine[1:]):                                   # one step per tick at most
            assert st.dist(a["node_id"], b["node_id"]) <= 1
            if a["outcome"] == "patching":                                 # a patch takes two ticks, on the spot
                assert (b["action"], b["node_id"]) == ("PATCH", a["node_id"]) and b["outcome"] in ("patched", "noop")
            if a["outcome"] == "restoring":                                # a restore takes three
                assert (b["action"], b["outcome"]) == ("RESTORE", "working")
        for i, x in enumerate(mine):
            if x["outcome"] == "restored":
                assert [y["outcome"] for y in mine[i - 2:i]] == ["restoring", "working"]
        # every agent's move stayed legal: no agent started a patch on a card somebody else had
        agents = [x for x in rows if x["kind"] == "agent" and x["team"] == "blue"]
        assert not [x for x in agents if x["action"] == "PATCH" and x["outcome"] == "invalid"]
        per_tick = {}
        for x in agents:
            if x["action"] == "PATCH" and x["outcome"] == "patching":
                key = (x["tick"], x["node_id"], x["cve_id"])
                assert key not in per_tick, "two agents began the same patch"
                per_tick[key] = x["actor_id"]
        # score attribution: the specialist's patches and restores are in the totals, nothing is lost
        assert sum(1 for n in s.nodes for c in n.cards if c.patched_by_id == "blue4") == \
            sum(1 for x in mine if x["outcome"] == "patched")
        assert sum(1 for x in s.track["restores"] if x["actor"] == "blue4") == sum(1 for x in mine if x["outcome"] == "restored")
        assert round(s.score.patch + s.score.restore + s.score.isolate_cost, 4) == round(s.score.blue, 4)


def test_a_busy_specialist_gets_no_new_decision(serving):
    s = fresh(serving)
    bk.spawn(s, "hardener")
    cards = [add_card(s, 19, x) for x in (0.95, 0.9, 0.85)]   # Triage claims one; two are left for it
    rng = random.Random(1)
    acts = policy.choose_all(s, rng)
    assert acts["blue4"].kind == "PATCH"
    c = next(x for x in cards if x.card_id == acts["blue4"].card_id)
    assert c.card_id not in [(s.memory[a]["target"] or {}).get("card") for a in st.BLUE_AGENT_IDS]
    for a in st.BLUE_AGENT_IDS:                                  # only the specialist acts in this test
        acts.pop(a, None)
    res = rules.step(s, acts, rng)
    mine = next(r for r in res.rows if r["actor_id"] == "blue4")
    assert mine["outcome"] == "patching" and s.actors["blue4"].busy_ticks == s.tuning.patch_ticks - 1
    assert "blue4" not in policy.choose_all(s, rng)                       # busy: the policy is not asked
    # an action handed in anyway is ignored: the patch simply finishes
    res = rules.step(s, {"blue4": rules.Action("blue4", "MOVE", target_node=18)}, rng)
    mine = next(r for r in res.rows if r["actor_id"] == "blue4")
    assert (mine["action"], mine["outcome"]) == ("PATCH", "patched") and s.actors["blue4"].node_id == 19
    worth = 2 * c.score                                          # JUMP-01 is worth 2
    assert c.patched_by_id == "blue4" and c.patch_gain == pytest.approx(worth)
    assert s.score.blue == pytest.approx(worth) and s.score.patch == pytest.approx(worth)
    d = [x for x in res.deltas if x["actor_id"] == "blue4"]
    assert d and d[0]["cause"] == "patch" and d[0]["amount"] == pytest.approx(worth)


# --------------------------------------------------------------------------- fog of war
def scramble_hidden_reds(s):
    """A copy of the state in which everything about an unrevealed red is different."""
    x = copy.deepcopy(s)
    for rid in st.RED_IDS:
        a = x.actors[rid]
        if a.revealed:
            continue
        a.node_id = (a.node_id + 7) % st.N_NODES
        a.action, a.target_node, a.busy_ticks, a.busy = "EXPLOIT", 3, 4, {"action": "WAIT", "node": 1, "card": None}
        x.memory[rid] = st.new_memory(rid)
        x.red_intel[rid] = {}
    return x


@pytest.mark.parametrize("role", ["responder", "hardener", "sentinel"])
def test_nothing_about_an_unrevealed_red_reaches_the_analysis_the_object_or_the_specialist(serving, next_serving, role):
    for seed in (2, 7):
        s = st.new_match(serving, seed, f"m-fog-{seed}")
        rng = random.Random(seed)
        flow = bk.BackupFlow(bk.BackupConfig(checkpoint_tick=14))
        flow.reset(s)
        all_rows, texts, hidden_ticks = [], [], 0
        while s.phase == "running":
            res = match.run_tick(s, {}, rng, new_world=next_serving if s.tick == 50 else None)
            all_rows += res.rows
            says = flow.on_tick(s, res.rows, all_rows)
            if flow.status == "asked" and s.tick >= flow.asked_tick + 2:
                says += flow.on_human(s, role, "Tester")
            texts += [x.text for x in says] + [e["text"] for e in s.pending_events]
            if s.phase != "running":
                break
            hidden = [r for r in st.RED_IDS if not s.actors[r].revealed]
            hidden_ticks += bool(hidden)
            # 1. the analysis is the same whatever the hidden reds are doing, and never needs a red row
            blue_rows = [r for r in all_rows if r["team"] == "blue"]
            x = scramble_hidden_reds(s)
            assert bk.analyse(s, all_rows) == bk.analyse(x, blue_rows)
            # 2. the specialist sees revealed reds only, and decides the same on the scrambled board
            if "blue4" in s.actors and s.actors["blue4"].busy_ticks == 0:
                v = policy.blue_view(s, "blue4")
                assert {r for r, _ in v.revealed_reds} == {r for r in st.RED_IDS if s.actors[r].revealed}
                a1 = bk.SPECIALIST_POLICIES[role](v, copy.deepcopy(s.memory["blue4"]), random.Random(0))
                a2 = bk.SPECIALIST_POLICIES[role](policy.blue_view(x, "blue4"), copy.deepcopy(x.memory["blue4"]), random.Random(0))
                assert (a1.kind, a1.target_node, a1.card_id, a1.reason) == (a2.kind, a2.target_node, a2.card_id, a2.reason)
            # 3. the wire object is the same on the scrambled board and names no attacker
            wire = json.dumps(flow.public(s))
            assert wire == json.dumps(flow.public(x))
            assert not any(n in wire for n in RED_NAMES)
        texts += [x.text for x in flow.on_match_end(s, all_rows)]
        assert hidden_ticks > 50 and flow.status == "joined"
        assert texts and not any(n in t for t in texts for n in RED_NAMES)


# --------------------------------------------------------------------------- the flow
def test_checkpoint_conversation_and_timing(serving):
    s = fresh(serving, clear=False)
    flow = bk.BackupFlow(CFG)
    flow.reset(s)
    assert flow.status == "watching" and s.backup["status"] == "watching" and s.backup["checkpoint_tick"] == 40
    assert s.backup["weakness"] is None and s.backup["players"] == [] and s.backup["recommended"] is None
    assert s.backup["options"] == ["responder", "hardener", "sentinel"] and s.backup["room"] == "Patch Tuesday - Blue team"
    assert tick_to(flow, s, 39) == []
    said = tick_to(flow, s, 48)
    assert [(t, x.speaker, x.kind, x.mention) for t, x in said] == [
        (40, "coach", "analysis", "blue_agents"),
        (42, "triage", "agent_line", "coach"), (44, "patcher", "agent_line", "coach"),
        (46, "hunter", "agent_line", "coach"),
        (48, "coach", "ask", "humans")]
    a = flow.analysis
    assert said[0][1].text == a.text and said[4][1].text == a.ask_text
    assert [x.text for _, x in said[1:4]] == [a.agent_lines[k] for k in ("blue1", "blue2", "blue3")]
    b = s.backup
    assert flow.status == "asked" and b["status"] == "asked"
    assert (b["checkpoint_tick"], b["asked_tick"], b["expires_tick"]) == (40, 48, 63)
    assert b["weakness"] == {"key": a.weakness_key, "title": a.title, "evidence": a.evidence}
    assert b["players"] == a.player_lines and b["recommended"] == a.recommended
    assert b["chosen"] is None and b["by"] is None and b["joined_tick"] is None and b["actor_id"] is None
    assert b["before"] is None and b["after"] is None
    # the two "coach" events: the analysis in one sentence, then the recommendation
    ev = st.take_events(s)
    assert [e["kind"] for e in ev] == ["coach", "coach"] and s.pending_events == []
    assert a.evidence[0] in ev[0]["text"] and bk.SPECIALISTS[a.recommended] in ev[1]["text"]
    assert all(x.text and "@" not in x.text and "[[" not in x.text for _, x in said)


def test_status_is_analysing_between_the_analysis_and_the_question(serving):
    s = fresh(serving, clear=False)
    flow = bk.BackupFlow(CFG)
    flow.reset(s)
    tick_to(flow, s, 41)
    assert flow.status == "analysing" and s.backup["status"] == "analysing" and s.backup["asked_tick"] is None
    assert flow.on_human(s, "yes", "Suman") == [] and flow.status == "analysing"   # nothing was asked yet


def test_early_trigger_when_red_leads_by_15_after_tick_20(serving):
    def first_analysis(red, blue):
        s = fresh(serving, clear=False)
        s.score.red, s.score.blue = red, blue
        flow = bk.BackupFlow(CFG)
        flow.reset(s)
        said = tick_to(flow, s, 45)
        return said[0][0]

    assert first_analysis(20.0, 5.0) == 21          # leads by 15: the first tick after 20
    assert first_analysis(19.9, 5.0) == 40          # leads by 14.9: the normal checkpoint
    assert first_analysis(5.0, 20.0) == 40


def test_a_human_can_call_coach_early(serving):
    s = fresh(serving, clear=False)
    flow = bk.BackupFlow(CFG)
    flow.reset(s)
    tick_to(flow, s, 12)
    assert flow.on_human(s, "what is the weather", "Suman") == [] and tick_to(flow, s, 14) == []
    assert flow.on_human(s, "@[[57278e5c-0b51-4e20-a813-5239a395d246]] ANALYZE please", "Suman") == []
    said = tick_to(flow, s, 15)
    assert [(t, x.kind) for t, x in said] == [(15, "analysis")] and s.backup["checkpoint_tick"] == 15
    for word in ("backup", "help", "analyse"):
        s2 = fresh(serving, clear=False)
        f2 = bk.BackupFlow(CFG)
        f2.reset(s2)
        tick_to(f2, s2, 11)
        f2.on_human(s2, f"coach {word}", "Suman")
        assert tick_to(f2, s2, 12)[0][1].kind == "analysis"


def test_a_call_before_the_attackers_move_waits_until_tick_10(serving):
    s = fresh(serving, clear=False)
    flow = bk.BackupFlow(CFG)
    flow.reset(s)
    tick_to(flow, s, 4)
    said = flow.on_human(s, "backup", "Suman")
    assert len(said) == 1 and said[0].kind == "decision" and "tick 10" in said[0].text
    assert flow.on_human(s, "backup", "Suman") == []                       # said once
    assert tick_to(flow, s, 9) == []
    assert [(t, x.kind) for t, x in tick_to(flow, s, 10)] == [(10, "analysis")]


@pytest.mark.parametrize("text,want", [
    ("yes", "yes"), ("OK", "yes"), ("go", "yes"), ("Do it!", "yes"), ("yes please, go ahead", "yes"),
    ("no", "no"), ("None", "no"), ("no, go away", "no"), ("yes, no problem", "yes"),
    ("hardener", "hardener"), ("yes, but the Sentinel please", "sentinel"), ("no... responder", "responder"),
    ("RESPONDER!!!", "responder"), ("@[[57278e5c-0b51-4e20-a813-5239a395d246]] yes", "yes"),
    ("", None), ("maybe later", None), ("not now", None), ("nobody knows", None), ("going", None),
    ("yesterday", None), ("<script>alert(1)</script>", None), (None, None), (12345, None),
    ("x" * 200 + " yes", None),                                           # beyond the length cap
])
def test_answer_words(text, want):
    assert bk.parse_reply(text) == want


def asked_flow(serving, cfg=CFG, seed=1):
    s = st.new_match(serving, seed, f"m-ask-{seed}")
    flow = bk.BackupFlow(cfg)
    flow.reset(s)
    tick_to(flow, s, 48)
    assert flow.status == "asked"
    st.take_events(s)
    return s, flow


def test_yes_brings_the_recommended_specialist_and_a_name_beats_yes(serving):
    s, flow = asked_flow(serving)
    rec = flow.analysis.recommended
    s.tick = 50
    assert flow.on_human(s, "hmm, let me think", "Suman Kolisetty") == [] and flow.status == "asked"
    said = flow.on_human(s, "yes", "Suman Kolisetty")
    assert [(x.speaker, x.kind, x.role, x.mention) for x in said] == [("coach", "decision", None, "humans"),
                                                                     (rec, "join", rec, "team")]
    assert said[0].text == f"Suman approved: {bk.SPECIALISTS[rec]} is joining the Blue team now."
    assert said[1].text.startswith(f"{bk.SPECIALISTS[rec]} here.")
    b = s.backup
    assert (b["status"], b["chosen"], b["by"], b["joined_tick"], b["actor_id"]) == ("joined", rec, "Suman Kolisetty", 50, "blue4")
    assert b["before"]["ticks"] == 50 and b["after"] is None
    assert s.actors["blue4"].role == rec and s.actors["blue4"].node_id == 19
    assert st.take_events(s) == [{"kind": "backup", "text": f"{bk.SPECIALISTS[rec]} joined the Blue team (approved by Suman via BAND)."}]
    # once per match: more answers change nothing
    assert flow.on_human(s, "no", "Suman") == [] and flow.on_human(s, "sentinel", "Suman") == []
    assert flow.status == "joined" and len(s.actors) == 8 and s.actors["blue4"].role == rec

    s, flow = asked_flow(serving)
    other = next(o for o in bk.OPTIONS if o != flow.analysis.recommended)
    said = flow.on_human(s, f"yes ok go, {other}", "Ana")
    assert flow.status == "joined" and s.backup["chosen"] == other and s.actors["blue4"].role == other
    assert said[0].text.startswith(f"Ana chose {bk.SPECIALISTS[other]} instead of my pick")
    assert said[1].speaker == other and said[1].role == other


def test_no_declines_and_nothing_joins(serving):
    s, flow = asked_flow(serving)
    said = flow.on_human(s, "no thanks", "Suman")
    assert [(x.speaker, x.kind) for x in said] == [("coach", "decision")]
    assert flow.status == "declined" and s.backup["status"] == "declined" and s.backup["by"] == "Suman"
    assert s.backup["chosen"] is None and s.backup["actor_id"] is None and "blue4" not in s.actors
    assert st.take_events(s) == [{"kind": "backup", "text": "Backup declined."}]
    assert tick_to(flow, s, 120) == [] and flow.on_human(s, "yes", "Suman") == [] and flow.on_human(s, "analyze", "Suman") == []
    assert "blue4" not in s.actors and flow.status == "declined"


def test_timeout_brings_coachs_pick_unless_auto_is_off(serving):
    s, flow = asked_flow(serving)
    assert tick_to(flow, s, 62) == []
    said = tick_to(flow, s, 63)
    rec = flow.analysis.recommended
    assert [(t, x.kind) for t, x in said] == [(63, "decision"), (63, "join")]
    assert said[0][1].text == f"No reply in 15 ticks, so I am calling in {bk.SPECIALISTS[rec]} myself."
    b = s.backup
    assert (b["status"], b["chosen"], b["by"], b["joined_tick"]) == ("joined", rec, "Coach (no reply)", 63)
    assert st.take_events(s)[0]["text"] == f"{bk.SPECIALISTS[rec]} joined the Blue team (no reply in BAND, Coach's own call)."

    off = bk.BackupConfig(checkpoint_tick=40, wait_ticks=15, gap_ticks=2, auto=False)
    s, flow = asked_flow(serving, off)
    said = tick_to(flow, s, 63)
    assert [(t, x.kind) for t, x in said] == [(63, "decision")] and "Standing down" in said[0][1].text
    assert flow.status == "declined" and s.backup["by"] == "Coach (no reply)" and "blue4" not in s.actors
    assert st.take_events(s) == [{"kind": "backup", "text": "Backup declined."}]


def test_no_checkpoint_in_the_last_30_ticks(serving):
    s = fresh(serving, clear=False)
    flow = bk.BackupFlow(bk.BackupConfig(checkpoint_tick=121))
    flow.reset(s)
    assert tick_to(flow, s, 149) == [] and flow.status == "watching"
    late = flow.on_human(s, "analyze", "Suman")
    assert len(late) == 1 and "Too late" in late[0].text and flow.on_human(s, "analyze", "Suman") == []
    assert flow.status == "watching" and "blue4" not in s.actors
    # tick 120 is the last one that still starts it (150 - 30)
    s = fresh(serving, clear=False)
    flow = bk.BackupFlow(bk.BackupConfig(checkpoint_tick=120))
    flow.reset(s)
    assert [t for t, _ in tick_to(flow, s, 120)] == [120]


def test_reset_between_matches(serving):
    s, flow = asked_flow(serving)
    flow.on_human(s, "yes", "Suman")
    assert flow.status == "joined" and flow.says
    s2 = st.new_match(serving, 2, "m-next")
    flow.reset(s2)
    assert flow.status == "watching" and flow.analysis is None and flow.says == []
    assert s2.backup["status"] == "watching" and s2.backup["chosen"] is None and "blue4" not in s2.actors
    assert [t for t, _ in tick_to(flow, s2, 40)] == [40]                  # a new match gets its own checkpoint
    # a state from another match handed in without reset() starts clean as well
    s3 = st.new_match(serving, 3, "m-third")
    assert flow.on_tick(s3, [], []) == [] and flow.status == "watching" and flow.match_id == "m-third"
    flow.reset(None)
    assert flow.status == "watching" and flow.public(None)["status"] == "watching"


def test_unavailable_stops_the_conversation_and_public_is_none_when_off(serving):
    s = fresh(serving, clear=False)
    flow = bk.BackupFlow(CFG)
    flow.reset(s)
    flow.set_unavailable("BAND is not reachable")
    assert tick_to(flow, s, 70) == [] and flow.on_human(s, "analyze", "Suman") == []
    assert flow.status == "unavailable" and s.backup["status"] == "unavailable" and "blue4" not in s.actors
    off = bk.BackupFlow(bk.BackupConfig(enabled=False))
    off.reset(s)
    assert off.public(s) is None and s.backup is None and tick_to(off, s, 90) == []
    assert "backup" not in st.snapshot(s)


def test_human_names_are_made_safe(serving):
    s, flow = asked_flow(serving)
    flow.on_human(s, "yes", "  Mallory\n<b>Eve</b>\x00" + "x" * 80)
    assert len(s.backup["by"]) <= 40 and "\n" not in s.backup["by"] and "\x00" not in s.backup["by"]
    assert bk.clean_name("") == "a player" and bk.clean_name(None) == "a player"


def test_a_broken_flow_never_raises_into_the_tick(serving, monkeypatch):
    s = fresh(serving, clear=False)
    flow = bk.BackupFlow(CFG)
    flow.reset(s)
    monkeypatch.setattr(bk, "analyse", lambda *a, **k: 1 / 0)
    assert tick_to(flow, s, 45) == [] and flow.status == "unavailable"


def test_events_reach_the_next_tick_when_nobody_takes_them(serving):
    s = st.new_match(serving, 1, "m-ev")
    rng = random.Random(1)
    flow = bk.BackupFlow(bk.BackupConfig(checkpoint_tick=12))
    flow.reset(s)
    kinds = []
    while s.tick < 40:
        res = match.run_tick(s, {}, rng)
        kinds += [(s.tick, e["kind"]) for e in res.events if e["kind"] in ("coach", "backup")]
        flow.on_tick(s, res.rows, [])
        if flow.status == "asked":
            flow.on_human(s, "yes", "Suman")
    # analysis at 12 and question at 20 are sent with the following tick (13 and 21); the join right after
    assert kinds == [(13, "coach"), (21, "coach"), (21, "backup")]
    assert all(e["kind"] in st.EVENT_KINDS for e in [{"kind": k} for _, k in kinds])


def test_snapshot_carries_the_object_and_the_eighth_actor(serving):
    r, _ = play(serving, 3, "coach", backup_config=bk.BackupConfig(checkpoint_tick=12))
    s = r["state"]
    msg = st.snapshot(s)
    assert msg["backup"] == r["backup"] == s.backup
    b = msg["backup"]
    assert set(b) >= {"status", "checkpoint_tick", "weakness", "players", "recommended", "options", "asked_tick",
                      "expires_tick", "chosen", "by", "joined_tick", "actor_id", "room", "before", "after"}
    assert b["status"] == "joined" and b["by"] == "Sim Player" and b["actor_id"] == "blue4"
    assert (b["checkpoint_tick"], b["asked_tick"], b["expires_tick"], b["joined_tick"]) == (12, 20, 35, 24)
    assert b["before"]["ticks"] == 24 and b["after"]["ticks"] == s.ticks_total - 24
    assert msg["actors"][7]["id"] == "blue4" and msg["actors"][7]["reinforcement"] is True
    json.dumps(msg)                                                        # the whole message serialises
    kinds = [x.kind for x in r["says"]]
    assert kinds[:7] == ["analysis", "agent_line", "agent_line", "agent_line", "ask", "decision", "join"]
    assert kinds[-1] == "summary" and set(kinds[7:-1]) <= {"report"}
    assert r["says"][-1].text.startswith("Match over: Blue ")
    assert 8 <= len(kinds) <= 30


def test_specialist_reports_are_spaced_and_capped(serving):
    for seed in (1, 2, 3):
        said = []
        s = st.new_match(serving, seed, f"m-rep-{seed}")
        rng = random.Random(seed)
        flow = bk.BackupFlow(bk.BackupConfig(checkpoint_tick=12))
        flow.reset(s)
        all_rows = []
        while s.phase == "running":
            res = match.run_tick(s, {}, rng)
            all_rows += res.rows
            for x in flow.on_tick(s, res.rows, all_rows):
                said.append((s.tick, x))
            if flow.status == "asked":
                flow.on_human(s, "hardener", "Suman")
        reports = [(t, x) for t, x in said if x.kind == "report"]
        assert all(x.speaker == "hardener" and x.mention == "coach" for _, x in reports)
        assert len(reports) <= flow.cfg.max_reports
        ticks = [flow.joined_tick] + [t for t, _ in reports]
        assert all(b - a >= flow.cfg.report_gap_ticks for a, b in zip(ticks, ticks[1:]))


# --------------------------------------------------------------------------- before / after
def test_before_after_metrics_match_a_hand_count(serving):
    s = fresh(serving)
    red = [0, 0, 5, 7, 9, 11, 11, 11, 13, 15]
    blue = [0, 1, 1, 1, 4, 4, 4, 7, 7, 7]
    s.track["scores"] = list(zip(red, blue))
    s.track["breached"] = [0, 0, 1, 1, 1, 0, 0, 1, 1, 1]
    s.track["breaches"] = [breach(2, 6), breach(7, 0)]
    s.track["restores"] = [{"tick": 5, "node": 6, "actor": "blue3"}]
    s.tick = 10
    before, after = bk.metrics_before_after(s, [], 6)
    # before = ticks 0..5: Red 11, Blue 4; one breach, restored after 3 ticks
    assert before == {"ticks": 6, "red_per_tick": 1.83, "blue_per_tick": 0.67, "breaches": 1,
                      "avg_breach_ticks": 3.0, "peak_breached": 1}
    # after = ticks 6..9: Red +4, Blue +3; one breach at tick 7, still breached at tick 10
    assert after == {"ticks": 4, "red_per_tick": 1.0, "blue_per_tick": 0.75, "breaches": 1,
                     "avg_breach_ticks": 3.0, "peak_breached": 1}
    b0, a0 = bk.metrics_before_after(s, [], 0)                              # joined at the very start
    assert b0["ticks"] == 0 and b0["red_per_tick"] == 0.0 and b0["avg_breach_ticks"] is None
    assert a0["ticks"] == 10 and a0["red_per_tick"] == 1.5 and a0["breaches"] == 2


def test_before_after_of_a_played_match_add_up_to_the_final_score(serving):
    r, rows = play(serving, 4, "responder", backup_config=bk.BackupConfig(checkpoint_tick=12))
    s, b = r["state"], r["backup"]
    j = b["joined_tick"]
    assert b["before"]["ticks"] + b["after"]["ticks"] == s.ticks_total
    red = b["before"]["red_per_tick"] * b["before"]["ticks"] + b["after"]["red_per_tick"] * b["after"]["ticks"]
    blue = b["before"]["blue_per_tick"] * b["before"]["ticks"] + b["after"]["blue_per_tick"] * b["after"]["ticks"]
    assert red == pytest.approx(s.score.red, abs=1.0) and blue == pytest.approx(s.score.blue, abs=1.0)
    breaches = [x for x in rows if x["outcome"] == "breach"]
    assert b["before"]["breaches"] == sum(1 for x in breaches if x["tick"] < j)
    assert b["after"]["breaches"] == sum(1 for x in breaches if x["tick"] >= j)
    assert b["before"]["breaches"] + b["after"]["breaches"] == s.score.breaches_total
    assert len(s.track["scores"]) == s.ticks_total and s.track["scores"][-1] == (s.score.red, s.score.blue)


def test_track_records_breaches_restores_and_detections_as_the_rules_resolve_them(serving):
    r, rows = play(serving, 7)
    s = r["state"]
    assert len(s.track["breaches"]) == s.score.breaches_total == sum(1 for x in rows if x["outcome"] == "breach")
    assert len(s.track["restores"]) == sum(1 for x in rows if x["outcome"] == "restored")
    assert s.track["red_ticks"] == 2 * (s.ticks_total - s.tuning.red_entry_delay)
    assert 0 < s.track["hidden_ticks"] <= s.track["red_ticks"]
    held = sum(e["ticks"] * s.nodes[e["node"]].value for e in bk.breach_episodes(s))
    assert held >= s.score.held                      # isolation can pause Red's income, never add to it


# --------------------------------------------------------------------------- determinism
@pytest.mark.parametrize("mode", ["coach", "auto", "sentinel"])
def test_same_seed_and_same_reply_give_the_same_match(serving, mode):
    a, rows_a = play(serving, 9, mode, humans="patch")
    b, rows_b = play(serving, 9, mode, humans="patch")
    assert digest(rows_a) == digest(rows_b) and a["score"] == b["score"] and a["backup"] == b["backup"]
    assert [(x.speaker, x.kind, x.text) for x in a["says"]] == [(x.speaker, x.kind, x.text) for x in b["says"]]
    assert "blue4" in a["state"].actors


def test_scripted_patching_humans_only_ever_move_and_patch(serving):
    r, rows = play(serving, 5, humans="patch")
    mine = [x for x in rows if x["kind"] == "human"]
    assert {x["action"] for x in mine} <= {"MOVE", "PATCH", "WAIT"}
    assert any(x["outcome"] == "patched" for x in mine)
    idle, _ = play(serving, 5)
    assert idle["score"] != r["score"]


def test_config_from_env(monkeypatch):
    for k in ("BAND_BACKUP_TICK", "BAND_BACKUP_WAIT_TICKS", "BAND_BACKUP_AUTO"):
        monkeypatch.delenv(k, raising=False)
    c = bk.BackupConfig.from_env(2.0)
    assert (c.checkpoint_tick, c.wait_ticks, c.auto, c.gap_ticks, c.report_gap_ticks) == (40, 15, True, 2, 8)
    monkeypatch.setenv("BAND_BACKUP_TICK", "25")
    monkeypatch.setenv("BAND_BACKUP_WAIT_TICKS", "10")
    monkeypatch.setenv("BAND_BACKUP_AUTO", "0")
    c = bk.BackupConfig.from_env(0.5)
    assert (c.checkpoint_tick, c.wait_ticks, c.auto, c.gap_ticks, c.report_gap_ticks) == (25, 10, False, 6, 30)
    monkeypatch.setenv("BAND_BACKUP_TICK", "nonsense")
    assert bk.BackupConfig.from_env(2.0).checkpoint_tick == 40
