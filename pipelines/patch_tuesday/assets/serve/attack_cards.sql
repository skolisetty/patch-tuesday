/* @bruin
name: serve.attack_cards
type: duckdb.sql
description: |
  The game deck, about 120 abstract attack cards for the world clock. Three picks, all CVEs with a
  known vendor and product: "hot" = the 50 best-ranked CVEs published in the last 730 days;
  "new" = the 30 best-ranked CVEs published in the current world week (they drop mid-match);
  "background" = 40 high-severity CVEs from the last 365 days in a fixed hash order (not by score),
  so the deck also holds low-score cards. Titles use vendor, product and weakness class only: no
  description text, no references, no exploit content.
owner: patch-tuesday-pipeline
domains:
  - world-simulation
tags:
  - serve
depends:
  - serve.patch_priority
  - stg.cve_technique
  - raw.world_clock
materialization:
  type: table
  strategy: create+replace
columns:
  - name: card_id
    type: BIGINT
    description: Card id, 1..n in patch-priority order.
    extends: AttackCard.ID
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: positive
  - name: cve_id
    type: VARCHAR
    description: CVE behind the card.
    extends: CVE.ID
    checks:
      - name: not_null
      - name: unique
      - name: pattern
        value: "^CVE-[0-9]{4}-[0-9]{4,19}$"
  - name: title
    type: VARCHAR
    description: "<Vendor> <Product>: <weakness class>."
    checks:
      - name: not_null
  - name: deck_pick
    type: VARCHAR
    description: Why the card is in the deck (hot, new, background). Not exported.
    checks:
      - name: accepted_values
        value: ["hot", "new", "background"]
  - name: published_date
    type: DATE
    description: NVD publication date.
    extends: CVE.PublishedDate
    checks:
      - name: not_null
  - name: age_days
    type: INTEGER
    description: Days from publication to the clock.
    checks:
      - name: not_null
      - name: non_negative
  - name: vendor
    type: VARCHAR
    description: CPE vendor.
    extends: CVE.Vendor
  - name: product
    type: VARCHAR
    description: CPE product.
    extends: CVE.Product
  - name: cwe_id
    type: VARCHAR
    description: Primary CWE.
    extends: CVE.CWE
  - name: cvss_base_score
    type: DOUBLE
    description: CVSS base score.
    extends: CVE.CVSSBaseScore
  - name: severity
    type: VARCHAR
    description: Severity band.
    checks:
      - name: accepted_values
        value: ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
  - name: score
    type: DOUBLE
    description: Score used for breach odds.
    extends: AttackCard.Score
    checks:
      - name: not_null
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: score_source
    type: VARCHAR
    description: model or epss.
    extends: AttackCard.ScoreSource
    checks:
      - name: not_null
      - name: accepted_values
        value: ["model", "epss"]
  - name: epss
    type: DOUBLE
    description: EPSS as of the clock.
    extends: EPSSScore.Score
  - name: epss_percentile
    type: DOUBLE
    description: EPSS percentile as of the clock.
    extends: EPSSScore.Percentile
  - name: model_score
    type: DOUBLE
    description: Challenger score, null when the gate failed.
    extends: ModelScore.Score
  - name: in_kev
    type: BOOLEAN
    description: In KEV as of the clock.
    checks:
      - name: not_null
  - name: technique_id
    type: VARCHAR
    description: ATT&CK technique.
    extends: Technique.ID
  - name: technique_name
    type: VARCHAR
    description: ATT&CK technique name.
    extends: Technique.Name
  - name: tactic
    type: VARCHAR
    description: ATT&CK tactic.
    extends: Technique.Tactic
  - name: mapping_source
    type: VARCHAR
    description: Rule that picked the technique.
    checks:
      - name: not_null
      - name: accepted_values
        value: ["kev_ctid", "cwe_capec", "heuristic", "none"]
  - name: is_new_this_week
    type: BOOLEAN
    description: Published inside the current world week (window_start..asof_date).
    checks:
      - name: not_null
  - name: priority_rank
    type: BIGINT
    description: Rank in patch_priority.json if within the top 500, else null.
custom_checks:
  - name: deck has between 60 and 150 cards
    description: The game expects about 120 cards.
    query: SELECT CAST(count(*) BETWEEN 60 AND 150 AS INTEGER) FROM serve.attack_cards
    value: 1
    blocking: true
  - name: deck uses the same score source as patch priority
    description: One score source per week across every served file.
    query: |
      SELECT count(*) FROM serve.attack_cards a
      WHERE a.score_source <> (SELECT max(score_source) FROM serve.patch_priority)
    value: 0
    blocking: true
  - name: ctid mapping only for cards already in KEV
    description: The CTID KEV-to-ATT&CK mapping exists only for KEV entries, so a kev_ctid card that is not in KEV at the clock would leak its future KEV entry.
    query: SELECT count(*) FROM serve.attack_cards WHERE mapping_source = 'kev_ctid' AND NOT in_kev
    value: 0
    blocking: true
  - name: no card text looks like a link or description
    description: Abstract cards only. Titles are short and contain no URL.
    query: SELECT count(*) FROM serve.attack_cards WHERE title LIKE '%://%' OR title ILIKE '%www.%' OR length(title) > 120
    value: 0
    blocking: true
@bruin */

WITH clock AS (
    SELECT asof_date, window_start FROM raw.world_clock
),
pool AS (
    SELECT p.*, t.weakness_class, t.technique_name, t.tactic, t.mapping_source,
           p.published_date >= clock.window_start AS is_new_this_week,
           clock.asof_date
    FROM serve.patch_priority p
    CROSS JOIN clock
    JOIN stg.cve_technique t ON t.cve_id = p.cve_id
    WHERE p.vendor IS NOT NULL AND p.product IS NOT NULL
),
hot AS (
    SELECT cve_id, 'hot' AS deck_pick FROM pool
    WHERE published_date >= asof_date - 730
    ORDER BY rank LIMIT 50
),
fresh AS (
    SELECT cve_id, 'new' AS deck_pick FROM pool
    WHERE is_new_this_week AND cve_id NOT IN (SELECT cve_id FROM hot)
    ORDER BY rank LIMIT 30
),
background AS (
    SELECT cve_id, 'background' AS deck_pick FROM pool
    WHERE published_date >= asof_date - 365 AND cvss_base_score >= 7.0
      AND cve_id NOT IN (SELECT cve_id FROM hot)
      AND cve_id NOT IN (SELECT cve_id FROM fresh)
    ORDER BY hash(cve_id || '|' || CAST(asof_date AS VARCHAR)), cve_id LIMIT 40
),
deck AS (
    SELECT * FROM hot UNION ALL SELECT * FROM fresh UNION ALL SELECT * FROM background
)
SELECT
    row_number() OVER (ORDER BY p.rank)  AS card_id,
    p.cve_id,
    -- "<Vendor> <Product>: <weakness class>", the vendor dropped when the product already starts
    -- with it; vendor capped at 3 words and product at 5 (some CPE product names are sentences)
    CASE WHEN p.product = p.vendor OR starts_with(p.product, p.vendor || '_') THEN ''
         ELSE array_to_string(list_transform(string_split(replace(p.vendor, '_', ' '), ' ')[1:3],
                                             lambda w: upper(w[1]) || w[2:]), ' ') || ' ' END
      || array_to_string(list_transform(string_split(replace(p.product, '_', ' '), ' ')[1:5],
                                        lambda w: upper(w[1]) || w[2:]), ' ')
      || ': ' || p.weakness_class        AS title,
    d.deck_pick,
    p.published_date,
    p.age_days,
    p.vendor,
    p.product,
    p.cwe_id,
    p.cvss_base_score,
    p.severity,
    p.score,
    p.score_source,
    p.epss,
    p.epss_percentile,
    p.model_score,
    p.in_kev,
    p.technique_id,
    p.technique_name,
    p.tactic,
    p.mapping_source,
    p.is_new_this_week,
    CASE WHEN p.rank <= 500 THEN p.rank END AS priority_rank
FROM deck d
JOIN pool p ON p.cve_id = d.cve_id
