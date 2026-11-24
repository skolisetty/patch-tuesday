/* @bruin
name: coaching.breach_spans
type: duckdb.sql
description: |
  One row per breach: which machine, the tick it was breached, the tick its restore finished (null
  while it is still breached) and how long it stayed breached. A breach is announced to the whole
  Blue team when it happens, so nothing here is hidden from the defenders; the row deliberately
  carries no attacker and no CVE, only flags.
  Blue moves before Red inside a tick, so a restore that finishes in the tick of a breach came
  first and does not end that breach: the restore that ends a breach is the first one on the
  machine at a later tick. Red is paid the machine's value at the end of every tick from the breach
  tick to the tick before the restore, except ticks the machine spent isolated (ticks_paid).
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - derive
depends:
  - coaching.ledger_rows
  - coaching.match_state
  - coaching.machines
  - coaching.cards
  - coaching.monitor_windows
  - coaching.isolation_ticks
materialization:
  type: table
  strategy: create+replace
columns:
  - name: breach_no
    type: INTEGER
    description: Breaches of the match in order (by tick, then machine), from 1.
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: positive
  - name: machine_id
    type: INTEGER
    description: Breached machine.
    checks:
      - name: not_null
  - name: machine_name
    type: VARCHAR
    description: Its name on the board.
    checks:
      - name: not_null
  - name: machine_value
    type: INTEGER
    description: Points Red earns per tick while it stays breached.
    checks:
      - name: positive
  - name: tick_breached
    type: INTEGER
    description: Tick of the breach.
    checks:
      - name: not_null
      - name: non_negative
  - name: tick_restored
    type: INTEGER
    description: Tick the restore finished; null while the machine is still breached.
  - name: still_breached
    type: BOOLEAN
    description: True when no restore has finished yet.
    checks:
      - name: not_null
  - name: restored_by
    type: VARCHAR
    description: Blue actor whose restore ended the breach; null while it is still breached.
  - name: ticks_breached
    type: INTEGER
    description: Ticks the machine stayed breached (tick_restored, or the checkpoint tick, minus tick_breached).
    checks:
      - name: not_null
      - name: positive
  - name: ticks_isolated
    type: INTEGER
    description: Of those ticks, how many the machine spent isolated (Red earns nothing then).
    checks:
      - name: non_negative
  - name: ticks_paid
    type: INTEGER
    description: Ticks Red was paid for this breach (ticks_breached - ticks_isolated).
    checks:
      - name: non_negative
  - name: first_breach
    type: BOOLEAN
    description: True for the first breach of the machine in the match (worth first_breach_points to Red).
    checks:
      - name: not_null
  - name: monitored_at_breach
    type: BOOLEAN
    description: True when the machine was monitored at the moment of the breach.
    checks:
      - name: not_null
  - name: card_untouched
    type: BOOLEAN
    description: True when no patch had been started on the card that was used.
    checks:
      - name: not_null
  - name: card_open_ticks
    type: INTEGER
    description: Ticks the card had been on the board when it was used (since tick 0, or since its dropped_tick).
    checks:
      - name: non_negative
  - name: long_open
    type: BOOLEAN
    description: True when the card was untouched and had been on the board for long_open_ticks or more.
    checks:
      - name: not_null
custom_checks:
  - name: a restore comes after its breach
    description: tick_restored must be later than tick_breached.
    query: SELECT count(*) FROM coaching.breach_spans WHERE tick_restored IS NOT NULL AND tick_restored <= tick_breached
    value: 0
    blocking: true
  - name: at most one open breach per machine
    description: A breached machine cannot be breached again before it is restored.
    query: SELECT count(*) FROM (SELECT machine_id FROM coaching.breach_spans WHERE still_breached GROUP BY 1 HAVING count(*) > 1)
    value: 0
    blocking: true
  - name: breach spans on one machine do not overlap
    description: The next breach of a machine starts at or after the restore of the previous one.
    query: |
      SELECT count(*) FROM coaching.breach_spans a JOIN coaching.breach_spans b
        ON a.machine_id = b.machine_id AND a.breach_no < b.breach_no
       AND (a.tick_restored IS NULL OR b.tick_breached < a.tick_restored)
    value: 0
    blocking: true
  - name: the board agrees with the open breaches
    description: |
      Every machine the board shows as breached has an open span, and every open span's machine is
      shown as breached or isolated (an isolated machine can still be breached underneath).
    query: |
      SELECT (SELECT count(*) FROM coaching.machines m WHERE m.machine_state = 'compromised'
                AND NOT EXISTS (SELECT 1 FROM coaching.breach_spans s WHERE s.machine_id = m.machine_id AND s.still_breached))
           + (SELECT count(*) FROM coaching.breach_spans s JOIN coaching.machines m ON m.machine_id = s.machine_id
               WHERE s.still_breached AND m.machine_state NOT IN ('compromised', 'isolated'))
    value: 0
    blocking: true
@bruin */

WITH ms AS (
    SELECT * FROM coaching.match_state
),
breaches AS (
    SELECT node_id AS machine_id, tick AS tick_breached, cve_id
    FROM coaching.ledger_rows
    WHERE team = 'red' AND action = 'EXPLOIT' AND outcome = 'breach'
),
restores AS (
    SELECT node_id AS machine_id, tick, actor_id
    FROM coaching.ledger_rows
    WHERE team = 'blue' AND action = 'RESTORE' AND outcome = 'restored'
),
spans AS (
    SELECT b.machine_id, b.tick_breached, b.cve_id,
           (SELECT min(r.tick) FROM restores r
             WHERE r.machine_id = b.machine_id AND r.tick > b.tick_breached) AS tick_restored
    FROM breaches b
)
SELECT
    CAST(row_number() OVER (ORDER BY s.tick_breached, s.machine_id) AS INTEGER)   AS breach_no,
    s.machine_id                                                                  AS machine_id,
    m.machine_name                                                                AS machine_name,
    m.machine_value                                                               AS machine_value,
    s.tick_breached                                                               AS tick_breached,
    s.tick_restored                                                               AS tick_restored,
    s.tick_restored IS NULL                                                       AS still_breached,
    (SELECT min(r.actor_id) FROM restores r
      WHERE r.machine_id = s.machine_id AND r.tick = s.tick_restored)             AS restored_by,
    CAST(coalesce(s.tick_restored, ms.state_tick) - s.tick_breached AS INTEGER)   AS ticks_breached,
    CAST((SELECT count(*) FROM coaching.isolation_ticks i
           WHERE i.machine_id = s.machine_id AND i.tick >= s.tick_breached
             AND i.tick < coalesce(s.tick_restored, ms.state_tick)) AS INTEGER)   AS ticks_isolated,
    CAST(coalesce(s.tick_restored, ms.state_tick) - s.tick_breached
         - (SELECT count(*) FROM coaching.isolation_ticks i
             WHERE i.machine_id = s.machine_id AND i.tick >= s.tick_breached
               AND i.tick < coalesce(s.tick_restored, ms.state_tick)) AS INTEGER) AS ticks_paid,
    row_number() OVER (PARTITION BY s.machine_id ORDER BY s.tick_breached) = 1    AS first_breach,
    EXISTS (SELECT 1 FROM coaching.monitor_windows w
             WHERE w.machine_id = s.machine_id
               AND s.tick_breached BETWEEN w.tick_from AND w.tick_last_action)    AS monitored_at_breach,
    NOT EXISTS (SELECT 1 FROM coaching.ledger_rows p
                 WHERE p.team = 'blue' AND p.action = 'PATCH' AND p.outcome = 'patching'
                   AND p.node_id = s.machine_id AND p.cve_id = s.cve_id
                   AND p.tick <= s.tick_breached)                                 AS card_untouched,
    CAST(s.tick_breached - coalesce(c.dropped_tick, 0) AS INTEGER)                AS card_open_ticks,
    (NOT EXISTS (SELECT 1 FROM coaching.ledger_rows p
                  WHERE p.team = 'blue' AND p.action = 'PATCH' AND p.outcome = 'patching'
                    AND p.node_id = s.machine_id AND p.cve_id = s.cve_id
                    AND p.tick <= s.tick_breached)
     AND s.tick_breached - coalesce(c.dropped_tick, 0) >= {{ var.long_open_ticks }}) AS long_open
FROM spans AS s
CROSS JOIN ms
JOIN coaching.machines AS m ON m.machine_id = s.machine_id
LEFT JOIN coaching.cards AS c ON c.machine_id = s.machine_id AND c.cve_id = s.cve_id
