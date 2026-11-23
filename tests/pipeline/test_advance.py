#!/usr/bin/env python3
"""Unit tests of scripts/advance.py's decisions, on real Bruin output captured in fixtures/.

Standard library only:  python tests/pipeline/test_advance.py   (or: python -m pytest tests/pipeline)

Review finding: the EPSS fallback (`bruin run --tag serve`) ran after ANY failed main run, so a
failed leakage or time-travel check upstream of the gate could still reach serving/. The fallback
must run only when the promotion gate's own checks were the only failures.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures"
_spec = importlib.util.spec_from_file_location("advance", ROOT / "scripts" / "advance.py")
advance = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(advance)


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------ failure classification
def test_gate_failure_alone_allows_the_fallback():
    kind, failed = advance.classify(fixture("bruin_gate_failed.txt"))
    assert kind == "gate", (kind, failed)
    assert failed == ["ml.eval_metrics:custom-check:challenger_beats_epss_on_precision@50"]


def test_leakage_check_failure_refuses_the_fallback():
    """Real output of `bruin run ... --var horizon_days=90`: feat.snapshots' blocking check failed."""
    kind, failed = advance.classify(fixture("bruin_leakage_check_failed.txt"))
    assert kind == "other"
    assert failed == ["feat.snapshots:custom-check:every_snapshot_is_a_sunday"]


def test_successful_serve_run_has_no_failures():
    assert advance.classify(fixture("bruin_serve_ok.txt")) == ("none", [])


def test_gate_plus_any_other_failure_refuses_the_fallback():
    out = fixture("bruin_gate_failed.txt") + "\n[02:37:12] Failed: stg.cve_technique:custom-check:ctid_mapping_only_for_cves_already_in_kev (5ms)\n"
    assert advance.classify(out)[0] == "other"
    out = fixture("bruin_gate_failed.txt") + "\n[02:37:12] Failed: feat.fingerprints:custom-check:a_training_snapshot_is_identical_at_every_world_clock (5ms)\n"
    assert advance.classify(out)[0] == "other"
    out = fixture("bruin_gate_failed.txt") + "\n[02:37:12] Failed: ml.eval_metrics:n_candidates:positive (1ms)\n"
    assert advance.classify(out)[0] == "other"


def test_asset_error_and_bruin_errors_refuse_the_fallback():
    assert advance.classify("[01:00:00] Failed: feat.snapshot_features (2s)\n")[0] == "other"
    assert advance.classify("Error: could not open the database\n") == ("other", ["Error"])
    # a summary FAIL line that no "Failed:" line explains
    assert advance.classify("FAIL raw.epss ....\n") == ("other", ["raw.epss"])
    # ... but a summary line for an asset whose gate check already failed is explained by it
    out = "[01:00:00] Failed: ml.eval_metrics:custom-check:challenger_beats_epss_on_precision@50 (3ms)\nFAIL ml.eval_metrics F...\n"
    assert advance.classify(out)[0] == "gate"


def test_diagnostic_failures_never_block():
    out = fixture("bruin_gate_failed.txt") + "\n[02:37:30] Failed: ml.sensitivity (3s)\n"
    kind, failed = advance.classify(out)
    assert kind == "gate" and "ml.sensitivity" in failed
    assert advance.classify("[02:37:30] Failed: ml.sensitivity:masked_hits_at_50:non_negative (1ms)\n")[0] == "none"


def test_every_gate_check_name_exists_in_eval_metrics():
    sql = (ROOT / "pipelines/patch_tuesday/assets/ml/eval_metrics.sql").read_text(encoding="utf-8")
    for chk in advance.GATE_CHECKS:
        asset, _, name = chk.split(":", 2)
        assert asset == "ml.eval_metrics"
        human = name.replace("_", " ")
        assert f"- name: {human}" in sql.lower(), chk


# ------------------------------------------------------------------ run_window decisions (Bruin faked)
class FakeBruin:
    """Replaces advance.bruin and the world.json file for run_window."""

    def __init__(self, world_dir: Path, main_out: str, write_main: dict | None, write_fb: dict | None):
        self.world = world_dir / "world.json"
        self.main_out, self.write_main, self.write_fb = main_out, write_main, write_fb
        self.calls: list[list[str]] = []

    def __call__(self, args, log_file, dry_run):
        self.calls.append(args)
        serve = "--tag" in args
        w = self.write_fb if serve else self.write_main
        if w is not None:
            self.world.write_text(json.dumps([w]), encoding="utf-8")
        return (0 if serve else 1), ("" if serve else self.main_out)


def _run(main_out, before, write_main=None, write_fb=None, end=dt.date(2026, 3, 29)):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "world.json").write_text(json.dumps([before]), encoding="utf-8")
        fake = FakeBruin(d, main_out, write_main, write_fb)
        old = advance.bruin, advance.WORLD, advance.LOG_DIR, advance.ROOT
        advance.bruin, advance.WORLD, advance.LOG_DIR, advance.ROOT = fake, d / "world.json", d / "logs", d
        try:
            res = advance.run_window(end - dt.timedelta(days=6), end, False, False)
        finally:
            advance.bruin, advance.WORLD, advance.LOG_DIR, advance.ROOT = old
        return res, fake.calls


BEFORE = {"asof_date": "2026-03-22", "pipeline_run_id": "run-old", "score_source": "epss"}


def test_run_window_refuses_fallback_after_a_leakage_failure():
    res, calls = _run(fixture("bruin_leakage_check_failed.txt"), BEFORE,
                      write_fb={"asof_date": "2026-03-29", "pipeline_run_id": "run-new", "score_source": "epss"})
    assert len(calls) == 1 and "--tag" not in calls[0]        # the --tag serve rebuild never ran
    assert res["ok"] is False and "every_snapshot_is_a_sunday" in res["message"]
    assert res["asof_date"] == "2026-03-22"


def test_run_window_runs_fallback_after_a_gate_failure():
    res, calls = _run(fixture("bruin_gate_failed.txt"), BEFORE,
                      write_fb={"asof_date": "2026-03-29", "pipeline_run_id": "run-new", "score_source": "epss",
                                "challenger_hits_at_50": 0, "epss_hits_at_50": 1})
    assert len(calls) == 2 and "--tag" in calls[1]
    assert res["ok"] is True and res["gate_passed"] is False and "EPSS" in res["message"]


def test_same_week_rerun_counts_only_with_a_new_run_id():
    """Review finding 2: a re-run of an already-served week must not count as moved because the
    old world.json already carries that asof_date."""
    week = {"asof_date": "2026-03-29", "pipeline_run_id": "run-1", "score_source": "model"}
    res, calls = _run(fixture("bruin_gate_failed.txt"), week, write_main=None,
                      write_fb={**week, "pipeline_run_id": "run-2", "score_source": "epss"})
    assert len(calls) == 2 and res["ok"] and res["score_source"] == "epss"
    res, calls = _run(fixture("bruin_leakage_check_failed.txt"), week)
    assert res["ok"] is False and len(calls) == 1


def test_test_vars_switch_parses_only_simple_pairs():
    os.environ["PT_TEST_VARS"] = "horizon_days=90, bad;rm=1,x=$(y)"
    try:
        assert advance.test_vars() == ["--var", "horizon_days=90"]
    finally:
        del os.environ["PT_TEST_VARS"]
    assert advance.test_vars() == []


# ------------------------------------------------------------------ lock with pid + staleness
def _with_lock(content: str | None, fn):
    with tempfile.TemporaryDirectory() as d:
        lock = Path(d) / ".advance.lock"
        if content is not None:
            lock.write_text(content, encoding="utf-8")
        old = advance.LOCK
        advance.LOCK = lock
        try:
            return fn(lock)
        finally:
            advance.LOCK = old


def test_lock_free_held_and_stale():
    here = "nt" if advance.IS_WINDOWS else "posix"
    assert _with_lock(None, lambda l: advance.lock_status())["lock"] == "free"
    mine = json.dumps({"pid": os.getpid(), "platform": here, "started_epoch": time.time()})
    assert _with_lock(mine, lambda l: advance.lock_status())["lock"] == "held"
    gone = json.dumps({"pid": 2 ** 22 + 12345, "platform": here, "started_epoch": time.time()})
    st = _with_lock(gone, lambda l: advance.lock_status())
    assert st["lock"] == "stale" and "gone" in st["why"]
    old = json.dumps({"pid": os.getpid(), "platform": here, "started_epoch": time.time() - 3 * 3600})
    assert _with_lock(old, lambda l: advance.lock_status())["lock"] == "stale"
    # the old format (no platform) cannot be judged by pid: held until it is old
    assert _with_lock('{"pid": 0, "who": "x"}', lambda l: advance.lock_status())["lock"] == "held"


def test_acquire_removes_a_stale_lock_but_not_a_live_one():
    here = "nt" if advance.IS_WINDOWS else "posix"
    gone = json.dumps({"pid": 2 ** 22 + 12345, "platform": here, "started_epoch": time.time()})

    def take(lock):
        ok = advance.acquire_lock()
        return ok, json.loads(lock.read_text(encoding="utf-8"))
    ok, info = _with_lock(gone, take)
    assert ok and info["pid"] == os.getpid() and info["platform"] == here and info["started_epoch"] > 0
    mine = json.dumps({"pid": os.getpid(), "platform": here, "started_epoch": time.time()})
    assert _with_lock(mine, lambda l: advance.acquire_lock()) is False


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"advance tests: {fails} failed")
    sys.exit(1 if fails else 0)
