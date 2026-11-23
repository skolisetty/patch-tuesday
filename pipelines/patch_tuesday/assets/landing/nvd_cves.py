"""@bruin
name: landing.nvd_cves
image: python:3.12
connection: duckdb-default
description: |
  Flattened NVD 2.0 year feeds (CVE-ID years 2020-2026), one row per CVE, parsed once on the
  day-0 full refresh. This is the offline mirror of the future: rows here are NOT visible to the
  world until raw.cves copies them up to the world clock. Deliberately NOT extracted: description
  text (only boolean keyword flags are kept), reference URLs and tags, cisa* fields, SSVC.
  vuln_status and last_modified are kept for audit and never used as features.
  Known approximation: CVSS, CWE and CPE are as of the download date, not as of each snapshot.
owner: patch-tuesday-pipeline
domains:
  - vulnerability-intel
tags:
  - landing
  - nvd

materialization:
  type: table
  strategy: create+replace

parameters:
  enforce_schema: true

columns:
  - name: cve_id
    type: varchar
    description: CVE identifier.
    extends: CVE.ID
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: pattern
        value: "^CVE-[0-9]{4}-[0-9]{4,19}$"
  - name: published_date
    type: date
    description: UTC date of the NVD `published` timestamp.
    extends: CVE.PublishedDate
    checks:
      - name: not_null
  - name: last_modified_date
    type: date
    description: UTC date of NVD `lastModified` at download time. Audit only; never a feature.
  - name: vuln_status
    type: varchar
    description: NVD vulnStatus at download time (Analyzed, Modified, Deferred, Rejected, ...). Audit and Rejected filter only.
    checks:
      - name: not_null
  - name: feed_year
    type: integer
    description: Year of the NVD year feed the record came from (the CVE-ID year).
    checks:
      - name: not_null
  - name: vendor
    type: varchar
    description: Vendor from the first vulnerable CPE match, else the CNA affected block. Lower case, underscores.
    extends: CVE.Vendor
  - name: product
    type: varchar
    description: Product from the first vulnerable CPE match, else the CNA affected block. Lower case, underscores.
    extends: CVE.Product
  - name: cwe_id
    type: varchar
    description: Primary CWE id (NVD Primary weakness first).
    extends: CVE.CWE
  - name: cvss_version
    type: varchar
    description: CVSS version the score came from (3.1, 3.0, 4.0 or 2.0).
    checks:
      - name: accepted_values
        value: ["3.1", "3.0", "4.0", "2.0"]
  - name: cvss_base_score
    type: double
    description: CVSS base score.
    extends: CVE.CVSSBaseScore
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 10.0
  - name: cvss_vector
    type: varchar
    description: CVSS vector string of the chosen metric.
  - name: exploitability_score
    type: double
    description: CVSS exploitability sub-score (v3.x / v2 only).
    checks:
      - name: non_negative
  - name: impact_score
    type: double
    description: CVSS impact sub-score (v3.x / v2 only).
    checks:
      - name: non_negative
  - name: av_network
    type: integer
    description: 1 if the vector has AV:N.
    checks:
      - name: accepted_values
        value: [0, 1]
  - name: ac_low
    type: integer
    description: 1 if AC:L.
  - name: pr_none
    type: integer
    description: 1 if PR:N.
  - name: ui_none
    type: integer
    description: 1 if UI:N.
  - name: scope_changed
    type: integer
    description: 1 if S:C (v3.x only).
  - name: c_high
    type: integer
    description: 1 if C:H.
  - name: i_high
    type: integer
    description: 1 if I:H.
  - name: a_high
    type: integer
    description: 1 if A:H.
  - name: kw_rce
    type: integer
    description: 1 if the English description mentions remote or arbitrary code execution.
    checks:
      - name: accepted_values
        value: [0, 1]
  - name: kw_unauth
    type: integer
    description: 1 if the description mentions an unauthenticated attacker.
  - name: kw_cmd_injection
    type: integer
    description: 1 if the description mentions command injection.
  - name: kw_priv_esc
    type: integer
    description: 1 if the description mentions privilege escalation.
  - name: kw_auth_bypass
    type: integer
    description: 1 if the description mentions an authentication bypass.
  - name: kw_deserialization
    type: integer
    description: 1 if the description mentions deserialization.
  - name: kw_overflow
    type: integer
    description: 1 if the description mentions a buffer/heap/stack overflow or out-of-bounds write.

custom_checks:
  - name: every feed year 2020-2026 loaded
    description: The seven NVD year feeds must all be present.
    query: SELECT count(DISTINCT feed_year) FROM landing.nvd_cves WHERE feed_year BETWEEN 2020 AND 2026
    value: 7
    blocking: true
@bruin"""

import gzip
import json
import os
import re
import time

import pandas as pd

YEARS = range(2020, 2027)
FEED = "data/raw/nvd/nvdcve-2.0-{year}.json.gz"  # cwd is the repo root for Python assets

# Keyword flags only. Exploitation-status words ("exploited", "in the wild") are deliberately absent.
KEYWORDS = {
    "kw_rce": re.compile(r"remote code execution|arbitrary code execution|execute arbitrary (?:code|commands)", re.I),
    "kw_unauth": re.compile(r"unauthenticated|without authentication|no authentication", re.I),
    "kw_cmd_injection": re.compile(r"command injection|inject (?:arbitrary )?(?:os )?commands", re.I),
    "kw_priv_esc": re.compile(r"privilege escalation|escalate privileges|elevation of privilege|elevate privileges", re.I),
    "kw_auth_bypass": re.compile(r"authentication bypass|bypass authentication|bypass the authentication", re.I),
    "kw_deserialization": re.compile(r"deserializ", re.I),
    "kw_overflow": re.compile(r"buffer overflow|heap overflow|stack overflow|heap-based|stack-based|out-of-bounds write", re.I),
}
VECTOR_FLAGS = {
    "av_network": ("AV", "N"), "ac_low": ("AC", "L"), "pr_none": ("PR", "N"), "ui_none": ("UI", "N"),
    "scope_changed": ("S", "C"), "c_high": ("C", "H"), "i_high": ("I", "H"), "a_high": ("A", "H"),
}
BAD_CWE = {"NVD-CWE-Other", "NVD-CWE-noinfo"}


def _norm(text):
    if not text:
        return None
    text = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return text or None


def _pick_cvss(metrics):
    """v3.1 Primary, else first v3.1, else v3.0 (Primary first), else v4.0, else v2."""
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40", "cvssMetricV2"):
        entries = metrics.get(key) or []
        if not entries:
            continue
        primary = [e for e in entries if e.get("type") == "Primary"]
        e = (primary or entries)[0]
        data = e.get("cvssData", {})
        return {
            "cvss_version": str(data.get("version")) if data.get("version") else None,
            "cvss_base_score": data.get("baseScore"),
            "cvss_vector": data.get("vectorString"),
            "exploitability_score": e.get("exploitabilityScore"),
            "impact_score": e.get("impactScore"),
        }
    return {"cvss_version": None, "cvss_base_score": None, "cvss_vector": None,
            "exploitability_score": None, "impact_score": None}


def _vector_flags(vector):
    parts = {}
    for piece in (vector or "").split("/"):
        if ":" in piece:
            k, v = piece.split(":", 1)
            parts[k] = v
    # CVSS v2 uses Au:N for "no authentication" and C:C for complete impact.
    out = {}
    for name, (k, v) in VECTOR_FLAGS.items():
        val = parts.get(k)
        if name == "pr_none" and val is None and "Au" in parts:
            val = "N" if parts["Au"] == "N" else "X"
        if name in ("c_high", "i_high", "a_high") and val == "C":
            val = "H"
        out[name] = 1 if val == v else 0
    return out


def _vendor_product(cve):
    for conf in cve.get("configurations") or []:
        for node in conf.get("nodes") or []:
            for m in node.get("cpeMatch") or []:
                if m.get("vulnerable"):
                    f = m.get("criteria", "").split(":")
                    if len(f) > 5 and f[3] not in ("*", "-"):
                        return _norm(f[3]), _norm(f[4]) if f[4] not in ("*", "-") else None
    for aff in cve.get("affected") or []:
        for d in aff.get("affectedData") or []:
            for cpe in d.get("cpes") or []:
                f = cpe.split(":")
                if len(f) > 5 and f[3] not in ("*", "-"):
                    return _norm(f[3]), _norm(f[4]) if f[4] not in ("*", "-") else None
    for aff in cve.get("affected") or []:
        for d in aff.get("affectedData") or []:
            v, p = d.get("vendor"), d.get("product")
            if v and v.lower() not in ("n/a", "unknown"):
                return _norm(v), _norm(p) if p and p.lower() not in ("n/a", "unknown") else None
    return None, None


def _cwe(cve):
    ws = cve.get("weaknesses") or []
    ws = sorted(ws, key=lambda w: (w.get("type") != "Primary", w.get("source") != "nvd@nist.gov"))
    for w in ws:
        for d in w.get("description") or []:
            val = d.get("value")
            if val and val not in BAD_CWE and val.startswith("CWE-"):
                return val
    return None


def _row(cve, year):
    desc = next((d.get("value", "") for d in cve.get("descriptions") or [] if d.get("lang") == "en"), "")
    cvss = _pick_cvss(cve.get("metrics") or {})
    vendor, product = _vendor_product(cve)
    row = {
        "cve_id": cve["id"],
        "published_date": cve["published"][:10],
        "last_modified_date": (cve.get("lastModified") or "")[:10] or None,
        "vuln_status": cve.get("vulnStatus") or "Unknown",
        "feed_year": year,
        "vendor": vendor,
        "product": product,
        "cwe_id": _cwe(cve),
        **cvss,
        **_vector_flags(cvss["cvss_vector"]),
    }
    for name, rx in KEYWORDS.items():
        row[name] = 1 if rx.search(desc) else 0
    return row


def materialize(**kwargs):
    if os.environ.get("BRUIN_FULL_REFRESH") != "1":
        print("landing.nvd_cves: not a full refresh, keeping the table parsed on day 0")
        return None
    rows = []
    for year in YEARS:
        t0 = time.time()
        with gzip.open(FEED.format(year=year), "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
        n0 = len(rows)
        for item in doc["vulnerabilities"]:
            rows.append(_row(item["cve"], year))
        del doc
        print(f"landing.nvd_cves: {year}: {len(rows) - n0} records in {time.time() - t0:.1f}s")
    df = pd.DataFrame(rows)
    df = df.drop_duplicates("cve_id", keep="last")
    df["published_date"] = pd.to_datetime(df["published_date"]).dt.date
    df["last_modified_date"] = pd.to_datetime(df["last_modified_date"]).dt.date
    print(f"landing.nvd_cves: {len(df)} rows total")
    return df
