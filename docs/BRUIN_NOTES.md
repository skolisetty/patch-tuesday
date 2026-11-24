# Bruin notes: what v0.11.831 really does

> A lab notebook written before the pipeline was built: small experiments that show how Bruin behaves, for readers who use Bruin themselves.
> You do not need it to play or to understand the project. How the project uses Bruin is in [PIPELINE.md](PIPELINE.md).

Every statement here was run on 2026-11-23 with Bruin v0.11.831 in WSL Ubuntu 24.04, in a throwaway repo at `~/bruin-smoke` (DuckDB file `~/bruin-smoke/smoke.duckdb`, pipeline `pipelines/smoke`). This file replaces every `[U]` and `[X]` Bruin item in `design.md`. Anything not run is listed under "Not tested" at the end.

Sample data: the KEV CSV (1,790 rows) and `epss_scores-2026-03-22.csv.gz` / `-2026-03-29.csv.gz` (321,918 and 323,647 rows).

## Verdicts at a glance

| | Question | Verdict |
|---|---|---|
| a | Python `materialize()` into DuckDB | Works. Columns are renamed to snake_case and two `_ingestr_*` columns are added |
| a | Returning `None` | Skips cleanly with a warning, but the table must already exist or its checks fail |
| a | Column checks and `custom_checks` in a `.py` asset | Both work |
| a | `from bruin import query` reading an earlier SQL asset's table | Works with `--workers 1` |
| b | `duckdb.seed` over the KEV CSV | Works. Headers become snake_case (`cveID` -> `cve_id`); dates are typed `DATE` |
| b | Parquet and JSONL seeds | Both work. JSONL columns come out in alphabetical order |
| c | `time_interval` + `read_csv` on `.csv.gz` with glob, `skip`, `filename` | Works exactly as in design.md |
| c | Relative paths in SQL | Resolve against the directory `bruin` is started from, not the repo root |
| d | Single `COPY ... TO` asset, single `INSERT INTO` asset, `strategy: ddl` | All work |
| d | Multi-statement SQL in an unmaterialised asset | Works, but Bruin does not create the schema for it |
| e | Failed blocking custom check | Table is still written, downstream is skipped, exit code 1 |
| e | Jinja in `custom_checks.query` | Renders |
| e | `retries: 0` and `tags:` list | Both validate |
| f | `--tag serve` with untagged upstreams | Runs only the tagged assets; upstreams are not run and not checked |
| g | Database under `/mnt/c` | Works, no lock problem seen |
| g | Embedded DuckDB | v1.5.2 (Python assets get pip `duckdb` 2.0.1, which reads and writes the same file fine) |
| h | `bruin validate` | Catches structure errors; exit 1 in plain mode, exit 0 with `-o json` |

## (a) Python assets

**Verdict: `materialize()` with `connection: duckdb-default` works; no fallback is needed.**

Working asset (`pipelines/smoke/assets/py/py_table.py`), with `pipelines/smoke/requirements.txt` holding the two lines `bruin-sdk[duckdb]` and `pandas`:

```python
"""@bruin
name: py.py_table
image: python:3.12
connection: duckdb-default

depends:
  - raw.world_clock

materialization:
  type: table
  strategy: create+replace

columns:
  - name: cve_id
    type: varchar
    checks:
      - name: not_null
      - name: unique
  - name: model_score
    type: double
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0

custom_checks:
  - name: py table has three rows
    query: SELECT count(*) FROM py.py_table
    value: 3
    blocking: true
@bruin"""

import os
import pandas as pd
from bruin import query


def materialize(**kwargs):
    clock = query("SELECT asof_date, window_start, pipeline_run_id FROM raw.world_clock")
    asof = os.environ["BRUIN_END_DATE"]
    return pd.DataFrame({...})
```

Command and decisive output:

```
bruin run --full-refresh --workers 1 --start-date 2020-01-01 --end-date "2026-03-22 23:59:59.999999" pipelines/smoke/assets/py/py_table.py
[00:36:38] Successfully collected the data from the asset, uploading to the destination...
[00:36:38] Successfully loaded the data from the asset into the destination.
[00:36:38] Finished: py.py_table (17.533s)
PASS py.py_table .....
 ✓ Quality checks       5 succeeded
```

**Column renaming and metadata columns.** The frame had columns `cve_id, model_score, camelCaseCol, "Mixed Name-X", asof_date (str), snapshot_date (python date), flag (bool), run_id`. `DESCRIBE py.py_table` gave:

```
cve_id VARCHAR, model_score DOUBLE, camel_case_col BIGINT, mixed_name_x VARCHAR,
asof_date VARCHAR, snapshot_date DATE, flag BOOLEAN, run_id VARCHAR,
_ingestr_loaded_at TIMESTAMP WITH TIME ZONE, _ingestr_run_id VARCHAR
```

- Names are forced to snake_case. Use lowercase snake_case in the frame so nothing changes.
- `_ingestr_loaded_at` and `_ingestr_run_id` are always appended. Never `SELECT *` from a Python-materialised table (or a seed) into a served file.
- Without `enforce_schema`, a declared `type: integer` still lands as `BIGINT` (types are inferred from the frame). A date held as a string stays `VARCHAR`.
- With `parameters: enforce_schema: true`, declared types are applied (`integer` -> `INTEGER`, a `YYYY-MM-DD` string declared `date` -> `DATE`). A frame column that is not declared is still loaded.

**Timing.** First ever Python run: 17.5 s for the asset, 19.6 s wall (uv 0.11.33 download about 3 s, then pyarrow, duckdb, numpy, pandas). Later runs: 1.0 to 1.4 s per Python asset. A 323,647-row frame with `enforce_schema` took 1.4 s in total; `query()` read those rows in 0.19 s. Loading is not slow, so there is no need to cap rows. An asset with no `image:` line downloaded the packages a second time, so give every Python asset the same `image: python:3.12`. The first seed run also installs ingestr 1.1.95 (about 9 s, once). Adding `scikit-learn` to a pipeline's `requirements.txt` (separate pipeline `pipelines/chk`, uv cache already warm for the rest): the first run downloaded scikit-learn and scipy and finished the asset in 9.6 s (11 s wall, exit 0); `sklearn.__version__` was `1.9.1`. Bruin used the `requirements.txt` in that pipeline's folder (the nearest one up from the asset).

**Returning `None`.**

```
[py.py_none] WARNING: materialize() returned None, skipping materialization
Finished: py.py_none (559ms)
PASS py.py_none .
```

The existing table is left untouched and its checks still run against it. Gotcha: if the table has never been created, the checks fail (`Catalog Error: Table with name py_none does not exist!`), the asset is marked failed and downstream is skipped with exit 1. So the day-0 `--full-refresh` run must return rows; `os.environ["BRUIN_FULL_REFRESH"] == "1"` is the right switch.

**`query()`.** Reads a table written by an earlier SQL asset in the same run (`raw.world_clock` showed the current run id). `DATE` columns arrive as `datetime64[us]`, strings as pandas `str` dtype (pandas 3.1.1). Cast before comparing with a date string.

**Working directory and environment.** `os.getcwd()` is the repo root (`/home/<you>/bruin-smoke`), even when `bruin` was started from `pipelines/smoke`. `__file__` is the real asset path. `materialize()` receives no kwargs. Environment seen in a full-refresh run:

```
BRUIN_ASSET='py.py_table'            BRUIN_THIS='py.py_table'
BRUIN_PIPELINE='smoke'               BRUIN_ENVIRONMENT='default'
BRUIN_RUN_ID='2026_11_23_00_36_21'   BRUIN_COMMIT_HASH=''   (repo had no commits)
BRUIN_CONNECTION='duckdb-default'    BRUIN_CONNECTION_TYPES='{"duckdb-default":"duckdb"}'
BRUIN_START_DATE='2020-01-01'        BRUIN_END_DATE='2026-03-22'
BRUIN_START_DATETIME='2020-01-01T00:00:00'           BRUIN_END_DATETIME='2026-03-22T23:59:59'
BRUIN_START_TIMESTAMP='2020-01-01T00:00:00.000000Z'  BRUIN_END_TIMESTAMP='2026-03-22T23:59:59.999999Z'
BRUIN_EXECUTION_DATE='2026-11-23'    BRUIN_EXECUTION_DATETIME='2026-11-23T08:36:21'
BRUIN_EXECUTION_TIMESTAMP='2026-11-23T08:36:21.317266Z'
BRUIN_FULL_REFRESH='1'               (empty string '' on a normal run)
BRUIN_SCHEMA_PREFIX=''  BRUIN_VARS='{}'  BRUIN_VARS_SCHEMA='{}'  PYTHONUNBUFFERED='1'
duckdb-default='{"name": ..., "path": "/home/<you>/bruin-smoke/smoke.duckdb"}'   (JSON, absolute path)
```

`BRUIN_RUN_ID` has the form `YYYY_MM_DD_HH_MM_SS` in local time; the execution timestamps are UTC.

**Fallbacks (not needed, both proven anyway).**
- Parquet file plus sibling seed: a `.parquet` seed loads (see b).
- Direct write: a Python asset with `connection: duckdb-default` and no `materialization` gets the `duckdb-default` env var; `duckdb.connect(cfg["path"])` (pip duckdb 2.0.1) created `py.direct_t`, and a downstream SQL asset read it in the same run.

## (b) Seed assets

**Verdict: the KEV CSV seed works; headers are converted to snake_case.**

```yaml
name: landing.kev
type: duckdb.seed

parameters:
    path: kev.csv
```

Log line: `Naming convention: snake_case (9 columns will be renamed)`; 1,790 rows. `DESCRIBE landing.kev`:

```
cve_id VARCHAR, vendor_project VARCHAR, product VARCHAR, vulnerability_name VARCHAR,
date_added DATE, short_description VARCHAR, required_action VARCHAR, due_date DATE,
known_ransomware_campaign_use VARCHAR, forensic_triage VARCHAR, notes VARCHAR, cwes VARCHAR,
_ingestr_loaded_at TIMESTAMP WITH TIME ZONE, _ingestr_run_id VARCHAR
```

- `raw/kev.sql` should use `cve_id`, `date_added` (already `DATE`), `known_ransomware_campaign_use`.
- The live KEV CSV now has 12 columns; `forensic_triage` is new.
- `path: ../../../../data/kev/kev.csv` (traversal out of the asset folder) works: 1,790 rows. Copying the CSV next to the asset is not required.
- Parquet: `path: kev_pq.parquet` with no `file_type` loads; source columns `cveID, dateAdded` again became `cve_id, date_added`.
- JSONL: `path: ledger.jsonl` with `file_type: jsonl` loads, with a `unique` column check passing. Types are inferred (`tick BIGINT`, `ok BOOLEAN`, `p_breach DOUBLE`, ISO `ts` -> `TIMESTAMP WITH TIME ZONE`, a key that is null in some rows is kept). `actorId` became `actor_id`. Columns come out sorted by name (`actor_id, cve_id, ledger_id, match_id, ok, p_breach, tick, ts`), so never rely on position.
- A seed is reloaded in full on every run (about 0.3 to 0.7 s each here).
- A missing seed file is a validate error: `Seed file does not exist or cannot be found (assets-seed-validation)`.

## (c) `time_interval` with `read_csv` on `.csv.gz`

**Verdict: the design.md asset works unchanged.**

```sql
/* @bruin
name: raw.epss
type: duckdb.sql
tags:
  - raw
depends:
  - raw.world_clock
materialization:
  type: table
  strategy: time_interval
  incremental_key: score_date
  time_granularity: date
columns:
  - name: cve_id
    type: VARCHAR
    checks:
      - name: not_null
  - name: epss
    type: DOUBLE
    checks:
      - name: min
        value: 0.0
      - name: max
        value: 1.0
  - name: score_date
    type: DATE
custom_checks:
  - name: no score from the future
    query: SELECT count(*) FROM raw.epss WHERE score_date > (SELECT asof_date FROM raw.world_clock)
    blocking: true
@bruin */

SELECT cve AS cve_id,
       CAST(epss AS DOUBLE) AS epss,
       CAST(percentile AS DOUBLE) AS percentile,
       CAST(regexp_extract(filename, '(\d{4}-\d{2}-\d{2})', 1) AS DATE) AS score_date
FROM read_csv(
  {% if full_refresh %}'data/epss/epss_scores-*.csv.gz'
  {% else %}'data/epss/epss_scores-{{ end_date }}.csv.gz'{% endif %},
  skip = 1, header = true, filename = true)
WHERE score_date <= '{{ end_date }}'
```

The `WHERE` on the `score_date` alias is accepted; no subquery is needed.

`bruin render --full-refresh --start-date 2020-01-01 --end-date "2026-03-22 23:59:59.999999" <asset>`:

```sql
BEGIN TRANSACTION;
DROP TABLE IF EXISTS raw.epss;
CREATE TABLE raw.epss AS SELECT ...
FROM read_csv(
  'data/epss/epss_scores-*.csv.gz'
  ,
  skip = 1, header = true, filename = true)
WHERE score_date <= '2026-03-22';
COMMIT;
```

`bruin render --start-date 2026-03-23 --end-date "2026-03-29 23:59:59.999999" <asset>`:

```sql
BEGIN TRANSACTION;
DELETE FROM raw.epss WHERE score_date BETWEEN '2026-03-23' AND '2026-03-29';
INSERT INTO raw.epss SELECT ...
FROM read_csv(
  'data/epss/epss_scores-2026-03-29.csv.gz',
  skip = 1, header = true, filename = true)
WHERE score_date <= '2026-03-29';
COMMIT;
```

Runs: full refresh as of 2026-03-22 loaded only that date (321,918 rows) although the glob matched both files, in 0.36 s. The incremental week added 2026-03-29 (323,647 rows) in 0.47 s. Running the same week again left the counts unchanged.

Variables as rendered in a real run (`raw.world_clock`):

| Variable | Full refresh, end `"2026-03-22 23:59:59.999999"` | Incremental week |
|---|---|---|
| `{{ start_date }}` | `2020-01-01` | `2026-03-23` |
| `{{ end_date }}` | `2026-03-22` | `2026-03-29` |
| `{{ start_timestamp }}` | `2020-01-01T00:00:00.000000Z` | `2026-03-23T00:00:00.000000Z` |
| `{{ end_timestamp }}` | `2026-03-22T23:59:59.999999Z` | `2026-03-29T23:59:59.999999Z` |
| `{{ end_datetime }}` | `2026-03-22T23:59:59` | `2026-03-29T23:59:59` |
| `{{ run_id }}` | `2026_11_23_00_35_35` | `2026_11_23_00_36_51` |
| `'{{ full_refresh }}'` as text | `True` | `False` |

Gotchas:
- `bruin render` prints `'your-run-id'` for `{{ run_id }}`; the real id only appears in `bruin run`.
- With a date-only `--end-date 2026-03-29`, `{{ end_date }}` is still `2026-03-29`; only the timestamp forms move to `T00:00:00`. With `time_granularity: date` the two forms behave the same, but keep the `23:59:59.999999` form as the contract says.
- **Relative paths in SQL (`read_csv`, `COPY ... TO`) resolve against the directory `bruin` is started from.** Started from `pipelines/smoke`, the same asset failed with `IO Error: No files found that match the pattern "data/epss/epss_scores-2026-03-29.csv.gz"`, and the export failed with `Cannot open file "serving/cards.json": No such file or directory`. Always start `bruin` from the repo root. (Python assets differ: their cwd is always the repo root.)
- A week whose EPSS file is missing fails `raw.epss` with that same "No files found" error; it does not load zero rows.
- An incremental run against a table that does not exist fails: `Catalog Error: Table with name epss does not exist! ... DELETE FROM raw.epss WHERE ...`. Day 0 must be `--full-refresh`.
- `bruin validate` warns when a table used in the query is missing from `depends`: `There are some tables that are referenced in the query but not included in the 'depends' list. (used-tables)`. It is a warning (exit 0). Tables used only inside `custom_checks` do not trigger it.
- `delete+insert` renders as a temp table, `DELETE ... WHERE asof_date in (SELECT DISTINCT asof_date FROM tmp)`, then `INSERT`, in one transaction.

## (d) Unmaterialised assets and `ddl`

**Verdict: single `COPY`, single `INSERT`, `ddl` and multi-statement bodies all run.**

Export asset (run from the repo root; the `serving/` folder must already exist):

```sql
/* @bruin
name: serve.export_cards
type: duckdb.sql
tags:
  - serve
depends:
  - serve.cards
@bruin */

COPY (SELECT * FROM serve.cards ORDER BY card_id) TO 'serving/cards.json' (FORMAT json, ARRAY true);
```

Output file (dates as `YYYY-MM-DD` strings, SQL NULL as `null`, booleans as `true`/`false`):

```json
[
	{"card_id":1,"cve_id":"CVE-2026-0001","model_score":0.1,"score_source":"model","asof_date":"2026-03-22","run_id":"2026_11_23_00_38_22","nothing":null,"in_kev":true},
	...
]
```

Insert asset (no `materialization`; `{{ run_id }}` renders):

```sql
/* @bruin
name: misc.promote
type: duckdb.sql
depends:
  - gate.metrics
  - misc.promotions
  - raw.world_clock
@bruin */

INSERT INTO misc.promotions
SELECT asof_date, 'hgb-' || CAST(asof_date AS VARCHAR), '{{ run_id }}', CAST(current_timestamp AS TIMESTAMP)
FROM gate.metrics
WHERE asof_date = (SELECT asof_date FROM raw.world_clock)
```

`ddl` asset (header only; a query after it is a validate error):

```sql
/* @bruin
name: misc.promotions
type: duckdb.sql
materialization:
  type: table
  strategy: ddl
columns:
  - name: asof_date
    type: DATE
  - name: model_version
    type: VARCHAR
  - name: pipeline_run_id
    type: VARCHAR
  - name: promoted_at
    type: TIMESTAMP
@bruin */
```

It renders to `CREATE TABLE IF NOT EXISTS misc.promotions (...)`, creates the schema too, and under `--full-refresh` logs `Full refresh detected, but DDL strategy is in use — table will NOT be dropped or recreated unless its type or table format has to change.` Rows inserted on day 0 survived later runs.

Gotchas:
- The insert asset is not idempotent: re-running the same week adds a second row. Either delete first (multi-statement works) or have readers use `SELECT DISTINCT` / `EXISTS`.
- Multi-statement body (`CREATE TABLE IF NOT EXISTS ...; DELETE ...; INSERT ...; INSERT ...;`) ran and all statements took effect. But on a fresh database it failed with `Catalog Error: Schema with name misc does not exist!`: Bruin creates schemas only for materialised assets. An unmaterialised asset that writes a table must depend on a materialised asset in the same schema, or run `CREATE SCHEMA IF NOT EXISTS` itself. Export assets named `serve.export_*` are unaffected because they create no table.
- `bruin query --connection duckdb-default --query "COPY (...) TO '...' (FORMAT parquet)"` also works (`Statement type: COPY`), so the `advance.py` fallback is available.

## (e) Checks and the gate

**Verdict: a failed blocking custom check keeps the table, skips downstream and exits 1.**

Gate asset used (`delete+insert` on `asof_date`, check expects 1 and gets 0):

```yaml
custom_checks:
  - name: gate passes only before 2026-03-25
    query: |
      SELECT count(*) FROM gate.metrics
      WHERE asof_date = (SELECT asof_date FROM raw.world_clock)
        AND asof_date < DATE '2026-03-25'
    value: 1
    blocking: true
    retries: 0
  - name: jinja renders inside custom check
    query: SELECT count(*) FROM gate.metrics WHERE asof_date = CAST('{{ end_date }}' AS DATE)
    value: 1
    blocking: true
```

Exact output of the failing week (`bruin run --workers 1 --start-date 2026-03-23 --end-date "2026-03-29 23:59:59.999999" pipelines/smoke/pipeline.yml`):

```
[00:39:06] Running:  gate.metrics:custom-check:gate_passes_only_before_2026-03-25
[00:39:06] Failed: gate.metrics:custom-check:gate_passes_only_before_2026-03-25 (3ms)
[00:39:06] Running:  gate.metrics:custom-check:jinja_renders_inside_custom_check
[00:39:06] Finished: gate.metrics:custom-check:jinja_renders_inside_custom_check (1ms)
...
PASS gate.metrics .F
PASS py.py_reader
UPSTREAM FAILED misc.promote
UPSTREAM FAILED serve.cards
UPSTREAM FAILED serve.export_cards

bruin run completed with failures in 3.914s

 ✗ Assets executed      1 failed due to checks / 11 succeeded / 3 skipped
 ✗ Quality checks       1 failed / 12 succeeded

1 assets failed
└── gate.metrics
    └── gate passes only before 2026-03-25 custom check - custom check 'gate passes only before 2026-03-25' has returned 0 instead of the expected 1
```

Process exit code: 1.

- The failed asset's table is written: `gate.metrics` held the 2026-03-29 row after the failed run. Checks do not roll back.
- Every transitive downstream is skipped (`UPSTREAM FAILED`). Assets not downstream of the gate still run. The old `serving/cards.json` stays as it was.
- Note the summary line says `PASS gate.metrics .F`: the word PASS refers to the main step. Parse `UPSTREAM FAILED`, the `F`, or better the result files, not the word PASS.
- The check was run once, so `retries: 0` is honoured.
- Jinja renders inside `custom_checks.query` (the `{{ end_date }}` check passed on both runs).
- `custom_checks` and column checks both work in a `.py` asset (see a).
- Top-level `retries: 0` in `pipeline.yml` and a `tags:` block list validate with no warning. The flow form `tags: [serve]` works too.
- `blocking` default is true: a failing check with no `blocking` key skipped its downstream.
- A failing check with `blocking: false` lets downstream run, but the run still ends `completed with failures` with exit code 1. So `advance.py` must not treat exit 1 as "gate failed"; decide from `world.json` as designed.

## (f) Selecting what runs

**Verdict: `--tag serve` runs only the tagged assets; untagged upstreams are neither run nor checked.**

```
bruin run --workers 1 --tag serve --start-date 2026-03-23 --end-date "2026-03-29 23:59:59.999999" pipelines/smoke/pipeline.yml
✓ Successfully validated 2 assets across 1 pipeline, all good.
[00:39:28] Running:  serve.cards
[00:39:28] Finished: serve.cards (37ms)
[00:39:28] Running:  serve.export_cards
[00:39:28] Finished: serve.export_cards (3ms)
bruin run completed successfully in 41ms     (exit 0)
```

This ran straight after the failed gate, so it is the working "rebuild serving in EPSS mode" command: the tagged assets ran in dependency order against whatever the upstream tables held, and `cards.json` came out with `score_source: "epss"` for 2026-03-29.

- Single asset: `bruin run ... pipelines/smoke/assets/serve/cards.sql` prints `Running only the asset 'serve.cards'` and runs that asset and its checks only.
- `--downstream` with an asset path works: `bruin run --workers 1 --downstream ... pipelines/smoke/assets/raw/world_clock.sql` prints `The downstream tasks will be executed as well.` and ran `raw.world_clock` plus all 7 transitive downstream assets (gate included, so it failed the same way).
- `bruin lineage <asset>` lists direct upstreams and downstreams with file paths. `--full` adds transitive ones (`gate.metrics` appeared for `serve.cards`). `-o json` gives `{"name","type","upstreams":[{name,type,executable_file,definition_file}],...}`. Output is asset-level only.
- `bruin backfill pipelines/smoke --start-date 2026-03-23 --end-date 2026-04-12 --partition weekly --timezone UTC --workers 1 --dry-run --output json` planned three partitions starting 2026-03-23, 03-30, 04-06 (each from Monday 00:00 to the next Monday 00:00). Only a Monday start date was tried, so this does not show whether weeks are anchored on the start date or on Mondays.

## (g) DuckDB file, locking, ad hoc queries

**Verdict: `/mnt/c` works; one writer at a time; `bruin query` is fine for ad hoc checks when nothing else has the file open.**

- A second environment with `path: "/mnt/c/Users/.../mntc.duckdb"` ran the full-refresh pipeline (7.4 s wall) and an incremental week (6.5 s) with correct row counts, and `bruin query --environment mntc` read it. No lock or I/O error. Timings were the same as on ext4 at this size.
- A relative `path: "smoke.duckdb"` resolved next to `.bruin.yml`. Bruin creates the file if missing.
- Embedded engine: `select version()` -> `v1.5.2` (first use prints `Installing the DuckDB ADBC driver v1.5.2...`). Python assets get pip `duckdb` 2.0.1 through `bruin-sdk[duckdb]`; it read the 1.5.2 file, wrote a table into it, and Bruin kept reading the file afterwards.
- No run active: a separate process opened the file with `duckdb.connect(path, read_only=True)` and read it; two read-only processes at once also worked.
- While a read-only reader holds the file, `bruin query` fails (Bruin opens read-write): `IO Error: Could not set lock on file ".../smoke.duckdb": Conflicting lock is held in /usr/bin/python3.12 (PID ...)`.
- During a run, while a SQL asset is executing, both a read-only open and `bruin query` fail: `IO Error: Could not set lock on file ".../smoke.duckdb": Conflicting lock is held in /home/<you>/.local/bin/bruin (PID ...)`. The run itself was not disturbed.
- During a run, while only a Python asset is executing, a read-only open from outside succeeded: Bruin releases the file around Python assets. So the lock is not held for the whole run; never rely on that.
- Ad hoc check syntax: `bruin query --connection duckdb-default --query "select ..."` (add `--environment <env>` for another environment; `-o json|csv` available). It prints upper-cased headers and shows `DATE` values as `2026-03-22T00:00:00Z`; that is display only, the stored type is `DATE`. `DESCRIBE schema.table` works.
- Run logs are written to `logs/runs/<pipeline>/<run_id>.json` under the repo root, one file per run, so git-ignore `logs/`.

## (h) What `bruin validate` errors look like

**Verdict: structural mistakes are reported per asset with a rule name; SQL and Jinja mistakes are only warnings.**

`bruin validate pipelines/bad` (exit code 1, last line `An error occurred: validation failed`):

```
Pipeline: bad (.)
  bad.bad_check (assets/bad_check.sql)
    └── Custom check 'check without a query' query cannot be empty (custom-check-query-exists)
  bad.bad_py (assets/bad_py.py)
    └── A task with materialization must have a connection defined (assets-python-validation)
  bad.bad_jinja (assets/bad_jinja.sql)
    └── failed to get missing dependencies: failed to render the query before parsing the SQL (used-tables)
  bad.bad_sql (assets/bad_sql.sql)
    └── failed to get missing dependencies: failed to get used tables: Invalid expression / Unexpected token. Line 1, Col: 7.
  bad.bad_seed (assets/bad_seed.asset.yml)
    └── Seed file does not exist or cannot be found (assets-seed-validation)
  bad.dup (assets/dup1.sql)
    └── Asset name 'bad.dup' is not unique, please make sure all the task names are unique (task-name-unique)
  bad.bad_type (assets/bad_type.sql)
    └── Invalid asset type 'duckdb.sqll' (valid-task-type)
  bad.bad_ti (assets/bad_ti.sql)
    ├── Materialization strategy 'time_interval' requires the 'incremental_key' field to be set (materialization-config)
    ├── Materialization strategy 'time_interval' requires the 'time_granularity' field to be set (materialization-config)
    └── 'time_granularity' can be either 'date' or 'timestamp'. (materialization-config)
  bad.bad_dep (assets/bad_dep.sql)
    └── Dependency 'bad.does_not_exist' does not exist (dependency-exists)
  bad.bad_ddl (assets/bad_ddl.sql)
    └── DDL strategy builds the table, from bruin metadata and does not accept a custom query (materialization-config)
  bad.bad_view (assets/bad_view.sql)
    └── Materialization strategy is not supported for views (materialization-config)

✘ Checked 1 pipeline and found 11 issues and 2 warnings, please check above.
An error occurred: validation failed
```

- Broken YAML in a header aborts the whole validation with one line: `An error occurred: error creating pipeline from path '...': error creating asset from file '.../bad_yaml.sql': yaml: line 6: mapping values are not allowed in this context (check indentation)`.
- Misspelt keys are warnings only (exit 0): `line 4: field materialisation not found in type pipeline.taskDefinition (unknown-asset-fields)` and, in `pipeline.yml`, `line 5: field nonsense_key not found in type pipeline.pipelineAlias (unknown-pipeline-fields)`. A misspelt `materialization` therefore silently makes the asset unmaterialised, so read the warnings.
- A misspelt column check name (`not_nul`) is not caught anywhere: `bruin validate` says `No issues found`, and `bruin run` exits 0 with `✓ Assets executed 2 succeeded` and no `Quality checks` line at all. The check is silently dropped, so after adding checks, confirm the `Quality checks N succeeded` count in the run output.
- Not caught by validate: broken SQL and broken Jinja (warnings only), and a file with no `name:` (accepted; the name is taken from the path).
- `bruin validate -o json` returns `[{"pipeline":"bad","issues":{"<asset>":[{"asset","description","context","severity":"critical"|"warning"}]}}]` and exits 0 even with critical issues. Use the plain form when the exit code matters.
- `bruin run` validates first and refuses to start on errors (`Failed to configure connection concurrency limits: ... no connection mapping found for asset type 'duckdb.sqll'`, exit 1).

## Changes this implies for design.md

1. `raw/kev.sql` reads `cve_id`, `date_added`, `known_ransomware_campaign_use` from `landing.kev`; the `accepted_values` check goes on `known_ransomware_campaign_use`.
2. No Python fallback ladder is needed. Keep `image: python:3.12` on every Python asset, and use lowercase snake_case frame columns.
3. Every query over a seed or a Python table lists its columns; `_ingestr_loaded_at` and `_ingestr_run_id` must not reach `serving/*.json`.
4. `ml.promote` is safe as a single `INSERT` because `ml.promotions` (ddl) creates the `ml` schema and is an upstream. Re-running a week appends a duplicate registry row, so serving should test `EXISTS`, or the asset should become `DELETE ...; INSERT ...;`.
5. `landing.nvd_cves` returning `None` on weekly runs is fine, given day 0 is a full refresh.
6. After a failed gate, `bruin run --workers 1 --tag serve ...` is the working rebuild; the five single-asset commands are not needed.
7. `advance.py` must read `world.json` to decide the outcome: exit code 1 covers a failed gate, a failed non-blocking check and a real error alike.
8. `serving/` must exist before the export assets run, and `bruin` must be started from the repo root.
9. The game or any viewer must not hold the pipeline database open, even read-only: that makes `bruin` fail with a lock error. (The contract already forbids it.)

## Not tested

- `bruin run --only checks`, `--continue`, `--selector`, and `bruin backfill` without `--dry-run`.
- `merge`, `append` and `delete+insert` strategies on Python assets; generator `materialize()`.
- `.csv.gz` as a seed file; seeds from a URL.
- Reading the WSL database from a Windows process.
- `BRUIN_COMMIT_HASH` in a repo that has commits (it was empty in the commit-less smoke repo).
- Larger `/mnt/c` databases (only about 16 MB was written there).
