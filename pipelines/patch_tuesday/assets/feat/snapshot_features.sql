/* @bruin
name: feat.snapshot_features
type: duckdb.sql
description: |
  One row per (snapshot, CVE): numeric features computed strictly as of the snapshot date, plus
  the label. Candidates at snapshot S are CVEs in raw.cves published on or before S; train and
  valid candidates must not be in raw.kev on or before S. y = 1 iff the CVE enters raw.kev in
  (S, S + H]; y is null for serve. EPSS is the latest file dated on or before S; every KEV- and
  CVE-history aggregate filters on dates <= S. Training rows keep every positive and a
  deterministic neg_sample_pct percent hash sample of negatives; valid and serve keep everything.
  Serve rows also include CVEs already in KEV (flag in_kev_asof) so the whole visible world can be
  scored. Excluded on purpose: cisa* fields, references, SSVC, vuln_status, last_modified,
  mapping_source and exploitation-status words in descriptions.
owner: patch-tuesday-pipeline
domains:
  - exploit-prediction
tags:
  - feat
  - leakage-controlled
depends:
  - feat.snapshots
  - raw.world_clock
  - raw.cves
  - raw.kev
  - raw.epss
materialization:
  type: table
  strategy: create+replace
columns:
  - name: role
    type: VARCHAR
    description: serve, valid or train.
    extends: Snapshot.Role
    checks:
      - name: not_null
      - name: accepted_values
        value: ["serve", "valid", "train"]
  - name: snapshot_date
    type: DATE
    description: As-of date of the features.
    extends: Snapshot.Date
    checks:
      - name: not_null
  - name: label_end
    type: DATE
    description: End of the label window; null for serve.
  - name: cve_id
    type: VARCHAR
    description: Candidate CVE.
    extends: CVE.ID
    checks:
      - name: not_null
  - name: published_date
    type: DATE
    description: NVD publication date (audit; the feature is age_days).
    extends: CVE.PublishedDate
    checks:
      - name: not_null
  - name: y
    type: INTEGER
    description: 1 if the CVE enters KEV in (snapshot_date, label_end]; null for serve.
    checks:
      - name: accepted_values
        value: [0, 1]
  - name: in_kev_asof
    type: BOOLEAN
    description: In raw.kev on or before snapshot_date (always false for train and valid).
    checks:
      - name: not_null
  - name: epss_date
    type: DATE
    description: Date of the EPSS file used for the snapshot (latest on or before it).
  - name: age_days
    type: INTEGER
    description: Days from publication to the snapshot.
    checks:
      - name: not_null
      - name: non_negative
  - name: cvss_base_score
    type: DOUBLE
    description: CVSS base score.
  - name: exploitability_score
    type: DOUBLE
    description: CVSS exploitability sub-score.
  - name: impact_score
    type: DOUBLE
    description: CVSS impact sub-score.
  - name: has_cvss
    type: INTEGER
    description: 1 if a CVSS score exists.
    checks:
      - name: accepted_values
        value: [0, 1]
  - name: av_network
    type: INTEGER
    description: AV:N flag.
  - name: ac_low
    type: INTEGER
    description: AC:L flag.
  - name: pr_none
    type: INTEGER
    description: PR:N flag.
  - name: ui_none
    type: INTEGER
    description: UI:N flag.
  - name: scope_changed
    type: INTEGER
    description: S:C flag.
  - name: c_high
    type: INTEGER
    description: C:H flag.
  - name: i_high
    type: INTEGER
    description: I:H flag.
  - name: a_high
    type: INTEGER
    description: A:H flag.
  - name: epss_asof
    type: DOUBLE
    description: EPSS score in the file dated epss_date.
    extends: EPSSScore.Score
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: epss_percentile_asof
    type: DOUBLE
    description: EPSS percentile in the same file.
    extends: EPSSScore.Percentile
  - name: epss_delta_28d
    type: DOUBLE
    description: epss_asof minus the EPSS score in the latest file dated on or before snapshot_date - 28.
  - name: vendor_kev_count_asof
    type: BIGINT
    description: KEV entries with date_added <= snapshot_date for the same vendor.
    checks:
      - name: non_negative
  - name: vendor_cve_count_asof
    type: BIGINT
    description: Visible CVEs published <= snapshot_date for the same vendor.
    checks:
      - name: non_negative
  - name: vendor_kev_ratio_asof
    type: DOUBLE
    description: vendor_kev_count_asof / (vendor_cve_count_asof + 1).
    checks:
      - name: non_negative
  - name: product_kev_count_asof
    type: BIGINT
    description: KEV entries with date_added <= snapshot_date for the same vendor and product.
    checks:
      - name: non_negative
  - name: cwe_kev_count_asof
    type: BIGINT
    description: KEV entries with date_added <= snapshot_date sharing the CWE.
    checks:
      - name: non_negative
  - name: kw_rce
    type: INTEGER
    description: Description flag, code execution.
  - name: kw_unauth
    type: INTEGER
    description: Description flag, unauthenticated.
  - name: kw_cmd_injection
    type: INTEGER
    description: Description flag, command injection.
  - name: kw_priv_esc
    type: INTEGER
    description: Description flag, privilege escalation.
  - name: kw_auth_bypass
    type: INTEGER
    description: Description flag, authentication bypass.
  - name: kw_deserialization
    type: INTEGER
    description: Description flag, deserialization.
  - name: kw_overflow
    type: INTEGER
    description: Description flag, overflow.
custom_checks:
  - name: no label window ends after the world clock
    description: Leakage control. Every train/valid label is fully observed by the clock.
    query: |
      SELECT count(*) FROM feat.snapshot_features
      WHERE role IN ('train', 'valid') AND label_end > (SELECT asof_date FROM raw.world_clock)
    value: 0
    blocking: true
  - name: serve rows carry no label
    description: Leakage control. The serve snapshot is the present; its outcome is unknown.
    query: SELECT count(*) FROM feat.snapshot_features WHERE role = 'serve' AND y IS NOT NULL
    value: 0
    blocking: true
  - name: no candidate published after its snapshot
    description: Leakage control. A snapshot only sees CVEs published by its date.
    query: SELECT count(*) FROM feat.snapshot_features WHERE published_date > snapshot_date
    value: 0
    blocking: true
  - name: no EPSS file dated after its snapshot
    description: Leakage control. EPSS features come from a file dated on or before the snapshot.
    query: SELECT count(*) FROM feat.snapshot_features WHERE epss_date > snapshot_date
    value: 0
    blocking: true
  - name: train and valid candidates are not yet in KEV
    description: The task is predicting new KEV entries; already-listed CVEs are not candidates.
    query: SELECT count(*) FROM feat.snapshot_features WHERE role <> 'serve' AND in_kev_asof
    value: 0
    blocking: true
  - name: positives are inside their label window
    description: Recomputes every positive label from raw.kev and checks it lies in (snapshot, label_end].
    query: |
      SELECT count(*) FROM feat.snapshot_features f
      LEFT JOIN raw.kev k ON k.cve_id = f.cve_id
      WHERE f.y = 1 AND NOT (k.date_added > f.snapshot_date AND k.date_added <= f.label_end)
    value: 0
    blocking: true
  - name: one row per snapshot and CVE
    description: (snapshot_date, cve_id) is unique.
    query: SELECT count(*) - count(DISTINCT (snapshot_date, cve_id)) FROM feat.snapshot_features
    value: 0
    blocking: true
  - name: serve snapshot matches the world clock
    description: The serve rows are for the current clock.
    query: |
      SELECT CAST(count(*) > 0 AS INTEGER) FROM feat.snapshot_features
      WHERE role = 'serve' AND snapshot_date = (SELECT asof_date FROM raw.world_clock)
    value: 1
    blocking: true
@bruin */

WITH epss_dates AS (
    SELECT DISTINCT score_date FROM raw.epss
),
snaps AS (
    SELECT s.role, s.snapshot_date, s.label_end,
           (SELECT max(d.score_date) FROM epss_dates d WHERE d.score_date <= s.snapshot_date)      AS epss_date,
           (SELECT max(d.score_date) FROM epss_dates d WHERE d.score_date <= s.snapshot_date - 28) AS epss_prev_date
    FROM feat.snapshots s
),
-- KEV history keys: NVD vendor/product/CWE when the KEV CVE was already published by its KEV date,
-- else CISA's own names. Both are fixed facts about the entry, so the keys do not move with the clock.
kev_keys AS (
    SELECT k.cve_id, k.date_added,
           coalesce(c.vendor, k.vendor_norm)   AS vendor,
           coalesce(c.product, k.product_norm) AS product,
           coalesce(c.cwe_id, k.kev_cwe_id)    AS cwe_id
    FROM raw.kev k
    LEFT JOIN raw.cves c ON c.cve_id = k.cve_id AND c.published_date <= k.date_added
),
vendor_kev AS (
    SELECT s.snapshot_date, kk.vendor, count(*) AS n
    FROM snaps s JOIN kev_keys kk ON kk.date_added <= s.snapshot_date
    WHERE kk.vendor IS NOT NULL
    GROUP BY s.snapshot_date, kk.vendor
),
product_kev AS (
    SELECT s.snapshot_date, kk.vendor, kk.product, count(*) AS n
    FROM snaps s JOIN kev_keys kk ON kk.date_added <= s.snapshot_date
    WHERE kk.vendor IS NOT NULL AND kk.product IS NOT NULL
    GROUP BY s.snapshot_date, kk.vendor, kk.product
),
cwe_kev AS (
    SELECT s.snapshot_date, kk.cwe_id, count(*) AS n
    FROM snaps s JOIN kev_keys kk ON kk.date_added <= s.snapshot_date
    WHERE kk.cwe_id IS NOT NULL
    GROUP BY s.snapshot_date, kk.cwe_id
),
vendor_cve AS (
    SELECT s.snapshot_date, c.vendor, count(*) AS n
    FROM snaps s JOIN raw.cves c ON c.published_date <= s.snapshot_date
    WHERE c.vendor IS NOT NULL
    GROUP BY s.snapshot_date, c.vendor
),
cand AS (
    SELECT s.role, s.snapshot_date, s.label_end, s.epss_date, s.epss_prev_date,
           c.cve_id, c.published_date, c.vendor, c.product, c.cwe_id,
           c.cvss_base_score, c.exploitability_score, c.impact_score,
           c.av_network, c.ac_low, c.pr_none, c.ui_none, c.scope_changed, c.c_high, c.i_high, c.a_high,
           c.kw_rce, c.kw_unauth, c.kw_cmd_injection, c.kw_priv_esc, c.kw_auth_bypass, c.kw_deserialization, c.kw_overflow,
           k.date_added AS kev_date_added   -- used for the label and the candidate filter only, never stored
    FROM snaps s
    JOIN raw.cves c ON c.published_date <= s.snapshot_date
    LEFT JOIN raw.kev k ON k.cve_id = c.cve_id
),
labelled AS (
    SELECT *,
           coalesce(kev_date_added <= snapshot_date, false) AS in_kev_asof,
           CASE WHEN role = 'serve' THEN NULL
                WHEN kev_date_added > snapshot_date AND kev_date_added <= label_end THEN 1
                ELSE 0 END AS y
    FROM cand
),
kept AS (
    SELECT * FROM labelled
    WHERE role = 'serve'
       OR (NOT in_kev_asof
           AND (role = 'valid'
                OR y = 1
                OR hash(cve_id || '|' || CAST(snapshot_date AS VARCHAR)) % 100 < {{ var.neg_sample_pct }}))
)
SELECT
    f.role,
    f.snapshot_date,
    f.label_end,
    f.cve_id,
    f.published_date,
    CAST(f.y AS INTEGER)                                         AS y,
    f.in_kev_asof,
    f.epss_date,
    CAST(date_diff('day', f.published_date, f.snapshot_date) AS INTEGER) AS age_days,
    f.cvss_base_score,
    f.exploitability_score,
    f.impact_score,
    CAST(f.cvss_base_score IS NOT NULL AS INTEGER)               AS has_cvss,
    f.av_network, f.ac_low, f.pr_none, f.ui_none, f.scope_changed, f.c_high, f.i_high, f.a_high,
    e0.epss                                                      AS epss_asof,
    e0.percentile                                                AS epss_percentile_asof,
    e0.epss - e1.epss                                            AS epss_delta_28d,
    coalesce(vk.n, 0)                                            AS vendor_kev_count_asof,
    coalesce(vc.n, 0)                                            AS vendor_cve_count_asof,
    coalesce(vk.n, 0) / (coalesce(vc.n, 0) + 1.0)                AS vendor_kev_ratio_asof,
    coalesce(pk.n, 0)                                            AS product_kev_count_asof,
    coalesce(ck.n, 0)                                            AS cwe_kev_count_asof,
    f.kw_rce, f.kw_unauth, f.kw_cmd_injection, f.kw_priv_esc, f.kw_auth_bypass, f.kw_deserialization, f.kw_overflow
FROM kept f
LEFT JOIN raw.epss e0     ON e0.cve_id = f.cve_id AND e0.score_date = f.epss_date
LEFT JOIN raw.epss e1     ON e1.cve_id = f.cve_id AND e1.score_date = f.epss_prev_date
LEFT JOIN vendor_kev vk   ON vk.snapshot_date = f.snapshot_date AND vk.vendor = f.vendor
LEFT JOIN vendor_cve vc   ON vc.snapshot_date = f.snapshot_date AND vc.vendor = f.vendor
LEFT JOIN product_kev pk  ON pk.snapshot_date = f.snapshot_date AND pk.vendor = f.vendor AND pk.product = f.product
LEFT JOIN cwe_kev ck      ON ck.snapshot_date = f.snapshot_date AND ck.cwe_id = f.cwe_id
