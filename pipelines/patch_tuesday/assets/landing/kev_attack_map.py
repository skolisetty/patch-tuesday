"""@bruin
name: landing.kev_attack_map
image: python:3.12
connection: duckdb-default
description: |
  Center for Threat-Informed Defense (CTID) Mappings Explorer: KEV CVE to ATT&CK 16.1 mappings
  (KEV snapshot 2025-07-28, 1,183 mapping objects). Only ids, names and mapping type are kept;
  free-text comments and reference URLs are dropped. Used only for CVEs already in raw.kev, so
  it never reveals a future KEV addition. mapping_source is never a model feature.
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
  - name: cve_id
    type: varchar
    description: KEV CVE id.
    extends: CVE.ID
    checks:
      - name: not_null
      - name: pattern
        value: "^CVE-[0-9]{4}-[0-9]{4,19}$"
  - name: technique_id
    type: varchar
    description: ATT&CK technique id from the mapping (ATT&CK 16.1 ids).
    checks:
      - name: not_null
  - name: technique_name
    type: varchar
    description: Technique name as given in the mapping file.
  - name: mapping_type
    type: varchar
    description: CTID mapping type (exploitation_technique, primary_impact, secondary_impact, uncategorized).
    checks:
      - name: not_null
  - name: mapping_priority
    type: integer
    description: 1 for exploitation_technique, 2 primary_impact, 3 secondary_impact, 9 other. Lower is preferred.
    checks:
      - name: positive

custom_checks:
  - name: mapping file loaded
    description: The CTID file has about 1,183 mapping objects.
    query: SELECT CAST(count(*) > 1000 AS INTEGER) FROM landing.kev_attack_map
    value: 1
    blocking: true
@bruin"""

import json
import os

import pandas as pd

MAPPING = "data/raw/mappings/kev_attack-16.1-enterprise.json"
PRIORITY = {"exploitation_technique": 1, "primary_impact": 2, "secondary_impact": 3}


def materialize(**kwargs):
    if os.environ.get("BRUIN_FULL_REFRESH") != "1":
        print("landing.kev_attack_map: not a full refresh, keeping the day-0 table")
        return None
    with open(MAPPING, encoding="utf-8") as fh:
        objs = json.load(fh)["mapping_objects"]
    rows = []
    for m in objs:
        cve, tid = m.get("capability_id"), m.get("attack_object_id")
        if not cve or not tid or not str(cve).startswith("CVE-"):
            continue
        mtype = m.get("mapping_type") or "uncategorized"
        rows.append({
            "cve_id": cve.strip(),
            "technique_id": tid.strip(),
            "technique_name": m.get("attack_object_name"),
            "mapping_type": mtype,
            "mapping_priority": PRIORITY.get(mtype, 9),
        })
    df = pd.DataFrame(rows).drop_duplicates(["cve_id", "technique_id", "mapping_type"])
    print(f"landing.kev_attack_map: {len(df)} rows for {df['cve_id'].nunique()} CVEs")
    return df
