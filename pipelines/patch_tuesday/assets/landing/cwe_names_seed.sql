/* @bruin
# Seed asset defined in a SQL comment header (see kev_seed.sql for why it is not a .asset.yml).
name: landing.cwe_names
type: duckdb.seed
description: |
  Hand-written short weakness-class labels for common CWE ids (project-authored, not copied from
  the CWE catalogue). Used only to build the abstract card title "<Vendor> <Product>: <weakness class>".
owner: patch-tuesday-pipeline
domains:
  - threat-mapping
tags:
  - landing
  - reference

parameters:
  path: cwe_names.csv

columns:
  - name: cwe_id
    type: varchar
    description: CWE id.
    extends: CVE.CWE
    primary_key: true
    checks:
      - name: not_null
      - name: unique
      - name: pattern
        value: "^CWE-[0-9]+$"
  - name: weakness_class
    type: varchar
    description: Short lower-case label for the weakness class.
    checks:
      - name: not_null
@bruin */
