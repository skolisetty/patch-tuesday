/* @bruin
name: ml.retract
type: duckdb.sql
description: |
  Clears any promotion already registered for the current world clock before the gate is evaluated,
  so re-running a week (for example the degraded-model test) can never serve a stale promotion.
  Unmaterialised: Bruin runs the DELETE as written. ml.promote then re-inserts only if the gate passes.
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - ml
  - gate
depends:
  - ml.promotions
  - raw.world_clock
@bruin */

DELETE FROM ml.promotions WHERE asof_date = (SELECT asof_date FROM raw.world_clock)
