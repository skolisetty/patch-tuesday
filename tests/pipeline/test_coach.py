#!/usr/bin/env python3
"""The coach pipeline (pipelines/coach) run for real on hand-checked fixtures.

    python tests/pipeline/test_coach.py        (needs WSL + bruin; run from the repo root)

Not a pytest module on purpose (like leakage_audit.py): it runs Bruin through scripts/coach.py,
about 2 s per case. For each case in coach_fixtures/ (slow_response, exposure, blind_spots) it
runs the pipeline on ledger.jsonl + state.json and compares EVERY figure of the metrics JSON with
expected.json, whose numbers were worked out by hand (coach_fixtures/NOTES.md). Then:

  - a tampered score breakdown must fail the blocking reconciliation check: the runner reports
    ok false and names the check, and no metrics file is written
  - a ledger with a later tick than the board, and a ledger of two matches, must be refused
  - the optional "seen" flags make the sighting figures exact
  - game/coach_bruin.py end to end with the real runner (request, poll, metrics)
  - fog of war: no key or value of the metrics file names an attacker, and --dry-run runs nothing
Exit code 0 when everything passed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "coach_fixtures"
CASES = ("slow_response", "exposure", "blind_spots")
RECONCILE_RED = "coaching.score_reconciliation:custom-check:red_income_from_the_ledger_reconciles_with_the_score_breakdown"
sys.path.insert(0, str(ROOT))

failures: list[str] = []
passed = 0


def check(ok: bool, what: str) -> None:
    global passed
    if ok:
        passed += 1
    else:
        failures.append(what)
        print(f"  FAIL {what}")


def coach(match_id: str, ledger: Path, state: Path, out: Path, *extra: str) -> dict:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "coach.py"), "--match", match_id,
                           "--ledger", str(ledger), "--state", str(state), "--out", str(out), *extra],
                          capture_output=True, text=True, cwd=str(ROOT))
    lines = proc.stdout.strip().splitlines()
    res = json.loads(lines[-1]) if lines else {"ok": False, "message": "no output"}
    res["_exit"] = proc.returncode
    return res


def same(got, want) -> bool:
    if isinstance(want, bool) or isinstance(got, bool):
        return got is want
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(float(got) - float(want)) < 0.0011
    return got == want


def compare(case: str, got: dict, want: dict) -> int:
    """Every figure of expected.json against the pipeline's file, and no figure left unchecked."""
    n = 0
    for key in ("match_id", "tick", "generated_by"):
        check(same(got.get(key), want[key]), f"{case}: {key} is {got.get(key)!r}, expected {want[key]!r}")
        n += 1
    groups = [("team", got.get("team") or {}, want["team"])]
    groups += [(f"agents.{a}", (got.get("agents") or {}).get(a) or {}, w) for a, w in want["agents"].items()]
    groups += [(f"players.{p}", (got.get("players") or {}).get(p) or {}, w) for p, w in want["players"].items()]
    for name, g, w in groups:
        check(set(g) == set(w), f"{case}: {name} has fields {sorted(set(g) ^ set(w))} that are not on both sides")
        for key, value in w.items():
            check(same(g.get(key), value), f"{case}: {name}.{key} is {g.get(key)!r}, expected {value!r}")
            n += 1
    check(set(got) == {"match_id", "tick", "generated_by", "pipeline_run_id", "team", "agents", "players"},
          f"{case}: top-level keys are {sorted(got)}")
    return n


def fixtures(tmp: Path) -> None:
    for case in CASES:
        folder = FIX / case
        want = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        out = tmp / f"{case}.json"
        res = coach(want["match_id"], folder / "ledger.jsonl", folder / "state.json", out)
        check(res["ok"] is True and res["_exit"] == 0, f"{case}: run failed: {res.get('message')}")
        if not res["ok"]:
            continue
        got = json.loads(out.read_text(encoding="utf-8"))
        n = compare(case, got, want)
        check(got.get("pipeline_run_id") == res["pipeline_run_id"] and bool(res["pipeline_run_id"]),
              f"{case}: run id in the file and in the runner's result differ")
        check(res["checks_passed"] > 200 and res["checks_failed"] == 0 and res["failed_checks"] == [],
              f"{case}: checks {res['checks_passed']} passed / {res['checks_failed']} failed")
        print(f"ok   {case}: {n} figures equal expected.json; {res['checks_passed']} checks passed in "
              f"{res['seconds']} s (run {res['pipeline_run_id']})")


def tampered(tmp: Path) -> None:
    """Red's 'held' moved by 3 points in the board: the ledger no longer explains the score."""
    folder = FIX / "slow_response"
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    state["score"]["breakdown"]["red"]["held"] += 3.0
    state["score"]["red"] += 3.0
    bad = tmp / "tampered_state.json"
    bad.write_text(json.dumps(state), encoding="utf-8")
    out = tmp / "tampered.json"
    res = coach("m-fixture-slow", folder / "ledger.jsonl", bad, out)
    check(res["ok"] is False and res["_exit"] == 1, "tampered breakdown: the runner must report ok false")
    check(RECONCILE_RED in res.get("failed_checks", []), f"tampered breakdown: failed checks are {res.get('failed_checks')}")
    check("red_income_from_the_ledger_reconciles" in res.get("message", ""), "tampered breakdown: the message names the check")
    check(res.get("metrics_path") is None and not out.exists(), "tampered breakdown: no metrics file may be written")
    check(not (ROOT / "data" / "coach" / "current" / "metrics.json").exists(),
          "tampered breakdown: data/coach/current/metrics.json must not exist after a failed run")
    print(f"ok   tampered score breakdown refused: {res.get('message')}")


def bad_ledgers(tmp: Path) -> None:
    folder = FIX / "exposure"
    rows = [json.loads(x) for x in (folder / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    late = tmp / "late.jsonl"
    late.write_text("\n".join(json.dumps(r) for r in rows + [{**rows[-1], "tick": 24, "ledger_id": "late-1"}]) + "\n",
                    encoding="utf-8")
    res = coach("m-fixture-exposure", late, folder / "state.json", tmp / "late.json")
    check(res["ok"] is False and any("ledger_ticks_do_not_exceed" in f for f in res["failed_checks"]),
          f"a ledger row at the board's tick must be refused: {res.get('failed_checks')}")
    mixed = tmp / "mixed.jsonl"
    mixed.write_text("\n".join(json.dumps(r) for r in rows[:-1] + [{**rows[-1], "match_id": "m-other"}]) + "\n",
                     encoding="utf-8")
    res = coach("m-fixture-exposure", mixed, folder / "state.json", tmp / "mixed.json")
    check(res["ok"] is False and any("match_id_is_single-valued" in f for f in res["failed_checks"]),
          f"a ledger of two matches must be refused: {res.get('failed_checks')}")
    print("ok   a ledger ahead of the board and a ledger of two matches are refused by blocking checks")


def seen_flags(tmp: Path) -> None:
    """blind_spots with the optional flag, set as if the detection roll at tick 11 had succeeded."""
    folder = FIX / "blind_spots"
    seen = {("red1", 10), ("red1", 11), ("red1", 12), ("red1", 23), ("red2", 22)}
    lines = []
    for x in (folder / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(x)
        if r["team"] == "red":
            r["seen"] = (r["actor_id"], r["tick"]) in seen
        lines.append(json.dumps(r))
    ledger = tmp / "seen.jsonl"
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp / "seen.json"
    res = coach("m-fixture-blind", ledger, folder / "state.json", out)
    check(res["ok"] is True, f"seen flags: run failed: {res.get('message')}")
    if res["ok"]:
        t = json.loads(out.read_text(encoding="utf-8"))["team"]
        check(t["sightings_basis"] == "seen_flags" and t["sightings_exact"] is True, "seen flags: basis/exact")
        check(t["attacker_ticks_unseen"] == 23 and t["attacker_ticks_unseen_if_all_detected"] == 23
              and same(t["attackers_unseen_share"], 0.821) and t["detections"] == 1,
              f"seen flags: unseen {t['attacker_ticks_unseen']}, detections {t['detections']}")
        print("ok   with the optional seen flags the sighting figures are exact (23 of 28 unseen, 1 detection)")


def through_the_game_seam(tmp: Path) -> None:
    from game import coach_bruin as cb
    folder = FIX / "exposure"
    rows = [json.loads(x) for x in (folder / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    check(cb.available() is True, "coach_bruin.available() should be True on this machine")
    t0 = time.monotonic()
    h = cb.request("m-fixture-exposure", 24, rows, state)
    asked = time.monotonic() - t0
    res = None
    while res is None and time.monotonic() - t0 < 30:
        time.sleep(0.05)
        res = cb.poll(h)
    total = time.monotonic() - t0
    check(asked < 0.2, f"request() took {asked:.3f} s, it must return at once")
    check(bool(res) and res["ok"] is True, f"coach_bruin run failed: {res and res['message']}")
    if res and res["ok"]:
        want = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        compare("coach_bruin/exposure", res["metrics"], want)
        n = cb.as_backup_numbers(res["metrics"])
        check(n["open_hot"] == 6 and n["held_share"] == 0.615 and n["players"]["p1"]["walked_past"] == 0,
              "as_backup_numbers mapping")
        print(f"ok   game/coach_bruin.py: request returned in {asked * 1000:.0f} ms, result after {total:.1f} s, "
              f"{res['checks_passed']} checks, run {res['pipeline_run_id']}")


def fog_and_dry_run(tmp: Path) -> None:
    out = tmp / "fog.json"
    folder = FIX / "blind_spots"
    res = coach("m-fixture-blind", folder / "ledger.jsonl", folder / "state.json", out)
    text = out.read_text(encoding="utf-8") if res["ok"] else ""
    check(res["ok"] and "red1" not in text and "red2" not in text and "CVE-" not in text
          and "node_id" not in text and "target" not in text,
          "fog: the metrics file names an attacker, a CVE or a position")
    before = sorted(p.name for p in (ROOT / "data" / "coach").iterdir())
    res = coach("m-dry", folder / "ledger.jsonl", folder / "state.json", tmp / "dry.json", "--dry-run")
    after = sorted(p.name for p in (ROOT / "data" / "coach").iterdir())
    check(res["ok"] is True and res["message"] == "dry run" and before == after and not (tmp / "dry.json").exists(),
          "--dry-run must run nothing and write nothing")
    print("ok   fog of war (no attacker, CVE or position in the metrics file) and --dry-run")


def main() -> int:
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="coach-test-") as d:
        tmp = Path(d)
        for step in (fixtures, tampered, bad_ledgers, seen_flags, through_the_game_seam, fog_and_dry_run):
            step(tmp)
    print(f"{'PASS' if not failures else 'FAIL'}: {passed} checks passed, {len(failures)} failed, in {time.time() - t0:.0f} s")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
