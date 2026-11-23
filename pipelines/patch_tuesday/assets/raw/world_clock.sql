/* @bruin
name: raw.world_clock
type: duckdb.sql
description: |
  The world clock. One row holding the as-of date (the run window end, always a Sunday),
  the Monday that starts the world week, and the Bruin run id. Every raw.* asset filters
  the offline mirror in landing.* up to this date, so later layers never see the future.
owner: patch-tuesday-pipeline
domains:
  - world-simulation
tags:
  - raw
  - clock
materialization:
  type: table
  strategy: create+replace
columns:
  - name: asof_date
    type: DATE
    description: World clock. The run window end; every visible row is dated on or before it.
    extends: Snapshot.Date
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: window_start
    type: DATE
    description: Monday that opens the current world week (asof_date - 6). Drives is_new_this_week.
    checks:
      - name: not_null
  - name: run_start_date
    type: DATE
    description: The --start-date Bruin was given. 2020-01-01 on a day-0 full refresh, else the Monday of the week.
    checks:
      - name: not_null
  - name: full_refresh
    type: BOOLEAN
    description: True when this run rebuilt every table (day 0).
    checks:
      - name: not_null
  - name: pipeline_run_id
    type: VARCHAR
    description: Bruin run id (YYYY_MM_DD_HH_MM_SS, local time) of the run that set the clock.
    checks:
      - name: not_null
  - name: horizon_days
    type: INTEGER
    description: Label horizon used by this run (pipeline variable horizon_days).
    checks:
      - name: positive
custom_checks:
  - name: exactly one clock row
    description: The clock is a single row.
    query: SELECT count(*) FROM raw.world_clock
    value: 1
    blocking: true
  - name: world clock is a Sunday
    description: Weeks run Monday to Sunday, and every EPSS snapshot file is dated on a Sunday.
    query: SELECT count(*) FROM raw.world_clock WHERE dayofweek(asof_date) <> 0
    value: 0
    blocking: true
  - name: an advance covers exactly one world week
    description: An incremental run must start on the Monday of the week it ends, or rows would be skipped or doubled.
    query: SELECT count(*) FROM raw.world_clock WHERE NOT full_refresh AND run_start_date <> window_start
    value: 0
    blocking: true
  - name: world clock is inside the replay range
    description: The offline mirror supports as-of dates 2026-03-22 to 2026-06-14 (EPSS v4 files 2025-03-23 to 2026-06-14).
    query: SELECT count(*) FROM raw.world_clock WHERE asof_date < DATE '2026-03-22' OR asof_date > DATE '2026-06-14'
    value: 0
    blocking: true
@bruin */

SELECT
    CAST('{{ end_date }}' AS DATE)                    AS asof_date,
    CAST('{{ end_date }}' AS DATE) - 6                AS window_start,
    CAST('{{ start_date }}' AS DATE)                  AS run_start_date,
    CAST('{{ full_refresh }}' = 'True' AS BOOLEAN)    AS full_refresh,
    '{{ run_id }}'                                    AS pipeline_run_id,
    CAST({{ var.horizon_days }} AS INTEGER)           AS horizon_days
