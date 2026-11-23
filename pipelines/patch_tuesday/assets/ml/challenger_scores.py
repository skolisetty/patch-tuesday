"""@bruin
name: ml.challenger_scores
image: python:3.12
connection: duckdb-default
description: |
  The weekly challenger. Trains a HistGradientBoostingClassifier on the train snapshots of
  feat.snapshot_features (negatives re-weighted by 100 / neg_sample_pct so scores stay near the
  true base rate), then scores every valid and serve row. Hyperparameters are fixed and were not
  tuned on any validation snapshot. If training has fewer than 20 positives the asset falls back
  to model_score = epss_asof, which ties EPSS and so fails the gate cleanly.
  TEST SWITCH: pipeline variable degrade_model=true replaces the model with a deterministic
  pseudo-random scorer so the promotion gate must fail (see README, "Proving the gate blocks").
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - ml
  - model

depends:
  - feat.snapshot_features
  - feat.fingerprints
  - raw.world_clock

materialization:
  type: table
  strategy: create+replace

parameters:
  enforce_schema: true

columns:
  - name: asof_date
    type: date
    description: World clock the model was trained at.
    extends: Snapshot.Date
    checks:
      - name: not_null
  - name: snapshot_date
    type: date
    description: Snapshot the row was scored for (asof - H for valid, asof for serve).
    upstreams:
      - table: feat.snapshot_features
        column: snapshot_date
    checks:
      - name: not_null
  - name: role
    type: varchar
    description: valid or serve.
    upstreams:
      - table: feat.snapshot_features
        column: role
    checks:
      - name: not_null
      - name: accepted_values
        value: ["valid", "serve"]
  - name: cve_id
    type: varchar
    description: Scored CVE.
    extends: CVE.ID
    upstreams:
      - table: feat.snapshot_features
        column: cve_id
    checks:
      - name: not_null
  - name: model_score
    type: double
    description: Predicted probability of entering KEV within H days.
    extends: ModelScore.Score
    upstreams:
      - table: feat.snapshot_features
        column: epss_asof
      - table: feat.snapshot_features
        column: vendor_kev_count_asof
      - table: feat.snapshot_features
        column: cvss_base_score
    checks:
      - name: not_null
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: model_version
    type: varchar
    description: hgb-<asof> for the real model, epssfallback-<asof> or degraded-test-<asof> otherwise.
    checks:
      - name: not_null
  - name: run_id
    type: varchar
    description: Bruin run id that trained the model.
    checks:
      - name: not_null
  - name: n_train_rows
    type: integer
    description: Training rows used.
    checks:
      - name: positive
  - name: n_train_positives
    type: integer
    description: Positive training rows used.
    checks:
      - name: non_negative

custom_checks:
  - name: both roles were scored
    description: The gate needs valid scores and serving needs serve scores.
    query: SELECT count(DISTINCT role) FROM ml.challenger_scores
    value: 2
    blocking: true
@bruin"""

import json
import os
import time

import numpy as np
import pandas as pd
from bruin import query
from sklearn.ensemble import HistGradientBoostingClassifier

FEATURES = [
    "age_days",
    "cvss_base_score", "exploitability_score", "impact_score", "has_cvss",
    "av_network", "ac_low", "pr_none", "ui_none", "scope_changed", "c_high", "i_high", "a_high",
    "epss_asof", "epss_percentile_asof", "epss_delta_28d",
    "vendor_kev_count_asof", "vendor_cve_count_asof", "vendor_kev_ratio_asof",
    "product_kev_count_asof", "cwe_kev_count_asof",
    "kw_rce", "kw_unauth", "kw_cmd_injection", "kw_priv_esc", "kw_auth_bypass", "kw_deserialization", "kw_overflow",
]
MIN_TRAIN_POSITIVES = 20


def _vars():
    try:
        return json.loads(os.environ.get("BRUIN_VARS") or "{}")
    except json.JSONDecodeError:
        return {}


def materialize(**kwargs):
    t0 = time.time()
    v = _vars()
    neg_pct = float(v.get("neg_sample_pct", 5))
    degrade = str(v.get("degrade_model", False)).lower() in ("true", "1")
    asof = os.environ["BRUIN_END_DATE"]
    run_id = os.environ["BRUIN_RUN_ID"]
    cols = ", ".join(FEATURES)

    # ORDER BY makes training deterministic: HGB's internal early-stopping split depends on row order.
    train = query(f"SELECT y, {cols} FROM feat.snapshot_features WHERE role = 'train' ORDER BY snapshot_date, cve_id")
    score = query(
        f"SELECT role, CAST(snapshot_date AS VARCHAR) AS snapshot_date, cve_id, {cols} "
        "FROM feat.snapshot_features WHERE role IN ('valid', 'serve') ORDER BY role, cve_id"
    )
    X_train = train[FEATURES].astype("float64")
    y_train = train["y"].astype("int64")
    n_pos = int(y_train.sum())
    print(f"ml.challenger_scores: train rows {len(train)}, positives {n_pos}, scoring {len(score)} rows")

    if degrade:
        # TEST SWITCH ONLY: a deliberately useless scorer, so the gate must fail.
        rng = np.random.default_rng(12345)
        scores = rng.uniform(0.0, 0.01, size=len(score))
        version = f"degraded-test-{asof}"
        print("ml.challenger_scores: WARNING degrade_model=true, serving a deliberately useless scorer (test switch)")
    elif n_pos < MIN_TRAIN_POSITIVES:
        scores = score["epss_asof"].fillna(0.0).to_numpy()
        version = f"epssfallback-{asof}"
        print(f"ml.challenger_scores: only {n_pos} training positives, falling back to EPSS (gate will tie and fail)")
    else:
        weights = np.where(y_train.to_numpy() == 1, 1.0, 100.0 / neg_pct)
        model = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.06, max_leaf_nodes=15,
            l2_regularization=1.0, min_samples_leaf=40, random_state=7,
        )
        model.fit(X_train, y_train, sample_weight=weights)
        scores = model.predict_proba(score[FEATURES].astype("float64"))[:, 1]
        version = f"hgb-{asof}"

    out = pd.DataFrame({
        "asof_date": asof,
        "snapshot_date": score["snapshot_date"],
        "role": score["role"],
        "cve_id": score["cve_id"],
        "model_score": np.clip(scores, 0.0, 1.0).astype("float64"),
        "model_version": version,
        "run_id": run_id,
        "n_train_rows": len(train),
        "n_train_positives": n_pos,
    })
    print(f"ml.challenger_scores: {version} scored {len(out)} rows in {time.time() - t0:.1f}s")
    return out
