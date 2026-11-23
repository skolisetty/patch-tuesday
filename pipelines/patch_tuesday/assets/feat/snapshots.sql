/* @bruin
name: feat.snapshots
type: duckdb.sql
description: |
  The snapshot calendar for this world clock T with horizon H (pipeline variable horizon_days):
  serve at T (no label), valid at T - H (label window (T-H, T]) and train_snapshots training
  snapshots at T - 2H - 28j (label windows end by T - H, so they never overlap validation).
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - feat
depends:
  - raw.world_clock
materialization:
  type: table
  strategy: create+replace
columns:
  - name: role
    type: VARCHAR
    description: serve, valid or train.
    extends: Snapshot.Role
    checks:
      - name: not_null
      - name: accepted_values
        value: ["serve", "valid", "train"]
  - name: idx
    type: INTEGER
    description: j for training snapshots (0 = newest), 0 otherwise.
    checks:
      - name: non_negative
  - name: snapshot_date
    type: DATE
    description: As-of date of the snapshot.
    extends: Snapshot.Date
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: label_end
    type: DATE
    description: Last day of the label window (snapshot_date + H); null for serve.
custom_checks:
  - name: one serve, one valid and the configured number of train snapshots
    description: Exactly 1 serve, 1 valid and train_snapshots train rows.
    query: |
      SELECT count(*) FROM (SELECT role, count(*) AS n FROM feat.snapshots GROUP BY role)
      WHERE (role = 'serve' AND n = 1) OR (role = 'valid' AND n = 1) OR (role = 'train' AND n = {{ var.train_snapshots }})
    value: 3
    blocking: true
  - name: every snapshot is a Sunday
    description: Each snapshot must match one EPSS Sunday file.
    query: SELECT count(*) FROM feat.snapshots WHERE dayofweek(snapshot_date) <> 0
    value: 0
    blocking: true
@bruin */

WITH w AS (SELECT asof_date, horizon_days AS h FROM raw.world_clock)
SELECT 'serve' AS role, 0 AS idx, asof_date AS snapshot_date, CAST(NULL AS DATE) AS label_end FROM w
UNION ALL
SELECT 'valid', 0, asof_date - h, asof_date FROM w
UNION ALL
SELECT 'train', CAST(j AS INTEGER), asof_date - 2 * h - 28 * CAST(j AS INTEGER), asof_date - h - 28 * CAST(j AS INTEGER)
FROM w, range(0, {{ var.train_snapshots }}) AS t(j)
