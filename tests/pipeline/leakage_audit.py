#!/usr/bin/env python3
"""Leakage audit of the pipeline database at the current world clock.

Run in WSL from the repo root while no Bruin run is active (it uses `bruin query`, which needs the
database lock):  python3 tests/pipeline/leakage_audit.py
Standard library only. Every query below must return 0 (or the stated value). Exit 0 = clean.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECKS = [
    # --- structural: raw.* never holds a row dated after the clock, and holds exactly the filtered mirror
    ("raw.cves has no CVE published after the clock",
     "SELECT count(*) FROM raw.cves WHERE published_date > (SELECT asof_date FROM raw.world_clock)", 0),
    ("raw.kev has no entry added after the clock",
     "SELECT count(*) FROM raw.kev WHERE date_added > (SELECT asof_date FROM raw.world_clock)", 0),
    ("raw.epss has no file dated after the clock",
     "SELECT count(*) FROM raw.epss WHERE score_date > (SELECT asof_date FROM raw.world_clock)", 0),
    ("raw.cves equals the mirror filtered at the clock (incremental loads added exactly the windows)",
     "SELECT abs((SELECT count(*) FROM raw.cves) - (SELECT count(*) FROM landing.nvd_cves "
     "WHERE vuln_status <> 'Rejected' AND published_date <= (SELECT asof_date FROM raw.world_clock)))", 0),
    ("raw.kev equals the mirror filtered at the clock",
     "SELECT abs((SELECT count(*) FROM raw.kev) - (SELECT count(*) FROM landing.kev "
     "WHERE date_added <= (SELECT asof_date FROM raw.world_clock)))", 0),
    ("raw.epss holds one file per Sunday from 2025-03-23 to the clock",
     "SELECT abs((SELECT count(DISTINCT score_date) FROM raw.epss) - "
     "(date_diff('day', DATE '2025-03-23', (SELECT asof_date FROM raw.world_clock)) // 7 + 1))", 0),
    # --- labels
    ("no train/valid label window ends after the clock",
     "SELECT count(*) FROM feat.snapshot_features WHERE role <> 'serve' AND label_end > (SELECT asof_date FROM raw.world_clock)", 0),
    ("training label windows end on or before the validation snapshot (no overlap with validation)",
     "SELECT count(*) FROM feat.snapshot_features WHERE role = 'train' AND label_end > "
     "(SELECT snapshot_date FROM feat.snapshots WHERE role = 'valid')", 0),
    ("serve rows carry no label",
     "SELECT count(*) FROM feat.snapshot_features WHERE role = 'serve' AND y IS NOT NULL", 0),
    ("labels recomputed independently from the full mirror agree with stored labels (train+valid)",
     "SELECT count(*) FROM feat.snapshot_features f LEFT JOIN landing.kev k ON k.cve_id = f.cve_id "
     "WHERE f.role <> 'serve' AND f.y <> CASE WHEN k.date_added > f.snapshot_date AND k.date_added <= f.label_end THEN 1 ELSE 0 END", 0),
    # --- features as of the snapshot
    ("no candidate published after its snapshot",
     "SELECT count(*) FROM feat.snapshot_features WHERE published_date > snapshot_date", 0),
    ("EPSS features come from a file dated on or before the snapshot",
     "SELECT count(*) FROM feat.snapshot_features WHERE epss_date > snapshot_date", 0),
    ("vendor KEV counts recomputed from landing.kev cut at the snapshot are never larger than stored "
     "(a larger recount would mean a future KEV row was used)",
     "WITH s AS (SELECT * FROM feat.snapshot_features WHERE role = 'valid' AND vendor_kev_count_asof > 0 "
     "ORDER BY hash(cve_id) LIMIT 300) "
     "SELECT count(*) FROM s WHERE vendor_kev_count_asof > (SELECT count(*) FROM landing.kev k "
     "LEFT JOIN landing.nvd_cves c ON c.cve_id = k.cve_id AND c.published_date <= k.date_added "
     "JOIN raw.cves rc ON rc.cve_id = s.cve_id "
     "WHERE k.date_added <= s.snapshot_date "
     "AND coalesce(c.vendor, trim(regexp_replace(lower(trim(k.vendor_project)), '[^a-z0-9]+', '_', 'g'), '_')) = rc.vendor)", 0),
    ("train/valid candidates are not already in KEV at their snapshot",
     "SELECT count(*) FROM feat.snapshot_features f JOIN landing.kev k ON k.cve_id = f.cve_id "
     "WHERE f.role <> 'serve' AND k.date_added <= f.snapshot_date", 0),
    ("no forbidden column reaches the feature table",
     "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'feat' AND table_name = 'snapshot_features' "
     "AND regexp_matches(column_name, 'mapping|kev_date|status|modified|cisa|desc|ssvc|reference|technique')", 0),
    ("time-travel fingerprints: every training snapshot built at two clocks is identical",
     "SELECT count(*) FROM (SELECT snapshot_date FROM feat.fingerprints GROUP BY snapshot_date "
     "HAVING count(DISTINCT fingerprint) > 1)", 0),
    # --- model and gate
    ("the challenger was trained and scored at the current clock",
     "SELECT count(*) FROM ml.challenger_scores WHERE asof_date <> (SELECT asof_date FROM raw.world_clock)", 0),
    ("the challenger scored only the validation snapshot and the serve snapshot",
     "SELECT count(*) FROM ml.challenger_scores s WHERE NOT ((s.role = 'valid' AND s.snapshot_date = "
     "(SELECT snapshot_date FROM feat.snapshots WHERE role = 'valid')) OR (s.role = 'serve' AND s.snapshot_date = "
     "(SELECT asof_date FROM raw.world_clock)))", 0),
    ("a promotion exists only for weeks whose challenger beat EPSS",
     "SELECT count(*) FROM ml.promotions p JOIN ml.eval_metrics m USING (asof_date) "
     "WHERE NOT (m.challenger_hits_at_50 > m.epss_hits_at_50 AND m.n_positives >= 10)", 0),
]

INFO = [
    ("world clock", "SELECT asof_date, window_start, pipeline_run_id FROM raw.world_clock"),
    ("gate history", "SELECT asof_date, model_version, n_candidates, n_positives, challenger_hits_at_50, epss_hits_at_50, "
                     "challenger_p_at_50, epss_p_at_50, gate_passed FROM ml.eval_metrics ORDER BY asof_date"),
    ("promotions", "SELECT asof_date, model_version, challenger_hits_at_50, epss_hits_at_50 FROM ml.promotions ORDER BY asof_date"),
    ("fingerprint overlaps", "SELECT snapshot_date, count(*) AS n_clocks, count(DISTINCT fingerprint) AS n_distinct "
                             "FROM feat.fingerprints GROUP BY snapshot_date HAVING count(*) > 1 ORDER BY snapshot_date"),
]


def bruin_query(sql: str) -> list[dict]:
    env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + os.pathsep + os.environ.get("PATH", ""))
    proc = subprocess.run(["bruin", "query", "--connection", "duckdb-default", "-o", "json", "--query", sql],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stdout + proc.stderr).strip()[-600:])
    doc = json.loads(proc.stdout)
    cols = [c["name"] for c in doc["columns"]]
    return [dict(zip(cols, r)) for r in doc["rows"]]


def main() -> int:
    bad = 0
    for title, sql in INFO:
        try:
            rows = bruin_query(sql)
            print(f"-- {title}")
            for r in rows:
                print("   ", json.dumps(r, default=str))
        except Exception as exc:
            print(f"-- {title}: {exc}")
    for title, sql, expect in CHECKS:
        try:
            rows = bruin_query(sql)
            got = list(rows[0].values())[0]
            ok = int(got) == expect
        except Exception as exc:
            got, ok = f"error: {exc}", False
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {title}  (got {got}, want {expect})")
    print(f"leakage audit: {len(CHECKS) - bad} passed, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
