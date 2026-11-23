/* @bruin
name: raw.epss
type: duckdb.sql
description: |
  FIRST EPSS scores visible to the world: one Sunday snapshot file per world week, read straight
  from data/raw/epss/*.csv.gz. Day 0 loads every file dated on or before the clock (53 files);
  each advance loads exactly the file dated at the new clock. Every file in range is EPSS model
  v2025.03.14 (v4). Features use the latest file dated on or before each snapshot.
owner: patch-tuesday-pipeline
domains:
  - vulnerability-intel
tags:
  - raw
  - epss
depends:
  - raw.world_clock
materialization:
  type: table
  strategy: time_interval
  incremental_key: score_date
  time_granularity: date
columns:
  - name: cve_id
    type: VARCHAR
    description: CVE id scored by EPSS.
    extends: CVE.ID
    checks:
      - name: not_null
  - name: epss
    type: DOUBLE
    description: EPSS probability.
    extends: EPSSScore.Score
    checks:
      - name: not_null
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: percentile
    type: DOUBLE
    description: EPSS percentile.
    extends: EPSSScore.Percentile
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: score_date
    type: DATE
    description: Date of the snapshot file; the incremental key.
    extends: EPSSScore.ScoreDate
    checks:
      - name: not_null
custom_checks:
  - name: no score from the future
    description: Structural leakage control. No visible EPSS file is dated after the world clock.
    query: SELECT count(*) FROM raw.epss WHERE score_date > (SELECT asof_date FROM raw.world_clock)
    value: 0
    blocking: true
  - name: the file for the world clock is loaded
    description: Every world week must have its own EPSS snapshot.
    query: SELECT CAST(count(*) > 100000 AS INTEGER) FROM raw.epss WHERE score_date = (SELECT asof_date FROM raw.world_clock)
    value: 1
    blocking: true
  - name: one score per CVE per file
    description: (cve_id, score_date) is unique.
    query: SELECT count(*) - count(DISTINCT (cve_id, score_date)) FROM raw.epss
    value: 0
    blocking: true
@bruin */

SELECT cve AS cve_id,
       CAST(epss AS DOUBLE) AS epss,
       CAST(percentile AS DOUBLE) AS percentile,
       CAST(regexp_extract(filename, '(\d{4}-\d{2}-\d{2})', 1) AS DATE) AS score_date
FROM read_csv(
  {% if full_refresh %}'data/raw/epss/epss_scores-*.csv.gz'
  {% else %}'data/raw/epss/epss_scores-{{ end_date }}.csv.gz'{% endif %},
  skip = 1, header = true, filename = true)
WHERE score_date <= '{{ end_date }}'
