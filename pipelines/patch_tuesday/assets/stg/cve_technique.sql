/* @bruin
name: stg.cve_technique
type: duckdb.sql
description: |
  One abstract ATT&CK technique and one weakness-class label per visible CVE, for the game cards.
  Order: (1) CTID KEV mapping, only for CVEs already in raw.kev, preferring exploitation_technique;
  (2) CWE to CAPEC to ATT&CK, best supported pair whose technique exists in ATT&CK 20.1;
  (3) heuristic T1190 when the vector is network-reachable with no privileges; (4) none.
  mapping_source is display metadata and is never a model feature (a CTID mapping exists only
  for KEV CVEs, so it would leak the label).
owner: patch-tuesday-pipeline
domains:
  - threat-mapping
tags:
  - stg
depends:
  - raw.cves
  - raw.kev
  - landing.attack_techniques
  - landing.kev_attack_map
  - landing.cwe_attack_map
  - landing.cwe_names
materialization:
  type: table
  strategy: create+replace
columns:
  - name: cve_id
    type: VARCHAR
    description: CVE identifier.
    extends: CVE.ID
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: cwe_id
    type: VARCHAR
    description: Primary CWE of the CVE.
    extends: CVE.CWE
  - name: weakness_class
    type: VARCHAR
    description: Short weakness label for the card title ("unclassified weakness" when unknown).
    checks:
      - name: not_null
  - name: technique_id
    type: VARCHAR
    description: Chosen ATT&CK technique, null when mapping_source is none.
    extends: Technique.ID
  - name: technique_name
    type: VARCHAR
    description: Technique name (ATT&CK 20.1, else the CTID file's name).
    extends: Technique.Name
  - name: tactic
    type: VARCHAR
    description: First tactic of the technique in ATT&CK 20.1.
    extends: Technique.Tactic
  - name: mapping_source
    type: VARCHAR
    description: Which rule picked the technique.
    checks:
      - name: not_null
      - name: accepted_values
        value: ["kev_ctid", "cwe_capec", "heuristic", "none"]
custom_checks:
  - name: every visible CVE has a mapping row
    description: stg.cve_technique covers raw.cves one to one.
    query: SELECT (SELECT count(*) FROM raw.cves) - (SELECT count(*) FROM stg.cve_technique)
    value: 0
    blocking: true
  - name: ctid mapping only for CVEs already in KEV
    description: A kev_ctid mapping must never reveal a KEV addition after the world clock.
    query: |
      SELECT count(*) FROM stg.cve_technique t
      WHERE t.mapping_source = 'kev_ctid'
        AND NOT EXISTS (SELECT 1 FROM raw.kev k WHERE k.cve_id = t.cve_id)
    value: 0
    blocking: true
@bruin */

WITH ctid AS (
    SELECT m.cve_id, m.technique_id, coalesce(a.technique_name, m.technique_name) AS technique_name, a.tactic,
           row_number() OVER (PARTITION BY m.cve_id ORDER BY m.mapping_priority, m.technique_id) AS rk
    FROM landing.kev_attack_map m
    JOIN raw.kev k ON k.cve_id = m.cve_id
    LEFT JOIN landing.attack_techniques a ON a.technique_id = m.technique_id
),
cwe_pick AS (
    SELECT p.cwe_id, p.technique_id, a.technique_name, a.tactic,
           row_number() OVER (PARTITION BY p.cwe_id ORDER BY p.n_capec DESC, p.technique_id) AS rk
    FROM landing.cwe_attack_map p
    JOIN landing.attack_techniques a ON a.technique_id = p.technique_id
),
t1190 AS (
    SELECT technique_id, technique_name, tactic FROM landing.attack_techniques WHERE technique_id = 'T1190'
)
SELECT
    c.cve_id,
    c.cwe_id,
    coalesce(n.weakness_class, 'unclassified weakness') AS weakness_class,
    CASE WHEN k.cve_id IS NOT NULL THEN k.technique_id
         WHEN w.cwe_id IS NOT NULL THEN w.technique_id
         WHEN c.av_network = 1 AND c.pr_none = 1 THEN h.technique_id END AS technique_id,
    CASE WHEN k.cve_id IS NOT NULL THEN k.technique_name
         WHEN w.cwe_id IS NOT NULL THEN w.technique_name
         WHEN c.av_network = 1 AND c.pr_none = 1 THEN h.technique_name END AS technique_name,
    CASE WHEN k.cve_id IS NOT NULL THEN k.tactic
         WHEN w.cwe_id IS NOT NULL THEN w.tactic
         WHEN c.av_network = 1 AND c.pr_none = 1 THEN h.tactic END AS tactic,
    CASE WHEN k.cve_id IS NOT NULL THEN 'kev_ctid'
         WHEN w.cwe_id IS NOT NULL THEN 'cwe_capec'
         WHEN c.av_network = 1 AND c.pr_none = 1 THEN 'heuristic'
         ELSE 'none' END AS mapping_source
FROM raw.cves c
LEFT JOIN ctid k ON k.cve_id = c.cve_id AND k.rk = 1
LEFT JOIN cwe_pick w ON w.cwe_id = c.cwe_id AND w.rk = 1
LEFT JOIN landing.cwe_names n ON n.cwe_id = c.cwe_id
CROSS JOIN t1190 h
