/* @bruin
name: coaching.isolation_ticks
type: duckdb.sql
description: |
  One row per machine per tick it spent isolated, up to the checkpoint. An ISOLATE action isolates
  its machine for isolate_ticks ticks, the tick of the action included. While isolated a machine
  costs Blue isolate_cost per tick and earns Red nothing, even if it is breached underneath, so
  these ticks are taken out of Red's income from holding (coaching.breach_spans.ticks_paid) and
  give Blue's isolation cost in the reconciliation.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - derive
depends:
  - coaching.ledger_rows
  - coaching.match_state
materialization:
  type: table
  strategy: create+replace
columns:
  - name: machine_id
    type: INTEGER
    description: Isolated machine.
    checks:
      - name: not_null
  - name: tick
    type: INTEGER
    description: A tick at whose end the machine was isolated.
    checks:
      - name: not_null
      - name: non_negative
  - name: isolated_by
    type: VARCHAR
    description: Blue actor who isolated it.
    checks:
      - name: not_null
custom_checks:
  - name: a machine is isolated once per tick
    description: Isolating an isolated machine is refused by the rules, so windows never overlap.
    query: SELECT count(*) FROM (SELECT machine_id, tick FROM coaching.isolation_ticks GROUP BY 1, 2 HAVING count(*) > 1)
    value: 0
    blocking: true
@bruin */

SELECT
    l.node_id                 AS machine_id,
    CAST(t.tick AS INTEGER)   AS tick,
    l.actor_id                AS isolated_by
FROM coaching.ledger_rows AS l
CROSS JOIN coaching.match_state AS ms
CROSS JOIN LATERAL (SELECT unnest(range(l.tick, l.tick + ms.isolate_ticks)) AS tick) AS t
WHERE l.team = 'blue'
  AND l.action = 'ISOLATE'
  AND l.outcome = 'isolated'
  AND t.tick < ms.state_tick
