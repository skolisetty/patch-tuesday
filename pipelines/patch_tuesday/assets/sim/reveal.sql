/* @bruin
name: sim.reveal
type: duckdb.sql
description: |
  End-of-match reveal: for every deck card, did the CVE really enter KEV within 91 days after the
  world clock? This is the ONLY asset besides raw.* that reads landing.kev (the future). It writes
  serving/reveal.json and nothing else; no feature, label or model reads its output, and the game
  shows it only after a match ends. Tagged serve as well as sim so the EPSS fallback rebuild also
  refreshes it, before world.json.
owner: patch-tuesday-pipeline
domains:
  - world-simulation
tags:
  - sim
  - serve
  - export
depends:
  - serve.attack_cards
  - landing.kev
  - raw.world_clock
@bruin */

COPY (
    SELECT a.cve_id,
           coalesce(k.date_added > w.asof_date AND k.date_added <= w.asof_date + 91, false) AS entered_kev_within_91d,
           CASE WHEN k.date_added <= w.asof_date + 91 THEN k.date_added END                 AS kev_date_added
    FROM serve.attack_cards a
    CROSS JOIN raw.world_clock w
    LEFT JOIN landing.kev k ON k.cve_id = a.cve_id
    ORDER BY a.card_id
) TO 'serving/reveal.json' (FORMAT json, ARRAY true)
