"""The Coach's analysis as a Bruin pipeline: the game-side seam.

    from game import coach_bruin
    if coach_bruin.available():
        handle = coach_bruin.request(state.match_id, state.tick, all_rows, snapshot(state))
    ...
    result = coach_bruin.poll(handle)      # None while it runs, then always the same dict
    if result and result["ok"]:
        metrics = result["metrics"]        # the shape in pipelines/coach/README.md

request() returns at once: it copies the row list, serialises the board, and starts one daemon
thread. The thread writes the two input files, runs scripts/coach.py (which runs
`bruin run pipelines/coach` through WSL, about 2 s here) and reads the metrics file. poll() never
blocks and never raises. A run that fails, cannot start, or takes longer than the timeout
(PT_COACH_BRUIN_TIMEOUT, default 20 s) gives {"ok": False, "metrics": None, ...}: the caller then
uses its own local figures. Nothing here touches the match state, the random stream or the tick loop.

Fog of war: the ledger rows passed in hold every actor's rows, the attackers' included. They are
written only to a git-ignored folder under data/coach/ for the pipeline to read, and what comes
back is aggregates (see the README). The board written for the pipeline is cut down to machines,
cards and scores: the `actors` list of the state message is dropped.

Environment:
  PT_COACH_BRUIN=0          switch the pipeline off (available() is False)
  PT_COACH_BRUIN_TIMEOUT    seconds a run may take (default 20)
  PT_COACH_SCRIPT           another runner script (tests)
  PT_COACH_DIR              another working folder than data/coach (tests)
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import config

log = logging.getLogger("game.coach_bruin")

DEFAULT_TIMEOUT_S = 20.0
KEEP_RUNS = 5
AGENT_IDS = ("blue1", "blue2", "blue3")
PLAYER_IDS = ("p1", "p2")
# The tuning constants the pipeline needs (pipelines/coach/pipeline.yml lists the same names).
TUNING_KEYS = ("first_breach_points", "restore_points", "isolate_cost", "monitor_ticks",
               "restore_monitor_ticks", "isolate_ticks", "breach_reveal_ticks", "detect_ticks",
               "red_entry_delay")
LEDGER_KEYS = ("ledger_id", "match_id", "tick", "actor_id", "team", "kind", "action", "node_id",
               "cve_id", "score_used", "outcome", "policy", "seen")

# runner(match_id, tick, ledger_path, state_path, out_path, timeout_s) -> scripts/coach.py's result dict
Runner = Callable[[str, int, Path, Path, Path, float], dict]

_lock = threading.Lock()
_seq = 0
_probe: dict[str, Any] = {"state": None, "thread": None}     # state: None unknown | True | False


def _script() -> Path:
    return Path(os.environ.get("PT_COACH_SCRIPT") or config.ROOT / "scripts" / "coach.py")


def _base_dir() -> Path:
    return Path(os.environ.get("PT_COACH_DIR") or config.DATA_DIR / "coach")


def timeout_seconds() -> float:
    try:
        t = float(os.environ.get("PT_COACH_BRUIN_TIMEOUT") or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return t if t > 0 else DEFAULT_TIMEOUT_S


# ------------------------------------------------------------------------------ availability
def _has_connection() -> bool:
    try:
        return "duckdb-coach" in (config.ROOT / ".bruin.yml").read_text(encoding="utf-8")
    except OSError:
        return False


def _probe_bruin() -> bool:
    """Is `bruin` there? Runs a tiny command through WSL (Windows) or looks on the PATH (WSL)."""
    try:
        if os.name == "nt":
            proc = subprocess.run(["wsl", "-e", "bash", "-lc", "test -x $HOME/.local/bin/bruin || command -v bruin"],
                                  capture_output=True, timeout=15)
            return proc.returncode == 0
        return bool(shutil.which("bruin") or (Path.home() / ".local" / "bin" / "bruin").exists())
    except Exception:           # noqa: BLE001  (no WSL, timeout, ...)
        return False


def _run_probe() -> None:
    ok = _probe_bruin()
    _probe["state"] = ok
    log.info("coach pipeline: bruin %s", "found" if ok else "not found, the Coach analyses locally")


def warm() -> None:
    """Start the one-off background check that Bruin exists (it takes about half a second through
    WSL, so it is never done inside available()). Safe to call any number of times; the server can
    call it at start-up so the answer is known long before the first checkpoint."""
    with _lock:
        if _probe["thread"] is None and _probe["state"] is None:
            th = threading.Thread(target=_run_probe, name="coach-bruin-probe", daemon=True)
            _probe["thread"] = th
            th.start()


def available() -> bool:
    """True when a pipeline run can be asked for: PT_COACH_BRUIN is not 0, the runner script and
    the duckdb-coach connection exist, WSL is there (on Windows) and Bruin was found. Never blocks:
    the Bruin check runs in the background (warm()), and until it has answered this says True if
    everything else is in place. A run that then cannot start simply comes back ok: False."""
    try:
        if (os.environ.get("PT_COACH_BRUIN") or "").strip() == "0":
            return False
        if not _script().is_file() or not _has_connection():
            return False
        if os.name == "nt" and not shutil.which("wsl"):
            return False
        warm()
        return _probe["state"] is not False
    except Exception:           # noqa: BLE001
        return False


def _reset_for_tests() -> None:
    global _seq
    with _lock:
        _probe["state"], _probe["thread"] = None, None
        _seq = 0


# ------------------------------------------------------------------------------ inputs
def pipeline_state(match_id: str, tick: int, public_state: dict) -> dict:
    """The board file the pipeline reads, cut from the game's state message: machines with their
    cards, the score with its breakdown, and the tuning constants. `actors` is not copied."""
    nodes = []
    for n in public_state.get("nodes") or []:
        cards = []
        for c in n.get("cards") or []:
            card = {"cve_id": c.get("cve_id"),
                    "score": c.get("score", c.get("danger")),
                    "in_kev": bool(c.get("in_kev", c.get("known_exploited", False))),
                    "priority_rank": c.get("priority_rank", c.get("priority")),
                    "patch_state": c.get("patch_state")}
            if c.get("dropped_tick") is not None:
                card["dropped_tick"] = c["dropped_tick"]
            cards.append(card)
        nodes.append({"id": n.get("id"), "name": n.get("name"), "value": n.get("value"),
                      "state": n.get("state"), "monitored": bool(n.get("monitored")), "cards": cards})
    sc = public_state.get("score") or {}
    bd = sc.get("breakdown") or {}
    score = {"red": sc.get("red"), "blue": sc.get("blue"),
             "breaches_total": sc.get("breaches_total"), "breaches_in_top50": sc.get("breaches_in_top50"),
             "breakdown": {"blue": dict(bd.get("blue") or {}), "red": dict(bd.get("red") or {})}}
    given = public_state.get("tuning") or {}
    base = dataclasses.asdict(config.TUNING)
    tuning = {k: given.get(k, base.get(k)) for k in TUNING_KEYS}
    return {"match_id": match_id, "tick": int(tick), "nodes": nodes, "score": score, "tuning": tuning}


def ledger_lines(match_id: str, tick: int, rows: list[dict]) -> list[str]:
    """This match's rows before `tick`, as JSON lines with only the fields the pipeline reads."""
    out = []
    for r in rows:
        if r.get("match_id") != match_id:
            continue
        t = r.get("tick")
        if not isinstance(t, int) or t >= tick:
            continue
        out.append(json.dumps({k: r.get(k) for k in LEDGER_KEYS if k in r}, separators=(",", ":")))
    return out


def _safe(match_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(match_id))[:80] or "match"


def cleanup(keep: int = KEEP_RUNS, base: Path | None = None) -> None:
    """Keep the `keep` newest run folders (data/coach/<match>-<tick>/) and the `keep` newest input
    folders (data/coach/inbox/...). Never touches data/coach/current/."""
    base = base or _base_dir()
    try:
        for parent, skip in ((base, {"current", "inbox"}), (base / "inbox", set())):
            if not parent.is_dir():
                continue
            dirs = sorted((p for p in parent.iterdir() if p.is_dir() and p.name not in skip),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            for old in dirs[max(0, keep):]:
                shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


# ------------------------------------------------------------------------------ the run
def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            proc.kill()
    except Exception:           # noqa: BLE001
        try:
            proc.kill()
        except Exception:       # noqa: BLE001
            pass


def subprocess_runner(match_id: str, tick: int, ledger: Path, state: Path, out: Path, timeout: float) -> dict:
    """The real runner: scripts/coach.py in a child process; its last stdout line is the result."""
    argv = [sys.executable, str(_script()), "--match", match_id, "--tick", str(tick),
            "--ledger", str(ledger), "--state", str(state), "--out", str(out), "--keep", str(KEEP_RUNS)]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(config.ROOT))
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return {"ok": False, "message": f"the coach pipeline did not finish in {timeout:g} s"}
    for line in reversed(stdout.decode("utf-8", errors="replace").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"ok": False, "message": f"the coach runner printed no result (exit {proc.returncode})"}


def metrics_problem(metrics: Any, match_id: str) -> str | None:
    """None when `metrics` has the documented shape for this match, else what is wrong."""
    if not isinstance(metrics, dict):
        return "the metrics file is not a JSON object"
    if metrics.get("match_id") != match_id:
        return "the metrics file is for another match"
    if metrics.get("generated_by") != "bruin" or not metrics.get("pipeline_run_id"):
        return "the metrics file was not written by the pipeline"
    if not isinstance(metrics.get("team"), dict):
        return "the metrics file has no team figures"
    for group, ids in (("agents", AGENT_IDS), ("players", PLAYER_IDS)):
        g = metrics.get(group)
        if not isinstance(g, dict) or any(not isinstance(g.get(i), dict) for i in ids):
            return f"the metrics file has no figures for every one of the {group}"
    return None


class Handle:
    """One requested run. Opaque to the caller: hand it back to poll()."""

    def __init__(self, match_id: str, tick: int, timeout: float):
        self.match_id, self.tick, self.timeout = match_id, tick, timeout
        self.started = time.monotonic()
        self.result: dict | None = None
        self._lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def _finish(self, result: dict) -> bool:
        """First result wins (the worker's, or poll()'s timeout)."""
        with self._lock:
            if self.result is None:
                self.result = result
                return True
            return False


def _result(ok: bool, message: str, metrics: dict | None = None, run_id: str | None = None,
            checks: int = 0, seconds: float = 0.0) -> dict:
    return {"ok": ok, "metrics": metrics, "pipeline_run_id": run_id, "checks_passed": int(checks or 0),
            "seconds": round(float(seconds or 0.0), 2), "message": message}


def _work(h: Handle, rows: list[dict], state_text: str, runner: Runner, base: Path, seq: int) -> None:
    t0 = time.monotonic()
    try:
        folder = base / "inbox" / f"{_safe(h.match_id)}-{h.tick:03d}-{seq}"
        folder.mkdir(parents=True, exist_ok=True)
        ledger, state, out = folder / "ledger.jsonl", folder / "state.json", folder / "metrics.json"
        lines = ledger_lines(h.match_id, h.tick, rows)
        ledger.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
        state.write_text(state_text, encoding="utf-8", newline="\n")
        res = runner(h.match_id, h.tick, ledger, state, out, h.timeout)
        seconds = time.monotonic() - t0
        if not isinstance(res, dict) or not res.get("ok"):
            msg = str((res or {}).get("message") or "the coach pipeline failed") if isinstance(res, dict) \
                else "the coach runner returned nothing"
            done = _result(False, msg[:300], None, None, (res or {}).get("checks_passed", 0) if isinstance(res, dict) else 0, seconds)
        else:
            try:
                metrics = json.loads(Path(res.get("metrics_path") or out).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                metrics = None
            problem = metrics_problem(metrics, h.match_id)
            if problem:
                done = _result(False, problem, None, None, res.get("checks_passed", 0), seconds)
            else:
                done = _result(True, f"{res.get('checks_passed', 0)} checks passed", metrics,
                               metrics["pipeline_run_id"], res.get("checks_passed", 0), seconds)
    except Exception as exc:    # noqa: BLE001  (never raise into the game)
        done = _result(False, f"coach pipeline error: {type(exc).__name__}", None, None, 0, time.monotonic() - t0)
    if h._finish(done):
        if done["ok"]:
            log.info("coach pipeline: match %s tick %d analysed by Bruin, %d checks passed in %.1f s (run %s)",
                     h.match_id, h.tick, done["checks_passed"], done["seconds"], done["pipeline_run_id"])
        else:
            log.info("coach pipeline: match %s tick %d not analysed by Bruin (%s); the local figures apply",
                     h.match_id, h.tick, _no_paths(done["message"]))
    cleanup(base=base)


def _no_paths(text: str) -> str:
    """Log lines never carry an absolute path of this machine."""
    return re.sub(r"(?:[A-Za-z]:\\|/(?:mnt|home|Users)/)\S+", "<path>", str(text))


def request(match_id: str, tick: int, ledger_rows: list[dict], public_state: dict, *,
            timeout: float | None = None, runner: Runner | None = None) -> Handle:
    """Start one analysis in the background and return its handle at once. Never raises.

    match_id, tick   the match and its checkpoint: `tick` ticks have been played (state.tick)
    ledger_rows      the match's ledger rows so far, every actor (rows of another match and rows
                     at `tick` or later are left out); each row may carry "seen" (see the README)
    public_state     the state message at the checkpoint (state.snapshot(state)): nodes with cards
                     and score with its breakdown are read, nothing else
    timeout, runner  for tests: seconds allowed, and a stand-in for scripts/coach.py
    """
    global _seq
    h = Handle(str(match_id), int(tick) if isinstance(tick, int) else 0,
               float(timeout) if timeout else timeout_seconds())
    try:
        rows = list(ledger_rows or [])
        state_text = json.dumps(pipeline_state(h.match_id, h.tick, public_state or {}), separators=(",", ":"))
        with _lock:
            _seq += 1
            seq = _seq
        th = threading.Thread(target=_work, name="coach-bruin-run", daemon=True,
                              args=(h, rows, state_text, runner or subprocess_runner, _base_dir(), seq))
        h.thread = th
        th.start()
    except Exception as exc:    # noqa: BLE001
        h._finish(_result(False, f"coach pipeline could not start: {type(exc).__name__}"))
    return h


def poll(handle: Handle | None) -> dict | None:
    """None while the run is in progress; then, every time, the same result:
    {"ok", "metrics", "pipeline_run_id", "checks_passed", "seconds", "message"}.
    ok False (failed, timed out, bad handle) means: use the local figures. Never blocks or raises."""
    try:
        if not isinstance(handle, Handle):
            return _result(False, "no coach pipeline run was started")
        if handle.result is None and time.monotonic() - handle.started > handle.timeout + 1.0:
            # The worker enforces the timeout itself; this covers a worker that is stuck.
            if handle._finish(_result(False, f"the coach pipeline did not finish in {handle.timeout:g} s",
                                      None, None, 0, time.monotonic() - handle.started)):
                log.info("coach pipeline: match %s tick %d timed out; the local figures apply",
                         handle.match_id, handle.tick)
        return handle.result
    except Exception:           # noqa: BLE001
        return _result(False, "coach pipeline error")


# ------------------------------------------------------------------------------ for the integrator
def as_backup_numbers(metrics: dict) -> dict:
    """The pipeline's metrics under the key names of game.backup.analyse(...).numbers, so one
    scoring and wording path can serve both sources. Read-only mapping, no arithmetic except
    rounding avg_breach_ticks to one decimal as the local path does.

    Sightings: hidden_share / hidden_ticks / detections are the pipeline's CERTAIN figures.
    When metrics["team"]["sightings_exact"] is false they are bounds, not the exact values (the
    README explains); "sightings_exact" is passed through so the caller can keep its own figure."""
    t = metrics["team"]

    def actor(a: dict, human: bool) -> dict:
        ticks = a.get("ticks", 0)
        waits = a.get("ticks_waiting", round(a.get("wait_share", 0.0) * ticks))
        out = {"rows": ticks, "waits": waits, "patches": a["cards_patched"], "worth": a["patch_points"],
               "restores": a["restores"], "restore_starts": a["restores_started"],
               "monitors": a["monitors"], "isolates": a["isolates"]}
        if human:
            out["walked_past"] = a["near_breached_without_restoring"]
            out["wait_share"] = a["wait_share"]
        return out

    return {
        "tick": metrics["tick"], "red": t["red_points"], "blue": t["blue_points"],
        "held": t["red_points_from_holding"], "held_share": t["red_share_from_holding"],
        "breaches": t["breaches"], "avg_breach_ticks": round(t["avg_ticks_breached"], 1),
        "longest_breach_ticks": t["longest_ticks_breached"],
        "breached_now": t["machines_breached_now"],
        "breached_now_names": list(t["machines_breached_now_names"]),
        "open_hot": t["open_hot_cards"],
        "breaches_long_open": t["breaches_through_long_open_cards"],
        "breaches_top50": t["breaches_through_top50_cards"],
        "red_ticks": t["attacker_ticks"], "hidden_ticks": t["attacker_ticks_unseen"],
        "hidden_share": t["attackers_unseen_share"],
        "breaches_unmonitored": t["breaches_on_unmonitored_machines"],
        "detections": t["detections"], "sightings_exact": t["sightings_exact"],
        "agents": {aid: actor(metrics["agents"][aid], False) for aid in AGENT_IDS},
        "players": {pid: actor(metrics["players"][pid], True) for pid in PLAYER_IDS},
    }
