/* @bruin
name: serve.patch_priority
type: duckdb.sql
description: |
  Patch priority for every visible CVE at the world clock, ranked by the score the game uses:
  the challenger's model_score if ml.promotions holds a promotion for this clock, else EPSS as of
  the clock (score_source epss, model_score null). The top 500 ranks are exported to
  serving/patch_priority.json; the full ranking feeds the deck.
owner: patch-tuesday-pipeline
domains:
  - world-simulation
tags:
  - serve
depends:
  - raw.world_clock
  - raw.cves
  - feat.snapshot_features
  - ml.challenger_scores
  - ml.promotions
  - ml.promote
  - stg.cve_technique
materialization:
  type: table
  strategy: create+replace
columns:
  - name: rank
    type: BIGINT
    description: 1 = patch first. Ties broken by EPSS then CVE id.
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: positive
  - name: cve_id
    type: VARCHAR
    description: CVE id.
    extends: CVE.ID
    checks:
      - name: not_null
      - name: unique
  - name: score
    type: DOUBLE
    description: Score used by the game.
    extends: AttackCard.Score
    checks:
      - name: not_null
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: score_source
    type: VARCHAR
    description: model or epss, the same on every row.
    extends: AttackCard.ScoreSource
    checks:
      - name: not_null
      - name: accepted_values
        value: ["model", "epss"]
  - name: model_version
    type: VARCHAR
    description: Promoted model version, null when serving EPSS.
  - name: epss
    type: DOUBLE
    description: EPSS as of the clock (null if the CVE is not in that file yet).
    extends: EPSSScore.Score
  - name: epss_percentile
    type: DOUBLE
    description: EPSS percentile as of the clock.
    extends: EPSSScore.Percentile
  - name: model_score
    type: DOUBLE
    description: Challenger score; null when the gate failed.
    extends: ModelScore.Score
  - name: in_kev
    type: BOOLEAN
    description: In raw.kev as of the clock.
    checks:
      - name: not_null
  - name: published_date
    type: DATE
    description: NVD publication date.
    extends: CVE.PublishedDate
  - name: age_days
    type: INTEGER
    description: Days from publication to the clock.
    checks:
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
    description: Severity band derived from cvss_base_score (CVSS v3 bands).
    checks:
      - name: accepted_values
        value: ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
  - name: technique_id
    type: VARCHAR
    description: ATT&CK technique from stg.cve_technique.
    extends: Technique.ID
custom_checks:
  - name: one score source per week
    description: Every row uses the same source.
    query: SELECT count(DISTINCT score_source) FROM serve.patch_priority
    value: 1
    blocking: true
  - name: model scores only when promoted for this clock
    description: The serve layer obeys the promotion registry.
    query: |
      SELECT count(*) FROM serve.patch_priority
      WHERE (score_source = 'model') <> EXISTS (
        SELECT 1 FROM ml.promotions p WHERE p.asof_date = (SELECT asof_date FROM raw.world_clock))
    value: 0
    blocking: true
  - name: ctid technique only for CVEs already in KEV
    description: Same rule as stg.cve_technique, re-checked where serving reads it.
    query: |
      SELECT count(*) FROM serve.patch_priority p
      JOIN stg.cve_technique t ON t.cve_id = p.cve_id
      WHERE t.mapping_source = 'kev_ctid' AND NOT p.in_kev
    value: 0
    blocking: true
  - name: serving covers every visible CVE
    description: One row per raw.cves row.
    query: SELECT (SELECT count(*) FROM raw.cves) - (SELECT count(*) FROM serve.patch_priority)
    value: 0
    blocking: true
@bruin */

WITH clock AS (
    SELECT asof_date FROM raw.world_clock
),
promo AS (
    SELECT max(p.model_version) AS model_version, count(*) > 0 AS promoted
    FROM ml.promotions p JOIN clock c ON p.asof_date = c.asof_date
),
base AS (
    SELECT f.cve_id, f.published_date, f.age_days, f.in_kev_asof AS in_kev,
           f.epss_asof AS epss, f.epss_percentile_asof AS epss_percentile,
           s.model_score AS challenger_score,
           promo.promoted, promo.model_version
    FROM feat.snapshot_features f
    CROSS JOIN clock
    CROSS JOIN promo
    LEFT JOIN ml.challenger_scores s
      ON s.role = 'serve' AND s.cve_id = f.cve_id AND s.asof_date = clock.asof_date
    WHERE f.role = 'serve' AND f.snapshot_date = clock.asof_date
),
scored AS (
    SELECT b.*,
           CASE WHEN b.promoted THEN coalesce(b.challenger_score, 0.0) ELSE coalesce(b.epss, 0.0) END AS score,
           CASE WHEN b.promoted THEN 'model' ELSE 'epss' END AS score_source,
           CASE WHEN b.promoted THEN b.challenger_score END AS model_score
    FROM base b
)
SELECT
    row_number() OVER (ORDER BY s.score DESC, s.epss DESC NULLS LAST, s.cve_id) AS rank,
    s.cve_id,
    s.score,
    s.score_source,
    CASE WHEN s.promoted THEN s.model_version END AS model_version,
    s.epss,
    s.epss_percentile,
    s.model_score,
    s.in_kev,
    s.published_date,
    s.age_days,
    c.vendor,
    c.product,
    c.cwe_id,
    c.cvss_base_score,
    CASE WHEN c.cvss_base_score >= 9.0 THEN 'CRITICAL'
         WHEN c.cvss_base_score >= 7.0 THEN 'HIGH'
         WHEN c.cvss_base_score >= 4.0 THEN 'MEDIUM'
         WHEN c.cvss_base_score > 0.0  THEN 'LOW' END AS severity,
    t.technique_id
FROM scored s
JOIN raw.cves c ON c.cve_id = s.cve_id
LEFT JOIN stg.cve_technique t ON t.cve_id = s.cve_id
