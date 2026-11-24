/* @bruin
name: coaching.attacker_sightings
type: duckdb.sql
description: |
  For each attacker and tick: was it in sight of the defenders at the end of the tick, and was it
  newly detected? FOG OF WAR: no position, target or card of an unseen attacker is kept. The only
  machine in this table is detected_on_machine, filled on the tick an attacker becomes visible,
  which is exactly what the defenders are shown at that moment.

  Two ways to know, named in "basis":
  - seen_flags: every attacker row of the ledger carries the optional "seen" flag. The figures are
    then exact and the two scenarios below are identical.
  - derived: the ledger has no flag, so the rules are replayed. An attacker is in sight at the end
    of a tick when (a) it breached a machine in the last breach_reveal_ticks - 1 ticks, or (b) it
    stands on a machine that is monitored at the end of the tick. Both are certain. A third way,
    (c) a failed exploit on a monitored machine is detected with a probability of 0.8 and then
    shows the attacker for detect_ticks - 1 ticks, depends on a dice roll the ledger does not
    record. So two scenarios are kept: "proven" counts (a) and (b) only (no such roll succeeded)
    and "if_detected" also counts (c) (every such roll succeeded). The truth lies between them and
    they are equal whenever (c) would not change anything.

  A detection is an attacker going from out of sight to in sight in a tick in which it did not
  breach (a breach shows the attacker anyway and is not credited to anyone's monitoring). It is
  credited to the Blue actor whose monitoring covered the machine (the latest window that covers it).
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - derive
depends:
  - coaching.ledger_rows
  - coaching.match_state
  - coaching.monitor_windows
materialization:
  type: table
  strategy: create+replace
columns:
  - name: attacker_id
    type: VARCHAR
    description: red1 or red2 (the attackers' names are public; where they are is not).
    checks:
      - name: not_null
      - name: accepted_values
        value: ["red1", "red2"]
  - name: tick
    type: INTEGER
    description: Tick.
    checks:
      - name: not_null
      - name: non_negative
  - name: active
    type: BOOLEAN
    description: True from red_entry_delay on; only active ticks count as attacker time.
    checks:
      - name: not_null
  - name: seen_proven
    type: BOOLEAN
    description: In sight at the end of the tick for certain.
    checks:
      - name: not_null
  - name: seen_if_detected
    type: BOOLEAN
    description: In sight at the end of the tick if every failed exploit on a monitored machine was detected.
    checks:
      - name: not_null
  - name: detection_proven
    type: BOOLEAN
    description: Newly in sight this tick, not through a breach, for certain.
    checks:
      - name: not_null
  - name: detection_if_detected
    type: BOOLEAN
    description: Newly in sight this tick, not through a breach, in the if_detected scenario.
    checks:
      - name: not_null
  - name: detected_on_machine
    type: INTEGER
    description: Machine the attacker was detected on (only on a tick with a detection in either scenario).
  - name: credited_actor_id
    type: VARCHAR
    description: Blue actor whose monitoring covered that machine; null when there was no detection.
  - name: basis
    type: VARCHAR
    description: seen_flags or derived (see the description).
    checks:
      - name: accepted_values
        value: ["seen_flags", "derived"]
custom_checks:
  - name: one row per attacker per tick
    description: The out-of-sight share is a count of these rows.
    query: SELECT count(*) FROM (SELECT attacker_id, tick FROM coaching.attacker_sightings GROUP BY 1, 2 HAVING count(*) > 1)
    value: 0
    blocking: true
  - name: what is proven is also seen in the other scenario
    description: The if_detected scenario only adds sightings.
    query: SELECT count(*) FROM coaching.attacker_sightings WHERE seen_proven AND NOT seen_if_detected
    value: 0
    blocking: true
  - name: no machine is kept for an attacker that was not detected
    description: Fog of war. A position may appear only on the tick the attacker became visible.
    query: |
      SELECT count(*) FROM coaching.attacker_sightings
      WHERE detected_on_machine IS NOT NULL AND NOT (detection_proven OR detection_if_detected)
    value: 0
    blocking: true
@bruin */

WITH ms AS (
    SELECT * FROM coaching.match_state
),
red AS (
    SELECT actor_id, tick, node_id, action, outcome, seen
    FROM coaching.ledger_rows
    WHERE team = 'red'
),
flags AS (
    SELECT count(*) > 0 AND count(*) = count(seen) AS has_flags FROM red
),
facts AS (
    SELECT
        r.actor_id, r.tick, r.node_id, r.seen,
        (r.action = 'EXPLOIT' AND r.outcome = 'breach') AS breached_now,
        EXISTS (SELECT 1 FROM red b CROSS JOIN ms
                 WHERE b.actor_id = r.actor_id AND b.action = 'EXPLOIT' AND b.outcome = 'breach'
                   AND r.tick BETWEEN b.tick AND b.tick + ms.breach_reveal_ticks - 2) AS by_breach,
        EXISTS (SELECT 1 FROM coaching.monitor_windows w
                 WHERE w.machine_id = r.node_id
                   AND r.tick BETWEEN w.tick_from AND w.tick_last_end) AS by_standing,
        EXISTS (SELECT 1 FROM red f CROSS JOIN ms
                 WHERE f.actor_id = r.actor_id AND f.action = 'EXPLOIT' AND f.outcome = 'failed'
                   AND r.tick BETWEEN f.tick AND f.tick + ms.detect_ticks - 2
                   AND EXISTS (SELECT 1 FROM coaching.monitor_windows w
                                WHERE w.machine_id = f.node_id
                                  AND f.tick BETWEEN w.tick_from AND w.tick_last_action)) AS by_detect_roll
    FROM red r
),
seen AS (
    SELECT
        f.actor_id, f.tick, f.node_id, f.breached_now,
        CASE WHEN fl.has_flags THEN f.seen ELSE (f.by_breach OR f.by_standing) END AS seen_proven,
        CASE WHEN fl.has_flags THEN f.seen ELSE (f.by_breach OR f.by_standing OR f.by_detect_roll) END AS seen_if_detected,
        CASE WHEN fl.has_flags THEN 'seen_flags' ELSE 'derived' END AS basis
    FROM facts f CROSS JOIN flags fl
),
steps AS (
    SELECT
        s.*,
        coalesce(lag(s.seen_proven) OVER w, false)      AS prev_proven,
        coalesce(lag(s.seen_if_detected) OVER w, false) AS prev_if_detected
    FROM seen s
    WINDOW w AS (PARTITION BY s.actor_id ORDER BY s.tick)
),
det AS (
    SELECT
        st.*,
        (st.seen_proven AND NOT st.prev_proven AND NOT st.breached_now)           AS detection_proven,
        (st.seen_if_detected AND NOT st.prev_if_detected AND NOT st.breached_now) AS detection_if_detected
    FROM steps st
)
SELECT
    d.actor_id                                   AS attacker_id,
    d.tick                                       AS tick,
    d.tick >= ms.red_entry_delay                 AS active,
    d.seen_proven                                AS seen_proven,
    d.seen_if_detected                           AS seen_if_detected,
    d.detection_proven                           AS detection_proven,
    d.detection_if_detected                      AS detection_if_detected,
    CASE WHEN d.detection_proven OR d.detection_if_detected THEN d.node_id END AS detected_on_machine,
    CASE WHEN d.detection_proven OR d.detection_if_detected THEN
        (SELECT arg_max(w.actor_id, w.tick_from) FROM coaching.monitor_windows w
          WHERE w.machine_id = d.node_id AND d.tick BETWEEN w.tick_from AND w.tick_last_action)
    END                                          AS credited_actor_id,
    d.basis                                      AS basis
FROM det AS d
CROSS JOIN ms
