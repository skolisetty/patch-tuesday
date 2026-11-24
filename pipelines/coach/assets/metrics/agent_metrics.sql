/* @bruin
name: coaching.agent_metrics
type: duckdb.sql
description: |
  One row per AI defender (blue1 Triage, blue2 Patcher, blue3 Hunter): the figures each agent
  quotes about itself in the Blue team's BAND room. Cards patched and their worth, restores, how
  often it broke off its own job to restore (restores_started) and attackers its monitoring revealed.
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
    description: blue1, blue2 or blue3.
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: accepted_values
        value: ["blue1", "blue2", "blue3"]
  - name: role
    type: VARCHAR
    description: triage, patcher or hunter.
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
    description: Share of its ticks spent waiting.
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
    description: Total worth of the cards it patched (machine value x danger).
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
    description: Restores started; for Triage and Patcher, the times they broke off their own job to restore.
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
    description: Certain detections credited to its monitoring.
    checks:
      - name: non_negative
  - name: near_breached_without_restoring
    type: INTEGER
    description: Breaches it stood within one step of without restoring them.
    checks:
      - name: non_negative
custom_checks:
  - name: three agents
    description: blue1, blue2 and blue3, always.
    query: SELECT count(*) FROM coaching.agent_metrics
    value: 3
    blocking: true
@bruin */

SELECT
    actor_id, role, ticks, ticks_waiting, wait_share, cards_patched, patch_worth, patch_points, restores,
    restores_started, monitors, isolates, attackers_revealed, near_breached_without_restoring
FROM coaching.blue_activity
WHERE kind = 'agent'
