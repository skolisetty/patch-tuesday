/* @bruin
name: coaching.score_reconciliation
type: duckdb.sql
description: |
  The pipeline's gate. One row that rebuilds all five parts of the score from the ledger and the
  machine values alone and puts each next to the scoreboard's own breakdown (state.json):
    Red:  first breaches (first_breach_points per machine breached for the first time) and holding
          (machine value x ticks paid, from coaching.breach_spans)
    Blue: patches (coaching.patches.points), restores (restore_points each) and the cost of
          isolating (isolate_cost per machine per isolated tick)
  The blocking checks require every part to agree within reconcile_tolerance (the scoreboard is
  rounded to 2 decimals). If they do not, the breach spans or the ledger snapshot are wrong, the
  metrics are not exported, and the game falls back to its own figures.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - metrics
  - reconciliation
depends:
  - coaching.match_state
  - coaching.breach_spans
  - coaching.patches
  - coaching.isolation_ticks
  - coaching.ledger_rows
materialization:
  type: table
  strategy: create+replace
columns:
  - name: match_id
    type: VARCHAR
    description: Match.
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: derived_first_breach
    type: DOUBLE
    description: Red's points for first breaches, from the ledger.
    checks:
      - name: non_negative
  - name: derived_held
    type: DOUBLE
    description: Red's points for holding breached machines, from the ledger and the machine values.
    checks:
      - name: non_negative
  - name: derived_patch
    type: DOUBLE
    description: Blue's points for patches, from the ledger and the machine values.
    checks:
      - name: non_negative
  - name: derived_restore
    type: DOUBLE
    description: Blue's points for restores, from the ledger.
    checks:
      - name: non_negative
  - name: derived_isolate_cost
    type: DOUBLE
    description: Blue's isolation cost (zero or negative), from the ledger.
    checks:
      - name: max
        value: 0.0
  - name: derived_breaches
    type: INTEGER
    description: Breaches in the ledger.
    checks:
      - name: non_negative
  - name: diff_first_breach
    type: DOUBLE
    description: derived - scoreboard.
  - name: diff_held
    type: DOUBLE
    description: derived - scoreboard.
  - name: diff_red_total
    type: DOUBLE
    description: (derived first breaches + derived holding) - Red's score.
  - name: diff_patch
    type: DOUBLE
    description: derived - scoreboard.
  - name: diff_restore
    type: DOUBLE
    description: derived - scoreboard.
  - name: diff_isolate_cost
    type: DOUBLE
    description: derived - scoreboard.
  - name: diff_blue_total
    type: DOUBLE
    description: (derived patches + restores + isolation cost) - Blue's score.
  - name: max_abs_diff
    type: DOUBLE
    description: Largest absolute difference of the seven above.
    checks:
      - name: non_negative
custom_checks:
  - name: exactly one reconciliation row
    description: One match, one row.
    query: SELECT count(*) FROM coaching.score_reconciliation
    value: 1
    blocking: true
  - name: red income from the ledger reconciles with the score breakdown
    description: |
      Red's points for holding and for first breaches, derived from the ledger and the machine
      values, must equal the scoreboard's breakdown and Red's score within reconcile_tolerance.
    query: |
      SELECT count(*) FROM coaching.score_reconciliation
      WHERE abs(diff_held) > {{ var.reconcile_tolerance }}
         OR abs(diff_first_breach) > {{ var.reconcile_tolerance }}
         OR abs(diff_red_total) > {{ var.reconcile_tolerance }}
    value: 0
    blocking: true
  - name: blue points from the ledger reconcile with the score breakdown
    description: |
      Blue's points for patches and restores and its isolation cost, derived from the ledger, must
      equal the scoreboard's breakdown and Blue's score within reconcile_tolerance.
    query: |
      SELECT count(*) FROM coaching.score_reconciliation
      WHERE abs(diff_patch) > {{ var.reconcile_tolerance }}
         OR abs(diff_restore) > {{ var.reconcile_tolerance }}
         OR abs(diff_isolate_cost) > {{ var.reconcile_tolerance }}
         OR abs(diff_blue_total) > {{ var.reconcile_tolerance }}
    value: 0
    blocking: true
  - name: breaches in the ledger equal the scoreboard's breach count
    description: Every breach the scoreboard counted must be a breach span, and the other way round.
    query: |
      SELECT count(*) FROM coaching.score_reconciliation r JOIN coaching.match_state s ON s.match_id = r.match_id
      WHERE r.derived_breaches <> s.state_breaches_total
    value: 0
    blocking: true
@bruin */

WITH d AS (
    SELECT
        ms.match_id,
        ms.first_breach_points * (SELECT count(*) FROM coaching.breach_spans WHERE first_breach)        AS derived_first_breach,
        CAST(coalesce((SELECT sum(machine_value * ticks_paid) FROM coaching.breach_spans), 0) AS DOUBLE) AS derived_held,
        coalesce((SELECT sum(points) FROM coaching.patches), 0.0)                                        AS derived_patch,
        ms.restore_points * (SELECT count(*) FROM coaching.ledger_rows
                              WHERE team = 'blue' AND action = 'RESTORE' AND outcome = 'restored')       AS derived_restore,
        -ms.isolate_cost * (SELECT count(*) FROM coaching.isolation_ticks)                               AS derived_isolate_cost,
        CAST((SELECT count(*) FROM coaching.breach_spans) AS INTEGER)                                    AS derived_breaches,
        ms.red_points, ms.blue_points, ms.state_first_breach, ms.state_held, ms.state_patch,
        ms.state_restore, ms.state_isolate_cost
    FROM coaching.match_state AS ms
)
SELECT
    d.match_id                                                         AS match_id,
    d.derived_first_breach                                             AS derived_first_breach,
    d.derived_held                                                     AS derived_held,
    d.derived_patch                                                    AS derived_patch,
    d.derived_restore                                                  AS derived_restore,
    d.derived_isolate_cost                                             AS derived_isolate_cost,
    d.derived_breaches                                                 AS derived_breaches,
    d.derived_first_breach - d.state_first_breach                      AS diff_first_breach,
    d.derived_held - d.state_held                                      AS diff_held,
    d.derived_first_breach + d.derived_held - d.red_points             AS diff_red_total,
    d.derived_patch - d.state_patch                                    AS diff_patch,
    d.derived_restore - d.state_restore                                AS diff_restore,
    d.derived_isolate_cost - d.state_isolate_cost                      AS diff_isolate_cost,
    d.derived_patch + d.derived_restore + d.derived_isolate_cost - d.blue_points AS diff_blue_total,
    greatest(abs(d.derived_first_breach - d.state_first_breach),
             abs(d.derived_held - d.state_held),
             abs(d.derived_first_breach + d.derived_held - d.red_points),
             abs(d.derived_patch - d.state_patch),
             abs(d.derived_restore - d.state_restore),
             abs(d.derived_isolate_cost - d.state_isolate_cost),
             abs(d.derived_patch + d.derived_restore + d.derived_isolate_cost - d.blue_points)) AS max_abs_diff
FROM d
