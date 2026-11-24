#!/usr/bin/env python3
"""Pipeline metrics next to the game's local Python metrics, for one fixture folder.

    python tests/pipeline/coach_compare.py tests/pipeline/coach_fixtures/exposure
    python tests/pipeline/coach_compare.py --all          # the three fixture folders

A fixture folder holds ledger.jsonl and state.json (the two inputs of the coach pipeline). The
script runs the real pipeline on them through scripts/coach.py (needs WSL + bruin) and prints its
figures. It then looks in game/backup.py for a function that computes the same metrics from the
SAME two inputs, that is from ledger rows and the public state message, with no live MatchState:

    names tried, in order:  metrics_from_inputs, metrics_from_rows, local_metrics, compute_metrics,
                            coach_metrics, team_metrics, metrics
    calls tried:            f(rows, state)  then  f(state, rows)      (rows: list of dicts, state: dict)
    result accepted:        a dict in the pipeline's shape (has "team"), or a dict in the layout of
                            backup.analyse(...).numbers (has "held_share"), which is mapped with
                            game.coach_bruin.as_backup_numbers

If one exists, every figure is compared and each disagreement is printed (exit code 1). If none
exists the script says so (exit code 0): at the time of writing game/backup.py only has
analyse(state: MatchState, ledger_rows), which needs the live match state and its state.track, so
it cannot be fed from a fixture folder. The comparison against analyse(...).numbers on live
simulated matches is tests/pipeline/coach_sim_check.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CANDIDATES = ("metrics_from_inputs", "metrics_from_rows", "local_metrics", "compute_metrics",
              "coach_metrics", "team_metrics", "metrics")
# keys of the numbers layout whose local value is rounded differently or is not exact in the pipeline
TOLERANCE = {"avg_breach_ticks": 0.06, "held_share": 0.0011, "hidden_share": 0.0011, "worth": 0.011,
             "wait_share": 0.0011, "held": 0.011, "red": 0.011, "blue": 0.011}
SIGHTING_KEYS = {"hidden_ticks", "hidden_share", "detections"}


def run_pipeline(folder: Path, out: Path) -> tuple[dict, dict | None]:
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "coach.py"), "--match", state["match_id"],
                           "--ledger", str(folder / "ledger.jsonl"), "--state", str(folder / "state.json"),
                           "--out", str(out)], capture_output=True, text=True, cwd=str(ROOT))
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    return res, (json.loads(out.read_text(encoding="utf-8")) if res["ok"] else None)


def local_function():
    """(name, function) of the first candidate game.backup exposes, or (None, why)."""
    try:
        from game import backup
    except Exception as exc:        # noqa: BLE001
        return None, f"game.backup could not be imported ({type(exc).__name__}: {exc})"
    for name in CANDIDATES:
        fn = getattr(backup, name, None)
        if callable(fn):
            return name, fn
    have = sorted(n for n in dir(backup) if not n.startswith("_") and callable(getattr(backup, n))
                  and getattr(getattr(backup, n), "__module__", "") == "game.backup")
    return None, ("game.backup has none of: " + ", ".join(CANDIDATES) + ". Its public callables are: "
                  + ", ".join(have))


def call_local(fn, rows: list[dict], state: dict):
    for args in ((rows, state), (state, rows)):
        try:
            out = fn(*args)
        except Exception:           # noqa: BLE001
            continue
        if isinstance(out, dict) and ("team" in out or "held_share" in out):
            return out
    return None


def flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def differences(pipeline: dict, local: dict, exact_sightings: bool) -> list[str]:
    a, b = flat(pipeline), flat(local)
    out = []
    for key in sorted(set(a) & set(b)):
        leaf = key.rsplit(".", 1)[-1]
        if leaf in SIGHTING_KEYS and not exact_sightings:
            continue                 # a bound, not the exact value: see the README
        x, y = a[key], b[key]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
            if abs(float(x) - float(y)) > TOLERANCE.get(leaf, 1e-9):
                out.append(f"{key}: pipeline {x}  local {y}")
        elif x != y:
            out.append(f"{key}: pipeline {x!r}  local {y!r}")
    only = sorted(set(b) - set(a))
    if only:
        out.append("only in the local metrics (not compared): " + ", ".join(only))
    return out


def compare_folder(folder: Path) -> int:
    from game import coach_bruin as cb
    print(f"== {folder.name}")
    with tempfile.TemporaryDirectory(prefix="coach-compare-") as tmp:
        res, metrics = run_pipeline(folder, Path(tmp) / "metrics.json")
    if not res["ok"]:
        print(f"pipeline failed: {res['message']}")
        return 1
    print(f"pipeline: {res['checks_passed']} checks passed in {res['seconds']} s, run {res['pipeline_run_id']}")
    for key, value in flat({"team": metrics["team"], "agents": metrics["agents"], "players": metrics["players"]}).items():
        print(f"  {key:55s} {value}")
    name, fn = local_function()
    if name is None:
        print(f"local metrics: not compared. {fn}")
        return 0
    rows = [json.loads(x) for x in (folder / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    local = call_local(fn, rows, state)
    if local is None:
        print(f"local metrics: game.backup.{name} exists but did not return a metrics dict for (rows, state) "
              f"or (state, rows); not compared.")
        return 0
    mine = metrics if "team" in local else cb.as_backup_numbers(metrics)
    diffs = differences(mine, local, bool(metrics["team"]["sightings_exact"]))
    real = [d for d in diffs if not d.startswith("only in")]
    for d in diffs:
        print("  DIFF " + d)
    print(f"local metrics (game.backup.{name}): {len(real)} disagreement(s)")
    return 1 if real else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", nargs="?", help="a folder with ledger.jsonl and state.json")
    ap.add_argument("--all", action="store_true", help="the three folders under tests/pipeline/coach_fixtures")
    args = ap.parse_args()
    base = Path(__file__).resolve().parent / "coach_fixtures"
    folders = [base / n for n in ("slow_response", "exposure", "blind_spots")] if args.all or not args.folder \
        else [Path(args.folder)]
    return max(compare_folder(f) for f in folders)


if __name__ == "__main__":
    sys.exit(main())
