/* @bruin
# Seed defined in a SQL comment header (see pipelines/patch_tuesday/assets/landing/kev_seed.sql).
name: game.agent_ledger
type: duckdb.seed
description: |
  The game's decision ledger (ledger/ledger.jsonl, written by the game server: one row per actor
  per tick) loaded into the pipeline warehouse for analysis. Run on demand only, between matches:
  bruin run --workers 1 pipelines/game_ledger/pipeline.yml
  It lives in its own pipeline because the seed file exists only after a match has been played,
  and a missing seed file is a validation error that would otherwise block the weekly pipeline.
  JSONL seed columns are inferred and come out in alphabetical order; never rely on position.
owner: patch-tuesday-pipeline
domains:
  - world-simulation
tags:
  - game

parameters:
  path: ../../../ledger/ledger.jsonl
  file_type: jsonl

columns:
  - name: ledger_id
    type: varchar
    description: Unique ledger row id.
    extends: AgentDecision.LedgerID
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: team
    type: varchar
    description: red or blue.
    checks:
      - name: accepted_values
        value: ["red", "blue"]
  - name: score_source
    type: varchar
    description: Score source of the world the decision was taken in.
    checks:
      - name: accepted_values
        value: ["model", "epss"]
  - name: p_breach
    type: double
    description: Breach probability the rules computed, when the action was an exploit.
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 0.95

custom_checks:
  - name: ledger is not empty
    description: The ledger seed should hold at least one decision.
    query: SELECT CAST(count(*) > 0 AS INTEGER) FROM game.agent_ledger
    value: 1
    blocking: true
@bruin */
