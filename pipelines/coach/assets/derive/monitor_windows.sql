/* @bruin
name: coaching.monitor_windows
type: duckdb.sql
description: |
  When each machine was monitored, and thanks to whom. One row per source: a MONITOR action keeps
  its machine monitored for monitor_ticks, and a finished restore keeps the restored machine
  monitored for restore_monitor_ticks (game/config.py). A machine is monitored whenever at least
  one window covers the tick (the game keeps the longest remaining time, which is the same thing).
  Two last ticks are kept because the game looks at monitoring at two moments of a tick:
  tick_last_action is the last tick an attacker's move on the machine meets the monitoring (breach
  chance halved, a failed exploit can be detected); tick_last_end is the last tick at whose end an
  attacker standing on the machine is in sight (one tick earlier, because the timer counts down
  before that test).
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
    description: Monitored machine.
    checks:
      - name: not_null
  - name: source
    type: VARCHAR
    description: monitor (a MONITOR action) or restore (the watch that follows a finished restore).
    checks:
      - name: accepted_values
        value: ["monitor", "restore"]
  - name: actor_id
    type: VARCHAR
    description: Blue actor whose action opened the window.
    checks:
      - name: not_null
      - name: accepted_values
        value: ["p1", "p2", "blue1", "blue2", "blue3", "blue4"]
  - name: tick_from
    type: INTEGER
    description: Tick of the action.
    checks:
      - name: not_null
      - name: non_negative
  - name: tick_last_action
    type: INTEGER
    description: Last tick an attacker's move on the machine is affected by this window.
    checks:
      - name: not_null
  - name: tick_last_end
    type: INTEGER
    description: Last tick at whose end an attacker standing on the machine is in sight through this window.
    checks:
      - name: not_null
custom_checks:
  - name: a window never ends before it starts
    description: tick_last_action is tick_from + length - 1 with a positive length.
    query: SELECT count(*) FROM coaching.monitor_windows WHERE tick_last_action < tick_from OR tick_last_end > tick_last_action
    value: 0
    blocking: true
@bruin */

SELECT
    l.node_id                                                          AS machine_id,
    CASE WHEN l.action = 'MONITOR' THEN 'monitor' ELSE 'restore' END   AS source,
    l.actor_id                                                         AS actor_id,
    l.tick                                                             AS tick_from,
    l.tick + CASE WHEN l.action = 'MONITOR' THEN ms.monitor_ticks ELSE ms.restore_monitor_ticks END - 1 AS tick_last_action,
    l.tick + CASE WHEN l.action = 'MONITOR' THEN ms.monitor_ticks ELSE ms.restore_monitor_ticks END - 2 AS tick_last_end
FROM coaching.ledger_rows AS l
CROSS JOIN coaching.match_state AS ms
WHERE l.team = 'blue'
  AND ((l.action = 'MONITOR' AND l.outcome = 'monitoring')
    OR (l.action = 'RESTORE' AND l.outcome = 'restored'))
