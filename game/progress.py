"""Live progress of scripts/advance.py, parsed from the Bruin log it streams to logs/advance/.

Pure parsing plus one small file tailer. The tailer is only ever called from a worker thread
(world.WorldManager), never from the tick loop. Nothing here is forwarded verbatim from the log
except an asset name that matches ASSET_NAME: the log's command line holds a local path and is
never sent to a client.

Lines this module understands (real Bruin output, see tests/game/fixtures/advance_week.log):

    $ cd /mnt/c/... && bruin run --workers 1 ...            one per Bruin command advance.py runs
    Analyzed the pipeline 'patch-tuesday' with 26 assets.
    ✓ Successfully validated 26 assets across 1 pipeline, all good.
    [13:24:43] Running:  landing.attack_techniques          an asset (no colon in the name)
    [13:24:45] Finished: landing.attack_techniques (1.848s)
    [13:24:50] Running:  landing.kev:custom-check:...       a quality check (colons): not counted
    [12:52:35] Failed: ml.eval_metrics:custom-check:challenger_beats_epss_on_precision@50 (12ms)
    bruin run completed successfully in 29.329s
    [exit 0]
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

# The seven steps the screen shows, in order. "gate" is the asset ml.eval_metrics (its checks are
# the promotion gate); sim.* assets count as "serve".
STAGES = ("landing", "raw", "stg", "feat", "ml", "gate", "serve")
GATE_ASSET = "ml.eval_metrics"
# Bookkeeping assets Bruin schedules at the very start of a run, before raw.* (they have no
# upstream). They are counted and shown as `current`, but they do not move the stage: otherwise
# the strip would light "ml" one second into the run and then fall back to "raw".
EARLY_ASSETS = frozenset({"ml.promotions", "ml.retract", "feat.snapshots"})

ASSET_NAME = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_LINE = re.compile(r"^\[\d\d:\d\d:\d\d\]\s+(Running|Finished|Failed):\s+(\S+)")
_ANALYZED = re.compile(r"^Analyzed the pipeline .* with (\d+) assets")
_VALIDATED = re.compile(r"Successfully validated (\d+) assets")
_COMPLETED = re.compile(r"^bruin run completed (successfully|with failures)")
_EXIT = re.compile(r"^\[exit (-?\d+)\]")


def stage_of(asset: str) -> str | None:
    """landing.kev -> "landing"; ml.eval_metrics -> "gate"; sim.reveal -> "serve"; else None."""
    if asset == GATE_ASSET:
        return "gate"
    schema = asset.split(".", 1)[0]
    if schema == "sim":
        return "serve"
    return schema if schema in STAGES else None


def empty_progress(message: str = "Waiting for the pipeline log.") -> dict:
    """What is sent while the log cannot be read (yet)."""
    return {"stage": None, "stage_index": None, "assets_done": 0, "assets_total": None,
            "current": None, "seconds": 0.0, "message": message}


class AdvanceProgress:
    """Feed it log text as it arrives; snapshot() is the `world.advance` object (minus seconds)."""

    def __init__(self) -> None:
        self._tail = ""                    # an unfinished last line, kept for the next feed
        self.commands = 0                  # Bruin commands seen ("$ " lines): 2 = the EPSS rebuild
        self.assets_total: int | None = None
        self.assets_done = 0
        self.current: str | None = None
        self.stage_index = 0
        self.started = False               # any line read at all
        self.gate_failed = False           # a check on ml.eval_metrics failed
        self.failed: list[str] = []        # failed assets/checks, in order (names only)
        self.completed: str | None = None  # "successfully" | "with failures" for the last command
        self.exit_code: int | None = None

    # ------------------------------------------------------------------ parsing
    def feed(self, text: str) -> None:
        if not text:
            return
        self.started = True
        lines = (self._tail + text).split("\n")
        self._tail = lines.pop()           # "" when the text ended on a newline
        for raw in lines:
            self._line(raw.rstrip("\r").strip())

    def _line(self, s: str) -> None:
        if not s:
            return
        if s.startswith("$ "):             # a new Bruin command: counts restart, the stage does not
            self.commands += 1
            self.assets_total, self.assets_done, self.current = None, 0, None
            self.completed, self.exit_code = None, None
            return
        m = _LINE.match(s)
        if m:
            what, name = m.group(1), m.group(2)
            if ":" in name:                # a quality check
                if what == "Failed":
                    self.failed.append(name[:160])
                    if name.split(":", 1)[0] == GATE_ASSET:
                        self.gate_failed = True
                return
            if not ASSET_NAME.match(name):
                return
            if what == "Running":
                self.current = name
                stage = stage_of(name)
                if stage is not None and name not in EARLY_ASSETS:
                    self.stage_index = max(self.stage_index, STAGES.index(stage))
            else:                          # Finished or Failed: the asset is over
                self.assets_done += 1
                if what == "Failed":
                    self.failed.append(name)
            return
        m = _VALIDATED.search(s) or _ANALYZED.match(s)
        if m:
            # "validated N" (the assets this command will run) comes after "Analyzed ... with N"
            # (the whole pipeline) and wins: a `--tag serve` rebuild validates 6 of 26.
            self.assets_total = int(m.group(1))
            return
        m = _COMPLETED.match(s)
        if m:
            self.completed = m.group(1)
            return
        m = _EXIT.match(s)
        if m:
            self.exit_code = int(m.group(1))

    # ------------------------------------------------------------------ output
    def message(self) -> str:
        if self.commands >= 2 and self.current is None:
            return "The gate blocked our model: rebuilding the board with the EPSS baseline."
        if self.current is None:
            return "Bruin is checking the pipeline."
        total = f" of {self.assets_total}" if self.assets_total else ""
        n = min(self.assets_done + 1, self.assets_total or self.assets_done + 1)
        head = "Rebuilding with the EPSS baseline: " if self.commands >= 2 else ""
        if self.completed is not None:
            if self.gate_failed and self.commands < 2:
                return "The gate blocked our model: the EPSS baseline keeps setting the danger scores."
            return f"{head}Bruin finished {self.assets_done}{total} assets."
        return f"{head}Bruin is running {self.current} (asset {n}{total})."

    def snapshot(self) -> dict:
        if not self.started:
            return empty_progress()
        return {"stage": STAGES[self.stage_index], "stage_index": self.stage_index,
                "assets_done": self.assets_done, "assets_total": self.assets_total,
                "current": self.current, "seconds": 0.0, "message": self.message()}


def final_progress(last: dict | None, ok: bool, message: str = "") -> dict:
    """The last object of an advance: stage "done" (all seven steps lit) or "failed" (the step it
    stopped on stays in stage_index)."""
    p = dict(last or empty_progress())
    if ok:
        p.update({"stage": "done", "stage_index": len(STAGES) - 1, "current": None,
                  "message": message or "The pipeline finished."})
    else:
        p.update({"stage": "failed", "message": message or "The pipeline run did not complete."})
    return p


def parse_log(text: str) -> dict:
    """Progress after reading a whole log (tests, and a log that only appears at the end)."""
    p = AdvanceProgress()
    p.feed(text)
    return p.snapshot()


class LogTail:
    """Follows the log of the advance that starts now: the newest logs/advance/*.log that did not
    exist, or has grown, since this object was made.

    A log left by an earlier run is never followed, however recently it was written: the logs
    present at construction are remembered (one directory listing) and skipped while unchanged.
    The file is looked for again on every poll, so a log that appears later takes over.

    poll() is blocking file I/O: call it from a worker thread. Every error is swallowed: a log
    that cannot be read just leaves the progress at stage null."""

    MAX_READ = 1 << 20                     # per poll; a whole run's log is about 60 KB

    def __init__(self, log_dir: Path, since: float | None = None) -> None:
        self.log_dir = Path(log_dir)
        self.since = since                 # kept for callers; the listing below decides what is old
        self.path: Path | None = None
        self.offset = 0
        self.progress = AdvanceProgress()
        self._lock = threading.Lock()      # two worker threads may poll at the end of an advance
        self._old: dict[str, tuple[int, int]] = {}     # name -> (mtime_ns, size) of earlier runs' logs
        for name, stamp, _ in self._list():
            self._old[name] = stamp

    def _list(self) -> list[tuple[str, tuple[int, int], Path]]:
        out = []
        try:
            for p in self.log_dir.glob("*.log"):
                try:
                    s = p.stat()
                except OSError:
                    continue
                out.append((p.name, (s.st_mtime_ns, s.st_size), p))
        except OSError:
            pass
        return out

    def _find(self) -> Path | None:
        best: tuple[int, str, Path] | None = None
        for name, stamp, p in self._list():
            if self._old.get(name) == stamp:           # an earlier run's log, untouched since
                continue
            if best is None or (stamp[0], name) > best[:2]:
                best = (stamp[0], name, p)
        return best[2] if best else None

    def poll(self) -> dict:
        with self._lock:
            return self._poll()

    def _poll(self) -> dict:
        try:
            found = self._find()
            if found is not None and found != self.path:
                # first sight of this advance's log, or a newer one took over: read it from the top
                self.path, self.offset, self.progress = found, 0, AdvanceProgress()
            if self.path is not None:
                with open(self.path, "rb") as fh:
                    fh.seek(self.offset)
                    data = fh.read(self.MAX_READ)
                if data:
                    self.offset += len(data)
                    self.progress.feed(data.decode("utf-8", errors="replace"))
        except Exception:
            pass
        return self.progress.snapshot()
