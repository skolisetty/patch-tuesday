"""@bruin
name: landing.cwe_attack_map
image: python:3.12
connection: duckdb-default
description: |
  CWE to ATT&CK technique pairs derived from MITRE CAPEC 2.1 STIX (stix-capec.json): every
  non-deprecated CAPEC attack pattern links its related CWEs (source_name "cwe") to its ATT&CK
  techniques (source_name "ATTACK"). n_capec counts how many CAPEC patterns support a pair, and
  pair_rank orders the techniques of one CWE (1 = best supported). Static reference data.
owner: patch-tuesday-pipeline
domains:
  - threat-mapping
tags:
  - landing
  - attack

materialization:
  type: table
  strategy: create+replace

parameters:
  enforce_schema: true

columns:
  - name: cwe_id
    type: varchar
    description: CWE id (CWE-NNN).
    extends: CVE.CWE
    checks:
      - name: not_null
      - name: pattern
        value: "^CWE-[0-9]+$"
  - name: technique_id
    type: varchar
    description: ATT&CK technique id named by the CAPEC pattern.
    checks:
      - name: not_null
  - name: n_capec
    type: integer
    description: Number of CAPEC patterns linking this CWE to this technique.
    checks:
      - name: positive
  - name: pair_rank
    type: integer
    description: Rank of the technique within the CWE (n_capec desc, then technique id).
    checks:
      - name: positive

custom_checks:
  - name: one row per cwe and technique
    description: Pairs are aggregated, so (cwe_id, technique_id) is unique.
    query: SELECT count(*) - count(DISTINCT (cwe_id, technique_id)) FROM landing.cwe_attack_map
    value: 0
    blocking: true
@bruin"""

import collections
import json
import os

import pandas as pd

CAPEC = "data/raw/mappings/stix-capec.json"


def materialize(**kwargs):
    if os.environ.get("BRUIN_FULL_REFRESH") != "1":
        print("landing.cwe_attack_map: not a full refresh, keeping the day-0 table")
        return None
    with open(CAPEC, encoding="utf-8") as fh:
        objects = json.load(fh)["objects"]
    pairs = collections.Counter()
    for o in objects:
        if o.get("type") != "attack-pattern" or o.get("revoked"):
            continue
        if o.get("x_capec_status") in ("Deprecated", "Obsolete"):
            continue
        refs = o.get("external_references", [])
        cwes = {r["external_id"] for r in refs if r.get("source_name") == "cwe" and r.get("external_id")}
        techs = set()
        for r in refs:
            if r.get("source_name") == "ATTACK" and r.get("external_id"):
                tid = r["external_id"].strip()
                techs.add(tid if tid.startswith("T") else "T" + tid)
        for cwe in cwes:
            for tid in techs:
                pairs[(cwe, tid)] += 1
    rows = [{"cwe_id": c, "technique_id": t, "n_capec": n} for (c, t), n in pairs.items()]
    df = pd.DataFrame(rows).sort_values(["cwe_id", "n_capec", "technique_id"], ascending=[True, False, True])
    df["pair_rank"] = df.groupby("cwe_id").cumcount() + 1
    print(f"landing.cwe_attack_map: {len(df)} pairs for {df['cwe_id'].nunique()} CWEs")
    return df.reset_index(drop=True)
