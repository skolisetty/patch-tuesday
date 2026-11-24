"""game/coach_bruin.py, offline: no WSL, no Bruin, no network. A fake runner stands in for
scripts/coach.py; every test works in a temporary folder (PT_COACH_DIR)."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import pytest

from game import coach_bruin as cb

MATCH = "m-test-1"


def row(tick: int, actor: str, action: str = "WAIT", outcome: str = "waiting", node: int = 0, **extra) -> dict:
    team = "red" if actor.startswith("red") else "blue"
    return {"ledger_id": f"{MATCH}-{tick:03d}-{actor}", "match_id": MATCH, "world_asof": "2026-03-22",
            "pipeline_run_id": "run", "model_version": None, "tick": tick, "ts": "2026-11-23T00:00:00+00:00",
            "actor_id": actor, "team": team, "kind": "human" if actor.startswith("p") else "agent",
            "action": action, "node_id": node, "cve_id": None, "score_used": None, "score_source": None,
            "p_breach": None, "roll": None, "outcome": outcome, "policy": "x",
            "reason_text": "a long sentence nobody needs in the pipeline", "llm_model": None,
            "llm_latency_ms": None, **extra}


def public_state(tick: int = 3) -> dict:
    return {
        "t": "state", "match_id": MATCH, "phase": "running", "tick": tick,
        "nodes": [{"id": i, "x": i % 5, "y": i // 5, "name": f"N-{i:02d}", "value": 1 + i % 3, "state": "healthy",
                   "monitored": False,
                   "cards": [{"card_id": i, "cve_id": f"CVE-2099-{i:04d}", "title": "t", "score": 0.5, "in_kev": i == 0,
                              "patch_state": "unpatched", "is_new": False, "technique_id": None, "tactic": None,
                              "priority_rank": i + 1}]} for i in range(20)],
        "actors": [{"id": "red1", "node_id": 7, "revealed": False}],
        "score": {"red": 5.0, "blue": 1.5, "healthy": 20, "compromised": 0, "isolated": 0, "breaches_total": 1,
                  "breaches_in_top50": 1, "rate": {"red": 0, "blue": 0},
                  "breakdown": {"blue": {"patch": 1.5, "restore": 0.0, "isolate_cost": 0.0},
                                "red": {"first_breach": 5.0, "held": 0.0}}},
    }


def good_metrics() -> dict:
    actor = {"ticks": 3, "ticks_waiting": 3, "wait_share": 1.0, "cards_patched": 0, "patch_worth": 0.0,
             "patch_points": 0.0, "restores": 0, "restores_started": 0, "monitors": 0, "isolates": 0,
             "attackers_revealed": 0, "near_breached_without_restoring": 0}
    team = {"red_points": 5.0, "blue_points": 1.5, "red_points_from_holding": 0.0,
            "red_points_from_first_breaches": 5.0, "red_share_from_holding": 0.0,
            "red_share_from_first_breaches": 1.0, "breaches": 1, "machines_ever_breached": 1,
            "avg_ticks_breached": 2.04, "longest_ticks_breached": 2, "machines_breached_now": 1,
            "machines_breached_now_names": ["N-07"], "open_hot_cards": 4,
            "breaches_through_long_open_cards": 0, "breaches_through_top50_cards": 1,
            "breaches_on_unmonitored_machines": 1, "attacker_ticks": 0, "attacker_ticks_unseen": 0,
            "attackers_unseen_share": 0.0, "attacker_ticks_unseen_if_all_detected": 0,
            "attackers_unseen_share_if_all_detected": 0.0, "detections": 0, "detections_if_all_detected": 0,
            "sightings_exact": True, "sightings_basis": "derived", "cards_patched": 0, "restores": 0,
            "score_reconciliation_max_diff": 0.0}
    return {"match_id": MATCH, "tick": 3, "generated_by": "bruin", "pipeline_run_id": "2026_11_23_10_00_00",
            "team": team, "agents": {a: {"role": "x", **actor} for a in cb.AGENT_IDS},
            "players": {p: dict(actor) for p in cb.PLAYER_IDS}}


def wait_for(handle, seconds: float = 5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        res = cb.poll(handle)
        if res is not None:
            return res
        time.sleep(0.01)
    raise AssertionError("the run never finished")


@pytest.fixture(autouse=True)
def coach_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PT_COACH_DIR", str(tmp_path / "coach"))
    monkeypatch.delenv("PT_COACH_BRUIN", raising=False)
    monkeypatch.delenv("PT_COACH_BRUIN_TIMEOUT", raising=False)
    monkeypatch.delenv("PT_COACH_SCRIPT", raising=False)
    cb._reset_for_tests()
    yield tmp_path / "coach"
    cb._reset_for_tests()


def ok_runner(seen: dict | None = None, metrics: dict | None = None, delay: float = 0.0):
    def run(match_id, tick, ledger, state, out, timeout):
        if delay:
            time.sleep(delay)
        if seen is not None:
            seen.update(match_id=match_id, tick=tick, ledger=Path(ledger), state=Path(state), out=Path(out),
                        timeout=timeout, ledger_text=Path(ledger).read_text(encoding="utf-8"),
                        state_obj=json.loads(Path(state).read_text(encoding="utf-8")))
        Path(out).write_text(json.dumps(metrics if metrics is not None else good_metrics()), encoding="utf-8")
        return {"ok": True, "metrics_path": str(out), "pipeline_run_id": "2026_11_23_10_00_00",
                "checks_passed": 261, "checks_failed": 0, "failed_checks": [], "seconds": 2.1, "message": "ok"}
    return run


# ------------------------------------------------------------------ request / poll
def test_request_returns_at_once_and_poll_is_none_while_the_run_is_in_progress():
    gate = threading.Event()

    def slow(match_id, tick, ledger, state, out, timeout):
        gate.wait(5)
        return ok_runner()(match_id, tick, ledger, state, out, timeout)

    t0 = time.monotonic()
    h = cb.request(MATCH, 3, [row(0, "p1")], public_state(), runner=slow)
    assert time.monotonic() - t0 < 0.5, "request must not wait for the run"
    assert cb.poll(h) is None
    gate.set()
    res = wait_for(h)
    assert res["ok"] is True
    assert cb.poll(h) is res, "poll returns the same result every time once the run is done"


def test_a_good_run_returns_the_metrics_and_the_run_facts():
    res = wait_for(cb.request(MATCH, 3, [row(0, "p1")], public_state(), runner=ok_runner()))
    assert set(res) == {"ok", "metrics", "pipeline_run_id", "checks_passed", "seconds", "message"}
    assert res["ok"] is True and res["checks_passed"] == 261
    assert res["pipeline_run_id"] == "2026_11_23_10_00_00"
    assert res["metrics"]["team"]["breaches"] == 1
    assert set(res["metrics"]["agents"]) == {"blue1", "blue2", "blue3"}
    assert set(res["metrics"]["players"]) == {"p1", "p2"}
    assert isinstance(res["seconds"], float) and res["seconds"] >= 0


def test_input_files_are_written_for_the_pipeline():
    seen: dict = {}
    rows = [row(t, a) for t in range(3) for a in ("p1", "p2", "blue1", "blue2", "blue3", "red1", "red2")]
    rows[5]["seen"] = True                               # red1 at tick 0, the optional flag
    rows.append(row(3, "p1"))                            # the checkpoint tick itself: not played yet
    rows.append({**row(1, "p1"), "match_id": "m-other", "ledger_id": "m-other-001-p1"})
    wait_for(cb.request(MATCH, 3, rows, public_state(), runner=ok_runner(seen)))

    lines = [json.loads(x) for x in seen["ledger_text"].splitlines()]
    assert len(lines) == 21, "rows at the checkpoint tick and rows of another match are left out"
    assert {x["match_id"] for x in lines} == {MATCH} and max(x["tick"] for x in lines) == 2
    assert all("reason_text" not in x and "roll" not in x and "p_breach" not in x for x in lines)
    assert {"ledger_id", "tick", "actor_id", "team", "kind", "action", "node_id", "outcome"} <= set(lines[0])
    assert [x.get("seen") for x in lines].count(True) == 1

    st = seen["state_obj"]
    assert set(st) == {"match_id", "tick", "nodes", "score", "tuning"}, "actors never reach the pipeline"
    assert st["match_id"] == MATCH and st["tick"] == 3 and len(st["nodes"]) == 20
    assert st["nodes"][0]["cards"][0] == {"cve_id": "CVE-2099-0000", "score": 0.5, "in_kev": True,
                                          "priority_rank": 1, "patch_state": "unpatched"}
    assert st["score"]["breakdown"]["red"] == {"first_breach": 5.0, "held": 0.0}
    assert st["tuning"]["first_breach_points"] == 5.0 and st["tuning"]["restore_monitor_ticks"] == 6
    assert seen["tick"] == 3 and seen["timeout"] == cb.DEFAULT_TIMEOUT_S
    assert seen["ledger"].parent.parent.name == "inbox"


def test_rows_added_after_the_request_do_not_reach_the_run():
    seen: dict = {}
    gate = threading.Event()

    def held(match_id, tick, ledger, state, out, timeout):
        gate.wait(5)
        return ok_runner(seen)(match_id, tick, ledger, state, out, timeout)

    rows = [row(0, "p1")]
    state = public_state()
    h = cb.request(MATCH, 3, rows, state, runner=held)
    rows.append(row(1, "p1"))                            # the tick loop keeps appending
    state["score"]["red"] = 99.0                         # ... and the next snapshot differs
    gate.set()
    wait_for(h)
    assert seen["state_obj"]["score"]["red"] == 5.0


def test_dropped_tick_and_alias_names_pass_through():
    st = public_state()
    st["nodes"][2]["cards"] = [{"cve_id": "CVE-1", "danger": 0.4, "known_exploited": True, "priority": 7,
                                "patch_state": "unpatched", "dropped_tick": 12}]
    out = cb.pipeline_state(MATCH, 3, st)
    assert out["nodes"][2]["cards"][0] == {"cve_id": "CVE-1", "score": 0.4, "in_kev": True, "priority_rank": 7,
                                           "patch_state": "unpatched", "dropped_tick": 12}


# ------------------------------------------------------------------ failure, timeout
def test_a_failed_run_is_ok_false_with_the_runners_message():
    def failing(*a):
        return {"ok": False, "checks_passed": 240, "failed_checks": ["coaching.score_reconciliation:custom-check:x"],
                "message": "coach pipeline failed: coaching.score_reconciliation:custom-check:x"}
    res = wait_for(cb.request(MATCH, 3, [row(0, "p1")], public_state(), runner=failing))
    assert res["ok"] is False and res["metrics"] is None and res["pipeline_run_id"] is None
    assert "score_reconciliation" in res["message"] and res["checks_passed"] == 240


def test_a_runner_that_raises_never_reaches_the_caller():
    def boom(*a):
        raise RuntimeError("wsl exploded")
    res = wait_for(cb.request(MATCH, 3, [row(0, "p1")], public_state(), runner=boom))
    assert res["ok"] is False and "RuntimeError" in res["message"]


def test_a_metrics_file_of_the_wrong_shape_or_match_is_refused():
    bad = good_metrics()
    del bad["agents"]["blue2"]
    res = wait_for(cb.request(MATCH, 3, [], public_state(), runner=ok_runner(metrics=bad)))
    assert res["ok"] is False and "agents" in res["message"]
    other = {**good_metrics(), "match_id": "m-other"}
    res = wait_for(cb.request(MATCH, 3, [], public_state(), runner=ok_runner(metrics=other)))
    assert res["ok"] is False and "another match" in res["message"]


def test_a_stuck_run_times_out_in_poll_and_a_late_result_is_ignored():
    gate = threading.Event()

    def stuck(match_id, tick, ledger, state, out, timeout):
        gate.wait(10)
        return ok_runner()(match_id, tick, ledger, state, out, timeout)

    h = cb.request(MATCH, 3, [], public_state(), runner=stuck, timeout=0.05)
    assert cb.poll(h) is None
    h.started -= 2.0                                     # as if 2 s had passed
    res = cb.poll(h)
    assert res is not None and res["ok"] is False and "did not finish" in res["message"]
    gate.set()
    h.thread.join(5)
    assert cb.poll(h) is res and cb.poll(h)["ok"] is False


def test_the_timeout_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("PT_COACH_BRUIN_TIMEOUT", "7.5")
    assert cb.timeout_seconds() == 7.5
    monkeypatch.setenv("PT_COACH_BRUIN_TIMEOUT", "nonsense")
    assert cb.timeout_seconds() == cb.DEFAULT_TIMEOUT_S
    seen: dict = {}
    monkeypatch.setenv("PT_COACH_BRUIN_TIMEOUT", "3")
    wait_for(cb.request(MATCH, 3, [], public_state(), runner=ok_runner(seen)))
    assert seen["timeout"] == 3.0


def test_the_real_runner_times_out_and_reports_it(tmp_path, monkeypatch):
    script = tmp_path / "slow_coach.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setenv("PT_COACH_SCRIPT", str(script))
    t0 = time.monotonic()
    res = wait_for(cb.request(MATCH, 3, [], public_state(), timeout=0.5), seconds=15)
    assert res["ok"] is False and "did not finish" in res["message"]
    assert time.monotonic() - t0 < 10


def test_the_real_runner_reads_the_last_json_line_of_the_script(tmp_path, monkeypatch):
    script = tmp_path / "fake_coach.py"
    script.write_text(
        "import json, sys\n"
        "out = sys.argv[sys.argv.index('--out') + 1]\n"
        f"open(out, 'w', encoding='utf-8').write({json.dumps(json.dumps(good_metrics()))})\n"
        "print('noise')\n"
        "print(json.dumps({'ok': True, 'metrics_path': out, 'pipeline_run_id': 'r', 'checks_passed': 9,"
        " 'seconds': 1.0, 'message': 'ok'}))\n", encoding="utf-8")
    monkeypatch.setenv("PT_COACH_SCRIPT", str(script))
    res = wait_for(cb.request(MATCH, 3, [row(0, "p1")], public_state()), seconds=20)
    assert res["ok"] is True and res["checks_passed"] == 9 and res["metrics"]["match_id"] == MATCH


def test_poll_with_a_bad_handle_and_request_with_bad_input_never_raise():
    assert cb.poll(None)["ok"] is False
    assert cb.poll("nonsense")["ok"] is False
    res = wait_for(cb.request(MATCH, 3, None, None, runner=ok_runner()))
    assert res["ok"] is True                              # empty inputs are the pipeline's to refuse
    res = wait_for(cb.request(MATCH, 3, [object()], {"nodes": "x"}, runner=ok_runner()))
    assert res["ok"] is False


# ------------------------------------------------------------------ available()
def test_available_is_false_when_switched_off(monkeypatch):
    monkeypatch.setattr(cb, "_has_connection", lambda: True)
    monkeypatch.setattr(cb, "_probe_bruin", lambda: True)
    monkeypatch.setattr(cb.shutil, "which", lambda name: "/usr/bin/" + name)
    assert cb.available() is True
    monkeypatch.setenv("PT_COACH_BRUIN", "0")
    assert cb.available() is False


def test_available_is_false_without_the_script_the_connection_or_wsl(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_probe_bruin", lambda: True)
    monkeypatch.setattr(cb.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cb, "_has_connection", lambda: False)
    assert cb.available() is False                        # no duckdb-coach in .bruin.yml
    monkeypatch.setattr(cb, "_has_connection", lambda: True)
    assert cb.available() is True
    monkeypatch.setenv("PT_COACH_SCRIPT", str(tmp_path / "missing.py"))
    assert cb.available() is False                        # no runner script
    monkeypatch.delenv("PT_COACH_SCRIPT")
    monkeypatch.setattr(cb.os, "name", "nt")
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)
    assert cb.available() is False                        # Windows without wsl.exe


def test_available_turns_false_once_the_background_check_finds_no_bruin(monkeypatch):
    monkeypatch.setattr(cb, "_has_connection", lambda: True)
    monkeypatch.setattr(cb.shutil, "which", lambda name: "/usr/bin/" + name)
    gate = threading.Event()

    def probe():
        gate.wait(5)
        return False

    monkeypatch.setattr(cb, "_probe_bruin", probe)
    t0 = time.monotonic()
    assert cb.available() is True, "unknown yet: optimistic, a run that cannot start comes back ok False"
    assert time.monotonic() - t0 < 0.5, "available() never waits for the check"
    gate.set()
    cb._probe["thread"].join(5)
    assert cb.available() is False


# ------------------------------------------------------------------ housekeeping, logging, mapping
def test_old_run_and_input_folders_are_removed(coach_dir):
    for i in range(8):
        d = coach_dir / f"m-old-{i:03d}"
        d.mkdir(parents=True)
        (d / "metrics.json").write_text("{}", encoding="utf-8")
        t = time.time() - 1000 + i
        import os
        os.utime(d, (t, t))
    (coach_dir / "current").mkdir()
    for i in range(7):
        wait_for(cb.request(MATCH, 3 + i, [], public_state(), runner=ok_runner()))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(list((coach_dir / "inbox").iterdir())) > cb.KEEP_RUNS:
        time.sleep(0.02)
    runs = [p.name for p in coach_dir.iterdir() if p.is_dir() and p.name not in ("current", "inbox")]
    assert sorted(runs) == [f"m-old-{i:03d}" for i in range(3, 8)], "the 5 newest run folders stay"
    assert (coach_dir / "current").is_dir()
    assert len(list((coach_dir / "inbox").iterdir())) <= cb.KEEP_RUNS


def test_info_log_lines_carry_no_absolute_path(caplog):
    def failing(match_id, tick, ledger, state, out, timeout):
        return {"ok": False, "message": f"coach pipeline failed: Error; see {ledger} and C:\\Users\\someone\\x.log "
                                        f"and /home/someone/y.log"}
    with caplog.at_level(logging.INFO, logger="game.coach_bruin"):
        wait_for(cb.request(MATCH, 3, [], public_state(), runner=failing))
        wait_for(cb.request(MATCH, 4, [], public_state(), runner=ok_runner()))
        time.sleep(0.05)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "not analysed by Bruin" in text and "analysed by Bruin, 261 checks passed" in text
    assert "someone" not in text and ":\\" not in text and "/home/" not in text and "/mnt/" not in text


def test_as_backup_numbers_maps_the_metrics_to_the_local_key_names():
    n = cb.as_backup_numbers(good_metrics())
    assert n["held_share"] == 0.0 and n["breaches"] == 1 and n["breached_now_names"] == ["N-07"]
    assert n["avg_breach_ticks"] == 2.0 and n["open_hot"] == 4 and n["breaches_top50"] == 1
    assert n["hidden_share"] == 0.0 and n["sightings_exact"] is True
    assert n["agents"]["blue1"] == {"rows": 3, "waits": 3, "patches": 0, "worth": 0.0, "restores": 0,
                                    "restore_starts": 0, "monitors": 0, "isolates": 0}
    assert n["players"]["p2"]["walked_past"] == 0 and n["players"]["p2"]["wait_share"] == 1.0
