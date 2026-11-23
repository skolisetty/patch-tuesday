/* @bruin
name: ml.promotions
type: duckdb.sql
description: |
  Promotion registry. strategy ddl: created once with CREATE TABLE IF NOT EXISTS and never dropped,
  not even by --full-refresh (reset.sh deletes the database file to start over). One row per world
  week whose challenger passed the gate. Serving uses the model only when this table has a row for
  the current clock; otherwise it serves EPSS.
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - ml
  - gate
materialization:
  type: table
  strategy: ddl
columns:
  - name: asof_date
    type: DATE
    description: World week the challenger was promoted for.
    extends: Snapshot.Date
    primary_key: true
  - name: model_version
    type: VARCHAR
    description: Promoted challenger version.
  - name: challenger_hits_at_50
    type: BIGINT
    description: Gate input.
  - name: epss_hits_at_50
    type: BIGINT
    description: Gate input.
  - name: challenger_p_at_50
    type: DOUBLE
    description: Gate input.
  - name: epss_p_at_50
    type: DOUBLE
    description: Gate input.
  - name: pipeline_run_id
    type: VARCHAR
    description: Run that promoted it.
  - name: promoted_at
    type: TIMESTAMP
    description: Wall-clock time of the promotion.
custom_checks:
  - name: at most one promotion per world week
    description: ml.retract plus ml.promote keep the registry idempotent per asof_date.
    query: SELECT count(*) - count(DISTINCT asof_date) FROM ml.promotions
    value: 0
    blocking: true
@bruin */
