/* @bruin
name: coaching.team_metrics
type: duckdb.sql
description: |
  One row: the evidence figures behind the Coach's three weaknesses (docs/BAND.md).
    Slow response: where Red's points came from, how long machines stayed breached, how many are breached now.
    Exposure:      dangerous open cards on valuable machines, breaches through cards left open, breaches through top-50 priority cards.
    Blind spots:   how often the attackers were out of sight, breaches on machines nobody monitored, detections.
  Built only after coaching.score_reconciliation passed, so the income figures are the scoreboard's.
  A share or an average with nothing to divide by is 0, not null. The field list, with every
  definition and null rule, is in pipelines/coach/README.md.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - metrics
depends:
  - coaching.match_state
  - coaching.score_reconciliation
  - coaching.breach_spans
  - coaching.machines
  - coaching.cards
  - coaching.attacker_sightings
  - coaching.patches
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
  - name: red_points
    type: DOUBLE
    description: Red's score at the checkpoint.
    checks:
      - name: non_negative
  - name: blue_points
    type: DOUBLE
    description: Blue's score at the checkpoint.
    checks:
      - name: not_null
  - name: red_points_from_holding
    type: DOUBLE
    description: Red's points earned by holding breached machines (machine value per tick breached and not isolated).
    checks:
      - name: non_negative
  - name: red_points_from_first_breaches
    type: DOUBLE
    description: Red's points earned by breaching a machine for the first time.
    checks:
      - name: non_negative
  - name: red_share_from_holding
    type: DOUBLE
    description: red_points_from_holding / (holding + first breaches); 0 when Red has no points.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: red_share_from_first_breaches
    type: DOUBLE
    description: red_points_from_first_breaches / (holding + first breaches); 0 when Red has no points.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: breaches
    type: INTEGER
    description: Breaches so far.
    checks:
      - name: non_negative
  - name: machines_ever_breached
    type: INTEGER
    description: Different machines breached at least once.
    checks:
      - name: non_negative
  - name: avg_ticks_breached
    type: DOUBLE
    description: Average ticks a breached machine stayed breached (open breaches count up to the checkpoint); 0 with no breach.
    checks:
      - name: non_negative
  - name: longest_ticks_breached
    type: INTEGER
    description: Longest of those; 0 with no breach.
    checks:
      - name: non_negative
  - name: machines_breached_now
    type: INTEGER
    description: Machines breached at the checkpoint (a breached machine that is isolated counts).
    checks:
      - name: non_negative
  - name: machines_breached_now_names
    type: VARCHAR[]
    description: Their names, by machine id.
  - name: open_hot_cards
    type: INTEGER
    description: Open (unpatched) cards with danger of hot_danger or more on machines of value hot_value or more.
    checks:
      - name: non_negative
  - name: breaches_through_long_open_cards
    type: INTEGER
    description: Breaches through a card that had been on the board, with no patch started, for long_open_ticks or more.
    checks:
      - name: non_negative
  - name: breaches_through_top50_cards
    type: INTEGER
    description: Breaches through a card that was priority 50 or better when breached (the game's own counter).
    checks:
      - name: non_negative
  - name: breaches_on_unmonitored_machines
    type: INTEGER
    description: Breaches of a machine nobody was monitoring at that moment.
    checks:
      - name: non_negative
  - name: attacker_ticks
    type: INTEGER
    description: Attacker time, two attackers per tick from red_entry_delay on.
    checks:
      - name: non_negative
  - name: attacker_ticks_unseen
    type: INTEGER
    description: Of those, ticks the attacker was not in sight, counting only sightings that are certain (an upper bound when sightings_exact is false).
    checks:
      - name: non_negative
  - name: attackers_unseen_share
    type: DOUBLE
    description: attacker_ticks_unseen / attacker_ticks; 0 before the attackers are active.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: attacker_ticks_unseen_if_all_detected
    type: INTEGER
    description: The same count if every failed exploit on a monitored machine was detected (the lower bound).
    checks:
      - name: non_negative
  - name: attackers_unseen_share_if_all_detected
    type: DOUBLE
    description: attacker_ticks_unseen_if_all_detected / attacker_ticks.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: detections
    type: INTEGER
    description: Times an attacker went from out of sight to in sight through monitoring, counting only certain ones.
    checks:
      - name: non_negative
  - name: detections_if_all_detected
    type: INTEGER
    description: The same count if every failed exploit on a monitored machine was detected.
    checks:
      - name: non_negative
  - name: sightings_exact
    type: BOOLEAN
    description: True when the two scenarios give the same sightings, so the sighting figures are exact.
    checks:
      - name: not_null
  - name: sightings_basis
    type: VARCHAR
    description: seen_flags (the ledger carried the "seen" flag) or derived (replayed from the rules).
    checks:
      - name: accepted_values
        value: ["seen_flags", "derived"]
  - name: cards_patched
    type: INTEGER
    description: Cards patched by the whole Blue team.
    checks:
      - name: non_negative
  - name: restores
    type: INTEGER
    description: Restores finished by the whole Blue team.
    checks:
      - name: non_negative
  - name: score_reconciliation_max_diff
    type: DOUBLE
    description: Largest difference between a score part derived from the ledger and the scoreboard (below reconcile_tolerance, or this row would not exist).
    checks:
      - name: non_negative
custom_checks:
  - name: exactly one team row
    description: One match, one row.
    query: SELECT count(*) FROM coaching.team_metrics
    value: 1
    blocking: true
  - name: the two income shares add up
    description: Holding plus first breaches is all of Red's income, so the shares sum to 1 (or both are 0).
    query: |
      SELECT count(*) FROM coaching.team_metrics
      WHERE abs(red_share_from_holding + red_share_from_first_breaches
                - CASE WHEN red_points_from_holding + red_points_from_first_breaches > 0 THEN 1 ELSE 0 END) > 0.002
    value: 0
    blocking: true
  - name: breach counts are consistent
    description: No subset of the breaches can be larger than the breaches, and machines breached now cannot exceed machines ever breached.
    query: |
      SELECT count(*) FROM coaching.team_metrics
      WHERE breaches_through_long_open_cards > breaches
         OR breaches_through_top50_cards > breaches
         OR breaches_on_unmonitored_machines > breaches
         OR machines_breached_now > machines_ever_breached
         OR machines_ever_breached > breaches
         OR longest_ticks_breached < avg_ticks_breached - 0.01
    value: 0
    blocking: true
  - name: sighting bounds are ordered
    description: The certain count of unseen ticks is the upper bound; neither bound exceeds attacker time.
    query: |
      SELECT count(*) FROM coaching.team_metrics
      WHERE attacker_ticks_unseen_if_all_detected > attacker_ticks_unseen
         OR attacker_ticks_unseen > attacker_ticks
    value: 0
    blocking: true
@bruin */

WITH sp AS (
    SELECT
        count(*)                                                    AS breaches,
        count(DISTINCT machine_id)                                  AS machines_ever_breached,
        coalesce(round(avg(ticks_breached), 2), 0.0)                AS avg_ticks_breached,
        coalesce(max(ticks_breached), 0)                            AS longest_ticks_breached,
        count(*) FILTER (WHERE still_breached)                      AS machines_breached_now,
        coalesce(list(machine_name ORDER BY machine_id) FILTER (WHERE still_breached), CAST([] AS VARCHAR[])) AS machines_breached_now_names,
        count(*) FILTER (WHERE long_open)                           AS breaches_through_long_open_cards,
        count(*) FILTER (WHERE NOT monitored_at_breach)             AS breaches_on_unmonitored_machines
    FROM coaching.breach_spans
),
si AS (
    SELECT
        count(*) FILTER (WHERE active)                              AS attacker_ticks,
        count(*) FILTER (WHERE active AND NOT seen_proven)          AS attacker_ticks_unseen,
        count(*) FILTER (WHERE active AND NOT seen_if_detected)     AS attacker_ticks_unseen_if_all_detected,
        count(*) FILTER (WHERE detection_proven)                    AS detections,
        count(*) FILTER (WHERE detection_if_detected)               AS detections_if_all_detected,
        count(*) FILTER (WHERE seen_proven <> seen_if_detected
                            OR detection_proven <> detection_if_detected) = 0 AS sightings_exact,
        coalesce(max(basis), 'derived')                             AS sightings_basis
    FROM coaching.attacker_sightings
),
hot AS (
    SELECT count(*) AS open_hot_cards
    FROM coaching.cards c JOIN coaching.machines m ON m.machine_id = c.machine_id
    WHERE c.patch_state = 'unpatched'
      AND c.danger >= {{ var.hot_danger }}
      AND m.machine_value >= {{ var.hot_value }}
)
SELECT
    ms.match_id                                                     AS match_id,
    ms.red_points                                                   AS red_points,
    ms.blue_points                                                  AS blue_points,
    round(r.derived_held, 2)                                        AS red_points_from_holding,
    round(r.derived_first_breach, 2)                                AS red_points_from_first_breaches,
    CASE WHEN r.derived_held + r.derived_first_breach > 0
         THEN round(r.derived_held / (r.derived_held + r.derived_first_breach), 3) ELSE 0.0 END AS red_share_from_holding,
    CASE WHEN r.derived_held + r.derived_first_breach > 0
         THEN round(r.derived_first_breach / (r.derived_held + r.derived_first_breach), 3) ELSE 0.0 END AS red_share_from_first_breaches,
    CAST(sp.breaches AS INTEGER)                                    AS breaches,
    CAST(sp.machines_ever_breached AS INTEGER)                      AS machines_ever_breached,
    CAST(sp.avg_ticks_breached AS DOUBLE)                           AS avg_ticks_breached,
    CAST(sp.longest_ticks_breached AS INTEGER)                      AS longest_ticks_breached,
    CAST(sp.machines_breached_now AS INTEGER)                       AS machines_breached_now,
    sp.machines_breached_now_names                                  AS machines_breached_now_names,
    CAST(hot.open_hot_cards AS INTEGER)                             AS open_hot_cards,
    CAST(sp.breaches_through_long_open_cards AS INTEGER)            AS breaches_through_long_open_cards,
    ms.state_breaches_in_top50                                      AS breaches_through_top50_cards,
    CAST(sp.breaches_on_unmonitored_machines AS INTEGER)            AS breaches_on_unmonitored_machines,
    CAST(si.attacker_ticks AS INTEGER)                              AS attacker_ticks,
    CAST(si.attacker_ticks_unseen AS INTEGER)                       AS attacker_ticks_unseen,
    CASE WHEN si.attacker_ticks > 0
         THEN round(si.attacker_ticks_unseen / si.attacker_ticks, 3) ELSE 0.0 END AS attackers_unseen_share,
    CAST(si.attacker_ticks_unseen_if_all_detected AS INTEGER)       AS attacker_ticks_unseen_if_all_detected,
    CASE WHEN si.attacker_ticks > 0
         THEN round(si.attacker_ticks_unseen_if_all_detected / si.attacker_ticks, 3) ELSE 0.0 END AS attackers_unseen_share_if_all_detected,
    CAST(si.detections AS INTEGER)                                  AS detections,
    CAST(si.detections_if_all_detected AS INTEGER)                  AS detections_if_all_detected,
    si.sightings_exact                                              AS sightings_exact,
    si.sightings_basis                                              AS sightings_basis,
    CAST((SELECT count(*) FROM coaching.patches) AS INTEGER)        AS cards_patched,
    CAST((SELECT count(*) FROM coaching.ledger_rows
           WHERE team = 'blue' AND action = 'RESTORE' AND outcome = 'restored') AS INTEGER) AS restores,
    round(r.max_abs_diff, 4)                                        AS score_reconciliation_max_diff
FROM coaching.match_state AS ms
JOIN coaching.score_reconciliation AS r ON r.match_id = ms.match_id
CROSS JOIN sp
CROSS JOIN si
CROSS JOIN hot
