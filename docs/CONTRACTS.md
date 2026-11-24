# Patch Tuesday: interface contracts

These are the fixed interfaces between the three parts of the project. Each part is built separately, so do not change a shape here without changing every reader.

| Part | Folder | Runs on |
|---|---|---|
| Pipeline (Bruin + DuckDB) | `pipelines/`, `scripts/` | WSL Ubuntu (`bruin` lives at `~/.local/bin/bruin`) |
| Game server (FastAPI) | `game/` | Windows Python 3.12, venv at `.venv/` |
| Web client (Vite + three.js) | `web/` | Windows Node 24 |

The repo root is `<repo>`, seen from WSL as `/mnt/c/<path-to>/patch-tuesday`.

## Runtime rules

- The pipeline database is `/home/<you>/.patch-tuesday/world.duckdb` inside WSL. Only `bruin` opens it, one run at a time.
- The game never opens the pipeline database. It reads `serving/*.json` and writes `data/game.duckdb`, `ledger/ledger.jsonl` and `ledger/reasons.jsonl`.
- `bruin` is always run from the repo root, with `--workers 1`.
- From Windows, call Bruin as `wsl -e bash -lc "cd /mnt/c/<path-to>/patch-tuesday && export PATH=$HOME/.local/bin:$PATH && bruin ..."`.
- Secrets live in `.env` at the repo root (git-ignored). Load them with code. Never print, log or echo a secret value.
- All attacks are abstract game moves. No exploit code, no payloads, no reference URLs, no CVE description text in any served file.

## Environment variables (`.env`)

| Name | Used by | Meaning |
|---|---|---|
| `NVD_API_KEY` | optional | Not needed: the pipeline uses the NVD bulk year feeds |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | game | OpenAI-compatible endpoint for one-sentence reasons. If `LLM_API_KEY` is empty, use template reasons |
| `BAND_API_KEY` | game | BAND agent key. Integration is off unless `BAND_ENABLED=1` |
| `TELEM_API_KEY` | game | Telem key. Integration is off unless `TELEM_ENABLED=1` |
| `BAND_KEY_*`, `BAND_ID_*`, `BAND_BLUE_CHAT_ID`, `BAND_BACKUP*`, `BAND_TEAM_*` | game | "Call for backup": see that section for the full list |

## Serving files (pipeline writes, game reads)

All are written with DuckDB `COPY ... (FORMAT json, ARRAY true)`, so every file is a JSON array. `world.json` is written last and is the game's reload trigger.

### `serving/world.json`: array with exactly one object

| Field | Type | Meaning |
|---|---|---|
| `asof_date` | `YYYY-MM-DD` | World clock (always a Sunday) |
| `window_start` | `YYYY-MM-DD` | First day of the run window |
| `pipeline_run_id` | string | Bruin run id |
| `score_source` | `"model"` or `"epss"` | `model` only if the gate passed for this `asof_date` |
| `model_version` | string or null | e.g. `hgb-2026-03-22` |
| `challenger_p_at_50`, `epss_p_at_50` | number or null | Precision in the top 50 on the validation snapshot |
| `challenger_hits_at_50`, `epss_hits_at_50`, `n_positives`, `n_candidates` | integer or null | Gate inputs |
| `n_cves_visible`, `n_kev_visible`, `n_new_cves_this_week`, `n_cards` | integer | World size counters |

### `serving/attack_cards.json`: the deck, about 120 objects

| Field | Type | Meaning |
|---|---|---|
| `card_id` | integer | Unique within the file |
| `cve_id` | string | `CVE-YYYY-NNNN` |
| `title` | string | Short label built from vendor, product and weakness class only |
| `published_date` | `YYYY-MM-DD` | |
| `age_days` | integer | Days from publication to `asof_date` |
| `vendor`, `product`, `cwe_id` | string or null | |
| `cvss_base_score` | number or null | |
| `severity` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` or null | |
| `score` | number 0..1 | The score the game uses for breach odds |
| `score_source` | `"model"` or `"epss"` | Same for every card in a file |
| `epss`, `epss_percentile` | number or null | EPSS as of `asof_date` |
| `model_score` | number or null | Null when the gate failed |
| `in_kev` | boolean | In the KEV list as of `asof_date` |
| `technique_id`, `technique_name`, `tactic` | string or null | MITRE ATT&CK mapping |
| `mapping_source` | `kev_ctid`, `cwe_capec`, `heuristic` or `none` | |
| `is_new_this_week` | boolean | Published inside the current run window |
| `priority_rank` | integer or null | Rank in `patch_priority.json`, if present there |

### `serving/patch_priority.json`: top 500 objects, ordered by `rank`

`rank`, `cve_id`, `score`, `score_source`, `epss`, `model_score`, `in_kev`, `vendor`, `product`, `cvss_base_score`, `technique_id`, `in_deck` (boolean).

### `serving/reveal.json` (optional): one object per deck card

`cve_id`, `entered_kev_within_91d` (boolean), `kev_date_added` (`YYYY-MM-DD` or null). Used only for the end-of-match reveal. The game must work when this file is absent.

## Advancing the world

`python scripts/advance.py` (standard library only, works from Windows and from WSL):

- Reads `asof_date` from `serving/world.json`, runs Bruin for the next Monday-to-Sunday window, then prints one JSON object on its last stdout line: `{"ok": bool, "asof_date": "...", "score_source": "...", "challenger_p_at_50": n, "epss_p_at_50": n, "gate_passed": bool, "message": "..."}`.
- `python scripts/advance.py --day0` rebuilds everything as of 2026-03-22.
- Refuses to start while `data/.advance.lock` exists.
- The last available week ends 2026-06-14. After that it returns `ok: false` with a message.

## Game server (port 8000)

### HTTP

| Route | Meaning |
|---|---|
| `GET /api/state` | Latest full state message |
| `GET /api/world` | Contents of `world.json` plus `advancing: bool` |
| `GET /api/ledger?limit=200` | Most recent ledger rows |
| `POST /api/match/start` | Start a new match (lobby or ended only) |
| `POST /api/match/reset` | Back to lobby |
| `POST /api/world/advance` | Start `advance.py` in the background; returns at once |

### WebSocket `/ws`

Server to client, once per tick (tick = 2.0 s, match = 150 ticks) and once on connect:

```json
{"t":"state","match_id":"m-...","phase":"lobby|running|ended","tick":0,"ticks_total":150,
 "world":{"asof_date":"2026-03-22","score_source":"epss","model_version":null,
          "challenger_p_at_50":null,"epss_p_at_50":null,"advancing":false},
 "nodes":[{"id":0,"x":0,"y":0,"name":"MAIL-01","value":2,
           "state":"healthy|compromised|isolated","monitored":false,
           "cards":[{"card_id":1,"cve_id":"CVE-...","title":"...","score":0.12,"in_kev":false,
                     "patch_state":"unpatched|patching|patched","is_new":false,
                     "technique_id":"T1190","tactic":"initial-access","priority_rank":7}]}],
 "actors":[{"id":"red1","name":"Opportunist","team":"red|blue","kind":"agent|human",
            "role":"...","node_id":3,"action":"MOVE","target_node":4,"revealed":true,"busy_ticks":0}],
 "score":{"red":0,"blue":0,"healthy":20,"compromised":0,"isolated":0,
          "breaches_total":0,"breaches_in_top50":0},
 "ledger":[],
 "events":[{"kind":"breach|patched|restored|drop|campaign|gate|intel|info","text":"..."}]}
```

- The grid is 5 columns by 4 rows; `id = y * 5 + x`.
- Actor ids: `red1`, `red2`, `blue1`, `blue2`, `blue3`, `p1`, `p2`. With "Call for backup" an eighth, `blue4`, can join during a match (see that section).
- `ledger` holds only the rows created this tick. Row fields: `ledger_id, match_id, world_asof, pipeline_run_id, model_version, tick, ts, actor_id, team, kind, action, node_id, cve_id, score_used, score_source, p_breach, roll, outcome, policy, reason_text, llm_model, llm_latency_ms`.

Server to client, when a late LLM reason arrives:

```json
{"t":"reason","ledger_id":"...","reason_text":"..."}
```

Client to server:

```json
{"t":"act","player":1,"action":"MOVE|PATCH|MONITOR|ISOLATE|RESTORE","dir":"N|S|E|W","cve":"CVE-..."}
{"t":"control","cmd":"start|reset|advance"}
```

- `dir` is only for `MOVE`. `N` is `y - 1`.
- The other actions apply to the node the player stands on. `cve` is optional for `PATCH`; without it the server patches the unpatched card with the best `priority_rank` on that node.
- The server keeps only the latest action per player per tick.

### Rules

- Red actions: `MOVE`, `SCAN`, `EXPLOIT`, `PIVOT`, `WAIT`. Blue actions: `MOVE`, `PATCH` (2 ticks), `MONITOR` (3 ticks), `ISOLATE` (5 ticks), `RESTORE` (3 ticks), `WAIT`.
- Breach odds for `EXPLOIT(node, card)`:

```
s        = clamp(card.score, 0, 1)
base     = 1 - (1 - s) ** 6
kev      = 0.15 if in_kev else 0.0
patch_m  = {unpatched: 1.0, patching: 0.4, patched: 0.0}[patch_state]
defense  = 0.5 if monitored else 1.0
recon    = 0.10 if scanned_by_this_red else 0.0
p_breach = clamp((base + kev + recon) * patch_m * defense, 0, 0.95)   # 0 if the node is isolated
```

- Scoring: red gets `+value` per compromised node per tick and `+5` per first breach; blue gets `+value * s` per card patched before any exploit attempt on it, `+3` per restore, `-1` per isolated node per tick.
- When `world.json` changes during a match, the new week's cards drop onto nodes (`drop` event). The red agents then start a campaign on the freshest high-score cards with no human input (`campaign` event).

## Explain-itself additions (server to client)

Everything in this section was added on top of the message above. No earlier field changed its name, type or meaning; a client that ignores the new fields keeps working. A client must read them defensively: an older server does not send them.

The full `{"t":"state"}` message now has three more top-level keys (`score_deltas`, `player_feedback`, `reveal`), `world` has nine more keys and `score` has two more. All of them are present in every state message, in every phase.

### `state.world` additions

```json
"world":{"asof_date":"2026-03-29","score_source":"model","model_version":"hgb-2026-03-29",
         "challenger_p_at_50":0.04,"epss_p_at_50":0.0,"advancing":false,
         "pipeline_run_id":"2026_11_23_13_24_43",
         "gate_passed":true,
         "challenger_hits_at_50":2,"epss_hits_at_50":0,
         "n_positives":32,"n_candidates":172912,
         "n_cves_visible":188844,"n_new_cves_this_week":1627,
         "advance":null}
```

| Field | Type | Meaning |
|---|---|---|
| `pipeline_run_id` | string or null | Bruin run id from `world.json`. Null when the file has none (the loader's internal placeholder `"unknown"` is sent as null). |
| `gate_passed` | boolean (null only if `score_source` is missing) | Exactly `score_source == "model"`. |
| `challenger_hits_at_50`, `epss_hits_at_50` | integer or null | Of each scorer's top 50, how many really became known exploited within 91 days. |
| `n_positives` | integer or null | How many CVEs in the validation week really became known exploited within 91 days. |
| `n_candidates` | integer or null | CVEs scored in the validation week. |
| `n_cves_visible` | integer or null | Real CVEs visible as of `asof_date`. Null when the file has no counter (a missing counter and 0 are both sent as null). |
| `n_new_cves_this_week` | integer or null | CVEs published inside the run window. Null whenever `n_cves_visible` is null. |
| `advance` | object or null | Live progress of `scripts/advance.py`; see below. |

`GET /api/world` also carries `advance` (same object). Its other keys are unchanged: the normalised `world.json` object plus `advancing`, `data_source` and `last_advance`.

### `state.world.advance`: live pipeline progress

`null` when no advance is running. While one runs:

```json
{"stage":"feat","stage_index":3,"assets_done":13,"assets_total":26,
 "current":"feat.snapshot_features","seconds":12.4,
 "message":"Bruin is running feat.snapshot_features (asset 14 of 26)."}
```

| Field | Type | Meaning |
|---|---|---|
| `stage` | `"landing"`, `"raw"`, `"stg"`, `"feat"`, `"ml"`, `"gate"`, `"serve"`, `"done"`, `"failed"` or null | The step the pipeline has reached. Null until the log can be read. |
| `stage_index` | integer 0..6 or null | Index of the step in `landing, raw, stg, feat, ml, gate, serve`. With `"done"` it is 6; with `"failed"` it is the step the run stopped on. |
| `assets_done` | integer | Assets finished (or failed) in the Bruin command now running. Quality checks are not counted. |
| `assets_total` | integer or null | Assets that command runs (26 for a full run, 6 for the EPSS rebuild). Null until Bruin has printed it. |
| `current` | string or null | Name of the asset running now, or the last one that ran, e.g. `feat.snapshot_features`. Only a name of the form `schema.asset` is ever forwarded. |
| `seconds` | number | Seconds since the advance started (server clock). |
| `message` | string | One plain sentence for the screen. Written by the server; never a raw log line. |

How it is produced:

- `scripts/advance.py` streams Bruin's output to `logs/advance/<timestamp>-<label>.log` line by line while Bruin runs (it used to write the file once, at the end). The finished file has the same content as before. Its decisions and its last-line JSON are unchanged: they are still taken from the complete output after Bruin exits.
- The server follows the newest log written since the advance started (a background task, every 0.5 s, in a worker thread; the tick loop only copies the latest parsed object). It reads lines of the form `[13:24:43] Running:  landing.attack_techniques` and `[13:24:45] Finished: landing.attack_techniques (1.848s)`. A name with a colon is a quality check and is not counted.
- Stage of an asset: its schema prefix. `ml.eval_metrics` is the `gate` step. `sim.*` counts as `serve`.
- The stage only moves forward. Bruin's real execution order is not strictly layer by layer: it runs `ml.promotions`, `ml.retract` and `feat.snapshots` at the start, next to `raw.*`, and `stg.cve_technique` after `feat.snapshot_features`; `ml.sensitivity` and `ml.promote` run after the gate asset. Those three early bookkeeping assets are counted and shown in `current` but do not move the stage, and a later asset from an earlier layer does not move it back. On a real run the stage goes `landing, raw, feat, ml, gate, serve` (`stg` is passed, never the current step).
- When the gate blocks the model, `advance.py` runs a second Bruin command (`--tag serve`) to rebuild the board with EPSS. `assets_done` and `assets_total` restart for that command; the stage stays at or after `gate`.
- If the log folder or file cannot be read (or an older `advance.py` that does not stream is used), the object is still sent with `stage`, `stage_index`, `assets_total` and `current` null and a correct `seconds`. A log problem never stops a tick.

When it is sent:

- In a running match: on every tick message while the advance runs.
- In the lobby or after a match has ended there is no tick, so the server publishes a state message every 1 s while the advance runs (same message shape, empty `ledger`, `events`, `score_deltas` and `player_feedback`).
- The state message that carries the `Advance finished: ...` or `Advance did not complete: ...` info event carries the closing object, with `stage` `"done"` or `"failed"`. If the new week is still waiting to be dealt onto a running board (`advancing` is still true), `"done"` stays until it has been dealt. After that `advance` is null again.

### `state.score` additions

```json
"score":{"red":61,"blue":16.4,"healthy":18,"compromised":2,"isolated":0,
         "breaches_total":3,"breaches_in_top50":1,
         "rate":{"red":5,"blue":0},
         "breakdown":{"blue":{"patch":12.4,"restore":9.0,"isolate_cost":-5.0},
                      "red":{"first_breach":15.0,"held":41.0}}}
```

- `rate`: points per tick right now. `red` = sum of `value` of the nodes whose `state` is `compromised`. `blue` = minus the number of nodes whose `state` is `isolated` (times the isolate cost, 1). An isolated node earns red nothing, breached underneath or not. Both are 0 in the lobby.
- `breakdown`: cumulative this match, rounded to 2 decimals. `blue.patch + blue.restore + blue.isolate_cost` equals `score.blue`; `red.first_breach + red.held` equals `score.red`. `isolate_cost` is zero or negative.

### `state.score_deltas`: every score change of this tick

A list; empty when nothing changed and outside a running match. One entry per change:

```json
{"team":"blue","amount":1.38,"cause":"patch","node_id":6,"actor_id":"p1","cve_id":"CVE-2026-33660"}
{"team":"blue","amount":3.0,"cause":"restore","node_id":7,"actor_id":"blue3","cve_id":null}
{"team":"blue","amount":-1.0,"cause":"isolate_cost","node_id":6,"actor_id":null,"cve_id":null}
{"team":"red","amount":5.0,"cause":"first_breach","node_id":7,"actor_id":"red1","cve_id":"CVE-..."}
{"team":"red","amount":2.0,"cause":"held","node_id":7,"actor_id":null,"cve_id":null}
```

| `cause` | Team | When | `amount` |
|---|---|---|---|
| `patch` | blue | A patch finished on a card nobody had attacked yet | `value * score`, rounded to 4 decimals. No entry when the patch earned nothing. |
| `restore` | blue | A restore finished | 3 |
| `isolate_cost` | blue | Every tick, once per isolated node | -1 |
| `first_breach` | red | The first breach of a node in this match | 5. A later breach of the same node earns no bonus and has no entry. |
| `held` | red | Every tick, once per compromised node | the node's `value` |

The sum of all `amount`s of a team over a match equals that team's final score (to rounding: the tests allow 0.01).

### `state.player_feedback`: what your key press did

A list; one entry per human action resolved this tick:

```json
{"player":1,"action":"PATCH","outcome":"patched","ok":true,
 "text":"Patched CVE-2026-33660: +1.38 (machine value 3 x danger 46%)."}
```

`player` is 1 or 2. `action` and `outcome` are the same as in that player's ledger row of this tick. `ok` is true for `moved`, `patching`, `patched`, `monitoring`, `isolated`, `restoring`, `restored`. There is no entry for `WAIT` (no key pressed) and none for the middle ticks of a multi-tick action (`working`).

| Action | Outcome | `ok` | Text |
|---|---|---|---|
| `MOVE` | `moved` | true | `Moved to WEB-01.` |
| `MOVE` | `blocked` | false | `Can't move: that is the edge of the network.` |
| `PATCH` | `patching` | true | `Patching CVE-... (2 ticks).` |
| `PATCH` | `patched` | true | `Patched CVE-...: +1.38 (machine value 3 x danger 46%).` or `Patched CVE-...: already attacked, no points.` |
| `PATCH` | `invalid` | false | `Nothing left to patch on this machine.`, or, for a card named in `cve`: `CVE-... is already being patched.`, `CVE-... is already patched.`, `Can't patch: that card is not on this machine.` |
| `PATCH` | `noop` | false | `Nothing left to patch: CVE-... was already closed.` (it was closed while the patch ran) |
| `MONITOR` | `monitoring` | true | `Monitoring DC-01 for 3 ticks: attackers here are revealed and their breach chance is cut.` |
| `ISOLATE` | `isolated` | true | `Isolated DC-01 for 5 ticks: no attack can land, but it costs 1 point per tick.` |
| `ISOLATE` | `invalid` | false | `Can't isolate: this machine is already isolated.` |
| `RESTORE` | `restoring` | true | `Restoring ERP-01 (3 ticks).` |
| `RESTORE` | `restored` | true | `Restored ERP-01: +3. Red no longer earns from it.` |
| `RESTORE` | `invalid` | false | `Nothing to restore: this machine is not breached.` |
| `RESTORE` | `noop` | false | `Nothing to restore: ERP-01 was already clean.` (someone else restored it first) |

### `state.reveal`: what really happened to the board's CVEs

`null` unless `phase` is `"ended"`, and `null` when the serving folder has no `reveal.json`. After a match has ended it stays in every later state message and in `GET /api/state` until the next match starts or the lobby is reset.

```json
{"n_cards":61,"n_real":1,"horizon_days":91,
 "items":[{"cve_id":"CVE-2025-34291","title":"Langflow: ...","node_id":12,"node_name":"ERP-01",
           "kev_date_added":"2026-05-21","days_after_asof":53,"priority_rank":41,
           "score":0.31,"score_source":"model","in_kev_at_asof":false,
           "fate":"patched","by":"Patcher","tick":44}]}
```

- `n_cards`: board cards that have an entry in `reveal.json`. `n_real`: how many of them have `entered_kev_within_91d` true (the length of `items`). These are the two numbers of the `Reveal: ...` info event, which is still sent.
- `items`: those cards, ordered by `kev_date_added`.
- `days_after_asof`: `kev_date_added` minus the `asof_date` of the week that reveal entry came from (the match's starting week, or the later week when the card arrived in, or was re-scored by, a mid-match drop). Null when a date is missing.
- `priority_rank`, `score`, `score_source`, `in_kev_at_asof`: the card as it stood on the board at the end of the match.
- `fate`: `"breached"` if a successful exploit used this card (`by` = the attacker's name, `tick` = the tick of the first one); else `"patched"` if its patch finished (`by` = who patched it, `tick` = when); else `"open"` (`by` and `tick` null). A card closed by a restore after a breach stays `"breached"`.

### The `gate` event text

Sent when a new week is loaded (in the lobby) or dealt onto a running board:

- Gate passed: `Gate passed: our model put 2 of the 32 soon-to-be-exploited bugs in its top 50; the EPSS baseline put 0. Our model (hgb-2026-03-29) now sets the danger scores.`
- Gate blocked: `Gate blocked: our model 0, EPSS baseline 1 (of 41). EPSS stays.`
- When `world.json` lacks `challenger_hits_at_50`, `epss_hits_at_50` or `n_positives`, only the shares are known and the fallback wording is used: `Gate passed for 2026-03-29: our model (hgb-2026-03-29) now sets the danger scores (hits in top 50: our model 4%, EPSS baseline 0%).` / `Gate blocked for 2026-03-29: the EPSS baseline sets the danger scores (...)`.
- The other event texts use the screen's words too (no rule or field changed):
  - `drop`: `Patch Tuesday 2026-03-29 (was 2026-03-22): 12 new cards dropped on 9 machines; danger set by our model.` (or `the EPSS baseline`).
  - `campaign`: `Red campaign on 6 fresh cards (top: CVE-2026-33660, danger 94%, on DB-01). Opportunist takes ...; Patient takes ....`
  - `breach`: `Opportunist breached FILE-01 via CVE-2026-33660 (breach chance 63%, roll 0.28), first breach +5.`
  - `info`: `Match started: data as of 2026-03-22, danger set by the EPSS baseline.`, `Advance finished: data as of 2026-03-29, danger set by our model.`

No other event text changed.

### Fog of war and the additions

None of the new fields shows more of a hidden attacker than the message did before:

- `score_deltas`: a red entry is either `held` (no actor, and the node is already shown as `compromised`) or `first_breach` (the `breach` event of the same tick already names the attacker, the node and the CVE, and a breach reveals the attacker for 2 ticks). If the attacker of a delta is not revealed while a match runs, its `actor_id` and `cve_id` are sent as null.
- `player_feedback` only describes the two human players' own actions.
- `reveal` is never sent while a match runs.
- `world.advance` holds pipeline progress only.

## Call for backup (BAND)

One use of BAND: a Coach agent analyses the Blue team during a match, says in a BAND room what the team is weakest at, and, when a human in the room agrees, calls in one specialist agent that joins the room and walks onto the board as an eighth actor. `docs/BAND.md` is the guide; this section is the contract.

Code: `game/backup.py` decides everything (analysis, the three specialist policies, the flow; no network). `game/bandteam.py` only carries the flow's lines to BAND and the humans' replies back. `scripts/band_setup.py` registers the agents and the room once.

### When it is on

The feature is on for a match when all of these hold at match start; otherwise the server behaves exactly as before (seven actors, the same random stream, no `backup` key in the state message):

- `BAND_ENABLED=1` (`scripts\start.ps1 -Band` sets it) and `BAND_BACKUP` is not `0`
- `BAND_KEY_COACH`, `BAND_KEY_TRIAGE`, `BAND_KEY_PATCHER`, `BAND_KEY_HUNTER`, `BAND_KEY_RESPONDER`, `BAND_KEY_HARDENER`, `BAND_KEY_SENTINEL` and `BAND_BLUE_CHAT_ID` are set
- the BAND side has not switched itself off in this server process (see "Limits")

### Wire additions (server to client)

`state.backup` is absent (read it as null) while the feature is off, in the lobby and after a reset. Otherwise:

```json
"backup": {
  "status": "watching|analysing|asked|joined|declined|unavailable",
  "checkpoint_tick": 40,
  "weakness": null,
  "players": [],
  "recommended": null, "options": ["responder","hardener","sentinel"],
  "asked_tick": null, "expires_tick": null,
  "chosen": null, "by": null,
  "joined_tick": null, "actor_id": null,
  "room": "Patch Tuesday - Blue team",
  "before": null, "after": null,
  "team_ok": null, "reason": null
}
```

| Field | Meaning |
|---|---|
| `status` | `watching` (before the checkpoint), `analysing` (Coach and the three blue agents are speaking), `asked` (Coach waits for a reply), `joined`, `declined`, `unavailable` (BAND went away or the flow failed; nothing more is asked this match) |
| `checkpoint_tick` | The tick the analysis is planned for; once it has run, the tick it ran at |
| `weakness` | null before the analysis, then `{"key":"slow_response|exposure|blind_spots","title":"Slow response","evidence":["Red earned 31 of its 46 points from machines left breached.","..."]}` |
| `players` | `[{"id":"p1","line":"Player 1 patched 4 cards worth 1.9 and restored nothing."}]`, one entry per human player, empty before the analysis |
| `recommended`, `options` | Coach's pick and the three specialists a human may name |
| `asked_tick`, `expires_tick` | When Coach asked and the tick the answer window closes |
| `chosen`, `by` | The specialist that joined, and who decided: the human's BAND display name, or `"Coach (no reply)"` |
| `joined_tick`, `actor_id` | The tick the specialist joined and its actor id, always `"blue4"` |
| `room` | The BAND room's title |
| `before`, `after` | `{"ticks","red_per_tick","blue_per_tick","breaches","avg_breach_ticks","peak_breached"}` for the ticks before the join and since it. `before` is set at the join, `after` when the match ends (it stays in `GET /api/state`). `avg_breach_ticks` is null when no machine was breached in that window |
| `team_ok` | true when Blue was ahead and no weakness stood out (Coach still offers the specialist that helps most) |
| `reason` | Why the status is `unavailable`, else null. Never holds a key |

After the join, `state.actors` has an eighth entry, last in the list: `{"id":"blue4","name":"Responder","team":"blue","kind":"agent","role":"responder","reinforcement":true, ...}`. The `reinforcement` key exists only on that entry. It starts at JUMP-01 (machine 19) and obeys the same rules, durations and scoring as any blue agent. Its ledger rows are normal rows (`actor_id` `blue4`, `kind` `agent`, `team` `blue`, `policy` = its role).

Two new `events[].kind` values: `coach` (the analysis in one sentence, then the recommendation) and `backup` (`Responder joined the Blue team (approved by Alex via BAND).`, `Backup declined.`). Neither goes to the highlights room of the BAND event mirror.

### Flow

1. Checkpoint, once per match: tick `BAND_BACKUP_TICK` (default 40); earlier when Red leads by 15 or more after tick 20; or when a human asks Coach in the room (not before tick 10, not in the last 30 ticks).
2. Coach posts the analysis and mentions Triage, Patcher and Hunter. Each of them posts one line with its own numbers, 3 seconds apart.
3. Coach posts its recommendation and mentions the humans in the room (at most three).
4. Answer window: `BAND_BACKUP_WAIT_TICKS` ticks (default 15). The first accepted human message decides. With no reply Coach calls in its own pick, or stands down when `BAND_BACKUP_AUTO=0`.
5. On a yes, Coach adds the specialist's agent to the room, the specialist introduces itself, and it is on the board from that tick. While it plays it reports at most once per 15 seconds.
6. At match end Coach posts the before/after summary. At the next match start, and on reset, Coach removes the specialist from the room (`DELETE /agent/chats/{chat}/participants/{id}`).

### The words Coach understands

A message counts only when a human (BAND `sender_type` `"User"`) wrote it in the room, it mentions Coach, it is younger than 60 seconds and it was written after the current match started. Its text is cut to 200 characters, mention tokens are removed, and it is matched against fixed words only (case does not matter). It is never sent to an LLM or a shell.

| When | Words | Effect |
|---|---|---|
| Before the analysis | `analyze`, `analyse`, `backup`, `help` | Coach runs the analysis now (from tick 10 on) |
| While Coach waits for a reply | `responder`, `hardener`, `sentinel` | That specialist joins. A name wins over every other word |
| While Coach waits for a reply | `yes`, `ok`, `go`, `do it` | Coach's recommended specialist joins |
| While Coach waits for a reply | `no`, `none` | No backup this match |

Anything else is acknowledged in BAND and ignored by the game.

### Environment variables

| Name | Used by | Meaning |
|---|---|---|
| `BAND_USER_API_KEY` | `band_setup.py` only | Your BAND user key (`band_u_...`), needed once to register the agents. The game server never reads it |
| `BAND_BACKUP` | game | `0` switches the feature off while the event mirror stays on |
| `BAND_KEY_<NAME>` | game, setup | Agent key of `COACH`, `TRIAGE`, `PATCHER`, `HUNTER`, `RESPONDER`, `HARDENER`, `SENTINEL`. Written by the setup script |
| `BAND_ID_<NAME>` | game, setup | The same agents' ids (not secret). When one is missing the server asks BAND once with that agent's key |
| `BAND_BLUE_CHAT_ID` | game, setup | The room "Patch Tuesday - Blue team" |
| `BAND_BACKUP_TICK` | game | Checkpoint tick, default 40 |
| `BAND_BACKUP_WAIT_TICKS` | game | Answer window in ticks, default 15 |
| `BAND_BACKUP_AUTO` | game | `0` = with no reply Coach stands down; default: Coach calls in its own pick |
| `BAND_TEAM_MIN_INTERVAL` | game | Seconds between two posts in the room, default 3 |
| `BAND_TEAM_POLL_SECONDS` | game | Seconds between two reads of Coach's inbox, default and minimum 2.5 |
| `BAND_TEAM_MAX_PER_HOUR`, `BAND_TEAM_MAX_POSTS` | game | Budget: room posts per rolling hour (default 200; more are dropped) and per server process (default 1000; then the feature switches itself off) |
| `BAND_TRUSTED_SENDER_IDS` | game | Test only, empty by default: comma-separated BAND ids whose messages count like a human's |
| `BAND_BASE_URL` | game, setup | Test only: another BAND API base URL. The setup script refuses plain http except for localhost |
| `PT_BACKUP_OFFLINE` | game | Rehearsal only: `1` runs the flow with no BAND at all. Nobody can reply, so Coach decides when the window closes |

### What the server sends to BAND, and the limits it keeps

- Each line is one chat message from the speaker's own agent key (`POST /agent/chats/{chat}/messages`), with mention objects `{"id","handle","name"}` read from the room's participant list. A whole match posts about 8 to 16 messages.
- One poller, with Coach's key only, reads Coach's inbox (`GET /agent/chats/{chat}/messages?limit=100`), and only while a match with the feature runs. Every row it gets is acknowledged with `/processing` and then `/processed`; `/failed` is never called. A row whose claim BAND refuses (204) is left for the next read. Rows from other agents are acknowledged and never acted on. Each message id is acted on at most once.
- HTTP 429: wait `Retry-After`, else 1, 2, 4, 8, 16 seconds. HTTP 503: retry after a short wait.
- HTTP 403 `limit_reached`: the feature is off until the server restarts, and `/api/status` names the limit. HTTP 403 "execution is stopped" or HTTP 401: that agent stops posting; when it is Coach, the feature is off. Five failed requests in a row: off.
- When the BAND side goes off during a match, `backup.status` becomes `unavailable` and the match goes on. A specialist that had already joined keeps playing.
- All BAND traffic runs in background tasks with a 5 second timeout. The tick loop only puts lines in a queue and takes replies out of one.

### `scripts/band_setup.py`

`python scripts/band_setup.py [--env PATH] [--suffix TEXT] [--dry-run]`. Standard library only. It reads `BAND_USER_API_KEY` from the env file (default `.env`), registers the seven agents that are missing, creates the room with Coach's key when `BAND_BLUE_CHAT_ID` is empty or unreadable, adds Triage, Patcher, Hunter and the owner as members (never the specialists), and posts one hello line from Coach when it created the room or added a member. It writes only `BAND_*` lines of the env file and prints names, ids and handles, never a key. A second run reads only. If an agent of a wanted name exists but the env file has no working key for it, the script stops before changing anything (exit code 3) and deletes nothing: regenerate that key in the BAND web app, or pass `--suffix` to register a separate set. `--dry-run` makes no POST and writes nothing. Exit codes: 0 done, 2 configuration problem, 3 stopped on an existing agent, 4 BAND refused or could not be reached.

### Fog of war

The analysis reads blue rows, the board, the score and the match history of breaches, restores and detections, all of which the defenders could see when they happened. Nothing about an unrevealed attacker reaches the BAND room, `state.backup` or the specialist's policy.

## Details that hold in code (not stated above)

### Fog of war in the state message

While `phase` is `"running"`, a red actor that is not revealed is sent as: `node_id` = the node where the defenders last saw it (its start node if never seen), `action` = `"WAIT"`, `target_node` = null, `revealed` = false, `busy_ticks` = 0. Its ledger row of the tick is sent with `action` = `"UNSEEN"`, `outcome` = `"undetected"`, `reason_text` = `"Not seen by the defenders."` and `node_id`, `cve_id`, `score_used`, `score_source`, `p_breach`, `roll`, `policy`, `llm_model`, `llm_latency_ms` null (every fogged row looks the same, whatever the red did). `GET /api/ledger` blanks the same rows while the match runs, and a late `{"t":"reason"}` for such a row is not sent. When the match ends the full rows are available from `GET /api/ledger`. A red is revealed for 2 ticks after a breach, for 3 ticks when a failed exploit on a monitored node is detected (80% chance), and while it stands on a monitored node.

### More HTTP details

| Route | Detail |
|---|---|
| `GET /api/status` | Which optional parts are live, no secrets: `phase`, `tick`, `tick_seconds`, `data_source` (`serving` or `serving_mock`), `asof_date`, `advancing`, `last_advance`, `llm` (`enabled`, `model`, `scheduled`, `ok`, `failed`, `dropped`), `band` (`enabled_flag`, `active`, `disabled_reason`, `chat_resolved`, `queued` and its counters), `band_team` ("Call for backup": `feature`, `enabled_flag`, `active`, `disabled_reason`, `room_resolved`, `listening`, `queued`, `posts`, `polls`, `errors`, `accepted`, `stopped_agents`, `specialists_in_room`, `counters`, `flow` = the flow's status in this match or null, `offline`), `telem` (`enabled`, `searches`), `clients` |
| `GET /api/world` | Also `data_source`, `last_advance` (the last result object of `advance.py`, or null) and `advance` |
| `GET /api/ledger?limit=` | `limit` 1..5000, default 200 |
| `POST /api/match/start?seed=` | `seed` optional. 200 with `{"ok":true,"match_id","seed","phase":"running"}`; 409 when a match is already running |
| `POST /api/world/advance` | 202 when started; 409 when an advance is already running; 503 when `scripts/advance.py` is not found |

### Origin checks

- A WebSocket handshake or a non-GET request with an `Origin` header that is not on the allow list, or with `Sec-Fetch-Site: cross-site`, is refused. The WebSocket is closed with code 1008 before it is accepted (the browser sees HTTP 403). The HTTP request gets 403 `{"ok": false, "message": "cross-site request refused"}`.
- Allow list: `http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:8000`, `http://127.0.0.1:8000`, plus the comma-separated origins in the environment variable `PT_ALLOWED_ORIGINS`.
- A request with no `Origin` header (curl, tests, health checks) is allowed. GET requests are never refused.
- At most 8 WebSocket clients; one more is closed with code 1013. A client message longer than 1024 bytes is ignored. A client that does not read (send queue of 8 full, or a send slower than 1 s) is closed with code 1011.

### `scripts/advance.py` options

| Option | Meaning |
|---|---|
| (none) | Run the next Monday-to-Sunday week after `serving/world.json`'s `asof_date` |
| `--day0` | Delete the pipeline database and rebuild everything as of 2026-03-22 |
| `--rerun` | Re-run the current week (same window, new run id). Log label `rerun-<date>` |
| `--dry-run` | Print the Bruin commands and exit. Takes no lock |
| `--lock-status` | Print `{"lock": "free" | "held" | "stale", ...}` (with `pid`, `platform`, `age_s`, and `why` when stale) and exit: code 1 when held, else 0 |

The lock file `data/.advance.lock` holds `{"pid", "platform", "started", "started_epoch"}`. A lock whose process is gone, or that is older than 2 hours, is stale and the next advance removes it; a live lock makes `advance.py` refuse to start. The last stdout line is always the one JSON object described under "Advancing the world".

## Web client (Vite dev server on port 5173)

- Proxies `/api` and `/ws` to `http://localhost:8000`.
- Player 1: `KeyW/KeyA/KeyS/KeyD` to move, `KeyE` patch, `KeyQ` monitor, `KeyR` restore, `KeyF` isolate.
- Player 2: arrow keys to move, `Enter` patch, `ShiftRight` monitor, `Slash` restore, `Period` isolate.
- Shows: the 3D grid and characters, scoreboard, world clock with score source and both precision numbers, the decision ledger, a patch-priority hint for the node each player stands on, Start / Reset / Advance week buttons, and the NVD notice: "This product uses data from the NVD API but is not endorsed or certified by the NVD."
