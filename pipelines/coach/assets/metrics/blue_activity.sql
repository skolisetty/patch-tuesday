/* @bruin
name: coaching.blue_activity
type: duckdb.sql
description: |
  One row for each of the five original Blue actors (the three AI defenders blue1 to blue3 and the
  two humans p1 and p2): what each did up to the checkpoint. Always five rows, also for an actor
  that never acted. coaching.agent_metrics and coaching.player_metrics are the two halves of it.
  near_breached_without_restoring counts breaches, not ticks: a breach counts for an actor when
  the actor stood on the breached machine or one step from it (the grid is 5 by 4, a step is up,
  down, left or right) at any tick while it was breached, was not the one who restored it, and is
  not restoring it at the checkpoint.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - metrics
depends:
  - coaching.ledger_rows
  - coaching.match_state
  - coaching.machines
  - coaching.patches
  - coaching.breach_spans
  - coaching.attacker_sightings
materialization:
  type: table
  strategy: create+replace
columns:
  - name: actor_id
    type: VARCHAR
    description: blue1, blue2, blue3, p1 or p2.
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: accepted_values
        value: ["blue1", "blue2", "blue3", "p1", "p2"]
  - name: kind
    type: VARCHAR
    description: agent or human.
    checks:
      - name: accepted_values
        value: ["agent", "human"]
  - name: role
    type: VARCHAR
    description: The actor's policy (triage, patcher, hunter, human); null if it has no ledger row.
  - name: ticks
    type: INTEGER
    description: Ledger rows of the actor (one per tick played).
    checks:
      - name: non_negative
  - name: ticks_waiting
    type: INTEGER
    description: Ticks whose action was WAIT.
    checks:
      - name: non_negative
  - name: wait_share
    type: DOUBLE
    description: ticks_waiting / ticks; 0 with no tick.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: cards_patched
    type: INTEGER
    description: Patches the actor finished.
    checks:
      - name: non_negative
  - name: patch_worth
    type: DOUBLE
    description: Total worth of those cards (machine value x danger).
    checks:
      - name: non_negative
  - name: patch_points
    type: DOUBLE
    description: Points those patches earned (a card that had already been attacked earns nothing).
    checks:
      - name: non_negative
  - name: restores
    type: INTEGER
    description: Restores the actor finished.
    checks:
      - name: non_negative
  - name: restores_started
    type: INTEGER
    description: Restores the actor started. For Triage and Patcher this is how often they broke off their own job to restore.
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
    description: Certain detections credited to the actor's monitoring (coaching.attacker_sightings.detection_proven).
    checks:
      - name: non_negative
  - name: near_breached_without_restoring
    type: INTEGER
    description: Breaches the actor stood within one step of, while breached, without restoring them (see the description).
    checks:
      - name: non_negative
custom_checks:
  - name: five blue actors
    description: Three AI defenders and two humans, whether or not they acted.
    query: SELECT count(*) FROM coaching.blue_activity
    value: 5
    blocking: true
  - name: restores finished do not exceed restores started
    description: A restore is started before it is finished.
    query: SELECT count(*) FROM coaching.blue_activity WHERE restores > restores_started
    value: 0
    blocking: true
  - name: patch points do not exceed patch worth
    description: A patch earns its worth or nothing.
    query: SELECT count(*) FROM coaching.blue_activity WHERE patch_points > patch_worth + 0.001
    value: 0
    blocking: true
@bruin */

WITH ms AS (
    SELECT * FROM coaching.match_state
),
actors AS (
    SELECT * FROM (VALUES ('blue1', 'agent'), ('blue2', 'agent'), ('blue3', 'agent'),
                          ('p1', 'human'), ('p2', 'human')) AS t(actor_id, kind)
),
acts AS (
    SELECT
        actor_id,
        arg_min(policy, tick)                                                    AS role,
        count(*)                                                                 AS ticks,
        count(*) FILTER (WHERE action = 'WAIT')                                  AS ticks_waiting,
        count(*) FILTER (WHERE action = 'RESTORE' AND outcome = 'restored')      AS restores,
        count(*) FILTER (WHERE action = 'RESTORE' AND outcome = 'restoring')     AS restores_started,
        count(*) FILTER (WHERE action = 'MONITOR' AND outcome = 'monitoring')    AS monitors,
        count(*) FILTER (WHERE action = 'ISOLATE' AND outcome = 'isolated')      AS isolates
    FROM coaching.ledger_rows
    WHERE team = 'blue'
    GROUP BY actor_id
),
pat AS (
    SELECT actor_id, count(*) AS cards_patched, sum(worth) AS patch_worth, sum(points) AS patch_points
    FROM coaching.patches
    GROUP BY actor_id
),
rev AS (
    SELECT credited_actor_id AS actor_id, count(*) AS attackers_revealed
    FROM coaching.attacker_sightings
    WHERE detection_proven AND credited_actor_id IS NOT NULL
    GROUP BY 1
),
near AS (
    SELECT a.actor_id, count(*) AS near_breached_without_restoring
    FROM actors a
    CROSS JOIN coaching.breach_spans s
    CROSS JOIN ms
    JOIN coaching.machines sm ON sm.machine_id = s.machine_id
    WHERE (s.restored_by IS NULL OR s.restored_by <> a.actor_id)
      AND NOT (s.still_breached AND EXISTS (
                  SELECT 1 FROM coaching.ledger_rows l
                   WHERE l.actor_id = a.actor_id AND l.tick = ms.state_tick - 1
                     AND l.action = 'RESTORE' AND l.outcome IN ('restoring', 'working')
                     AND l.node_id = s.machine_id))
      AND EXISTS (
            SELECT 1 FROM coaching.ledger_rows l
            JOIN coaching.machines lm ON lm.machine_id = l.node_id
            WHERE l.actor_id = a.actor_id
              AND l.tick > s.tick_breached
              AND l.tick <= coalesce(s.tick_restored, ms.state_tick)
              AND abs(lm.x - sm.x) + abs(lm.y - sm.y) <= 1)
    GROUP BY a.actor_id
)
SELECT
    a.actor_id                                                         AS actor_id,
    a.kind                                                             AS kind,
    acts.role                                                          AS role,
    CAST(coalesce(acts.ticks, 0) AS INTEGER)                           AS ticks,
    CAST(coalesce(acts.ticks_waiting, 0) AS INTEGER)                   AS ticks_waiting,
    CASE WHEN coalesce(acts.ticks, 0) > 0
         THEN round(acts.ticks_waiting / acts.ticks, 3) ELSE 0.0 END   AS wait_share,
    CAST(coalesce(pat.cards_patched, 0) AS INTEGER)                    AS cards_patched,
    round(coalesce(pat.patch_worth, 0.0), 2)                           AS patch_worth,
    round(coalesce(pat.patch_points, 0.0), 2)                          AS patch_points,
    CAST(coalesce(acts.restores, 0) AS INTEGER)                        AS restores,
    CAST(coalesce(acts.restores_started, 0) AS INTEGER)                AS restores_started,
    CAST(coalesce(acts.monitors, 0) AS INTEGER)                        AS monitors,
    CAST(coalesce(acts.isolates, 0) AS INTEGER)                        AS isolates,
    CAST(coalesce(rev.attackers_revealed, 0) AS INTEGER)               AS attackers_revealed,
    CAST(coalesce(near.near_breached_without_restoring, 0) AS INTEGER) AS near_breached_without_restoring
FROM actors AS a
LEFT JOIN acts ON acts.actor_id = a.actor_id
LEFT JOIN pat ON pat.actor_id = a.actor_id
LEFT JOIN rev ON rev.actor_id = a.actor_id
LEFT JOIN near ON near.actor_id = a.actor_id
