/* @bruin
name: ml.promote
type: duckdb.sql
description: |
  Registers the challenger for this world week. Unmaterialised single INSERT. It is downstream of
  ml.eval_metrics, so Bruin runs it only when every blocking gate check passed; a failed gate means
  this never runs and the week serves EPSS.
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - ml
  - gate
depends:
  - ml.eval_metrics
  - ml.promotions
  - ml.retract
  - raw.world_clock
@bruin */

INSERT INTO ml.promotions
SELECT m.asof_date, m.model_version, m.challenger_hits_at_50, m.epss_hits_at_50,
       m.challenger_p_at_50, m.epss_p_at_50, '{{ run_id }}', CAST(current_timestamp AS TIMESTAMP)
FROM ml.eval_metrics m
WHERE m.asof_date = (SELECT asof_date FROM raw.world_clock)
