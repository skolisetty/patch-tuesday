"""Paths, the .env loader and the tunable constants.

Secrets are loaded into os.environ and are never printed or logged from here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
# PT_* variables exist so tests can point the server at temporary folders.
SERVING_DIR = Path(os.environ.get("PT_SERVING_DIR") or ROOT / "serving")
MOCK_DIR = Path(os.environ.get("PT_MOCK_DIR") or ROOT / "serving_mock")
DATA_DIR = ROOT / "data"
LEDGER_DIR = Path(os.environ.get("PT_LEDGER_DIR") or ROOT / "ledger")
GAME_DB = Path(os.environ.get("PT_GAME_DB") or DATA_DIR / "game.duckdb")
LEDGER_JSONL = LEDGER_DIR / "ledger.jsonl"
REASONS_JSONL = LEDGER_DIR / "reasons.jsonl"
ADVANCE_SCRIPT = Path(os.environ.get("PT_ADVANCE_SCRIPT") or ROOT / "scripts" / "advance.py")
ADVANCE_LOCK = DATA_DIR / ".advance.lock"

_loaded = False


def load_env(path: Path | None = None, force: bool = False) -> list[str]:
    """Read KEY=VALUE lines into os.environ. Existing variables win.

    Returns the names that were set (names only, never values).
    """
    global _loaded
    if _loaded and not force and path is None:
        return []
    path = path or ENV_FILE
    names: list[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        _loaded = True
        return names
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            continue
        if key not in os.environ or os.environ[key] == "":
            os.environ[key] = value
            names.append(key)
    _loaded = True
    return names


def env(name: str, default: str = "") -> str:
    load_env()
    return os.environ.get(name, default).strip()


def env_flag(name: str) -> bool:
    return env(name) in ("1", "true", "TRUE", "yes", "on")


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Tuning:
    """Constants. The first block is fixed by docs/CONTRACTS.md; the rest is balance tuning."""

    # --- fixed by the contract ---
    ticks_total: int = 150
    breach_k: int = 6
    kev_bonus: float = 0.15
    recon_bonus: float = 0.10
    patching_mult: float = 0.4
    monitor_mult: float = 0.5
    p_cap: float = 0.95
    patch_ticks: int = 2
    monitor_ticks: int = 3
    isolate_ticks: int = 5
    restore_ticks: int = 3
    first_breach_points: float = 5.0
    restore_points: float = 3.0
    isolate_cost: float = 1.0

    # --- balance tuning (not in the contract) ---
    # Retuned on the REAL serving deck (as-of 2026-03-22, EPSS mode; humans idle), 30 seeds each:
    #   no mid-match drop: red 15 / blue 15, 2.67 breaches per match
    #   drop of the real week 2026-03-29 at tick 60: red 18 / blue 12, 3.50 breaches per match
    # Before (24 / 12 / 18): red 2 / blue 28 without a drop. The real deck is bimodal (KEV cards at
    # EPSS ~0.94, the rest mostly < 0.02) so blue cleared the few good cards before red arrived.
    # The mock deck is harder on blue with these values (red wins nearly every mock match).
    red_entry_delay: int = 10         # ticks both reds wait before their first action
    exploit_cooldown_fail: int = 5    # red is busy this many ticks after a failed exploit
    exploit_cooldown_success: int = 12  # ... after a breach (establishing the foothold)
    eject_cooldown: int = 10          # red thrown out of an isolated node
    detect_prob: float = 0.8          # failed exploit on a monitored node reveals the red
    detect_ticks: int = 3
    breach_reveal_ticks: int = 2      # a breach is loud
    restore_monitor_ticks: int = 6    # a restored node stays watched for a while
    pivot_range: int = 2
    max_cards_per_node: int = 5
    drop_max_cards: int = 12
    campaign_ticks: int = 30
    campaign_targets: int = 6
    world_change_cutoff: int = 25     # no drop in the last N ticks (after tick 125 it waits for the next match)
    eps_opportunist: float = 0.08
    eps_patient: float = 0.03
    eps_triage: float = 0.15
    eps_patcher: float = 0.05
    eps_hunter: float = 0.05
    opportunist_min_odds: float = 0.10
    opportunist_give_up: int = 2      # failures on one card before it is dropped
    patient_start_odds: float = 0.60  # patience threshold, decays while idle
    patient_min_odds: float = 0.30
    patient_decay: float = 0.03
    patient_stale_ticks: int = 14     # scan intel older than this is not trusted
    hunter_isolate_pressure: int = 3
    responder_range: int = 3         # Triage/Patcher break off to restore a node this close if nearest
    patcher_restore_min: int = 1    # Patcher restores the node it stands on when this many are compromised
    # Hunter INTEL (Telem search). Only switched on by the server when TELEM_ENABLED=1.
    intel_enabled: bool = False
    intel_cooldown: int = 30        # ticks between two INTEL moves
    intel_max_per_match: int = 3    # each one is a paid search

    def with_overrides(self, **kw) -> "Tuning":
        names = {f.name: f.type for f in fields(self)}
        clean = {}
        for k, v in kw.items():
            if k not in names:
                raise KeyError(f"unknown tuning constant: {k}")
            cur = getattr(self, k)
            clean[k] = type(cur)(float(v)) if isinstance(cur, int) else type(cur)(v)
        return replace(self, **clean)


TUNING = Tuning()

TICK_SECONDS_DEFAULT = 2.0


def tick_seconds() -> float:
    """Tick length: PT_TICK_SECONDS (tests and rehearsals), else TICK_SECONDS, else 2.0."""
    for name in ("PT_TICK_SECONDS", "TICK_SECONDS"):
        v = env(name)
        if v:
            try:
                x = float(v)
            except ValueError:
                continue
            if x > 0:
                return x
    return TICK_SECONDS_DEFAULT
