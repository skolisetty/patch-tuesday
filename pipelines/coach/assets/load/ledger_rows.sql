/* @bruin
name: coaching.ledger_rows
type: duckdb.sql
description: |
  This match's decision ledger up to the checkpoint, one row per actor per tick, read from
  data/coach/current/ledger.jsonl (the game's ledger rows, see docs/CONTRACTS.md).
  FOG OF WAR: this landing table is the only place in the pipeline that holds the attackers' rows
  (where they stood, what they tried). It is never exported. Every table built from it is either
  about the Blue team, about things the Blue team was shown (a breach is announced, an attacker on
  a monitored machine is in sight) or a count.
  "seen" is optional: when the game adds it to the attackers' rows (true = that attacker was in
  sight of the defenders at the end of the tick) the sighting figures are exact; without it they
  are derived from the rules (see coaching.attacker_sightings).
owner: patch-tuesday-pipeline
domains:
  - blue-team-coaching
tags:
  - coach
  - load
depends:
  - coaching.match_state
materialization:
  type: table
  strategy: create+replace
columns:
  - name: ledger_id
    type: VARCHAR
    description: Unique ledger row id.
    extends: AgentDecision.LedgerID
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: match_id
    type: VARCHAR
    description: Match the row belongs to.
    checks:
      - name: not_null
  - name: tick
    type: INTEGER
    description: Tick of the decision, from 0.
    checks:
      - name: not_null
      - name: non_negative
  - name: actor_id
    type: VARCHAR
    description: p1, p2 (humans), blue1 to blue3 (AI defenders), blue4 (a specialist, if one joined), red1, red2 (attackers).
    checks:
      - name: not_null
      - name: accepted_values
        value: ["p1", "p2", "blue1", "blue2", "blue3", "blue4", "red1", "red2"]
  - name: team
    type: VARCHAR
    description: red or blue.
    checks:
      - name: accepted_values
        value: ["red", "blue"]
  - name: kind
    type: VARCHAR
    description: human or agent.
    checks:
      - name: accepted_values
        value: ["human", "agent"]
  - name: action
    type: VARCHAR
    description: The move. A multi-tick move repeats on every tick it runs.
    checks:
      - name: accepted_values
        value: ["MOVE", "PATCH", "MONITOR", "ISOLATE", "RESTORE", "WAIT", "INTEL", "SCAN", "EXPLOIT", "PIVOT"]
  - name: node_id
    type: INTEGER
    description: Machine the actor stands on after the move.
    checks:
      - name: not_null
      - name: min
        value: 0
      - name: max
        value: 19
  - name: cve_id
    type: VARCHAR
    description: CVE of the card the move was about, if any.
  - name: score_used
    type: DOUBLE
    description: Danger of that card when the move was resolved.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: outcome
    type: VARCHAR
    description: What happened (patching, patched, restoring, restored, monitoring, isolated, breach, failed, moved, waiting and so on).
    checks:
      - name: not_null
  - name: policy
    type: VARCHAR
    description: Policy that chose the move (triage, patcher, hunter, human and so on).
  - name: seen
    type: BOOLEAN
    description: Optional, attackers' rows only. True when the attacker was in sight of the defenders at the end of the tick.
custom_checks:
  - name: the match id is single-valued
    description: A ledger snapshot that mixes matches would mix their breaches.
    query: SELECT count(DISTINCT match_id) FROM coaching.ledger_rows
    value: 1
    blocking: true
  - name: ledger and board are the same match
    description: The ledger snapshot and state.json must describe the same match.
    query: SELECT count(*) FROM coaching.ledger_rows WHERE match_id NOT IN (SELECT match_id FROM coaching.match_state)
    value: 0
    blocking: true
  - name: ledger ticks do not exceed the state's tick
    description: After N ticks the ledger holds ticks 0 to N - 1. A later row means the board was captured before the ledger.
    query: SELECT count(*) FROM coaching.ledger_rows WHERE tick >= (SELECT state_tick FROM coaching.match_state)
    value: 0
    blocking: true
  - name: every actor has one row for every tick played
    description: The breach, income and position figures need the whole history; a gap would silently lower them.
    query: |
      SELECT count(*) FROM (
          SELECT actor_id FROM coaching.ledger_rows
          WHERE actor_id IN ('p1', 'p2', 'blue1', 'blue2', 'blue3', 'red1', 'red2')
          GROUP BY actor_id
          HAVING count(*) = (SELECT state_tick FROM coaching.match_state)
             AND count(DISTINCT tick) = (SELECT state_tick FROM coaching.match_state)
      )
    value: 7
    blocking: true
@bruin */

SELECT
    l.ledger_id                       AS ledger_id,
    l.match_id                        AS match_id,
    CAST(l.tick AS INTEGER)           AS tick,
    l.actor_id                        AS actor_id,
    l.team                            AS team,
    l.kind                            AS kind,
    l.action                          AS action,
    CAST(l.node_id AS INTEGER)        AS node_id,
    l.cve_id                          AS cve_id,
    CAST(l.score_used AS DOUBLE)      AS score_used,
    l.outcome                         AS outcome,
    l.policy                          AS policy,
    CAST(l.seen AS BOOLEAN)           AS seen
FROM read_json(
    'data/coach/current/ledger.jsonl',
    format = 'newline_delimited',
    columns = {
        ledger_id: 'VARCHAR', match_id: 'VARCHAR', tick: 'INTEGER', actor_id: 'VARCHAR',
        team: 'VARCHAR', kind: 'VARCHAR', action: 'VARCHAR', node_id: 'INTEGER', cve_id: 'VARCHAR',
        score_used: 'DOUBLE', outcome: 'VARCHAR', policy: 'VARCHAR', seen: 'BOOLEAN'
    }
) AS l
