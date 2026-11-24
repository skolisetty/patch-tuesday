#!/usr/bin/env python3
"""Run the Coach's analysis as a Bruin pipeline (pipelines/coach) for one checkpoint of one match.

Standard library only. Works from Windows Python (Bruin is run through `wsl -e bash -lc`) and from
Python inside WSL (Bruin is run through `bash -lc`), like scripts/advance.py. Usage:

    python scripts/coach.py --match <id> --ledger <jsonl> --state <json> --out <json>
    python scripts/coach.py --match <id> --ledger <jsonl> --state <json> --dry-run   # print the commands only

  --ledger  this match's ledger rows up to the checkpoint (JSON lines, the game's ledger row shape)
  --state   the blue-visible board at the checkpoint (the game's state message, or the reduced
            shape in pipelines/coach/README.md); its "tick" names the checkpoint
  --out     where to copy the metrics JSON (default: the run folder's metrics.json)
  --keep N  after the run, keep only the N newest run folders under data/coach/ (0 = keep all)

What it does: makes the run folder data/coach/<match>-<tick>/ and copies the two inputs into it and
into data/coach/current/ (the fixed folder the pipeline's SQL reads; data/ is git-ignored), runs
`bruin run --workers 1 pipelines/coach/pipeline.yml` from the repo root, reads
data/coach/current/metrics.json (written by the pipeline's last asset only when every blocking
check passed) and copies it to the run folder and to --out. The whole Bruin output is kept in the
run folder as bruin.log.

The last stdout line is always one JSON object:
    {"ok": bool, "metrics_path": "...", "pipeline_run_id": "...", "checks_passed": n,
     "checks_failed": n, "failed_checks": ["asset:custom-check:name", ...], "seconds": s,
     "message": "..."}
ok is true only when Bruin exited 0, nothing was reported as failed, and a fresh metrics file for
this match exists. Progress goes to stderr.

It holds its own lock, data/.coach.lock ({"pid", "platform", "started_epoch"}), for the length of
the run, because data/coach/current/ and the coach DuckDB file are single-writer. It never looks at
data/.advance.lock: the coach pipeline has its own DuckDB connection (duckdb-coach in .bruin.yml)
and can run while the weekly pipeline advances. A lock whose process is gone, or older than
LOCK_STALE_S, is removed by the next run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "coach"
CURRENT = DATA / "current"
LOCK = ROOT / "data" / ".coach.lock"
PIPELINE = "pipelines/coach/pipeline.yml"
CONNECTION = "duckdb-coach"
IS_WINDOWS = os.name == "nt"
LOCK_STALE_S = 180
RESERVED = {"current", "inbox"}       # folders under data/coach/ that are not run folders

_FAILED_LINE = re.compile(r"^\[\d\d:\d\d:\d\d\]\s+Failed:\s+(\S+)")
_FAIL_SUMMARY = re.compile(r"^FAIL(?:ED)?\s+([A-Za-z0-9_.]+)")
_CHECKS_LINE = re.compile(r"Quality checks\s+(?:(\d+)\s+failed\s*/\s*)?(?:(\d+)\s+succeeded)?")


def log(msg: str) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(f"[coach] {safe}", file=sys.stderr, flush=True)


def wsl_path(p: Path) -> str:
    """C:\\Users\\x -> /mnt/c/Users/x when on Windows; unchanged inside WSL."""
    s = str(p)
    if IS_WINDOWS and len(s) > 1 and s[1] == ":":
        return "/mnt/" + s[0].lower() + s[2:].replace("\\", "/")
    return s


# A PATH without the Windows folders WSL appends to it. Measured on this machine (Bruin v0.11.831):
# with the inherited PATH (68 /mnt/c entries) every `bruin` start costs about 1.8 s before it does
# anything (`bruin --help` 1.8 s against 0.08 s); with this PATH a whole coach run takes about 2 s
# instead of 5. The coach pipeline is SQL only, so it needs nothing else on the PATH.
CLEAN_PATH = "$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def bruin_command() -> str:
    return (f"cd {shlex.quote(wsl_path(ROOT))} && export PATH={CLEAN_PATH} && "
            f"bruin run --workers 1 {PIPELINE}")


def shell_argv(cmd: str) -> list[str]:
    return ["wsl", "-e", "bash", "-lc", cmd] if IS_WINDOWS else ["bash", "-lc", cmd]


def failed_checks(output: str) -> list[str]:
    """What Bruin reported as failed: 'asset:custom-check:name', 'asset:column:check' or an asset
    name. A Bruin-level "Error:" line (nothing ran) is reported as "Error"."""
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
        elif (s.startswith("Error:") or s.startswith("An error occurred")) and "Error" not in seen:
            seen.append("Error")
    for asset in summary:
        if not any(f == asset or f.startswith(asset + ":") for f in seen):
            seen.append(asset)
    return seen


def check_counts(output: str) -> tuple[int, int]:
    """(passed, failed) from Bruin's "Quality checks" summary line; (0, 0) when there is none."""
    passed = failed = 0
    for line in output.splitlines():
        if "Quality checks" not in line:
            continue
        m = _CHECKS_LINE.search(line)
        if m:
            failed = int(m.group(1) or 0)
            passed = int(m.group(2) or 0)
    return passed, failed


def safe_name(match_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", match_id)[:80] or "match"


def has_connection() -> bool:
    try:
        return CONNECTION in (ROOT / ".bruin.yml").read_text(encoding="utf-8")
    except OSError:
        return False


# ---------------------------------------------------------------------------- lock
def _pid_alive(pid: int, platform: str) -> bool | None:
    """True/False when it can tell on this platform, None when it cannot."""
    here = "nt" if IS_WINDOWS else "posix"
    if pid <= 0 or platform != here:
        return None
    try:
        if here == "posix":
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259              # STILL_ACTIVE
    except Exception:
        return None


def lock_is_stale() -> bool:
    try:
        info = json.loads(LOCK.read_text(encoding="utf-8"))
        started = float(info.get("started_epoch") or LOCK.stat().st_mtime)
    except FileNotFoundError:
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        try:
            return time.time() - LOCK.stat().st_mtime > LOCK_STALE_S
        except OSError:
            return True
    if time.time() - started > LOCK_STALE_S:
        return True
    try:
        pid = int(info.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return _pid_alive(pid, str(info.get("platform"))) is False


def acquire_lock() -> bool:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if attempt == 0 and lock_is_stale():
                log("removing a stale data/.coach.lock")
                try:
                    LOCK.unlink()
                except FileNotFoundError:
                    pass
                continue
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "platform": "nt" if IS_WINDOWS else "posix",
                                 "started_epoch": time.time()}))
        return True
    return False


def release_lock() -> None:
    try:
        LOCK.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------- run
def result(ok: bool, message: str, metrics_path: str | None = None, run_id: str | None = None,
           passed: int = 0, failed: int = 0, failed_names: list[str] | None = None,
           seconds: float = 0.0) -> dict:
    return {"ok": ok, "metrics_path": metrics_path, "pipeline_run_id": run_id,
            "checks_passed": passed, "checks_failed": failed, "failed_checks": failed_names or [],
            "seconds": round(seconds, 2), "message": message}


def prune(keep: int) -> None:
    """Keep the `keep` newest run folders under data/coach/ (never current/ or inbox/)."""
    if keep <= 0 or not DATA.is_dir():
        return
    runs = sorted((p for p in DATA.iterdir() if p.is_dir() and p.name not in RESERVED),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for old in runs[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def read_metrics(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
        data = json.loads(text)
    except (OSError, ValueError):
        return None
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else None


def run(match_id: str, ledger: Path, state: Path, out: Path | None, tick: int | None, keep: int,
        dry_run: bool) -> dict:
    t0 = time.time()
    if not ledger.is_file() or not state.is_file():
        return result(False, "the ledger or the state file does not exist")
    if tick is None:
        try:
            tick = int(json.loads(state.read_text(encoding="utf-8")).get("tick"))
        except (OSError, ValueError, TypeError, AttributeError):
            return result(False, "the state file is not a JSON object with a tick")
    run_dir = DATA / f"{safe_name(match_id)}-{tick:03d}"
    out = out or (run_dir / "metrics.json")
    cmd = bruin_command()
    if dry_run:
        for line in (f"mkdir -p {run_dir.relative_to(ROOT).as_posix()} {CURRENT.relative_to(ROOT).as_posix()}",
                     f"cp <ledger> <state>  ->  {run_dir.relative_to(ROOT).as_posix()}/ and {CURRENT.relative_to(ROOT).as_posix()}/",
                     " ".join(shell_argv(cmd)[:-1]) + " " + shlex.quote(cmd),
                     f"cp {CURRENT.relative_to(ROOT).as_posix()}/metrics.json  ->  the run folder and --out"):
            print(line, file=sys.stderr)
        return result(True, "dry run")
    if not has_connection():
        return result(False, f".bruin.yml at the repo root has no {CONNECTION} connection (see .bruin.yml.example)")
    if not acquire_lock():
        return result(False, "another coach run is in progress (data/.coach.lock exists)")
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        CURRENT.mkdir(parents=True, exist_ok=True)
        fresh = CURRENT / "metrics.json"
        for stale in (fresh, run_dir / "metrics.json"):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
        for src, name in ((ledger, "ledger.jsonl"), (state, "state.json")):
            for folder in (run_dir, CURRENT):
                if src.resolve() != (folder / name).resolve():
                    shutil.copyfile(src, folder / name)
        proc = subprocess.run(shell_argv(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = proc.stdout.decode("utf-8", errors="replace")
        (run_dir / "bruin.log").write_text(f"$ {cmd}\n{output}\n[exit {proc.returncode}]\n", encoding="utf-8")
        seconds = time.time() - t0
        failed_names = failed_checks(output)
        passed, failed = check_counts(output)
        for line in output.splitlines():
            s = line.strip()
            if any(k in s for k in ("Assets executed", "Quality checks", "custom check '", "completed with failures",
                                    "completed successfully", "Error:", "Failed:")):
                log("  " + s[:240])
        log_rel = (run_dir / "bruin.log").relative_to(ROOT).as_posix()
        if proc.returncode != 0 or failed_names:
            names = ", ".join(failed_names[:6]) or f"bruin exited {proc.returncode}"
            return result(False, f"coach pipeline failed: {names}; see {log_rel}", None, None,
                          passed, max(failed, len([f for f in failed_names if ':' in f])), failed_names, seconds)
        metrics = read_metrics(fresh)
        if not metrics or metrics.get("match_id") != match_id or not metrics.get("pipeline_run_id"):
            return result(False, f"the pipeline ran but wrote no metrics for this match; see {log_rel}",
                          None, None, passed, failed, failed_names, seconds)
        shutil.copyfile(fresh, run_dir / "metrics.json")
        if out.resolve() != (run_dir / "metrics.json").resolve():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fresh, out)
        prune(keep)
        seconds = time.time() - t0
        return result(True, f"coach pipeline ok for {match_id} at tick {tick}: {passed} checks passed in {seconds:.1f}s",
                      str(out), metrics["pipeline_run_id"], passed, failed, failed_names, seconds)
    finally:
        release_lock()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--match", required=True, help="match id (must be the match_id of both input files)")
    ap.add_argument("--ledger", required=True, help="ledger rows of the match up to the checkpoint (JSON lines)")
    ap.add_argument("--state", required=True, help="the blue-visible board at the checkpoint (JSON)")
    ap.add_argument("--out", help="where to copy the metrics JSON")
    ap.add_argument("--tick", type=int, help="checkpoint tick (default: the state file's tick)")
    ap.add_argument("--keep", type=int, default=0, help="keep only the N newest run folders (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="print the commands and exit")
    args = ap.parse_args()
    try:
        out = run(args.match, Path(args.ledger), Path(args.state), Path(args.out) if args.out else None,
                  args.tick, args.keep, args.dry_run)
    except Exception as exc:        # report, never crash without the JSON line
        out = result(False, f"coach.py error: {type(exc).__name__}: {exc}")
    log(out["message"])
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
