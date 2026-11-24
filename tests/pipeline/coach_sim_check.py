#!/usr/bin/env python3
"""Check the coach pipeline against the game engine itself, on freshly simulated matches.

Run as a script from the repo root (needs WSL + bruin, like the other tests/pipeline files):

    python tests/pipeline/coach_sim_check.py                      # seeds 1-6, tick 40, humans idle and scripted
    python tests/pipeline/coach_sim_check.py --seeds 3 --tick 60 --seen
    python tests/pipeline/coach_sim_check.py --dump DIR --seed 7  # only write ledger.jsonl + state.json

For each seed it plays a headless match (game.simulate's loop, no server, no ports, no ledger
files) up to the checkpoint tick, writes the ledger rows and the public state message exactly as
the game would hand them to game/coach_bruin.py, runs the real pipeline through scripts/coach.py,
and compares every figure with what the game's own bookkeeping says:

  - state.track (written by rules.step): breaches with their monitored / untouched / open flags,
    restores, detections, attacker ticks out of sight
  - state.score (the breakdown the pipeline's blocking reconciliation checks against)
  - game.backup.analyse(state, rows).numbers, when game/backup.py has it

The sighting figures (attackers out of sight, detections) are exact only with --seen, which adds
the optional per-row "seen" flag; without it the pipeline derives them from the rules and reports
two bounds, and this script checks that the engine's value lies between them.
Exit code 0 when every seed agrees.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from game import config, match, state as st, world  # noqa: E402
from game.simulate import scripted_patchers  # noqa: E402


def play(seed: int, ticks: int, humans: str = "idle", seen: bool = False):
    """(state, rows) after `ticks` ticks of a headless match."""
    serving = world.load_current()
    state = st.new_match(serving, seed, f"m-sim-{seed}-{humans}", config.TUNING)
    rng = random.Random(seed)
    rows: list[dict] = []
    while state.phase == "running" and state.tick < ticks:
        acts = scripted_patchers(state) if humans == "patch" else {}
        res = match.run_tick(state, acts, rng)
        for r in res.rows:
            r = dict(r)
            if seen and r["team"] == "red":
                r["seen"] = bool(state.actors[r["actor_id"]].revealed)
            rows.append(r)
    return state, rows


def write_inputs(folder: Path, state, rows: list[dict]) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    ledger, board = folder / "ledger.jsonl", folder / "state.json"
    with open(ledger, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    snap = st.snapshot(state)
    snap["tuning"] = dataclasses.asdict(state.tuning)
    board.write_text(json.dumps(snap, separators=(",", ":")), encoding="utf-8")
    return ledger, board


def run_pipeline(match_id: str, ledger: Path, board: Path, out: Path) -> tuple[dict, dict | None]:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "coach.py"), "--match", match_id,
                           "--ledger", str(ledger), "--state", str(board), "--out", str(out)],
                          capture_output=True, text=True, cwd=str(ROOT))
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    metrics = json.loads(out.read_text(encoding="utf-8")) if res["ok"] else None
    return res, metrics


def engine_figures(state, rows: list[dict]) -> dict:
    """The same figures from the engine's own bookkeeping (state.track, state.score, the cards)."""
    tr, sc = state.track, state.score
    restores = tr["restores"]
    eps = []
    for b in tr["breaches"]:
        r = next((r for r in restores if r["node"] == b["node"] and r["tick"] > b["tick"]), None)
        eps.append((r["tick"] if r else state.tick) - b["tick"])
    out = {
        "red_points_from_holding": round(sc.held, 2),
        "red_points_from_first_breaches": round(sc.first_breach, 2),
        "breaches": len(tr["breaches"]),
        "avg_ticks_breached": round(sum(eps) / len(eps), 2) if eps else 0.0,
        "longest_ticks_breached": max(eps, default=0),
        "machines_breached_now": sum(1 for n in state.nodes if n.is_compromised),
        "open_hot_cards": sum(1 for n in state.nodes if n.value >= 2 for c in n.cards
                              if c.patch_state == "unpatched" and c.score >= 0.30),
        "breaches_through_long_open_cards": sum(1 for b in tr["breaches"] if b["untouched"] and b["open_ticks"] >= 10),
        "breaches_through_top50_cards": sc.breaches_in_top50,
        "breaches_on_unmonitored_machines": sum(1 for b in tr["breaches"] if not b["monitored"]),
        "attacker_ticks": tr["red_ticks"],
        "attacker_ticks_unseen": tr["hidden_ticks"],
        "detections": len(tr["detections"]),
    }
    actors = {}
    for aid in ("blue1", "blue2", "blue3", "p1", "p2"):
        mine = [r for r in rows if r["actor_id"] == aid]
        cards = [(n, c) for n in state.nodes for c in n.cards if c.patched_by_id == aid]
        actors[aid] = {
            "cards_patched": len(cards),
            "patch_points": round(sum(c.patch_gain or 0.0 for _, c in cards), 2),
            "patch_worth": round(sum(n.value * c.score for n, c in cards), 2),
            "restores": sum(1 for r in restores if r["actor"] == aid),
            "restores_started": sum(1 for r in mine if r["action"] == "RESTORE" and r["outcome"] == "restoring"),
            "monitors": sum(1 for r in mine if r["action"] == "MONITOR" and r["outcome"] == "monitoring"),
            "wait_share": round(sum(1 for r in mine if r["action"] == "WAIT") / len(mine), 3) if mine else 0.0,
        }
    out["actors"] = actors
    return out


def compare(seed: int, humans: str, tick: int, seen: bool, folder: Path) -> list[str]:
    """Problems found for one seed (empty = agreement)."""
    state, rows = play(seed, tick, humans, seen)
    ledger, board = write_inputs(folder, state, rows)
    res, m = run_pipeline(state.match_id, ledger, board, folder / "metrics.json")
    label = f"seed {seed} humans {humans}"
    if not res["ok"]:
        return [f"{label}: pipeline failed: {res['message']}"]
    eng = engine_figures(state, rows)
    team, bad = m["team"], []
    for key, want in eng.items():
        if key in ("actors", "attacker_ticks_unseen", "detections"):
            continue
        got = team[key]
        if abs(float(got) - float(want)) > 0.011:
            bad.append(f"{label}: team.{key} pipeline {got} engine {want}")
    # sightings: exact with seen flags or when the two bounds coincide, else the engine lies between
    lo, hi = team["attacker_ticks_unseen_if_all_detected"], team["attacker_ticks_unseen"]
    unseen = eng["attacker_ticks_unseen"]
    if team["sightings_exact"]:
        if hi != unseen or team["detections"] != eng["detections"]:
            bad.append(f"{label}: sightings said exact but unseen {hi} vs engine {unseen}, "
                       f"detections {team['detections']} vs engine {eng['detections']}")
    elif not (lo <= unseen <= hi):
        bad.append(f"{label}: engine unseen ticks {unseen} outside the pipeline's bounds {lo}..{hi}")
    for aid, figs in eng["actors"].items():
        got_all = (m["agents"] if aid.startswith("blue") else m["players"])[aid]
        for key, want in figs.items():
            if abs(float(got_all[key]) - float(want)) > 0.011:
                bad.append(f"{label}: {aid}.{key} pipeline {got_all[key]} engine {want}")
    try:                                    # game/backup.py is another builder's file: optional
        from game import backup as bk
        n = bk.analyse(state, rows).numbers
        pairs = {"held_share": "red_share_from_holding", "breaches": "breaches",
                 "longest_breach_ticks": "longest_ticks_breached", "breached_now": "machines_breached_now",
                 "open_hot": "open_hot_cards", "breaches_long_open": "breaches_through_long_open_cards",
                 "breaches_top50": "breaches_through_top50_cards",
                 "breaches_unmonitored": "breaches_on_unmonitored_machines"}
        for theirs, mine in pairs.items():
            if theirs in n and abs(float(n[theirs]) - float(team[mine])) > 0.0011:
                bad.append(f"{label}: backup.analyse {theirs} {n[theirs]} vs pipeline {mine} {team[mine]}")
        for pid in ("p1", "p2"):
            w = (n.get("players") or {}).get(pid, {}).get("walked_past")
            if w is not None and w != m["players"][pid]["near_breached_without_restoring"]:
                bad.append(f"{label}: backup walked_past[{pid}] {w} vs pipeline "
                           f"{m['players'][pid]['near_breached_without_restoring']}")
    except Exception as exc:                # noqa: BLE001
        bad.append(f"{label}: could not compare with game.backup.analyse ({type(exc).__name__}: {exc})")
    print(f"{'ok  ' if not bad else 'FAIL'} {label}: tick {tick}, {eng['breaches']} breaches, red "
          f"{state.score.red:.1f} blue {state.score.blue:.1f}, unseen {unseen}/{eng['attacker_ticks']} "
          f"(pipeline {lo}..{hi}, exact={team['sightings_exact']}), {res['checks_passed']} checks, {res['seconds']}s")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seeds", type=int, default=6, help="how many seeds, starting at --seed")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tick", type=int, default=40, help="checkpoint tick")
    ap.add_argument("--seen", action="store_true", help='add the optional "seen" flag to the attackers\' rows')
    ap.add_argument("--humans", choices=("idle", "patch", "both"), default="both")
    ap.add_argument("--dump", help="only write ledger.jsonl and state.json for --seed into this folder")
    args = ap.parse_args()
    if args.dump:
        state, rows = play(args.seed, args.tick, "patch" if args.humans == "patch" else "idle", args.seen)
        ledger, board = write_inputs(Path(args.dump), state, rows)
        print(json.dumps({"match_id": state.match_id, "ledger": str(ledger), "state": str(board), "rows": len(rows)}))
        return 0
    bad: list[str] = []
    modes = ("idle", "patch") if args.humans == "both" else (args.humans,)
    with tempfile.TemporaryDirectory(prefix="coach-sim-") as tmp:
        for seed in range(args.seed, args.seed + args.seeds):
            for humans in modes:
                bad += compare(seed, humans, args.tick, args.seen, Path(tmp) / f"{seed}-{humans}")
    for line in bad:
        print("  " + line)
    print(f"{'PASS' if not bad else 'FAIL'}: {len(bad)} disagreement(s)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
