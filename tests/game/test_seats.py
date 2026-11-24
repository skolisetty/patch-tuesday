"""Player seats (name + per-browser lock) and Coach's cover for an inactive player."""
import random

from game import config, rules, server
from game import state as st


def make(monkeypatch, idle="0"):
    monkeypatch.setenv("PT_IDLE_TICKS", idle)
    g = server.Game() if hasattr(server, "Game") else type(server.game)()
    return g


def test_a_taken_seat_only_obeys_its_own_browser(monkeypatch):
    g = make(monkeypatch)
    ok, text = g.seat_claim(1, "token-aaaaaaaa", "  Ali<ce>!  ", "pub12345")
    assert ok and "Alice" in text and g.state.actors["p1"].name == "Alice"
    assert g.seats_public()["p1"] == {"name": "Alice", "id": "pub12345", "auto": False}
    # another browser cannot take it or move it
    ok2, text2 = g.seat_claim(1, "token-bbbbbbbb", "Bob", "pub99999")
    assert not ok2 and "taken by Alice" in text2
    assert g.human_input({"t": "act", "player": 1, "action": "MONITOR", "token": "token-bbbbbbbb"}) is False
    assert g.human_input({"t": "act", "player": 1, "action": "MONITOR"}) is False
    assert g.human_input({"t": "act", "player": 1, "action": "MONITOR", "token": "token-aaaaaaaa"}) is True
    # the free seat is open to anyone, as before
    assert g.human_input({"t": "act", "player": 2, "action": "MONITOR"}) is True
    # only the owner can release
    assert g.seat_release(1, "token-bbbbbbbb") == (False, "")
    assert g.seat_release(1, "token-aaaaaaaa")[0] and g.state.actors["p1"].name == "Player 1"
    assert g.seats_public()["p1"] is None


def test_a_seat_frees_itself_after_its_browser_is_gone(monkeypatch):
    g = make(monkeypatch)
    g.socket_online("token-aaaaaaaa", +1)
    g.seat_claim(2, "token-aaaaaaaa", "Naman", "pubnaman")
    g._expire_seats()
    assert g.seats[2] is not None
    g.socket_online("token-aaaaaaaa", -1)
    g.seats[2]["last_seen"] -= g.SEAT_GRACE + 1
    assert g._expire_seats() is True and g.seats[2] is None


def test_bad_tokens_and_names_are_refused_or_cleaned(monkeypatch):
    g = make(monkeypatch)
    assert g.seat_claim(1, "short", "x", "pub12345") == (False, "")
    assert g.seat_claim(3, "token-aaaaaaaa", "x", "pub12345") == (False, "")
    ok, _ = g.seat_claim(1, "token-aaaaaaaa", "<script>alert(1)</script>" * 3, "pub12345")
    assert ok and "<" not in g.seats[1]["name"] and len(g.seats[1]["name"]) <= 16
    ok, _ = g.seat_claim(2, "token-aaaaaaaa", "", "pub12345")
    assert ok and g.seats[2]["name"] == "Player 2"


def test_coach_covers_an_inactive_player_and_hands_back_on_input(monkeypatch):
    g = make(monkeypatch, idle="5")
    g.state = st.new_match(g.world.current, seed=3, match_id="m-test", tuning=config.TUNING)
    rng = random.Random(3)
    covered_actions = []
    for _ in range(12):
        humans = g.take_human_actions(g.state)
        if "p1" in humans:
            covered_actions.append(humans["p1"].kind)
        rules.step(g.state, humans, rng)
    assert g.autopilot["p1"] and g.autopilot["p2"]
    assert covered_actions and all(k in rules.BLUE_ACTIONS for k in covered_actions)
    assert g.state.actors["p1"].name.endswith("(auto)")
    assert any("inactive" in e["text"] for e in g.pending_events)
    assert g.seats_public()["p1"]["auto"] is True
    # the player presses a key: control comes back at once
    assert g.human_input({"t": "act", "player": 1, "action": "MONITOR"}) is True
    assert g.autopilot["p1"] is False and g.state.actors["p1"].name == "Player 1"
    assert any("back in control" in e["text"] for e in g.pending_events)


def test_the_cover_is_off_when_idle_ticks_is_zero(monkeypatch):
    g = make(monkeypatch, idle="0")
    g.state = st.new_match(g.world.current, seed=3, match_id="m-test", tuning=config.TUNING)
    rng = random.Random(3)
    for _ in range(25):
        humans = g.take_human_actions(g.state)
        assert humans == {}
        rules.step(g.state, humans, rng)
    assert g.autopilot == {"p1": False, "p2": False}
