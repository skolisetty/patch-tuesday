/* @bruin
name: coaching.cards
type: duckdb.sql
description: |
  Every card on the board at the checkpoint (state.json "nodes[].cards"): danger, known-exploited
  flag, priority and patch state, all of it visible to the Blue team. dropped_tick is the tick the
  card landed on its machine in a mid-match "Advance week"; it is optional in state.json and a
  missing value means the card has been on the board since tick 0.
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - load
depends:
  - coaching.machines
materialization:
  type: table
  strategy: create+replace
columns:
  - name: machine_id
    type: INTEGER
    description: Machine the card sits on.
    checks:
      - name: not_null
  - name: cve_id
    type: VARCHAR
    description: CVE of the card.
    extends: CVE.ID
    checks:
      - name: not_null
  - name: danger
    type: DOUBLE
    description: Danger of the card (the served score, our model or the EPSS baseline), 0 to 1.
    checks:
      - name: not_null
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: known_exploited
    type: BOOLEAN
    description: True when the CVE is in CISA KEV as of the world clock.
    checks:
      - name: not_null
  - name: priority
    type: INTEGER
    description: Priority #N of the CVE on the patch priority list (null when it is not ranked).
    checks:
      - name: positive
  - name: patch_state
    type: VARCHAR
    description: unpatched (open), patching or patched.
    checks:
      - name: accepted_values
        value: ["unpatched", "patching", "patched"]
  - name: dropped_tick
    type: INTEGER
    description: Tick the card arrived mid-match; null for a card dealt at the start.
    checks:
      - name: non_negative
custom_checks:
  - name: every card sits on a machine of the board
    description: A card on an unknown machine would silently drop out of the exposure figures.
    query: SELECT count(*) FROM coaching.cards c WHERE NOT EXISTS (SELECT 1 FROM coaching.machines m WHERE m.machine_id = c.machine_id)
    value: 0
    blocking: true
  - name: a CVE appears once per machine
    description: Breaches are matched to cards by machine and CVE.
    query: SELECT count(*) FROM (SELECT machine_id, cve_id FROM coaching.cards GROUP BY 1, 2 HAVING count(*) > 1)
    value: 0
    blocking: true
@bruin */

SELECT
    u.machine_id                                               AS machine_id,
    u.c.cve_id                                                 AS cve_id,
    CAST(coalesce(u.c.score, u.c.danger) AS DOUBLE)            AS danger,
    coalesce(u.c.in_kev, u.c.known_exploited, false)           AS known_exploited,
    CAST(coalesce(u.c.priority_rank, u.c.priority) AS INTEGER) AS priority,
    u.c.patch_state                                            AS patch_state,
    CAST(u.c.dropped_tick AS INTEGER)                          AS dropped_tick
FROM (
    SELECT CAST(nn.n.id AS INTEGER) AS machine_id, unnest(nn.n.cards) AS c
    FROM (
        SELECT unnest(s.nodes) AS n
        FROM read_json(
            'data/coach/current/state.json',
            columns = {nodes: 'STRUCT(id INTEGER, cards STRUCT(cve_id VARCHAR, score DOUBLE, danger DOUBLE, in_kev BOOLEAN, known_exploited BOOLEAN, priority_rank INTEGER, priority INTEGER, patch_state VARCHAR, dropped_tick INTEGER)[])[]'}
        ) AS s
    ) AS nn
) AS u
