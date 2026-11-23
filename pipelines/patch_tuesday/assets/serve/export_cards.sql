/* @bruin
name: serve.export_cards
type: duckdb.sql
description: |
  Writes serving/attack_cards.json (a JSON array, the exact field list of docs/CONTRACTS.md).
  Unmaterialised single COPY; DuckDB writes a temporary file and swaps it in when complete.
  Bruin must be started from the repo root, and serving/ must exist.
owner: patch-tuesday-pipeline
domains:
  - world-simulation
tags:
  - serve
  - export
depends:
  - serve.attack_cards
@bruin */

COPY (
    SELECT card_id, cve_id, title, published_date, age_days, vendor, product, cwe_id,
           cvss_base_score, severity, score, score_source, epss, epss_percentile, model_score,
           in_kev, technique_id, technique_name, tactic, mapping_source, is_new_this_week, priority_rank
    FROM serve.attack_cards
    ORDER BY card_id
) TO 'serving/attack_cards.json' (FORMAT json, ARRAY true)
