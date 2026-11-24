#!/usr/bin/env python3
"""Write the three coach-pipeline fixtures: <case>/ledger.jsonl and <case>/state.json.

    python tests/pipeline/coach_fixtures/make_fixtures.py

Each case is a 24-tick match cut down to the moves that matter, in the game's real ledger row
shape (the 22 fields of game/state.py LEDGER_FIELDS; rows were modelled on real rows of
ledger/ledger.jsonl and simplified: one sentence of reason text, no dice values). Every actor has
one row per tick; a tick that is not listed below is a WAIT on the spot.

This script only expands the move lists into rows. It computes NO figure: each case's
expected.json was worked out by hand and NOTES.md shows the working.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TICKS = 24
NAMES = [("MAIL-01", 2), ("WEB-01", 2), ("WEB-02", 1), ("VPN-01", 3), ("DNS-01", 1),
         ("APP-01", 2), ("DB-01", 3), ("FILE-01", 2), ("HR-01", 1), ("DEV-01", 1),
         ("CI-01", 2), ("DC-01", 3), ("ERP-01", 3), ("WIKI-01", 1), ("PRINT-01", 1),
         ("BKP-01", 2), ("IOT-01", 1), ("POS-01", 2), ("LAB-01", 1), ("JUMP-01", 2)]
ACTORS = [("p1", "blue", "human", "human", 11), ("p2", "blue", "human", "human", 8),
          ("blue1", "blue", "agent", "triage", 6), ("blue2", "blue", "agent", "patcher", 12),
          ("blue3", "blue", "agent", "hunter", 7), ("red1", "red", "agent", "opportunist", 0),
          ("red2", "red", "agent", "patient", 19)]
FIELDS = ["ledger_id", "match_id", "world_asof", "pipeline_run_id", "model_version", "tick", "ts",
          "actor_id", "team", "kind", "action", "node_id", "cve_id", "score_used", "score_source",
          "p_breach", "roll", "outcome", "policy", "reason_text", "llm_model", "llm_latency_ms"]


def M(node):                       # MOVE to node
    return ("MOVE", "moved", node, None, None)


def W(outcome="waiting"):          # WAIT with a named outcome (infiltrating, digging in, ...)
    return ("WAIT", outcome, None, None, None)


def A(action, outcome, cve=None, danger=None):   # an action on the machine the actor stands on
    return (action, outcome, None, cve, danger)


def span(first, last, move):
    return {t: move for t in range(first, last + 1)}


def rows_for(match_id: str, moves: dict) -> list[dict]:
    out = []
    where = {a[0]: a[4] for a in ACTORS}
    for tick in range(TICKS):
        for aid, team, kind, policy, _ in ACTORS:
            action, outcome, node, cve, danger = moves.get(aid, {}).get(tick, W())
            if node is not None:
                where[aid] = node
            row = dict.fromkeys(FIELDS)
            row.update({
                "ledger_id": f"{match_id}-{tick:03d}-{aid}", "match_id": match_id, "world_asof": "2026-03-22",
                "pipeline_run_id": "fixture", "tick": tick, "ts": "2026-11-23T12:00:00.000+00:00",
                "actor_id": aid, "team": team, "kind": kind, "action": action, "node_id": where[aid],
                "cve_id": cve, "score_used": danger, "score_source": "epss" if danger is not None else None,
                "outcome": outcome, "policy": policy,
                "reason_text": f"{aid} {action} at {NAMES[where[aid]][0]} (fixture row).",
            })
            out.append(row)
    return out


def board(match_id: str, score: dict, special: dict) -> dict:
    """special: machine id -> {"state", "monitored", "cards": [(cve, danger, kev, priority, patch_state[, dropped_tick])]}"""
    nodes = []
    for i, (name, value) in enumerate(NAMES):
        sp = special.get(i, {})
        cards = []
        for c in sp.get("cards", []):
            card = {"cve_id": c[0], "score": c[1], "in_kev": c[2], "priority_rank": c[3], "patch_state": c[4]}
            if len(c) > 5:
                card["dropped_tick"] = c[5]
            cards.append(card)
        nodes.append({"id": i, "name": name, "value": value, "state": sp.get("state", "healthy"),
                      "monitored": sp.get("monitored", False), "cards": cards})
    return {"match_id": match_id, "tick": TICKS, "nodes": nodes, "score": score}


def write(case: str, match_id: str, moves: dict, state: dict) -> None:
    folder = HERE / case
    folder.mkdir(exist_ok=True)
    with open(folder / "ledger.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in rows_for(match_id, moves):
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    (folder / "state.json").write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ 1. slow response
SLOW = {
    "red1": {**span(0, 9, W("infiltrating")), 10: M(1), 11: M(6),
             12: A("EXPLOIT", "breach", "CVE-2099-1001", 0.90), **span(13, 23, W("digging in"))},
    "red2": {**span(0, 9, W("infiltrating")), 10: M(18), 11: M(17), 12: A("SCAN", "scanned"),
             13: A("EXPLOIT", "breach", "CVE-2099-1002", 0.60), **span(14, 23, W("digging in"))},
    "blue1": {0: A("PATCH", "patching", "CVE-2099-1003", 0.20), 1: A("PATCH", "patched", "CVE-2099-1003", 0.20),
              2: M(5), 3: A("PATCH", "patching", "CVE-2099-1004", 0.50), 4: A("PATCH", "patched", "CVE-2099-1004", 0.50)},
    "blue2": {0: A("PATCH", "patching", "CVE-2099-1005", 0.10), 1: A("PATCH", "patched", "CVE-2099-1005", 0.10)},
    "blue3": {18: M(6), 19: A("RESTORE", "restoring"), 20: A("RESTORE", "working"), 21: A("RESTORE", "restored")},
    "p2": {14: M(13), 15: M(18), 16: M(17),
           17: A("PATCH", "patching", "CVE-2099-1006", 0.40), 18: A("PATCH", "patched", "CVE-2099-1006", 0.40)},
}
SLOW_STATE = board("m-fixture-slow", {
    "red": 59.0, "blue": 5.7, "breaches_total": 2, "breaches_in_top50": 1,
    "breakdown": {"blue": {"patch": 2.7, "restore": 3.0, "isolate_cost": 0.0}, "red": {"first_breach": 10.0, "held": 49.0}}}, {
    0: {"cards": [("CVE-2099-1010", 0.29, False, 400, "unpatched")]},
    3: {"cards": [("CVE-2099-1008", 0.30, False, 90, "unpatched")]},
    5: {"cards": [("CVE-2099-1004", 0.50, False, 60, "patched")]},
    6: {"monitored": True, "cards": [("CVE-2099-1001", 0.90, True, 12, "patched"), ("CVE-2099-1003", 0.20, False, 150, "patched")]},
    8: {"cards": [("CVE-2099-1009", 0.95, True, 2, "unpatched")]},
    12: {"cards": [("CVE-2099-1005", 0.10, False, None, "patched"), ("CVE-2099-1007", 0.35, False, 75, "unpatched")]},
    17: {"state": "compromised", "cards": [("CVE-2099-1002", 0.60, False, 300, "unpatched"), ("CVE-2099-1006", 0.40, False, 70, "patched")]},
})

# ------------------------------------------------------------------ 2. exposure
EXPOSURE = {
    "red1": {**span(0, 9, W("infiltrating")), 10: A("EXPLOIT", "breach", "CVE-2099-2001", 0.80),
             **span(11, 22, W("digging in")), 23: M(1)},
    "red2": {**span(0, 9, W("infiltrating")), 10: M(18), 11: M(17),
             12: A("EXPLOIT", "failed", "CVE-2099-2002", 0.50), **span(13, 17, W("regrouping")),
             18: A("EXPLOIT", "breach", "CVE-2099-2002", 0.50), 19: W("digging in"),
             20: ("WAIT", "ejected", 19, None, None), **span(21, 23, W("ejected"))},
    "blue1": {0: A("PATCH", "patching", "CVE-2099-2003", 0.05), 1: A("PATCH", "patched", "CVE-2099-2003", 0.05)},
    "blue2": {15: M(17), 16: A("MONITOR", "monitoring"), 20: A("ISOLATE", "isolated"),
              21: A("PATCH", "patching", "CVE-2099-2002", 0.50), 22: A("PATCH", "patched", "CVE-2099-2002", 0.50)},
    "blue3": {11: M(6), 12: M(5), 13: M(0), 14: A("RESTORE", "restoring"), 15: A("RESTORE", "working"),
              16: A("RESTORE", "restored")},
    "p1": {20: M(10), 21: M(15), 23: A("PATCH", "patching", "CVE-2099-2011", 0.70)},
    "p2": {2: A("PATCH", "patching", "CVE-2099-2004", 0.02), 3: A("PATCH", "patched", "CVE-2099-2004", 0.02)},
}
EXPOSURE_STATE = board("m-fixture-exposure", {
    "red": 26.0, "blue": -0.83, "breaches_total": 2, "breaches_in_top50": 2,
    "breakdown": {"blue": {"patch": 0.17, "restore": 3.0, "isolate_cost": -4.0}, "red": {"first_breach": 10.0, "held": 16.0}}}, {
    0: {"cards": [("CVE-2099-2001", 0.80, True, 3, "patched")]},
    3: {"cards": [("CVE-2099-2005", 0.94, True, 1, "unpatched"), ("CVE-2099-2006", 0.45, False, 20, "unpatched")]},
    6: {"cards": [("CVE-2099-2003", 0.05, False, None, "patched"), ("CVE-2099-2007", 0.60, False, 15, "unpatched")]},
    8: {"cards": [("CVE-2099-2004", 0.02, False, None, "patched"), ("CVE-2099-2012", 0.90, True, 2, "unpatched")]},
    10: {"cards": [("CVE-2099-2013", 0.50, False, 30, "unpatched")]},
    11: {"cards": [("CVE-2099-2008", 0.35, False, 45, "unpatched")]},
    12: {"cards": [("CVE-2099-2009", 0.31, False, 55, "unpatched"), ("CVE-2099-2010", 0.29, False, 60, "unpatched")]},
    15: {"cards": [("CVE-2099-2011", 0.70, False, 10, "patching")]},
    17: {"state": "isolated", "cards": [("CVE-2099-2002", 0.50, False, 40, "patched", 12)]},
})

# ------------------------------------------------------------------ 3. blind spots
BLIND = {
    "red1": {**span(0, 9, W("infiltrating")), 10: M(5), 11: A("EXPLOIT", "failed", "CVE-2099-3001", 0.55),
             **span(12, 16, W("regrouping")), 17: M(10), 18: A("SCAN", "scanned"),
             23: A("EXPLOIT", "breach", "CVE-2099-3002", 0.45)},
    "red2": {**span(0, 9, W("infiltrating")), 10: M(14), 11: M(13), 12: A("SCAN", "scanned"),
             13: A("EXPLOIT", "failed", "CVE-2099-3003", 0.40), **span(14, 18, W("regrouping")),
             22: A("EXPLOIT", "breach", "CVE-2099-3003", 0.40), 23: W("digging in")},
    "blue1": {8: M(5), 9: A("MONITOR", "monitoring"), 10: M(6)},
    "blue2": {0: A("PATCH", "patching", "CVE-2099-3004", 0.25), 1: A("PATCH", "patched", "CVE-2099-3004", 0.25)},
    "p2": {20: M(13), 22: A("PATCH", "patching", "CVE-2099-3003", 0.40), 23: A("PATCH", "patched", "CVE-2099-3003", 0.40)},
}
BLIND_STATE = board("m-fixture-blind", {
    "red": 14.0, "blue": 0.75, "breaches_total": 2, "breaches_in_top50": 0,
    "breakdown": {"blue": {"patch": 0.75, "restore": 0.0, "isolate_cost": 0.0}, "red": {"first_breach": 10.0, "held": 4.0}}}, {
    5: {"cards": [("CVE-2099-3001", 0.55, False, 120, "unpatched")]},
    10: {"state": "compromised", "cards": [("CVE-2099-3002", 0.45, False, None, "unpatched")]},
    12: {"cards": [("CVE-2099-3004", 0.25, False, 200, "patched")]},
    13: {"state": "compromised", "cards": [("CVE-2099-3003", 0.40, False, 80, "patched")]},
})


def main() -> None:
    write("slow_response", "m-fixture-slow", SLOW, SLOW_STATE)
    write("exposure", "m-fixture-exposure", EXPOSURE, EXPOSURE_STATE)
    write("blind_spots", "m-fixture-blind", BLIND, BLIND_STATE)
    print("wrote 3 fixtures, 168 ledger rows each")


if __name__ == "__main__":
    main()
