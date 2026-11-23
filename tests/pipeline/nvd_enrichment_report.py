#!/usr/bin/env python3
"""Report how NVD's download-date enrichment differs between future-KEV CVEs and the rest.

Run in WSL from the repo root while no Bruin run is active:  python3 tests/pipeline/nvd_enrichment_report.py
Standard library only; it reads the pipeline database through `bruin query` (like leakage_audit.py).

Why: landing.nvd_cves takes CPE vendor/product, the CWE (NVD-sourced first) and the Primary CVSS
from NVD records downloaded on 2026-11-23, after every train and valid label window closed. NVD
analyses KEV CVEs first, so whether these fields are filled is correlated with the label. This
script prints that correlation for the current world clock, and the ml.sensitivity history:
the gate re-run with every NVD-derived feature removed. It is a report; it never fails.
"""
from __future__ import annotations

import json
import sys

from leakage_audit import bruin_query

GROUPS = """
SELECT f.role, f.y,
       count(*)                                                                  AS n,
       round(avg(CASE WHEN l.vuln_status IN ('Analyzed', 'Modified') THEN 1 ELSE 0 END), 3) AS nvd_analyzed,
       round(avg(CASE WHEN l.vuln_status = 'Deferred' THEN 1 ELSE 0 END), 3)      AS deferred,
       round(avg(CASE WHEN l.cwe_id IS NULL THEN 1 ELSE 0 END), 3)                AS cwe_null,
       round(avg(CASE WHEN l.vendor IS NULL THEN 1 ELSE 0 END), 3)                AS vendor_null,
       round(avg(CASE WHEN l.cvss_base_score IS NULL THEN 1 ELSE 0 END), 3)       AS cvss_null,
       round(avg(CASE WHEN l.last_modified_date > f.label_end THEN 1 ELSE 0 END), 3) AS modified_after_label_end
FROM feat.snapshot_features f
JOIN landing.nvd_cves l ON l.cve_id = f.cve_id
WHERE f.role IN ('train', 'valid')
GROUP BY 1, 2 ORDER BY 1, 2
"""
SENSITIVITY = """
SELECT s.asof_date, s.n_positives, s.full_hits_at_50, s.masked_hits_at_50, s.epss_hits_at_50,
       s.masked_beats_epss, m.challenger_hits_at_50 AS gate_challenger_hits_at_50, m.gate_passed
FROM ml.sensitivity s
LEFT JOIN ml.eval_metrics m ON m.asof_date = s.asof_date AND m.pipeline_run_id = s.pipeline_run_id
ORDER BY s.asof_date
"""


def main() -> int:
    clock = bruin_query("SELECT asof_date FROM raw.world_clock")[0]["asof_date"]
    print(f"-- NVD enrichment by role and label at clock {clock} (landing.nvd_cves, downloaded 2026-11-23)")
    for r in bruin_query(GROUPS):
        print("   ", json.dumps(r, default=str))
    print("-- ml.sensitivity: hits@50 of the served challenger, of the challenger without NVD-derived features, and of EPSS")
    for r in bruin_query(SENSITIVITY):
        print("   ", json.dumps(r, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
