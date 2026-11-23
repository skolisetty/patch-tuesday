"""The decision ledger: data/game.duckdb (one connection, one thread) and ledger/ledger.jsonl.

All methods are synchronous and must be called from the thread that created the Ledger
(the event-loop thread in the server). One batch per tick.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb

from . import config
from .state import LEDGER_FIELDS

_TYPES = {
    "ledger_id": "VARCHAR", "match_id": "VARCHAR", "world_asof": "VARCHAR", "pipeline_run_id": "VARCHAR",
    "model_version": "VARCHAR", "tick": "INTEGER", "ts": "VARCHAR", "actor_id": "VARCHAR", "team": "VARCHAR",
    "kind": "VARCHAR", "action": "VARCHAR", "node_id": "INTEGER", "cve_id": "VARCHAR", "score_used": "DOUBLE",
    "score_source": "VARCHAR", "p_breach": "DOUBLE", "roll": "DOUBLE", "outcome": "VARCHAR", "policy": "VARCHAR",
    "reason_text": "VARCHAR", "llm_model": "VARCHAR", "llm_latency_ms": "INTEGER",
}
_INSERT = f"INSERT INTO agent_ledger ({', '.join(LEDGER_FIELDS)}) VALUES ({', '.join('?' for _ in LEDGER_FIELDS)})"


def now_ts() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


class Ledger:
    def __init__(self, db_path: Path | str | None = None, jsonl_path: Path | str | None = None,
                 reasons_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else config.GAME_DB
        self.jsonl_path = Path(jsonl_path) if jsonl_path is not None else config.LEDGER_JSONL
        self.reasons_path = Path(reasons_path) if reasons_path is not None else config.REASONS_JSONL
        for p in (self.db_path, self.jsonl_path, self.reasons_path):
            p.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.db_path))
        cols = ", ".join(f"{name} {_TYPES[name]}" for name in LEDGER_FIELDS)
        self.con.execute(f"CREATE TABLE IF NOT EXISTS agent_ledger ({cols})")
        self.rows_written = 0

    def write(self, rows: list[dict]) -> int:
        """Insert one tick's rows in one transaction and append them to ledger.jsonl."""
        if not rows:
            return 0
        for r in rows:
            if not r.get("ts"):
                r["ts"] = now_ts()
        params = [[r.get(k) for k in LEDGER_FIELDS] for r in rows]
        self.con.execute("BEGIN")
        try:
            self.con.executemany(_INSERT, params)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        with open(self.jsonl_path, "a", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps({k: r.get(k) for k in LEDGER_FIELDS}, separators=(",", ":")) + "\n")
        self.rows_written += len(rows)
        return len(rows)

    def update_reason(self, ledger_id: str, reason_text: str, llm_model: str | None,
                      llm_latency_ms: int | None) -> None:
        """A late LLM reason: update the row and append to reasons.jsonl."""
        self.con.execute(
            "UPDATE agent_ledger SET reason_text = ?, llm_model = ?, llm_latency_ms = ? WHERE ledger_id = ?",
            [reason_text, llm_model, llm_latency_ms, ledger_id])
        with open(self.reasons_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"ledger_id": ledger_id, "reason_text": reason_text, "llm_model": llm_model,
                                 "llm_latency_ms": llm_latency_ms, "ts": now_ts()}, separators=(",", ":")) + "\n")

    def recent(self, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit), 5000))
        cur = self.con.execute(
            f"SELECT {', '.join(LEDGER_FIELDS)} FROM agent_ledger ORDER BY ts DESC, tick DESC, ledger_id DESC LIMIT ?",
            [limit])
        return [dict(zip(LEDGER_FIELDS, row)) for row in cur.fetchall()]

    def count(self, match_id: str | None = None) -> int:
        if match_id:
            return self.con.execute("SELECT count(*) FROM agent_ledger WHERE match_id = ?", [match_id]).fetchone()[0]
        return self.con.execute("SELECT count(*) FROM agent_ledger").fetchone()[0]

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass


class NullLedger:
    """Counts rows only. Used by the headless runner when no ledger files are wanted."""

    def __init__(self):
        self.rows_written = 0

    def write(self, rows: list[dict]) -> int:
        self.rows_written += len(rows)
        return len(rows)

    def update_reason(self, *a, **k) -> None:
        pass

    def recent(self, limit: int = 200) -> list[dict]:
        return []

    def count(self, match_id: str | None = None) -> int:
        return self.rows_written

    def close(self) -> None:
        pass
