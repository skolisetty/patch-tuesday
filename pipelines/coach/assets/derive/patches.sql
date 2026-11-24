/* @bruin
name: coaching.patches
type: duckdb.sql
description: |
  One row per finished patch (a PATCH that reached the outcome "patched"): who patched which card,
  what it was worth (machine value x danger) and the points it earned. A patch earns its worth only
  if the card had not been attacked before the patch finished; a card that was already attacked is
  closed for no points (the game tells the Blue team so when the patch lands).
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - derive
depends:
  - coaching.ledger_rows
  - coaching.machines
materialization:
  type: table
  strategy: create+replace
columns:
  - name: actor_id
    type: VARCHAR
    description: Blue actor who finished the patch.
    checks:
      - name: not_null
  - name: tick
    type: INTEGER
    description: Tick the patch finished.
    checks:
      - name: not_null
      - name: non_negative
  - name: machine_id
    type: INTEGER
    description: Machine the card sits on.
    checks:
      - name: not_null
  - name: cve_id
    type: VARCHAR
    description: CVE of the patched card.
    extends: CVE.ID
    checks:
      - name: not_null
  - name: danger
    type: DOUBLE
    description: Danger of the card when the patch finished.
    checks:
      - name: not_null
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: worth
    type: DOUBLE
    description: Machine value x danger.
    checks:
      - name: not_null
      - name: non_negative
  - name: attacked_before
    type: BOOLEAN
    description: True when an exploit had already been tried on the card before the patch finished.
    checks:
      - name: not_null
  - name: points
    type: DOUBLE
    description: Points the patch earned Blue (worth, or 0 when attacked_before).
    checks:
      - name: not_null
      - name: non_negative
custom_checks:
  - name: a card is patched once
    description: A patched card cannot be patched again.
    query: SELECT count(*) FROM (SELECT machine_id, cve_id FROM coaching.patches GROUP BY 1, 2 HAVING count(*) > 1)
    value: 0
    blocking: true
@bruin */

SELECT
    p.actor_id                                   AS actor_id,
    p.tick                                       AS tick,
    p.node_id                                    AS machine_id,
    p.cve_id                                     AS cve_id,
    p.score_used                                 AS danger,
    m.machine_value * p.score_used               AS worth,
    EXISTS (SELECT 1 FROM coaching.ledger_rows e
             WHERE e.team = 'red' AND e.action = 'EXPLOIT' AND e.outcome IN ('failed', 'breach')
               AND e.node_id = p.node_id AND e.cve_id = p.cve_id AND e.tick < p.tick) AS attacked_before,
    CASE WHEN EXISTS (SELECT 1 FROM coaching.ledger_rows e
                       WHERE e.team = 'red' AND e.action = 'EXPLOIT' AND e.outcome IN ('failed', 'breach')
                         AND e.node_id = p.node_id AND e.cve_id = p.cve_id AND e.tick < p.tick)
         THEN 0.0 ELSE m.machine_value * p.score_used END AS points
FROM coaching.ledger_rows AS p
JOIN coaching.machines AS m ON m.machine_id = p.node_id
WHERE p.team = 'blue' AND p.action = 'PATCH' AND p.outcome = 'patched'
