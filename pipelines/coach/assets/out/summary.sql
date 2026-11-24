/* @bruin
name: coaching.summary
type: duckdb.sql
description: |
  The one-row summary of this analysis: which match and tick, which Bruin run, how much input was
  read and the headline figure of each of the three weaknesses. It is the header of the exported
  metrics file and the row to look at first with `bruin query`. It depends on every metrics table,
  so it exists only when all their blocking checks passed.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - summary
depends:
  - coaching.match_state
  - coaching.team_metrics
  - coaching.agent_metrics
  - coaching.player_metrics
  - coaching.ledger_rows
materialization:
  type: table
  strategy: create+replace
columns:
  - name: match_id
    type: VARCHAR
    description: Match analysed.
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: tick
    type: INTEGER
    description: Checkpoint tick (ticks played).
    checks:
      - name: not_null
      - name: positive
  - name: generated_by
    type: VARCHAR
    description: Always "bruin".
    checks:
      - name: accepted_values
        value: ["bruin"]
  - name: pipeline_run_id
    type: VARCHAR
    description: Bruin run id of this analysis.
    checks:
      - name: not_null
  - name: ledger_rows
    type: INTEGER
    description: Ledger rows read.
    checks:
      - name: positive
  - name: red_points
    type: DOUBLE
    description: Red's score.
    checks:
      - name: non_negative
  - name: blue_points
    type: DOUBLE
    description: Blue's score.
    checks:
      - name: not_null
  - name: red_share_from_holding
    type: DOUBLE
    description: Slow response headline. Share of Red's points earned by holding breached machines.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: open_hot_cards
    type: INTEGER
    description: Exposure headline. Dangerous open cards on valuable machines.
    checks:
      - name: non_negative
  - name: attackers_unseen_share
    type: DOUBLE
    description: Blind spots headline. Share of attacker time out of sight.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: agents
    type: INTEGER
    description: Rows in coaching.agent_metrics.
    checks:
      - name: positive
  - name: players
    type: INTEGER
    description: Rows in coaching.player_metrics.
    checks:
      - name: positive
custom_checks:
  - name: exactly one summary row
    description: One analysis, one row.
    query: SELECT count(*) FROM coaching.summary
    value: 1
    blocking: true
  - name: the summary is this run's
    description: The summary must carry the run id of the checkpoint row written in this run.
    query: SELECT count(*) FROM coaching.summary s JOIN coaching.match_state m ON m.match_id = s.match_id AND m.pipeline_run_id = s.pipeline_run_id
    value: 1
    blocking: true
@bruin */

SELECT
    ms.match_id                                                      AS match_id,
    ms.state_tick                                                    AS tick,
    'bruin'                                                          AS generated_by,
    ms.pipeline_run_id                                               AS pipeline_run_id,
    CAST((SELECT count(*) FROM coaching.ledger_rows) AS INTEGER)     AS ledger_rows,
    t.red_points                                                     AS red_points,
    t.blue_points                                                    AS blue_points,
    t.red_share_from_holding                                         AS red_share_from_holding,
    t.open_hot_cards                                                 AS open_hot_cards,
    t.attackers_unseen_share                                         AS attackers_unseen_share,
    CAST((SELECT count(*) FROM coaching.agent_metrics) AS INTEGER)   AS agents,
    CAST((SELECT count(*) FROM coaching.player_metrics) AS INTEGER)  AS players
FROM coaching.match_state AS ms
JOIN coaching.team_metrics AS t ON t.match_id = ms.match_id
