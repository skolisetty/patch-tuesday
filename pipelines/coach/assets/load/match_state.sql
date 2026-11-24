/* @bruin
name: coaching.match_state
type: duckdb.sql
description: |
  One row: the checkpoint the Coach analyses. Read from data/coach/current/state.json, the
  blue-visible board at the checkpoint (scripts/coach.py puts the file there before the run).
  Holds the match id, the tick, both scores with their breakdown as the scoreboard shows them,
  the game constants (state.json's "tuning" object, else the pipeline variables) and this run's id.
  Tick N means N ticks have been played: the ledger rows of the match carry ticks 0 to N - 1.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - load
materialization:
  type: table
  strategy: create+replace
columns:
  - name: match_id
    type: VARCHAR
    description: Match the checkpoint belongs to.
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: state_tick
    type: INTEGER
    description: Ticks played when the board was captured.
    checks:
      - name: not_null
      - name: positive
  - name: red_points
    type: DOUBLE
    description: Red's score on the scoreboard.
    checks:
      - name: not_null
      - name: non_negative
  - name: blue_points
    type: DOUBLE
    description: Blue's score on the scoreboard (isolating costs points, so it may be negative).
    checks:
      - name: not_null
  - name: state_first_breach
    type: DOUBLE
    description: Scoreboard breakdown, Red, points for first breaches.
    checks:
      - name: not_null
      - name: non_negative
  - name: state_held
    type: DOUBLE
    description: Scoreboard breakdown, Red, points for holding breached machines.
    checks:
      - name: not_null
      - name: non_negative
  - name: state_patch
    type: DOUBLE
    description: Scoreboard breakdown, Blue, points for patches.
    checks:
      - name: not_null
      - name: non_negative
  - name: state_restore
    type: DOUBLE
    description: Scoreboard breakdown, Blue, points for restores.
    checks:
      - name: not_null
      - name: non_negative
  - name: state_isolate_cost
    type: DOUBLE
    description: Scoreboard breakdown, Blue, cost of isolating (zero or negative).
    checks:
      - name: not_null
      - name: max
        value: 0.0
  - name: state_breaches_total
    type: INTEGER
    description: Breaches so far, as the scoreboard counts them.
    checks:
      - name: not_null
      - name: non_negative
  - name: state_breaches_in_top50
    type: INTEGER
    description: Breaches through a card that was priority 50 or better when it was breached (counted by the game's rules).
    checks:
      - name: not_null
      - name: non_negative
  - name: first_breach_points
    type: DOUBLE
    description: Game constant in force.
    checks:
      - name: positive
  - name: restore_points
    type: DOUBLE
    description: Game constant in force.
    checks:
      - name: positive
  - name: isolate_cost
    type: DOUBLE
    description: Game constant in force.
    checks:
      - name: non_negative
  - name: monitor_ticks
    type: INTEGER
    description: Game constant in force.
    checks:
      - name: positive
  - name: restore_monitor_ticks
    type: INTEGER
    description: Game constant in force.
    checks:
      - name: positive
  - name: isolate_ticks
    type: INTEGER
    description: Game constant in force.
    checks:
      - name: positive
  - name: breach_reveal_ticks
    type: INTEGER
    description: Game constant in force.
    checks:
      - name: positive
  - name: detect_ticks
    type: INTEGER
    description: Game constant in force.
    checks:
      - name: positive
  - name: red_entry_delay
    type: INTEGER
    description: Game constant in force.
    checks:
      - name: non_negative
  - name: pipeline_run_id
    type: VARCHAR
    description: Bruin run id of this analysis (YYYY_MM_DD_HH_MM_SS, local time).
    checks:
      - name: not_null
custom_checks:
  - name: exactly one checkpoint row
    description: state.json describes one board.
    query: SELECT count(*) FROM coaching.match_state
    value: 1
    blocking: true
  - name: breaches in the top 50 do not exceed breaches
    description: The scoreboard's two breach counters must be consistent.
    query: SELECT count(*) FROM coaching.match_state WHERE state_breaches_in_top50 > state_breaches_total
    value: 0
    blocking: true
@bruin */

SELECT
    s.match_id                                                              AS match_id,
    CAST(s.tick AS INTEGER)                                                 AS state_tick,
    CAST(s.score.red AS DOUBLE)                                             AS red_points,
    CAST(s.score.blue AS DOUBLE)                                            AS blue_points,
    CAST(s.score.breakdown.red.first_breach AS DOUBLE)                      AS state_first_breach,
    CAST(s.score.breakdown.red.held AS DOUBLE)                              AS state_held,
    CAST(s.score.breakdown.blue.patch AS DOUBLE)                            AS state_patch,
    CAST(s.score.breakdown.blue.restore AS DOUBLE)                          AS state_restore,
    CAST(s.score.breakdown.blue.isolate_cost AS DOUBLE)                     AS state_isolate_cost,
    CAST(s.score.breaches_total AS INTEGER)                                 AS state_breaches_total,
    CAST(s.score.breaches_in_top50 AS INTEGER)                              AS state_breaches_in_top50,
    CAST(coalesce(s.tuning.first_breach_points, {{ var.first_breach_points }}) AS DOUBLE)      AS first_breach_points,
    CAST(coalesce(s.tuning.restore_points, {{ var.restore_points }}) AS DOUBLE)                AS restore_points,
    CAST(coalesce(s.tuning.isolate_cost, {{ var.isolate_cost }}) AS DOUBLE)                    AS isolate_cost,
    CAST(coalesce(s.tuning.monitor_ticks, {{ var.monitor_ticks }}) AS INTEGER)                 AS monitor_ticks,
    CAST(coalesce(s.tuning.restore_monitor_ticks, {{ var.restore_monitor_ticks }}) AS INTEGER) AS restore_monitor_ticks,
    CAST(coalesce(s.tuning.isolate_ticks, {{ var.isolate_ticks }}) AS INTEGER)                 AS isolate_ticks,
    CAST(coalesce(s.tuning.breach_reveal_ticks, {{ var.breach_reveal_ticks }}) AS INTEGER)     AS breach_reveal_ticks,
    CAST(coalesce(s.tuning.detect_ticks, {{ var.detect_ticks }}) AS INTEGER)                   AS detect_ticks,
    CAST(coalesce(s.tuning.red_entry_delay, {{ var.red_entry_delay }}) AS INTEGER)             AS red_entry_delay,
    '{{ run_id }}'                                                          AS pipeline_run_id
FROM read_json(
    'data/coach/current/state.json',
    columns = {
        match_id: 'VARCHAR',
        tick: 'INTEGER',
        score: 'STRUCT(red DOUBLE, blue DOUBLE, breaches_total INTEGER, breaches_in_top50 INTEGER, breakdown STRUCT(blue STRUCT(patch DOUBLE, restore DOUBLE, isolate_cost DOUBLE), red STRUCT(first_breach DOUBLE, held DOUBLE)))',
        tuning: 'STRUCT(first_breach_points DOUBLE, restore_points DOUBLE, isolate_cost DOUBLE, monitor_ticks INTEGER, restore_monitor_ticks INTEGER, isolate_ticks INTEGER, breach_reveal_ticks INTEGER, detect_ticks INTEGER, red_entry_delay INTEGER)'
    }
) AS s
