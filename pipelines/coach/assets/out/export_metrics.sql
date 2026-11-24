/* @bruin
name: coaching.export_metrics
type: duckdb.sql
description: |
  Writes data/coach/current/metrics.json: one JSON object, the shape documented in
  pipelines/coach/README.md ({"match_id","tick","generated_by","pipeline_run_id","team",
  "agents":{"blue1","blue2","blue3"},"players":{"p1","p2"}}). Unmaterialised single COPY, and the
  last asset: it depends on coaching.summary, which depends on every metrics table, so the file is
  written only when every blocking check of the run passed (the score reconciliation included).
  scripts/coach.py deletes the previous file before the run and copies the new one to the run
  folder. Bruin must be started from the repo root, and data/coach/current/ must exist.
  FOG OF WAR: the file holds aggregates only. No attacker position, target or card is in it.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - export
depends:
  - coaching.summary
  - coaching.team_metrics
  - coaching.agent_metrics
  - coaching.player_metrics
@bruin */

COPY (
    SELECT
        s.match_id                                                              AS match_id,
        s.tick                                                                  AS tick,
        s.generated_by                                                          AS generated_by,
        s.pipeline_run_id                                                       AS pipeline_run_id,
        (SELECT t FROM (SELECT * EXCLUDE (match_id) FROM coaching.team_metrics) AS t) AS team,
        struct_pack(
            blue1 := (SELECT a FROM (SELECT * EXCLUDE (actor_id) FROM coaching.agent_metrics WHERE actor_id = 'blue1') AS a),
            blue2 := (SELECT a FROM (SELECT * EXCLUDE (actor_id) FROM coaching.agent_metrics WHERE actor_id = 'blue2') AS a),
            blue3 := (SELECT a FROM (SELECT * EXCLUDE (actor_id) FROM coaching.agent_metrics WHERE actor_id = 'blue3') AS a)
        )                                                                       AS agents,
        struct_pack(
            p1 := (SELECT p FROM (SELECT * EXCLUDE (actor_id) FROM coaching.player_metrics WHERE actor_id = 'p1') AS p),
            p2 := (SELECT p FROM (SELECT * EXCLUDE (actor_id) FROM coaching.player_metrics WHERE actor_id = 'p2') AS p)
        )                                                                       AS players
    FROM coaching.summary AS s
) TO 'data/coach/current/metrics.json' (FORMAT json)
