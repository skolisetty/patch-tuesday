/* @bruin
name: raw.cves
type: duckdb.sql
description: |
  CVEs visible to the world: NVD records published on or before the world clock, loaded
  incrementally one world week at a time (time_interval on published_date). Rejected records
  are dropped (they can never be exploited or enter KEV). Features, labels and the model read
  only raw.*, so a CVE published after the clock does not exist for them.
owner: patch-tuesday-pipeline
domains:
  - vulnerability-intel
tags:
  - raw
  - nvd
depends:
  - raw.world_clock
  - landing.nvd_cves
materialization:
  type: table
  strategy: time_interval
  incremental_key: published_date
  time_granularity: date
columns:
  - name: cve_id
    type: VARCHAR
    description: CVE identifier.
    extends: CVE.ID
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: pattern
        value: "^CVE-[0-9]{4}-[0-9]{4,19}$"
  - name: published_date
    type: DATE
    description: NVD publication date; the incremental key.
    extends: CVE.PublishedDate
    checks:
      - name: not_null
  - name: vuln_status
    type: VARCHAR
    description: NVD status at download time. Audit only.
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
  - name: cvss_version
    type: VARCHAR
    description: CVSS version of the chosen metric.
  - name: cvss_base_score
    type: DOUBLE
    description: CVSS base score.
    extends: CVE.CVSSBaseScore
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 10.0
  - name: exploitability_score
    type: DOUBLE
    description: CVSS exploitability sub-score.
  - name: impact_score
    type: DOUBLE
    description: CVSS impact sub-score.
  - name: av_network
    type: INTEGER
    description: 1 if AV:N.
    checks:
      - name: not_null
  - name: ac_low
    type: INTEGER
    description: 1 if AC:L.
  - name: pr_none
    type: INTEGER
    description: 1 if PR:N (v2 Au:N).
    checks:
      - name: not_null
  - name: ui_none
    type: INTEGER
    description: 1 if UI:N.
  - name: scope_changed
    type: INTEGER
    description: 1 if S:C.
  - name: c_high
    type: INTEGER
    description: 1 if C:H.
  - name: i_high
    type: INTEGER
    description: 1 if I:H.
  - name: a_high
    type: INTEGER
    description: 1 if A:H.
  - name: kw_rce
    type: INTEGER
    description: Description keyword flag, code execution.
  - name: kw_unauth
    type: INTEGER
    description: Description keyword flag, unauthenticated.
  - name: kw_cmd_injection
    type: INTEGER
    description: Description keyword flag, command injection.
  - name: kw_priv_esc
    type: INTEGER
    description: Description keyword flag, privilege escalation.
  - name: kw_auth_bypass
    type: INTEGER
    description: Description keyword flag, authentication bypass.
  - name: kw_deserialization
    type: INTEGER
    description: Description keyword flag, deserialization.
  - name: kw_overflow
    type: INTEGER
    description: Description keyword flag, overflow / out-of-bounds write.
custom_checks:
  - name: no CVE from the future
    description: Structural leakage control. No visible CVE is published after the world clock.
    query: SELECT count(*) FROM raw.cves WHERE published_date > (SELECT asof_date FROM raw.world_clock)
    value: 0
    blocking: true
  - name: no rejected records
    description: Rejected CVE records are not vulnerabilities.
    query: SELECT count(*) FROM raw.cves WHERE vuln_status = 'Rejected'
    value: 0
    blocking: true
@bruin */

SELECT
    cve_id, published_date, vuln_status, vendor, product, cwe_id,
    cvss_version, cvss_base_score, exploitability_score, impact_score,
    av_network, ac_low, pr_none, ui_none, scope_changed, c_high, i_high, a_high,
    kw_rce, kw_unauth, kw_cmd_injection, kw_priv_esc, kw_auth_bypass, kw_deserialization, kw_overflow
FROM landing.nvd_cves
WHERE vuln_status <> 'Rejected'
  AND published_date <= '{{ end_date }}'
{% if not full_refresh %}
  AND published_date >= '{{ start_date }}'
{% endif %}
