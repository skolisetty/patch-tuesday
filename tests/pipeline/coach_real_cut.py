#!/usr/bin/env python3
"""Cut one real match out of a ledger.jsonl and run the coach pipeline on it.

    python tests/pipeline/coach_real_cut.py --ledger <ledger.jsonl> [--match ID] [--tick 40] [--out DIR]

A ledger file holds no board, so state.json is REBUILT here from the rows, by a small Python replay
that is independent of the pipeline's SQL (machine states tick by tick, then the five score parts).
What that means for the figures of such a run:

  - The reconciliation check compares the pipeline's SQL with this Python replay, not with the
    game's own scoreboard. It is a cross-check of two implementations. (The check against the real
    scoreboard is tests/pipeline/coach_sim_check.py, which has the live state.)
  - The board's cards are only the cards some ledger row names (patched, patching or exploited).
    Cards nobody touched are not in a ledger, so open_hot_cards is a lower bound here.
  - Priorities are not in a ledger: breaches_through_top50_cards is reported as 0 and is NOT a
    finding of this run. dropped_tick is unknown too (cards count as on the board since tick 0).

Standard library only; the ledger file is only read. Prints the runner's result and the metrics.
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAYOUT = [("MAIL-01", 2), ("WEB-01", 2), ("WEB-02", 1), ("VPN-01", 3), ("DNS-01", 1),
          ("APP-01", 2), ("DB-01", 3), ("FILE-01", 2), ("HR-01", 1), ("DEV-01", 1),
          ("CI-01", 2), ("DC-01", 3), ("ERP-01", 3), ("WIKI-01", 1), ("PRINT-01", 1),
          ("BKP-01", 2), ("IOT-01", 1), ("POS-01", 2), ("LAB-01", 1), ("JUMP-01", 2)]
ORDER = ["p1", "p2", "blue1", "blue2", "blue3", "blue4", "red1", "red2"]   # the game's resolve order


def rebuild_state(match_id: str, tick: int, rows: list[dict]) -> dict:
    """Replay the rows: who is breached / isolated when, and what each side scored."""
    state = ["healthy"] * 20            # healthy | compromised | isolated
    under = ["healthy"] * 20            # what an isolated machine returns to
    iso_left = [0] * 20
    mon_left = [0] * 20
    ever = [False] * 20
    cards: dict[tuple[int, str], dict] = {}
    attempted: set[tuple[int, str]] = set()
    sc = collections.Counter()
    by_tick = collections.defaultdict(list)
    for r in rows:
        by_tick[r["tick"]].append(r)
    for t in range(tick):
        for r in sorted(by_tick[t], key=lambda r: ORDER.index(r["actor_id"])):
            n, a, o, cve = r["node_id"], r["action"], r["outcome"], r.get("cve_id")
            if cve and a in ("PATCH", "EXPLOIT"):
                c = cards.setdefault((n, cve), {"cve_id": cve, "score": r.get("score_used") or 0.0, "in_kev": False,
                                                "priority_rank": None, "patch_state": "unpatched"})
                if r.get("score_used") is not None:
                    c["score"] = r["score_used"]
            if a == "PATCH" and o == "patching":
                cards[(n, cve)]["patch_state"] = "patching"
            elif a == "PATCH" and o == "patched":
                cards[(n, cve)]["patch_state"] = "patched"
                if (n, cve) not in attempted:
                    sc["patch"] += LAYOUT[n][1] * (r.get("score_used") or 0.0)
            elif a == "MONITOR" and o == "monitoring":
                mon_left[n] = max(mon_left[n], 3)
            elif a == "ISOLATE" and o == "isolated":
                under[n], state[n], iso_left[n] = state[n], "isolated", 5
            elif a == "RESTORE" and o == "restored":
                if state[n] == "isolated":
                    under[n] = "healthy"
                else:
                    state[n] = "healthy"
                for (m, _), c in cards.items():
                    if m == n and c.get("breached"):
                        c["patch_state"] = "patched"
                mon_left[n] = max(mon_left[n], 6)
                sc["restore"] += 3.0
            elif a == "EXPLOIT" and o in ("failed", "breach"):
                attempted.add((n, cve))
                if o == "breach":
                    state[n] = "compromised"
                    cards[(n, cve)]["breached"] = True
                    sc["breaches"] += 1
                    if not ever[n]:
                        ever[n] = True
                        sc["first_breach"] += 5.0
        for n in range(20):             # end of tick: scoring, then timers
            if state[n] == "compromised":
                sc["held"] += LAYOUT[n][1]
            elif state[n] == "isolated":
                sc["isolate_cost"] -= 1.0
        for n in range(20):
            mon_left[n] = max(0, mon_left[n] - 1)
            if state[n] == "isolated":
                iso_left[n] -= 1
                if iso_left[n] <= 0:
                    state[n], iso_left[n] = under[n], 0
    nodes = []
    for i, (name, value) in enumerate(LAYOUT):
        cs = [{k: v for k, v in c.items() if k != "breached"} for (m, _), c in cards.items() if m == i]
        nodes.append({"id": i, "name": name, "value": value, "state": state[i], "monitored": mon_left[i] > 0, "cards": cs})
    red = sc["first_breach"] + sc["held"]
    blue = sc["patch"] + sc["restore"] + sc["isolate_cost"]
    return {"match_id": match_id, "tick": tick, "nodes": nodes,
            "score": {"red": round(red, 2), "blue": round(blue, 2), "breaches_total": sc["breaches"],
                      "breaches_in_top50": 0,
                      "breakdown": {"blue": {"patch": round(sc["patch"], 2), "restore": round(sc["restore"], 2),
                                             "isolate_cost": round(sc["isolate_cost"], 2)},
                                    "red": {"first_breach": round(sc["first_breach"], 2), "held": round(sc["held"], 2)}}}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", required=True, help="a ledger.jsonl (only read)")
    ap.add_argument("--match", help="match id (default: the last match that reached --tick with a breach before it)")
    ap.add_argument("--tick", type=int, default=40)
    ap.add_argument("--out", help="folder for the cut (default: a temporary folder)")
    args = ap.parse_args()
    by_match = collections.defaultdict(list)
    with open(args.ledger, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                by_match[r["match_id"]].append(r)
    match_id = args.match
    if not match_id:
        for mid, rs in by_match.items():
            if max(r["tick"] for r in rs) >= args.tick - 1 and any(
                    r["action"] == "EXPLOIT" and r["outcome"] == "breach" and r["tick"] < args.tick for r in rs):
                match_id = mid
    if match_id not in by_match:
        print(json.dumps({"ok": False, "message": "no such match in the ledger"}))
        return 1
    rows = [r for r in by_match[match_id] if r["tick"] < args.tick]
    folder = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="coach-real-"))
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "ledger.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    state = rebuild_state(match_id, args.tick, rows)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "coach.py"), "--match", match_id,
                           "--ledger", str(folder / "ledger.jsonl"), "--state", str(folder / "state.json"),
                           "--out", str(folder / "metrics.json")], capture_output=True, text=True, cwd=str(ROOT))
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    print(f"match {match_id}, cut at tick {args.tick}: {len(rows)} ledger rows; rebuilt score {state['score']}")
    print(json.dumps(res))
    if res["ok"]:
        print(json.dumps(json.loads((folder / "metrics.json").read_text(encoding="utf-8")), indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
