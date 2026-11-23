"""@bruin
name: ml.sensitivity
image: python:3.12
connection: duckdb-default
description: |
  Diagnostic, not a gate: how much does the challenger depend on NVD enrichment? NVD's CPE
  vendor/product, its CWE and its Primary CVSS come from records taken at download time, and
  NVD analyses KEV CVEs first, so whether those fields are filled (and with what) is correlated
  with the future label (README, "Leakage audit"). This asset retrains the challenger with the
  same fixed hyperparameters on the same rows WITHOUT every NVD-derived feature (CVSS score,
  sub-scores and vector flags; vendor, product and CWE KEV counts; vendor CVE count and ratio)
  and counts its future-KEV hits in the top 50 of the same validation candidates. Kept: age,
  EPSS as of the snapshot (level, percentile, 28-day delta) and the description keyword flags.
  delete+insert on asof_date keeps one row per world week. Nothing downstream reads it, and
  scripts/advance.py never blocks a week on it.
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - ml
  - diagnostic

depends:
  - feat.snapshot_features
  - feat.fingerprints
  - ml.challenger_scores
  - raw.world_clock

materialization:
  type: table
  strategy: delete+insert
  incremental_key: asof_date

parameters:
  enforce_schema: true

columns:
  - name: asof_date
    type: date
    description: World clock of the comparison.
    extends: Snapshot.Date
    primary_key: true
    checks:
      - name: not_null
  - name: pipeline_run_id
    type: varchar
    description: Run that computed it.
    checks:
      - name: not_null
  - name: n_positives
    type: integer
    description: Validation candidates that entered KEV in the label window.
    checks:
      - name: non_negative
  - name: full_hits_at_50
    type: integer
    description: Positives in the top 50 of the served challenger (all 28 features), recomputed from ml.challenger_scores.
    checks:
      - name: non_negative
  - name: masked_hits_at_50
    type: integer
    description: Positives in the top 50 of the challenger retrained without NVD-derived features.
    checks:
      - name: non_negative
  - name: epss_hits_at_50
    type: integer
    description: Positives in the top 50 by EPSS as of the validation snapshot (same rule as the gate).
    checks:
      - name: non_negative
  - name: masked_hits_at_100
    type: integer
    description: Positives in the top 100 of the masked challenger.
    checks:
      - name: non_negative
  - name: masked_beats_epss
    type: boolean
    description: Would the masked challenger pass the gate rule (strictly more hits@50 than EPSS)?
    checks:
      - name: not_null
  - name: n_features_kept
    type: integer
    description: Features the masked challenger was trained on.
    checks:
      - name: positive
@bruin"""

import json
import os

import numpy as np
import pandas as pd
from bruin import query
from sklearn.ensemble import HistGradientBoostingClassifier

# Everything that comes from NVD's own enrichment of the record (CPE, NVD CWE, Primary CVSS).
NVD_DERIVED = {
    "cvss_base_score", "exploitability_score", "impact_score", "has_cvss",
    "av_network", "ac_low", "pr_none", "ui_none", "scope_changed", "c_high", "i_high", "a_high",
    "vendor_kev_count_asof", "vendor_cve_count_asof", "vendor_kev_ratio_asof",
    "product_kev_count_asof", "cwe_kev_count_asof",
}
ALL_FEATURES = [
    "age_days",
    "cvss_base_score", "exploitability_score", "impact_score", "has_cvss",
    "av_network", "ac_low", "pr_none", "ui_none", "scope_changed", "c_high", "i_high", "a_high",
    "epss_asof", "epss_percentile_asof", "epss_delta_28d",
    "vendor_kev_count_asof", "vendor_cve_count_asof", "vendor_kev_ratio_asof",
    "product_kev_count_asof", "cwe_kev_count_asof",
    "kw_rce", "kw_unauth", "kw_cmd_injection", "kw_priv_esc", "kw_auth_bypass", "kw_deserialization", "kw_overflow",
]
KEPT = [f for f in ALL_FEATURES if f not in NVD_DERIVED]


def _vars():
    try:
        return json.loads(os.environ.get("BRUIN_VARS") or "{}")
    except json.JSONDecodeError:
        return {}


def _hits(order_df: pd.DataFrame, by: list[str], ascending: list[bool], k: int) -> int:
    top = order_df.sort_values(by, ascending=ascending, na_position="last", kind="mergesort").head(k)
    return int(top["y"].sum())


def materialize(**kwargs):
    v = _vars()
    neg_pct = float(v.get("neg_sample_pct", 5))
    asof = os.environ["BRUIN_END_DATE"][:10]
    run_id = os.environ["BRUIN_RUN_ID"]
    cols = ", ".join(KEPT)
    train = query(f"SELECT y, {cols} FROM feat.snapshot_features WHERE role = 'train' ORDER BY snapshot_date, cve_id")
    valid = query(
        f"SELECT f.cve_id, f.y, f.epss_asof AS epss_rank, f.epss_percentile_asof AS epss_pct_rank, "
        f"s.model_score, {', '.join('f.' + c for c in KEPT)} "
        "FROM feat.snapshot_features f "
        "JOIN ml.challenger_scores s ON s.cve_id = f.cve_id AND s.role = 'valid' AND s.snapshot_date = f.snapshot_date "
        "WHERE f.role = 'valid' ORDER BY f.cve_id"
    )
    y = train["y"].astype("int64")
    weights = np.where(y.to_numpy() == 1, 1.0, 100.0 / neg_pct)
    model = HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.06, max_leaf_nodes=15,
        l2_regularization=1.0, min_samples_leaf=40, random_state=7,
    )
    model.fit(train[KEPT].astype("float64"), y, sample_weight=weights)
    valid["masked"] = model.predict_proba(valid[KEPT].astype("float64"))[:, 1]
    valid["y"] = valid["y"].astype("int64")
    # Same tie-breaks as ml.eval_metrics: score DESC then cve_id; EPSS DESC NULLS LAST, percentile, cve_id.
    masked50 = _hits(valid, ["masked", "cve_id"], [False, True], 50)
    masked100 = _hits(valid, ["masked", "cve_id"], [False, True], 100)
    full50 = _hits(valid, ["model_score", "cve_id"], [False, True], 50)
    epss50 = _hits(valid, ["epss_rank", "epss_pct_rank", "cve_id"], [False, False, True], 50)
    print(f"ml.sensitivity: {asof}: positives {int(valid['y'].sum())}, hits@50 full {full50}, "
          f"masked (no NVD-derived features, {len(KEPT)} kept) {masked50}, EPSS {epss50}")
    return pd.DataFrame([{
        "asof_date": asof, "pipeline_run_id": run_id, "n_positives": int(valid["y"].sum()),
        "full_hits_at_50": full50, "masked_hits_at_50": masked50, "epss_hits_at_50": epss50,
        "masked_hits_at_100": masked100, "masked_beats_epss": bool(masked50 > epss50),
        "n_features_kept": len(KEPT),
    }])
