/* @bruin
name: coaching.player_metrics
type: duckdb.sql
description: |
  One row per human player (p1, p2), for the Coach's kind and factual line about each of them:
  share of ticks spent waiting, patches and restores done, the worth of the patches, and how many
  breached machines the player stood within one step of without restoring them.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - metrics
depends:
  - coaching.blue_activity
materialization:
  type: table
  strategy: create+replace
columns:
  - name: actor_id
    type: VARCHAR
    description: p1 or p2.
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: accepted_values
        value: ["p1", "p2"]
  - name: ticks
    type: INTEGER
    description: Ticks played.
    checks:
      - name: non_negative
  - name: ticks_waiting
    type: INTEGER
    description: Ticks whose action was WAIT.
    checks:
      - name: non_negative
  - name: wait_share
    type: DOUBLE
    description: ticks_waiting / ticks; 0 with no tick. 1.0 means the player has not acted yet.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: cards_patched
    type: INTEGER
    description: Patches finished.
    checks:
      - name: non_negative
  - name: patch_worth
    type: DOUBLE
    description: Total worth of the cards the player patched (machine value x danger).
    checks:
      - name: non_negative
  - name: patch_points
    type: DOUBLE
    description: Points those patches earned.
    checks:
      - name: non_negative
  - name: restores
    type: INTEGER
    description: Restores finished.
    checks:
      - name: non_negative
  - name: restores_started
    type: INTEGER
    description: Restores started.
    checks:
      - name: non_negative
  - name: monitors
    type: INTEGER
    description: MONITOR actions.
    checks:
      - name: non_negative
  - name: isolates
    type: INTEGER
    description: ISOLATE actions that took effect.
    checks:
      - name: non_negative
  - name: attackers_revealed
    type: INTEGER
    description: Certain detections credited to the player's monitoring.
    checks:
      - name: non_negative
  - name: near_breached_without_restoring
    type: INTEGER
    description: Breaches the player stood within one step of, while breached, without restoring them.
    checks:
      - name: non_negative
custom_checks:
  - name: two players
    description: p1 and p2, always.
    query: SELECT count(*) FROM coaching.player_metrics
    value: 2
    blocking: true
@bruin */

SELECT
    actor_id, ticks, ticks_waiting, wait_share, cards_patched, patch_worth, patch_points, restores,
    restores_started, monitors, isolates, attackers_revealed, near_breached_without_restoring
FROM coaching.blue_activity
WHERE kind = 'human'
