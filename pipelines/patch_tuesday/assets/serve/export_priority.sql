/* @bruin
name: serve.export_priority
type: duckdb.sql
description: |
  Writes serving/patch_priority.json: the top 500 of serve.patch_priority ordered by rank, with
  in_deck set when the CVE is also an attack card. Unmaterialised single COPY.
owner: patch-tuesday-pipeline
domains:
  - world-simulation
tags:
  - serve
  - export
depends:
  - serve.patch_priority
  - serve.attack_cards
@bruin */

COPY (
    SELECT p.rank, p.cve_id, p.score, p.score_source, p.epss, p.model_score, p.in_kev,
           p.vendor, p.product, p.cvss_base_score, p.technique_id,
           (a.cve_id IS NOT NULL) AS in_deck
    FROM serve.patch_priority p
    LEFT JOIN serve.attack_cards a ON a.cve_id = p.cve_id
    WHERE p.rank <= 500
    ORDER BY p.rank
) TO 'serving/patch_priority.json' (FORMAT json, ARRAY true)
