"""Headless match runner for balance checks.

    python -m game.simulate --ticks 150 --seed 7 --fast
    python -m game.simulate --seeds 20 --seed 1 --fast        # win split over 20 seeds

Humans p1/p2 send nothing, so they WAIT every tick. --fast is the default headless mode
(no sleeping, no LLM); without it the runner sleeps the real tick length.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
from pathlib import Path

from . import config, match, state as st, world
from .ledger import Ledger, NullLedger


def run_match(serving, seed: int, ticks: int = 150, tuning: config.Tuning | None = None,
              drop_tick: int = -1, next_serving=None, ledger=None, tick_seconds: float = 0.0,
              match_id: str | None = None, on_tick=None) -> dict:
    tuning = (tuning or config.TUNING).with_overrides(ticks_total=ticks)
    state = st.new_match(serving, seed, match_id or f"m-sim-{seed}", tuning)
    rng = random.Random(seed)
    ledger = ledger or NullLedger()
    actions = collections.Counter()
    outcomes = collections.Counter()
    event_kinds = collections.Counter()
    events_log: list[dict] = []
    while state.phase == "running":
        t0 = time.monotonic()
        pending = next_serving if (next_serving is not None and state.tick == drop_tick) else None
        res = match.run_tick(state, {}, rng, new_world=pending)
        ledger.write(res.rows)
        for r in res.rows:
            actions[f"{r['actor_id']}:{r['action']}"] += 1
            outcomes[r["outcome"]] += 1
        for e in res.events:
            event_kinds[e["kind"]] += 1
            events_log.append({"tick": state.tick, **e})
        if on_tick:
            on_tick(state, res)
        if tick_seconds > 0:
            time.sleep(max(0.0, tick_seconds - (time.monotonic() - t0)))
    s = st.score_public(state)
    winner = "red" if s["red"] > s["blue"] else "blue" if s["blue"] > s["red"] else "draw"
    patched = sum(1 for n in state.nodes for c in n.cards if c.patch_state == "patched")
    total = sum(len(n.cards) for n in state.nodes)
    return {"seed": seed, "match_id": state.match_id, "ticks": state.tick, "winner": winner, "score": s,
            "cards_patched": patched, "cards_total": total, "ledger_rows": ledger.rows_written,
            "actions": dict(sorted(actions.items())), "outcomes": dict(sorted(outcomes.items())),
            "events": dict(sorted(event_kinds.items())), "world_asof": state.world.get("asof_date"),
            "score_source": state.world.get("score_source"), "events_log": events_log, "state": state}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Headless Patch Tuesday match")
    ap.add_argument("--ticks", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--seeds", type=int, default=1, help="run this many consecutive seeds and print the win split")
    ap.add_argument("--fast", action="store_true", help="no real-time delay (default behaviour headless)")
    ap.add_argument("--realtime", action="store_true", help="sleep the real tick length between ticks")
    ap.add_argument("--serving", type=str, default=None, help="serving folder (default: serving/ else serving_mock/)")
    ap.add_argument("--next", type=str, default=None, help="folder holding the next week, dropped at --drop-tick")
    ap.add_argument("--drop-tick", type=int, default=60, help="tick of the mid-match world change, -1 for none")
    ap.add_argument("--ledger-dir", type=str, default=None, help="write game.duckdb and ledger.jsonl into this folder")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="override a tuning constant")
    ap.add_argument("--events", action="store_true", help="print the event log of each match")
    args = ap.parse_args(argv)

    serving = world.load_serving(Path(args.serving)) if args.serving else world.load_current()
    next_dir = Path(args.next) if args.next else Path(serving.directory) / "next"
    next_serving = None
    if args.drop_tick >= 0 and (next_dir / "world.json").is_file():
        next_serving = world.load_serving(next_dir)
    overrides = dict(kv.split("=", 1) for kv in args.set)
    tuning = config.TUNING.with_overrides(**overrides) if overrides else config.TUNING

    ledger = None
    if args.ledger_dir:
        d = Path(args.ledger_dir)
        ledger = Ledger(d / "game.duckdb", d / "ledger.jsonl", d / "reasons.jsonl")

    results = []
    for seed in range(args.seed, args.seed + args.seeds):
        r = run_match(serving, seed, args.ticks, tuning, args.drop_tick, next_serving, ledger,
                      tick_seconds=config.TICK_SECONDS_DEFAULT if args.realtime else 0.0)
        r.pop("state")
        log = r.pop("events_log")
        results.append(r)
        if args.events:
            for e in log:
                print(f"  t{e['tick']:03d} [{e['kind']}] {e['text']}")
        if args.seeds == 1:
            print(json.dumps(r, indent=1))
        else:
            s = r["score"]
            print(f"seed {seed:3d}: {r['winner']:5s} red {s['red']:7.1f} blue {s['blue']:7.1f} "
                  f"breaches {s['breaches_total']:2d} (top50 {s['breaches_in_top50']:2d}) "
                  f"patched {r['cards_patched']}/{r['cards_total']} rows {r['ledger_rows']}")
    if args.seeds > 1:
        wins = collections.Counter(r["winner"] for r in results)
        n = len(results)
        print(json.dumps({
            "data": serving.source, "asof": serving.asof, "matches": n, "ticks": args.ticks,
            "red_wins": wins["red"], "blue_wins": wins["blue"], "draws": wins["draw"],
            "avg_red": round(sum(r["score"]["red"] for r in results) / n, 1),
            "avg_blue": round(sum(r["score"]["blue"] for r in results) / n, 1),
            "avg_breaches": round(sum(r["score"]["breaches_total"] for r in results) / n, 2),
            "avg_patched": round(sum(r["cards_patched"] for r in results) / n, 1),
        }))
    if ledger:
        ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
