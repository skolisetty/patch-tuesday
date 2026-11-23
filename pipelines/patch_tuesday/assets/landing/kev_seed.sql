/* @bruin
# Seed asset (type duckdb.seed) defined in a SQL comment header instead of kev.asset.yml:
# on the /mnt/c drvfs mount every file has mode 777, and Bruin's valid-executable-file rule
# rejects a YAML asset definition with execute bits. Comment-header definitions are exempt.
name: landing.kev
type: duckdb.seed
description: |
  The full CISA Known Exploited Vulnerabilities catalogue (catalogVersion 2026.11.20, 1,790 rows),
  loaded from kev.csv (a byte copy of data/raw/kev/known_exploited_vulnerabilities.csv, CC0).
  This is the offline mirror of the future: only raw.kev (rows dated up to the world clock) and the
  end-of-match reveal (sim.reveal) may read it. Bruin renames the camelCase headers to snake_case
  (cveID -> cve_id, dateAdded -> date_added). Column checks are written as custom checks because
  Bruin validates declared seed columns against the CSV header names but runs column checks
  against the renamed columns, so no declared name can satisfy both.
owner: patch-tuesday-pipeline
domains:
  - vulnerability-intel
tags:
  - landing
  - kev

parameters:
  path: kev.csv

custom_checks:
  - name: kev catalogue has 1790 rows
    description: Row count of catalogVersion 2026.11.20.
    query: SELECT count(*) FROM landing.kev
    value: 1790
    blocking: true
  - name: cve_id is not null and unique
    description: Column check written as SQL (see description).
    query: SELECT count(*) - count(DISTINCT cve_id) FROM landing.kev
    value: 0
    blocking: true
  - name: cve_id matches the KEV schema pattern
    description: The CVE id pattern from the KEV JSON schema.
    query: SELECT count(*) FROM landing.kev WHERE NOT regexp_full_match(cve_id, 'CVE-[0-9]{4}-[0-9]{4,19}')
    value: 0
    blocking: true
  - name: date_added is a non-null date
    description: Every entry has a KEV date.
    query: SELECT count(*) FROM landing.kev WHERE date_added IS NULL
    value: 0
    blocking: true
  - name: ransomware flag is Known or Unknown
    description: accepted_values written as SQL.
    query: SELECT count(*) FROM landing.kev WHERE known_ransomware_campaign_use NOT IN ('Known', 'Unknown')
    value: 0
    blocking: true
@bruin */
