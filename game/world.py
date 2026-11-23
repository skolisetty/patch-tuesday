"""Serving-file loader, world.json watcher and the scripts/advance.py wrapper.

Nothing in this module is called from inside rules.step: disk and subprocess work happens
in background tasks, and the tick loop only picks up an already-loaded ServingData object.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
MAPPING_SOURCES = {"kev_ctid", "cwe_capec", "heuristic", "none"}


class ServingError(Exception):
    pass


# ---------------------------------------------------------------- tolerant coercion
def _s(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # drop NaN


def _i(v: Any) -> int | None:
    x = _f(v)
    return None if x is None else int(x)


def _b(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "t", "yes")
    return bool(v)


def _score(v: Any) -> float:
    x = _f(v)
    return 0.0 if x is None else min(1.0, max(0.0, x))


def read_json_array(path: Path, retries: int = 4, delay: float = 0.15) -> list[dict]:
    """Read a whole JSON file, close it at once, and retry if it is half-written."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            text = path.read_text(encoding="utf-8-sig")
            data = json.loads(text)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise ValueError("top level is not an array")
            return [d for d in data if isinstance(d, dict)]
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
            last = exc
            if attempt + 1 < retries:
                time.sleep(delay)
    raise ServingError(f"could not parse {path.name}: {type(last).__name__}")


def normalise_card(raw: dict, fallback_id: int, world_source: str) -> dict | None:
    cve = _s(raw.get("cve_id"))
    if not cve:
        return None
    card_id = _i(raw.get("card_id"))
    sev = _s(raw.get("severity"))
    sev = sev.upper() if sev else None
    src = _s(raw.get("score_source")) or world_source
    mapping = _s(raw.get("mapping_source")) or "none"
    vendor, product = _s(raw.get("vendor")), _s(raw.get("product"))
    title = _s(raw.get("title")) or " ".join(x for x in (vendor, product) if x) or cve
    return {
        "card_id": fallback_id if card_id is None else card_id,
        "cve_id": cve,
        "title": title[:120],
        "published_date": _s(raw.get("published_date")),
        "age_days": _i(raw.get("age_days")) or 0,
        "vendor": vendor,
        "product": product,
        "cwe_id": _s(raw.get("cwe_id")),
        "cvss_base_score": _f(raw.get("cvss_base_score")),
        "severity": sev if sev in SEVERITIES else None,
        "score": _score(raw.get("score")),
        "score_source": src if src in ("model", "epss") else "epss",
        "epss": _f(raw.get("epss")),
        "epss_percentile": _f(raw.get("epss_percentile")),
        "model_score": _f(raw.get("model_score")),
        "in_kev": _b(raw.get("in_kev")),
        "technique_id": _s(raw.get("technique_id")),
        "technique_name": _s(raw.get("technique_name")),
        "tactic": _s(raw.get("tactic")),
        "mapping_source": mapping if mapping in MAPPING_SOURCES else "none",
        "is_new_this_week": _b(raw.get("is_new_this_week")),
        "priority_rank": _i(raw.get("priority_rank")),
    }


def normalise_world(raw: dict) -> dict:
    src = _s(raw.get("score_source")) or "epss"
    out = {
        "asof_date": _s(raw.get("asof_date")) or "1970-01-04",
        "window_start": _s(raw.get("window_start")),
        "pipeline_run_id": _s(raw.get("pipeline_run_id")) or "unknown",
        "score_source": src if src in ("model", "epss") else "epss",
        "model_version": _s(raw.get("model_version")),
        "challenger_p_at_50": _f(raw.get("challenger_p_at_50")),
        "epss_p_at_50": _f(raw.get("epss_p_at_50")),
    }
    for k in ("challenger_hits_at_50", "epss_hits_at_50", "n_positives", "n_candidates"):
        out[k] = _i(raw.get(k))
    for k in ("n_cves_visible", "n_kev_visible", "n_new_cves_this_week", "n_cards"):
        out[k] = _i(raw.get(k)) or 0
    return out


@dataclass
class ServingData:
    world: dict
    cards: list[dict]
    priority: list[dict] = field(default_factory=list)
    reveal: dict[str, dict] = field(default_factory=dict)
    source: str = "serving"          # "serving" or "serving_mock"
    directory: str = ""

    @property
    def asof(self) -> str:
        return self.world["asof_date"]

    def priority_for(self, cve_id: str) -> int | None:
        if not hasattr(self, "_rank"):
            self._rank = {p["cve_id"]: p["rank"] for p in self.priority}
        return self._rank.get(cve_id)


def load_serving(directory: Path, source: str | None = None) -> ServingData:
    """Load one serving folder. world.json and attack_cards.json are required; the rest is optional."""
    directory = Path(directory)
    try:
        world_rows = read_json_array(directory / "world.json")
        card_rows = read_json_array(directory / "attack_cards.json")
    except FileNotFoundError as exc:
        raise ServingError(f"missing {Path(exc.filename or '').name or 'serving file'} in {directory.name}")
    if not world_rows:
        raise ServingError("world.json holds no object")
    world = normalise_world(world_rows[0])

    cards, seen = [], set()
    for n, raw in enumerate(card_rows):
        c = normalise_card(raw, 100000 + n, world["score_source"])
        if c is None or c["card_id"] in seen:
            continue
        seen.add(c["card_id"])
        cards.append(c)
    if not cards:
        raise ServingError("attack_cards.json holds no usable card")

    priority: list[dict] = []
    try:
        for raw in read_json_array(directory / "patch_priority.json"):
            cve, rank = _s(raw.get("cve_id")), _i(raw.get("rank"))
            if cve and rank is not None:
                priority.append({
                    "rank": rank, "cve_id": cve, "score": _score(raw.get("score")),
                    "score_source": _s(raw.get("score_source")) or world["score_source"],
                    "epss": _f(raw.get("epss")), "model_score": _f(raw.get("model_score")),
                    "in_kev": _b(raw.get("in_kev")), "vendor": _s(raw.get("vendor")),
                    "product": _s(raw.get("product")),
                    "cvss_base_score": _f(raw.get("cvss_base_score")),
                    "technique_id": _s(raw.get("technique_id")), "in_deck": _b(raw.get("in_deck")),
                })
        priority.sort(key=lambda p: p["rank"])
    except (FileNotFoundError, ServingError):
        priority = []
    rank_of = {p["cve_id"]: p["rank"] for p in priority}
    for c in cards:
        if c["priority_rank"] is None and c["cve_id"] in rank_of:
            c["priority_rank"] = rank_of[c["cve_id"]]

    reveal: dict[str, dict] = {}
    try:
        for raw in read_json_array(directory / "reveal.json"):
            cve = _s(raw.get("cve_id"))
            if cve:
                reveal[cve] = {"entered_kev_within_91d": _b(raw.get("entered_kev_within_91d")),
                               "kev_date_added": _s(raw.get("kev_date_added"))}
    except (FileNotFoundError, ServingError):
        reveal = {}

    world["n_cards"] = world["n_cards"] or len(cards)
    return ServingData(world=world, cards=cards, priority=priority, reveal=reveal,
                       source=source or directory.name, directory=str(directory))


def pick_serving_dir(serving: Path | None = None, mock: Path | None = None) -> tuple[Path, str]:
    serving = serving or config.SERVING_DIR
    mock = mock or config.MOCK_DIR
    if (serving / "world.json").is_file() and (serving / "attack_cards.json").is_file():
        return serving, "serving"
    return mock, "serving_mock"


def load_current(serving: Path | None = None, mock: Path | None = None) -> ServingData:
    """serving/ when it is usable, else serving_mock/."""
    directory, source = pick_serving_dir(serving, mock)
    try:
        return load_serving(directory, source)
    except ServingError:
        if source == "serving":
            return load_serving(mock or config.MOCK_DIR, "serving_mock")
        raise


def file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


# ---------------------------------------------------------------- advance.py wrapper
class WorldManager:
    """Holds the current ServingData, watches world.json and runs advance.py in the background."""

    def __init__(self, serving_dir: Path | None = None, mock_dir: Path | None = None,
                 advance_script: Path | None = None, poll_seconds: float = 1.0):
        self.serving_dir = Path(serving_dir or config.SERVING_DIR)
        self.mock_dir = Path(mock_dir or config.MOCK_DIR)
        self.advance_script = Path(advance_script or config.ADVANCE_SCRIPT)
        self.poll_seconds = poll_seconds
        self.current: ServingData = load_current(self.serving_dir, self.mock_dir)
        self._stamp = file_stamp(Path(self.current.directory) / "world.json")
        self.advancing = False
        self.last_result: dict | None = None
        self._advance_task: asyncio.Task | None = None
        self._listeners: list = []          # callables(ServingData), called on the event loop
        self._result_listeners: list = []   # callables(dict)
        self._poll_lock: asyncio.Lock | None = None
        self.advance_started: float | None = None   # time.monotonic() of the running advance

    def on_change(self, fn) -> None:
        self._listeners.append(fn)

    def on_advance_result(self, fn) -> None:
        self._result_listeners.append(fn)

    def _reload_if_changed(self) -> ServingData | None:
        """Blocking; run in a worker thread. Returns new data when world.json changed.

        The mock folder is a start-up fallback only. Once real serving data is loaded, an
        incomplete serving/ (scripts/reset.sh and advance.py --day0 delete the files before the
        rebuild) keeps the current data, and the next poll tries again."""
        directory, source = pick_serving_dir(self.serving_dir, self.mock_dir)
        if source != "serving" and self.current.source == "serving":
            return None
        stamp = file_stamp(directory / "world.json")
        if stamp is None or (str(directory) == self.current.directory and stamp == self._stamp):
            return None
        try:
            data = load_serving(directory, source)
        except ServingError:
            return None  # half-written; the next poll tries again
        self._stamp = stamp
        if str(directory) == self.current.directory and data.world == self.current.world:
            return None  # same world object again (the file was only touched): not a new week
        return data

    async def poll_once(self) -> ServingData | None:
        # The watcher and the end of an advance can poll at the same moment; the lock makes
        # sure one world.json change is announced (and dropped onto the board) only once.
        if self._poll_lock is None:
            self._poll_lock = asyncio.Lock()
        async with self._poll_lock:
            data = await asyncio.to_thread(self._reload_if_changed)
            if data is None:
                return None
            self.current = data
            for fn in self._listeners:
                fn(data)
            return data

    async def watch(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)

    # -- advance
    def start_advance(self, day0: bool = False) -> dict:
        """Start scripts/advance.py in the background and return at once."""
        if self.advancing:
            return {"ok": False, "advancing": True, "message": "an advance is already running"}
        if not self.advance_script.is_file():
            res = {"ok": False, "advancing": False,
                   "message": f"scripts/advance.py not found at {self.advance_script}; the pipeline part is not installed"}
            self.last_result = res
            return res
        self.advancing = True
        self.advance_started = time.monotonic()
        self._advance_task = asyncio.get_running_loop().create_task(self._run_advance(day0))
        return {"ok": True, "advancing": True, "message": "advance started"}

    async def _run_advance(self, day0: bool) -> dict:
        result: dict
        try:
            try:
                result = await asyncio.to_thread(run_advance_blocking, self.advance_script, day0)
            except Exception as exc:  # never let the background task die silently
                result = {"ok": False, "message": f"advance failed to run: {type(exc).__name__}"}
            if self.advance_started is not None:
                result["seconds"] = round(time.monotonic() - self.advance_started, 1)
            self.last_result = result
            try:
                await self.poll_once()       # load the new week before "advancing" goes false
            except Exception:
                pass
        finally:
            self.advancing = False
            self.advance_started = None
        for fn in self._result_listeners:
            fn(result)
        return result

    async def wait_advance(self) -> dict | None:
        if self._advance_task:
            return await self._advance_task
        return self.last_result


def parse_advance_output(stdout: str) -> dict | None:
    """advance.py prints one JSON object on its last stdout line."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def run_advance_blocking(script: Path, day0: bool = False, timeout: float = 3600.0) -> dict:
    """Run advance.py as a subprocess (thread-based so it works on any Windows event loop)."""
    import subprocess

    cmd = [sys.executable, str(script)] + (["--day0"] if day0 else [])
    # No console window pops up for advance.py (and the wsl.exe it starts) when the server
    # itself runs without one, as scripts/start.ps1 does.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(cmd, cwd=str(script.parent.parent), capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, creationflags=flags)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"advance.py timed out after {int(timeout)} s"}
    except OSError as exc:
        return {"ok": False, "message": f"could not start advance.py: {type(exc).__name__}"}
    obj = parse_advance_output(proc.stdout or "")
    if obj is None:
        return {"ok": False, "message": f"advance.py exited {proc.returncode} without a JSON result line"}
    obj.setdefault("ok", proc.returncode == 0)
    obj.setdefault("message", "")
    return obj
