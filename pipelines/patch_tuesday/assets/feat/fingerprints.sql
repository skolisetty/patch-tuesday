/* @bruin
name: feat.fingerprints
type: duckdb.sql
description: |
  Time-travel leakage audit. Each run stores an md5 fingerprint of every training snapshot's
  feature rows (delete+insert on asof_date keeps one row set per world clock). Because training
  snapshots are 28 days apart and the clock moves 7 days a week, the snapshot that was train j at
  clock T is train j+1 at clock T+28. If any feature or label used information dated after its
  snapshot, the same snapshot would fingerprint differently at the later clock. The blocking check
  compares them, and the model (ml.challenger_scores) depends on this asset, so a leak stops the
  model from being trained or promoted.
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - feat
  - leakage-controlled
  - audit
depends:
  - feat.snapshot_features
  - raw.world_clock
materialization:
  type: table
  strategy: delete+insert
  incremental_key: asof_date
columns:
  - name: asof_date
    type: DATE
    description: World clock of the run that built the snapshot.
    extends: Snapshot.Date
    checks:
      - name: not_null
  - name: snapshot_date
    type: DATE
    description: Training snapshot date.
    checks:
      - name: not_null
  - name: n_rows
    type: BIGINT
    description: Training rows for the snapshot.
    checks:
      - name: positive
  - name: n_positives
    type: BIGINT
    description: Positive labels in the snapshot.
    checks:
      - name: non_negative
  - name: fingerprint
    type: VARCHAR
    description: md5 over every stored feature and label of the snapshot's rows, ordered by cve_id.
    checks:
      - name: not_null
custom_checks:
  - name: a training snapshot is identical at every world clock
    description: |
      Leakage control. Counts snapshot dates that were fingerprinted at two or more clocks with
      different results. Any non-zero value means a feature or label depends on data after its snapshot.
    query: |
      SELECT count(*) FROM (
        SELECT snapshot_date FROM feat.fingerprints
        GROUP BY snapshot_date HAVING count(DISTINCT fingerprint) > 1)
    value: 0
    blocking: true
@bruin */

SELECT
    w.asof_date,
    f.snapshot_date,
    count(*)    AS n_rows,
    sum(f.y)    AS n_positives,
    md5(string_agg(CAST(struct_pack(
        f.cve_id, f.y, f.label_end, f.published_date, f.epss_date, f.age_days,
        f.cvss_base_score, f.exploitability_score, f.impact_score, f.has_cvss,
        f.av_network, f.ac_low, f.pr_none, f.ui_none, f.scope_changed, f.c_high, f.i_high, f.a_high,
        f.epss_asof, f.epss_percentile_asof, f.epss_delta_28d,
        f.vendor_kev_count_asof, f.vendor_cve_count_asof, f.vendor_kev_ratio_asof,
        f.product_kev_count_asof, f.cwe_kev_count_asof,
        f.kw_rce, f.kw_unauth, f.kw_cmd_injection, f.kw_priv_esc, f.kw_auth_bypass, f.kw_deserialization, f.kw_overflow
    ) AS VARCHAR), '' ORDER BY f.cve_id)) AS fingerprint
FROM feat.snapshot_features f
CROSS JOIN raw.world_clock w
WHERE f.role = 'train'
GROUP BY w.asof_date, f.snapshot_date
