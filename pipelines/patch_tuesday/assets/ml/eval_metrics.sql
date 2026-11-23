/* @bruin
name: ml.eval_metrics
type: duckdb.sql
description: |
  The promotion gate. Ranks the same validation candidates (snapshot asof - H, label window
  (asof - H, asof]) by the challenger score and by EPSS as of the validation snapshot, and counts
  future-KEV hits in each top 50. delete+insert on asof_date keeps one row per world week, so the
  table is the history of every gate decision, pass or fail. The blocking custom check "challenger
  beats EPSS on precision@50" is the gate: when it fails, ml.promote and every serve asset are
  skipped and the week runs on EPSS.
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - ml
  - gate
depends:
  - raw.world_clock
  - feat.snapshot_features
  - ml.challenger_scores
materialization:
  type: table
  strategy: delete+insert
  incremental_key: asof_date
columns:
  - name: asof_date
    type: DATE
    description: World clock of the decision.
    extends: Snapshot.Date
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: valid_snapshot_date
    type: DATE
    description: Validation snapshot (asof - H).
    checks:
      - name: not_null
  - name: model_version
    type: VARCHAR
    description: Challenger version evaluated.
    checks:
      - name: not_null
  - name: pipeline_run_id
    type: VARCHAR
    description: Run that evaluated it.
    checks:
      - name: not_null
  - name: n_candidates
    type: BIGINT
    description: Validation candidates (published by the snapshot, not yet in KEV).
    checks:
      - name: positive
  - name: n_positives
    type: BIGINT
    description: Candidates that entered KEV in the label window.
    checks:
      - name: non_negative
  - name: challenger_hits_at_50
    type: BIGINT
    description: Positives in the challenger's top 50.
    checks:
      - name: non_negative
  - name: epss_hits_at_50
    type: BIGINT
    description: Positives in EPSS's top 50 (EPSS as of the validation snapshot).
    checks:
      - name: non_negative
  - name: challenger_p_at_50
    type: DOUBLE
    description: challenger_hits_at_50 / 50.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: epss_p_at_50
    type: DOUBLE
    description: epss_hits_at_50 / 50.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: challenger_hits_at_100
    type: BIGINT
    description: Positives in the challenger's top 100 (reported, not gated).
  - name: epss_hits_at_100
    type: BIGINT
    description: Positives in EPSS's top 100 (reported, not gated).
  - name: gate_passed
    type: BOOLEAN
    description: Informational copy of the gate rule. The blocking check, not this column, decides promotion.
    checks:
      - name: not_null
custom_checks:
  - name: challenger beats EPSS on precision@50
    description: Promotion gate. Fails unless the challenger finds strictly more future-KEV CVEs in its top 50 than EPSS does. A tie fails.
    query: |
      SELECT count(*) FROM ml.eval_metrics
      WHERE asof_date = (SELECT asof_date FROM raw.world_clock)
        AND challenger_hits_at_50 > epss_hits_at_50
    value: 1
    blocking: true
  - name: validation set has at least 10 positives
    description: Promotion gate. Too few positives make precision@50 meaningless.
    query: |
      SELECT count(*) FROM ml.eval_metrics
      WHERE asof_date = (SELECT asof_date FROM raw.world_clock) AND n_positives >= 10
    value: 1
    blocking: true
  - name: challenger was scored for this as-of date
    description: Promotion gate. A stale score table must never be promoted.
    query: SELECT count(*) FROM ml.challenger_scores WHERE asof_date <> (SELECT asof_date FROM raw.world_clock)
    value: 0
    blocking: true
@bruin */

WITH v AS (
    SELECT f.cve_id, f.y, f.snapshot_date, f.epss_asof, f.epss_percentile_asof, s.model_score, s.model_version
    FROM feat.snapshot_features f
    JOIN ml.challenger_scores s
      ON s.cve_id = f.cve_id AND s.role = 'valid' AND s.snapshot_date = f.snapshot_date
    WHERE f.role = 'valid'
),
r AS (
    SELECT *,
        row_number() OVER (ORDER BY model_score DESC, cve_id) AS rk_model,
        row_number() OVER (ORDER BY epss_asof DESC NULLS LAST, epss_percentile_asof DESC NULLS LAST, cve_id) AS rk_epss
    FROM v
),
agg AS (
    SELECT
        max(snapshot_date)                                AS valid_snapshot_date,
        max(model_version)                                AS model_version,
        count(*)                                          AS n_candidates,
        sum(y)                                            AS n_positives,
        sum(CASE WHEN rk_model <= 50  THEN y ELSE 0 END)  AS challenger_hits_at_50,
        sum(CASE WHEN rk_epss  <= 50  THEN y ELSE 0 END)  AS epss_hits_at_50,
        sum(CASE WHEN rk_model <= 100 THEN y ELSE 0 END)  AS challenger_hits_at_100,
        sum(CASE WHEN rk_epss  <= 100 THEN y ELSE 0 END)  AS epss_hits_at_100
    FROM r
)
SELECT
    w.asof_date,
    a.valid_snapshot_date,
    a.model_version,
    w.pipeline_run_id,
    a.n_candidates,
    CAST(a.n_positives AS BIGINT)            AS n_positives,
    CAST(a.challenger_hits_at_50 AS BIGINT)  AS challenger_hits_at_50,
    CAST(a.epss_hits_at_50 AS BIGINT)        AS epss_hits_at_50,
    a.challenger_hits_at_50 / 50.0           AS challenger_p_at_50,
    a.epss_hits_at_50 / 50.0                 AS epss_p_at_50,
    CAST(a.challenger_hits_at_100 AS BIGINT) AS challenger_hits_at_100,
    CAST(a.epss_hits_at_100 AS BIGINT)       AS epss_hits_at_100,
    (a.challenger_hits_at_50 > a.epss_hits_at_50 AND a.n_positives >= 10) AS gate_passed
FROM raw.world_clock w
CROSS JOIN agg a
