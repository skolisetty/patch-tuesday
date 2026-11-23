/* @bruin
name: serve.export_world
type: duckdb.sql
description: |
  Writes serving/world.json last: one object with the world clock, the score source, the gate
  inputs and world-size counters. It depends on every other export, so it is the game's reload
  trigger. It also depends on ml.eval_metrics, so a failed gate skips it in the main run; the
  EPSS fallback (bruin run --tag serve) then writes it with score_source "epss" and the gate
  numbers that failed. scripts/advance.py decides the outcome from this file. The gate numbers are
  joined on the run id as well as the clock: ml.eval_metrics keeps one row per world week, so a
  row left by an earlier run of the same week must never be reported as this run's gate.
owner: patch-tuesday-pipeline
domains:
  - world-simulation
tags:
  - serve
  - export
depends:
  - serve.export_cards
  - serve.export_priority
  - sim.reveal
  - serve.attack_cards
  - serve.patch_priority
  - ml.eval_metrics
  - ml.promotions
  - raw.world_clock
  - raw.cves
  - raw.kev
@bruin */

COPY (
    SELECT
        w.asof_date,
        w.window_start,
        w.pipeline_run_id,
        (SELECT max(score_source) FROM serve.patch_priority)  AS score_source,
        (SELECT max(model_version) FROM serve.patch_priority) AS model_version,
        m.challenger_p_at_50,
        m.epss_p_at_50,
        m.challenger_hits_at_50,
        m.epss_hits_at_50,
        m.n_positives,
        m.n_candidates,
        (SELECT count(*) FROM raw.cves)                                                       AS n_cves_visible,
        (SELECT count(*) FROM raw.kev)                                                        AS n_kev_visible,
        (SELECT count(*) FROM raw.cves c WHERE c.published_date BETWEEN w.window_start AND w.asof_date) AS n_new_cves_this_week,
        (SELECT count(*) FROM serve.attack_cards)                                             AS n_cards
    FROM raw.world_clock w
    LEFT JOIN ml.eval_metrics m ON m.asof_date = w.asof_date AND m.pipeline_run_id = w.pipeline_run_id
) TO 'serving/world.json' (FORMAT json, ARRAY true)
