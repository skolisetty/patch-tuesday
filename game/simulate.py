"""Headless match runner for balance checks.

    python -m game.simulate --ticks 150 --seed 7 --fast
    python -m game.simulate --seeds 20 --seed 1 --fast        # win split over 20 seeds

Humans p1/p2 send nothing, so they WAIT every tick (--humans patch scripts them to patch).
--fast is the default headless mode (no sleeping, no LLM); without it the runner sleeps the real
tick length.

"Call for backup" (game/backup.py) runs offline here, with no BAND at all:

    python -m game.simulate --seeds 30 --backup coach            # a human answers "yes" to Coach
    python -m game.simulate --seeds 30 --backup auto             # nobody answers: the timeout rule
    python -m game.simulate --seeds 30 --backup hardener         # a human names that specialist
    python -m game.simulate --seeds 30 --compare --humans patch  # the table: every condition, same seeds
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
from pathlib import Path

from . import backup as bk, config, match, rules, state as st, world
from .ledger import Ledger, NullLedger

BACKUP_MODES = ("off", "coach", "auto", "decline", "responder", "hardener", "sentinel")
HUMAN_MODES = ("idle", "patch")
_DIR_OF = {v: k for k, v in st.DIRS.items()}


def scripted_patchers(state: st.MatchState) -> dict:
    """--humans patch: both players only ever patch. Each walks to the open card with the best
    worth (machine value x danger) per step and patches it. A person is slower than a bot, so each
    acts on every other tick. They never restore, monitor or isolate. No random draw."""
    acts: dict = {}
    taken: set[int] = set()
    for pid in st.HUMAN_IDS:
        a = state.actors[pid]
        player = int(pid[1:])
        if a.busy_ticks > 0 or (state.tick + player) % 2:
            continue
        best = None
        for n in state.nodes:
            if n.state != "healthy":
                continue
            for c in n.unpatched():
                if c.card_id in taken:
                    continue
                key = (n.value * c.score / (1 + st.dist(a.node_id, n.id)), -c.card_id)
                if best is None or key > best[0]:
                    best = (key, n, c)
        if best is None:
            continue
        _, n, c = best
        taken.add(c.card_id)
        if n.id == a.node_id:
            acts[pid] = rules.human_action(state, player, "PATCH", cve=c.cve_id)
        else:
            nxt = st.step_toward(a.node_id, n.id)
            ax, ay = st.node_xy(a.node_id)
            nx, ny = st.node_xy(nxt)
            acts[pid] = rules.human_action(state, player, "MOVE", _DIR_OF[(nx - ax, ny - ay)])
    return acts


def run_match(serving, seed: int, ticks: int = 150, tuning: config.Tuning | None = None,
              drop_tick: int = -1, next_serving=None, ledger=None, tick_seconds: float = 0.0,
              match_id: str | None = None, on_tick=None, backup: str | None = None,
              humans: str = "idle", backup_config: "bk.BackupConfig | None" = None,
              reply: str | None = None, reply_delay: int = 4, reply_by: str = "Sim Player",
              ask_tick: int | None = None) -> dict:
    """One headless match.

    backup: None/"off" = no flow at all (the game exactly as before); "coach" = the flow runs and
    a human answers "yes" `reply_delay` ticks after Coach asks; "auto" = nobody answers (the
    timeout rule decides); "decline" = a human answers "no"; a specialist's name = a human names
    that specialist. `reply` overrides the answer text. humans: "idle" or "patch".
    ask_tick: a human writes "@coach analyze" at that tick (calls Coach before its checkpoint)."""
    tuning = (tuning or config.TUNING).with_overrides(ticks_total=ticks)
    state = st.new_match(serving, seed, match_id or f"m-sim-{seed}", tuning)
    rng = random.Random(seed)
    flow = None
    all_rows: list[dict] = []
    if backup and backup != "off":
        if backup not in BACKUP_MODES:
            raise ValueError(f"unknown backup mode: {backup}")
        flow = bk.BackupFlow(backup_config or bk.BackupConfig())
        flow.reset(state)
        if reply is None:
            reply = {"coach": "yes", "auto": None, "decline": "no"}.get(backup, backup)
    ledger = ledger or NullLedger()
    actions = collections.Counter()
    outcomes = collections.Counter()
    event_kinds = collections.Counter()
    events_log: list[dict] = []
    while state.phase == "running":
        t0 = time.monotonic()
        pending = next_serving if (next_serving is not None and state.tick == drop_tick) else None
        acts = scripted_patchers(state) if humans == "patch" else {}
        res = match.run_tick(state, acts, rng, new_world=pending)
        ledger.write(res.rows)
        if flow is not None:
            all_rows.extend(res.rows)
            if ask_tick is not None and state.tick == ask_tick:
                flow.on_human(state, "analyze", reply_by)
            flow.on_tick(state, res.rows, all_rows)
            if (reply is not None and flow.status == "asked"
                    and state.tick >= flow.asked_tick + reply_delay):
                flow.on_human(state, reply, reply_by)
            if state.phase == "ended":
                flow.on_match_end(state, all_rows)
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
            "score_source": state.world.get("score_source"), "events_log": events_log, "state": state,
            "backup": flow.public(state) if flow is not None else None,
            "says": list(flow.says) if flow is not None else [],
            "analysis": flow.analysis if flow is not None else None}


def summarise(results: list[dict]) -> dict:
    n = len(results)
    wins = collections.Counter(r["winner"] for r in results)
    red = sum(r["score"]["red"] for r in results) / n
    blue = sum(r["score"]["blue"] for r in results) / n
    picks = collections.Counter((r.get("backup") or {}).get("chosen") or "-" for r in results)
    recs = collections.Counter((r.get("backup") or {}).get("recommended") or "-" for r in results)
    joins = [r["backup"]["joined_tick"] for r in results if (r.get("backup") or {}).get("joined_tick") is not None]
    return {"matches": n, "blue_wins": wins["blue"], "red_wins": wins["red"], "draws": wins["draw"],
            "blue_win_rate": round(wins["blue"] / n, 3), "avg_blue": round(blue, 1), "avg_red": round(red, 1),
            "avg_margin": round(blue - red, 1),
            "avg_breaches": round(sum(r["score"]["breaches_total"] for r in results) / n, 2),
            "chosen": dict(picks), "recommended": dict(recs),
            "avg_join_tick": round(sum(joins) / len(joins), 1) if joins else None}


COMPARE_CONDITIONS = (("no backup", "off"), ("Coach's recommendation (human says yes)", "coach"),
                      ("Coach's recommendation (no reply, timeout)", "auto"),
                      ("Responder forced", "responder"), ("Hardener forced", "hardener"),
                      ("Sentinel forced", "sentinel"))


def compare(serving, seeds: list[int], humans: str = "idle", ticks: int = 150, tuning=None,
            drop_tick: int = -1, next_serving=None, backup_config=None, reply_delay: int = 4,
            ask_tick: int | None = None) -> list[dict]:
    """Every condition on the same seeds: one summary row per condition."""
    out = []
    for label, mode in COMPARE_CONDITIONS:
        res = [run_match(serving, s, ticks, tuning, drop_tick, next_serving, backup=mode, humans=humans,
                         backup_config=backup_config, reply_delay=reply_delay, ask_tick=ask_tick) for s in seeds]
        out.append({"condition": label, "mode": mode, "humans": humans, **summarise(res)})
    return out


def compare_markdown(rows: list[dict]) -> str:
    base = rows[0]
    lines = ["| Condition | Blue wins | Blue win rate | Avg Blue | Avg Red | Avg margin (Blue - Red) | Margin vs no backup | Avg breaches |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['condition']} | {r['blue_wins']} / {r['matches']} | {r['blue_win_rate']:.0%} | "
                     f"{r['avg_blue']:.1f} | {r['avg_red']:.1f} | {r['avg_margin']:+.1f} | "
                     f"{r['avg_margin'] - base['avg_margin']:+.1f} | {r['avg_breaches']:.2f} |")
    return "\n".join(lines)


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
    ap.add_argument("--backup", choices=BACKUP_MODES, default="off",
                    help="Call for backup, offline: coach = a human says yes; auto = no reply (timeout); "
                         "decline = a human says no; a specialist's name = a human names it")
    ap.add_argument("--humans", choices=HUMAN_MODES, default="idle", help="idle, or scripted to patch")
    ap.add_argument("--backup-tick", type=int, default=40, help="Coach's checkpoint tick")
    ap.add_argument("--reply-delay", type=int, default=4, help="ticks between Coach's question and the scripted reply")
    ap.add_argument("--ask-tick", type=int, default=None,
                    help="a human writes '@coach analyze' at this tick (calls Coach before its checkpoint)")
    ap.add_argument("--says", action="store_true", help="print what would be said in the BAND room")
    ap.add_argument("--compare", action="store_true",
                    help="run every backup condition on the same seeds and print the table")
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

    bcfg = bk.BackupConfig(checkpoint_tick=args.backup_tick)
    if args.compare:
        rows = compare(serving, list(range(args.seed, args.seed + args.seeds)), args.humans, args.ticks, tuning,
                       args.drop_tick, next_serving, bcfg, args.reply_delay, args.ask_tick)
        print(f"data {serving.source} as of {serving.asof}, {args.seeds} seeds from {args.seed}, "
              f"{args.ticks} ticks, humans {args.humans}")
        print(compare_markdown(rows))
        for r in rows:
            if r["mode"] in ("coach", "auto"):
                print(f"{r['condition']}: recommended {r['recommended']}, joined at tick {r['avg_join_tick']} on average")
        return 0

    results = []
    for seed in range(args.seed, args.seed + args.seeds):
        r = run_match(serving, seed, args.ticks, tuning, args.drop_tick, next_serving, ledger,
                      tick_seconds=config.TICK_SECONDS_DEFAULT if args.realtime else 0.0,
                      backup=args.backup, humans=args.humans, backup_config=bcfg, reply_delay=args.reply_delay,
                      ask_tick=args.ask_tick)
        r.pop("state")
        r.pop("analysis")
        says = r.pop("says")
        if args.says:
            for sy in says:
                print(f"  [{sy.kind}] {sy.speaker} -> {sy.mention}: {sy.text}")
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
            "backup": args.backup, "humans": args.humans,
            "backup_chosen": summarise(results)["chosen"] if args.backup != "off" else None,
        }))
    if ledger:
        ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
