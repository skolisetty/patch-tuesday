/* @bruin
name: raw.kev
type: duckdb.sql
description: |
  KEV entries visible to the world: rows of landing.kev with date_added on or before the world
  clock, loaded one world week at a time (time_interval on date_added). Labels and every
  KEV-derived feature are computed from this table only, never from landing.kev.
owner: patch-tuesday-pipeline
domains:
  - vulnerability-intel
tags:
  - raw
  - kev
depends:
  - raw.world_clock
  - landing.kev
materialization:
  type: table
  strategy: time_interval
  incremental_key: date_added
  time_granularity: date
columns:
  - name: cve_id
    type: VARCHAR
    description: CVE id of the KEV entry.
    extends: CVE.ID
    primary_key: true
    checks:
      - name: not_null
      - name: unique
  - name: date_added
    type: DATE
    description: Date CISA added the entry; the incremental key.
    extends: KEVEntry.DateAdded
    checks:
      - name: not_null
  - name: vendor_project
    type: VARCHAR
    description: Vendor or project named by CISA.
    checks:
      - name: not_null
  - name: product
    type: VARCHAR
    description: Product named by CISA.
  - name: vendor_norm
    type: VARCHAR
    description: vendor_project lower-cased with non-alphanumerics as underscores (fallback join key to CPE vendors).
    checks:
      - name: not_null
  - name: product_norm
    type: VARCHAR
    description: product normalised the same way.
  - name: kev_cwe_id
    type: VARCHAR
    description: First CWE listed by CISA for the entry, if any.
  - name: known_ransomware_campaign_use
    type: VARCHAR
    description: Known or Unknown.
    checks:
      - name: accepted_values
        value: ["Known", "Unknown"]
custom_checks:
  - name: no KEV row from the future
    description: Structural leakage control. No visible KEV entry is dated after the world clock.
    query: SELECT count(*) FROM raw.kev WHERE date_added > (SELECT asof_date FROM raw.world_clock)
    value: 0
    blocking: true
@bruin */

SELECT
    cve_id,
    date_added,
    vendor_project,
    product,
    trim(regexp_replace(lower(trim(vendor_project)), '[^a-z0-9]+', '_', 'g'), '_') AS vendor_norm,
    nullif(trim(regexp_replace(lower(trim(product)), '[^a-z0-9]+', '_', 'g'), '_'), '') AS product_norm,
    nullif(regexp_extract(coalesce(cwes, ''), '(CWE-[0-9]+)', 1), '') AS kev_cwe_id,
    known_ransomware_campaign_use
FROM landing.kev
WHERE date_added <= '{{ end_date }}'
{% if not full_refresh %}
  AND date_added >= '{{ start_date }}'
{% endif %}
