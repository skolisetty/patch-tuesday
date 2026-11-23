"""@bruin
name: landing.attack_techniques
image: python:3.12
connection: duckdb-default
description: |
  MITRE ATT&CK Enterprise 20.1 techniques and sub-techniques (active only: revoked and
  deprecated objects are dropped), with their first tactic. Static reference data.
  ATT&CK is (c) The MITRE Corporation, used under the licence in LICENSES/ATTACK-LICENSE.txt.
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
  - name: technique_id
    type: varchar
    description: ATT&CK technique id.
    extends: Technique.ID
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: pattern
        value: "^T[0-9]{4}(\\.[0-9]{3})?$"
  - name: technique_name
    type: varchar
    description: ATT&CK technique name.
    extends: Technique.Name
    checks:
      - name: not_null
  - name: tactic
    type: varchar
    description: First tactic (kill chain phase name) of the technique.
    extends: Technique.Tactic
  - name: tactics
    type: varchar
    description: All tactics, comma separated.
  - name: is_subtechnique
    type: boolean
    description: True for a sub-technique (Txxxx.yyy).
    checks:
      - name: not_null
  - name: parent_technique_id
    type: varchar
    description: Parent technique id (the part before the dot).
    checks:
      - name: not_null

custom_checks:
  - name: bundle has hundreds of techniques
    description: Guards against an empty or truncated bundle.
    query: SELECT CAST(count(*) > 500 AS INTEGER) FROM landing.attack_techniques
    value: 1
    blocking: true
@bruin"""

import json
import os

import pandas as pd

BUNDLE = "data/raw/attack/enterprise-attack-20.1.json"


def materialize(**kwargs):
    if os.environ.get("BRUIN_FULL_REFRESH") != "1":
        print("landing.attack_techniques: not a full refresh, keeping the day-0 table")
        return None
    with open(BUNDLE, encoding="utf-8") as fh:
        objects = json.load(fh)["objects"]
    rows = {}
    for o in objects:
        if o.get("type") != "attack-pattern" or o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        tid = next((r.get("external_id") for r in o.get("external_references", [])
                    if r.get("source_name") == "mitre-attack"), None)
        if not tid:
            continue
        phases = [p["phase_name"] for p in o.get("kill_chain_phases", []) if p.get("kill_chain_name") == "mitre-attack"]
        rows[tid] = {
            "technique_id": tid,
            "technique_name": o.get("name"),
            "tactic": phases[0] if phases else None,
            "tactics": ",".join(phases) if phases else None,
            "is_subtechnique": "." in tid,
            "parent_technique_id": tid.split(".")[0],
        }
    df = pd.DataFrame(sorted(rows.values(), key=lambda r: r["technique_id"]))
    print(f"landing.attack_techniques: {len(df)} active techniques")
    return df
