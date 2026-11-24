/* @bruin
name: coaching.machines
type: duckdb.sql
description: |
  The 20 machines of the board as the Blue team sees them at the checkpoint (state.json "nodes"):
  value, state and whether the machine is monitored right now. The grid is 5 columns by 4 rows and
  machine_id = y * 5 + x, so x and y are derived here for the "one step away" figures.
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
  - name: machine_id
    type: INTEGER
    description: Machine id, 0 to 19 (y * 5 + x).
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: min
        value: 0
      - name: max
        value: 19
  - name: machine_name
    type: VARCHAR
    description: Name on the board, for example DC-01.
    checks:
      - name: not_null
  - name: machine_value
    type: INTEGER
    description: Points Red earns per tick while the machine is breached (1 to 3).
    checks:
      - name: not_null
      - name: positive
  - name: machine_state
    type: VARCHAR
    description: State on the board at the checkpoint. The game's word for breached is "compromised".
    checks:
      - name: accepted_values
        value: ["healthy", "compromised", "isolated"]
  - name: monitored_now
    type: BOOLEAN
    description: True when the machine is monitored at the checkpoint.
    checks:
      - name: not_null
  - name: x
    type: INTEGER
    description: Grid column, 0 to 4.
    checks:
      - name: not_null
  - name: y
    type: INTEGER
    description: Grid row, 0 to 3.
    checks:
      - name: not_null
custom_checks:
  - name: the board has 20 machines
    description: The grid is 5 by 4; fewer machines means state.json is not a whole board.
    query: SELECT count(*) FROM coaching.machines
    value: 20
    blocking: true
@bruin */

SELECT
    CAST(u.n.id AS INTEGER)                AS machine_id,
    u.n.name                               AS machine_name,
    CAST(u.n.value AS INTEGER)             AS machine_value,
    u.n.state                              AS machine_state,
    coalesce(u.n.monitored, false)         AS monitored_now,
    CAST(u.n.id % 5 AS INTEGER)            AS x,
    CAST(u.n.id // 5 AS INTEGER)           AS y
FROM (
    SELECT unnest(s.nodes) AS n
    FROM read_json(
        'data/coach/current/state.json',
        columns = {nodes: 'STRUCT(id INTEGER, name VARCHAR, value INTEGER, state VARCHAR, monitored BOOLEAN)[]'}
    ) AS s
) AS u
