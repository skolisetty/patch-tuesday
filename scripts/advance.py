#!/usr/bin/env python3
"""Advance the Patch Tuesday world by one Monday-to-Sunday week, or rebuild day 0.

Standard library only. Works from Windows Python (Bruin is run through `wsl -e bash -lc`) and from
Python inside WSL (Bruin is run through `bash -lc`). Usage:

    python scripts/advance.py               # next week after serving/world.json's asof_date
    python scripts/advance.py --day0        # delete the pipeline database and rebuild as of 2026-03-22
    python scripts/advance.py --rerun       # re-run the current week (same window, new run id)
    python scripts/advance.py --dry-run     # print the Bruin commands only
    python scripts/advance.py --lock-status # print {"lock": "free"|"held"|"stale", ...} and exit

The last stdout line is always one JSON object (docs/CONTRACTS.md):
    {"ok": bool, "asof_date": "...", "score_source": "...", "challenger_p_at_50": n,
     "epss_p_at_50": n, "gate_passed": bool, "message": "..."}
Progress goes to stderr; the full Bruin output goes to logs/advance/<timestamp>-<label>.log.

The outcome is decided from serving/world.json (it has "moved" when it carries the new asof_date
AND a new pipeline_run_id) together with the checks Bruin reports as failed, never from Bruin's exit
code alone (exit 1 covers a failed gate and a real error alike). If the main run did not move
world.json, the serve assets are rebuilt in EPSS mode with `bruin run --tag serve` for the same
window ONLY when the failed checks are the promotion gate's own (GATE_CHECKS on ml.eval_metrics).
That means every leakage, time-travel and consistency check upstream of the gate passed in this
run, so the tables the serve assets read are this run's. Any other failure returns ok:false with
the failed check names and leaves serving/ as it was.

The lock file data/.advance.lock holds {"pid", "platform", "started", "started_epoch"}. A lock
whose process is gone, or that is older than LOCK_STALE_S, is stale; the next advance removes it.

TEST SWITCHES (never set them in play):
  PT_DEGRADE_MODEL=1        passes `--var degrade_model=true`: ml.challenger_scores serves a
                            deliberately useless scorer, so the gate must fail.
  PT_TEST_VARS="k=v,k2=v2"  passes extra `--var k=v` to the MAIN run only (for example
                            horizon_days=90 makes a blocking leakage check fail), to test that
                            such a failure never reaches serving through the EPSS fallback.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORLD = ROOT / "serving" / "world.json"
LOCK = ROOT / "data" / ".advance.lock"
LOG_DIR = ROOT / "logs" / "advance"
PIPELINE = "pipelines/patch_tuesday/pipeline.yml"
DAY0 = dt.date(2026, 3, 22)
LAST = dt.date(2026, 6, 14)       # last EPSS file in the offline mirror
FIRST_RUN_START = "2020-01-01"
IS_WINDOWS = os.name == "nt"
LOCK_STALE_S = 2 * 3600           # the game server gives advance.py one hour

# The promotion gate: the only failures after which the EPSS fallback may rebuild serving.
GATE_CHECKS = {
    "ml.eval_metrics:custom-check:challenger_beats_epss_on_precision@50",
    "ml.eval_metrics:custom-check:validation_set_has_at_least_10_positives",
    "ml.eval_metrics:custom-check:challenger_was_scored_for_this_as-of_date",
}
# Diagnostics that nothing serving depends on: a failure there is reported, never blocking.
DIAGNOSTIC_ASSETS = {"ml.sensitivity"}
_FAILED_LINE = re.compile(r"^\[\d\d:\d\d:\d\d\]\s+Failed:\s+(\S+)")
_FAIL_SUMMARY = re.compile(r"^FAIL(?:ED)?\s+([A-Za-z0-9_.]+)")


def log(msg: str) -> None:
    safe = msg.replace("✓", "ok").replace("✗", "x").encode("ascii", "replace").decode("ascii")
    print(f"[advance] {safe}", file=sys.stderr, flush=True)


def wsl_path(p: Path) -> str:
    """C:\\Users\\x -> /mnt/c/Users/x when on Windows; unchanged inside WSL."""
    s = str(p)
    if IS_WINDOWS and len(s) > 1 and s[1] == ":":
        return "/mnt/" + s[0].lower() + s[2:].replace("\\", "/")
    return s


def db_path() -> str | None:
    cfg = ROOT / ".bruin.yml"
    if not cfg.is_file():
        return None
    m = re.search(r'^\s*path:\s*"?([^"\n]+?)"?\s*$', cfg.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def shell(cmd: str, log_file: Path | None = None) -> tuple[int, str]:
    """Run a bash command line in WSL (from Windows) or locally (inside WSL)."""
    argv = ["wsl", "-e", "bash", "-lc", cmd] if IS_WINDOWS else ["bash", "-lc", cmd]
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", errors="replace")
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(f"$ {cmd}\n{out}\n[exit {proc.returncode}]\n")
    return proc.returncode, out


def bruin(args: list[str], log_file: Path, dry_run: bool) -> tuple[int, str]:
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && export PATH=$HOME/.local/bin:$PATH && "
           + " ".join(["bruin"] + [shlex.quote(a) for a in args]))
    if dry_run:
        print(cmd, file=sys.stderr)
        return 0, ""
    t0 = time.time()
    rc, out = shell(cmd, log_file)
    log(f"bruin {' '.join(args[:2])} ... exit {rc} in {time.time() - t0:.0f}s")
    for line in out.splitlines():
        s = line.strip()
        if any(k in s for k in ("Assets executed", "Quality checks", "UPSTREAM FAILED", "custom check '",
                                "completed with failures", "completed successfully", "Error:", "Failed:")):
            log("  " + s[:240])
    return rc, out


def failed_checks(output: str) -> list[str]:
    """What Bruin reported as failed: 'asset:custom-check:name', 'asset:column:check' or an asset
    name. A Bruin-level "Error:" line (no asset ran) is reported as "Error"."""
    seen: list[str] = []
    summary: list[str] = []
    for line in output.splitlines():
        s = line.strip()
        m = _FAILED_LINE.match(s)
        if m:
            if m.group(1) not in seen:
                seen.append(m.group(1))
            continue
        m = _FAIL_SUMMARY.match(s) if not s.startswith("UPSTREAM") else None
        if m:
            summary.append(m.group(1))
        elif s.startswith("Error:") and "Error" not in seen:
            seen.append("Error")
    # A FAIL line in the end-of-run summary that no "Failed:" line explains is a failure of its own.
    for asset in summary:
        if not any(f == asset or f.startswith(asset + ":") for f in seen):
            seen.append(asset)
    return seen


def classify(output: str) -> tuple[str, list[str]]:
    """("gate", failed): the promotion gate's checks were the only blocking failures.
    ("none", failed): nothing blocking failed (diagnostic failures may be listed).
    ("other", failed): anything else, including a leakage, fingerprint or consistency check."""
    failed = failed_checks(output)
    blocking = [f for f in failed if f.split(":")[0] not in DIAGNOSTIC_ASSETS]
    if not blocking:
        return "none", failed
    if all(f in GATE_CHECKS for f in blocking):
        return "gate", failed
    return "other", failed


def read_world() -> dict | None:
    for _ in range(3):
        try:
            data = json.loads(WORLD.read_text(encoding="utf-8"))
            return data[0] if isinstance(data, list) and data else None
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            time.sleep(0.5)
    return None


def result(ok: bool, world: dict | None, message: str) -> dict:
    w = world or {}
    return {
        "ok": ok,
        "asof_date": w.get("asof_date"),
        "score_source": w.get("score_source"),
        "challenger_p_at_50": w.get("challenger_p_at_50"),
        "epss_p_at_50": w.get("epss_p_at_50"),
        "gate_passed": bool(ok and w.get("score_source") == "model"),
        "message": message,
    }


def gate_text(w: dict) -> str:
    ch, ep = w.get("challenger_hits_at_50"), w.get("epss_hits_at_50")
    if ch is None or ep is None:
        return "no gate numbers"
    return f"challenger {ch} vs EPSS {ep} future-KEV hits in the top 50 (n_positives {w.get('n_positives')})"


def test_vars() -> list[str]:
    out: list[str] = []
    for kv in (os.environ.get("PT_TEST_VARS") or "").split(","):
        kv = kv.strip()
        if re.fullmatch(r"[a-z_]+=[A-Za-z0-9_.-]+", kv):
            out += ["--var", kv]
    return out


def run_window(start: dt.date, end: dt.date, full_refresh: bool, dry_run: bool,
               label: str | None = None) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = label or (f"day0-{end}" if full_refresh else f"week-{end}")
    log_file = LOG_DIR / f"{stamp}-{label}.log"
    window = ["--start-date", start.isoformat(), "--end-date", f"{end.isoformat()} 23:59:59.999999"]
    extra = []
    degrade = os.environ.get("PT_DEGRADE_MODEL", "").strip().lower() in ("1", "true", "yes")
    if degrade:
        extra = ["--var", "degrade_model=true"]
        log("TEST SWITCH PT_DEGRADE_MODEL=1: the challenger is deliberately degraded")
    tv = test_vars()
    if tv:
        log(f"TEST SWITCH PT_TEST_VARS: {' '.join(tv[1::2])} (main run only)")
    before = read_world() or {}

    main = ["run", "--workers", "1"] + (["--full-refresh"] if full_refresh else []) + window + extra + tv + [PIPELINE]
    rc_main, out_main = bruin(main, log_file, dry_run)
    if dry_run:
        bruin(["run", "--workers", "1", "--tag", "serve"] + window + [PIPELINE], log_file, dry_run)
        return result(True, None, "dry run")

    def moved(w: dict | None) -> bool:
        # A new run id as well as the new clock: a re-run of the same week, or a run whose serve
        # assets were skipped, must never count as moved because an older file has that date.
        return (bool(w) and w.get("asof_date") == end.isoformat()
                and w.get("pipeline_run_id") != before.get("pipeline_run_id"))

    world = read_world()
    kind, failed = classify(out_main)
    fallback = False
    rc_fb = None
    tag = " [TEST SWITCH degrade_model=true]" if degrade else ""
    tag += " [TEST SWITCH PT_TEST_VARS]" if tv else ""
    rel = log_file.relative_to(ROOT)
    if not moved(world):
        if kind != "gate":
            names = ", ".join(failed[:6]) or "no failed check reported"
            why = ("checks other than the promotion gate failed" if kind == "other"
                   else f"bruin exited {rc_main} and no blocking check failed")
            log(f"world.json did not move and the EPSS fallback is NOT run: {why}: {names}")
            return result(False, world, f"run for {end} failed ({why}: {names}); serving/ left at "
                                        f"{(world or {}).get('asof_date')}; see {rel}{tag}")
        fallback = True
        log("world.json did not move because the promotion gate failed: rebuilding the serve assets "
            "in EPSS mode (bruin run --tag serve)")
        rc_fb, out_fb = bruin(["run", "--workers", "1", "--tag", "serve"] + window + [PIPELINE], log_file, dry_run)
        world = read_world()
        fb_kind, fb_failed = classify(out_fb)
        if fb_kind == "other" and not moved(world):
            return result(False, world, f"run for {end}: the gate failed and the EPSS rebuild failed too "
                                        f"({', '.join(fb_failed[:6])}); see {rel}{tag}")

    if not moved(world):
        return result(False, world, f"run for {end} failed: world.json still at {(world or {}).get('asof_date')} "
                                    f"(bruin exit {rc_main}, fallback exit {rc_fb}); see {rel}{tag}")
    w = world
    if w.get("score_source") == "model":
        msg = f"world at {end}: gate passed, serving the model {w.get('model_version')} ({gate_text(w)})"
    else:
        msg = f"world at {end}: gate failed, serving EPSS ({gate_text(w)})"
        if fallback:
            msg += "; serve assets rebuilt in EPSS mode"
    diag = [f for f in failed if f.split(":")[0] in DIAGNOSTIC_ASSETS]
    if diag:
        msg += f"; diagnostic failures (not blocking): {', '.join(diag[:4])}"
    return result(True, w, msg + tag)


# ---------------------------------------------------------------------------- lock
def _pid_alive(pid: int, platform: str) -> bool | None:
    """True/False when it can tell, None when it cannot (the lock then counts as held)."""
    if pid <= 0:
        return None
    here = "nt" if IS_WINDOWS else "posix"
    try:
        if platform == here == "posix":
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True
        if platform == here == "nt":
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            k32.CloseHandle(h)
            return bool(ok) and code.value == 259          # STILL_ACTIVE
        if platform == "nt":                               # a Windows advance, seen from WSL
            exe = "/mnt/c/Windows/System32/tasklist.exe"
            if not os.path.exists(exe):
                return None
            out = subprocess.run([exe, "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                                 capture_output=True, timeout=15).stdout.decode("utf-8", "replace")
            return f'"{pid}"' in out
        if platform == "posix":                            # a WSL advance, seen from Windows
            rc = subprocess.run(["wsl", "-e", "kill", "-0", str(pid)], capture_output=True, timeout=30).returncode
            return rc == 0
    except Exception:
        return None
    return None


def lock_status() -> dict:
    """{"lock": "free"|"held"|"stale", ...}. Stale: its process is gone, or it is too old."""
    try:
        raw = LOCK.read_text(encoding="utf-8")
        mtime = LOCK.stat().st_mtime
    except FileNotFoundError:
        return {"lock": "free"}
    except OSError as exc:
        return {"lock": "held", "why": f"unreadable ({type(exc).__name__})"}
    try:
        info = json.loads(raw)
    except ValueError:
        info = {}
    if not isinstance(info, dict):
        info = {}
    try:
        started = float(info.get("started_epoch") or mtime)
    except (TypeError, ValueError):
        started = mtime
    age = time.time() - started
    out = {"pid": info.get("pid"), "platform": info.get("platform"), "age_s": int(age)}
    if age > LOCK_STALE_S:
        return {"lock": "stale", "why": f"older than {LOCK_STALE_S // 3600} h", **out}
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    alive = _pid_alive(pid, str(info.get("platform"))) if info.get("platform") else None
    if alive is False:
        return {"lock": "stale", "why": f"process {pid} is gone", **out}
    return {"lock": "held", **out}


def acquire_lock() -> bool:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            st = lock_status()
            if attempt == 0 and st["lock"] == "stale":
                log(f"removing a stale data/.advance.lock ({st.get('why')})")
                try:
                    LOCK.unlink()
                except FileNotFoundError:
                    pass
                continue
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "platform": "nt" if IS_WINDOWS else "posix",
                                 "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "started_epoch": time.time()}))
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--day0", action="store_true", help="delete the pipeline database and rebuild as of 2026-03-22")
    ap.add_argument("--rerun", action="store_true", help="re-run the current week (same window, new run id)")
    ap.add_argument("--dry-run", action="store_true", help="print the Bruin commands and exit")
    ap.add_argument("--lock-status", action="store_true", help="print the lock state as JSON and exit")
    args = ap.parse_args()
    if args.lock_status:
        st = lock_status()
        print(json.dumps(st))
        return 1 if st["lock"] == "held" else 0

    if not args.dry_run and not acquire_lock():
        out = result(False, read_world(), "another advance is running (data/.advance.lock exists); refusing to start")
        print(json.dumps(out))
        return 1
    try:
        (ROOT / "serving").mkdir(exist_ok=True)
        if args.day0:
            db = db_path()
            if not db:
                out = result(False, None, ".bruin.yml with a duckdb path is missing at the repo root")
            else:
                if not args.dry_run:
                    log(f"day 0: deleting {db} and rebuilding everything as of {DAY0}")
                    shell(f"rm -f {shlex.quote(db)} {shlex.quote(db + '.wal')}")
                out = run_window(dt.date.fromisoformat(FIRST_RUN_START), DAY0, True, args.dry_run)
        else:
            world = read_world()
            if not world or not world.get("asof_date"):
                out = result(False, world, "serving/world.json is missing; run: python scripts/advance.py --day0")
            elif args.rerun:
                asof = dt.date.fromisoformat(world["asof_date"])
                out = run_window(asof - dt.timedelta(days=6), asof, False, args.dry_run, label=f"rerun-{asof}")
            else:
                asof = dt.date.fromisoformat(world["asof_date"])
                end = asof + dt.timedelta(days=7)
                if end > LAST:
                    out = result(False, world, f"no more weeks: the last available week ends {LAST.isoformat()}")
                else:
                    out = run_window(asof + dt.timedelta(days=1), end, False, args.dry_run)
    except Exception as exc:  # report, never crash without the JSON line
        out = result(False, read_world(), f"advance.py error: {type(exc).__name__}: {exc}")
    finally:
        if not args.dry_run:
            try:
                LOCK.unlink()
            except FileNotFoundError:
                pass
    log(out["message"])
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
