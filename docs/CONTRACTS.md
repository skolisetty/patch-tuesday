# Patch Tuesday: interface contracts

These are the fixed interfaces between the three parts of the project. Each part is built separately, so do not change a shape here without changing every reader.

| Part | Folder | Runs on |
|---|---|---|
| Pipeline (Bruin + DuckDB) | `pipelines/`, `scripts/` | WSL Ubuntu (`bruin` lives at `~/.local/bin/bruin`) |
| Game server (FastAPI) | `game/` | Windows Python 3.12, venv at `.venv/` |
| Web client (Vite + three.js) | `web/` | Windows Node 24 |

The repo root is `C:\Users\kolis\Documents\Hackathon\patch-tuesday`, seen from WSL as `/mnt/c/Users/kolis/Documents/Hackathon/patch-tuesday`.

## Runtime rules

- The pipeline database is `/home/kolis/.patch-tuesday/world.duckdb` inside WSL. Only `bruin` opens it, one run at a time.
- The game never opens the pipeline database. It reads `serving/*.json` and writes `data/game.duckdb`, `ledger/ledger.jsonl` and `ledger/reasons.jsonl`.
- `bruin` is always run from the repo root, with `--workers 1`.
- From Windows, call Bruin as `wsl -e bash -lc "cd /mnt/c/Users/kolis/Documents/Hackathon/patch-tuesday && export PATH=$HOME/.local/bin:$PATH && bruin ..."`.
- Secrets live in `.env` at the repo root (git-ignored). Load them with code. Never print, log or echo a secret value.
- All attacks are abstract game moves. No exploit code, no payloads, no reference URLs, no CVE description text in any served file.

## Environment variables (`.env`)

| Name | Used by | Meaning |
|---|---|---|
| `NVD_API_KEY` | optional | Not needed: the pipeline uses the NVD bulk year feeds |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | game | OpenAI-compatible endpoint for one-sentence reasons. If `LLM_API_KEY` is empty, use template reasons |
| `BAND_API_KEY` | game | BAND agent key. Integration is off unless `BAND_ENABLED=1` |
| `TELEM_API_KEY` | game | Telem key. Integration is off unless `TELEM_ENABLED=1` |

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
- Actor ids: `red1`, `red2`, `blue1`, `blue2`, `blue3`, `p1`, `p2`.
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

## Web client (Vite dev server on port 5173)

- Proxies `/api` and `/ws` to `http://localhost:8000`.
- Player 1: `KeyW/KeyA/KeyS/KeyD` to move, `KeyE` patch, `KeyQ` monitor, `KeyR` restore, `KeyF` isolate.
- Player 2: arrow keys to move, `Enter` patch, `ShiftRight` monitor, `Slash` restore, `Period` isolate.
- Shows: the 3D grid and characters, scoreboard, world clock with score source and both precision numbers, the decision ledger, a patch-priority hint for the node each player stands on, Start / Reset / Advance week buttons, and the NVD notice: "This product uses data from the NVD API but is not endorsed or certified by the NVD."
