# Running Patch Tuesday

One runbook: set up once, daily commands, sharing with teammates, troubleshooting.

Two places run code:

- **WSL Ubuntu** runs the data pipeline (Bruin + DuckDB). It writes `serving/*.json`.
- **Windows** runs the game server (Python, port 8000) and the web client (Node, port 5173). They
  read `serving/*.json`.

In this page `<repo>` is the folder you cloned into, for example `C:\code\patch-tuesday`. Inside WSL
the same folder is `/mnt/c/code/patch-tuesday`; this page writes that as `<repo-in-wsl>`.

Terms are in the [glossary](GLOSSARY.md).

## 1. Set up once

1. **WSL and Bruin.** Install WSL Ubuntu (24.04 was used: `wsl --install -d Ubuntu-24.04` in an
   admin PowerShell). Inside Ubuntu install Bruin v0.11.831 with its install script from
   https://getbruin.com/docs (it puts `bruin` in `~/.local/bin`), and put that folder on the PATH:
   `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc`. Check with `bruin --version`. Bruin
   manages its own Python for the Python assets through uv; WSL needs only `python3` (3.12) for
   the scripts.
2. **Data.** In WSL, from the repo root: `bash scripts/pull_data.sh` (NVD feeds, KEV, 65 EPSS
   files, ATT&CK, mappings, licence texts, the robot model).
3. **Pipeline config.** `mkdir -p ~/.patch-tuesday` (DuckDB does not create a missing folder),
   then `cp .bruin.yml.example .bruin.yml` and set `path` to `/home/<you>/.patch-tuesday/world.duckdb`.
   Keep the database on the WSL ext4 disk, not under `/mnt/c`. Check with
   `bruin validate pipelines/patch_tuesday` (expected: `26 assets ... all good`).
4. **Secrets.** `cp .env.example .env` and fill in only what you use (`LLM_BASE_URL`, `LLM_API_KEY`,
   `LLM_MODEL` for LLM reasons; `BAND_*`, `TELEM_*` for the optional integrations). Every value may
   stay empty: the game then uses template reasons. `.env` is git-ignored; never paste a key on a
   command line.
5. **Day 0.** `python3 scripts/advance.py --day0` in WSL (about 2 minutes: NVD parse 69 s, EPSS 53
   files 53 s) writes `serving/*.json` with data as of 2026-03-22.
6. **Game and client (Windows, repo root, PowerShell).**

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\pip install -r game\requirements.txt
   cd web; npm install; cd ..
   ```

   Vite needs Node 20.19+ or 22.12+.
7. **Play.** `scripts\start.ps1`, then open http://localhost:5173.

`serving\*.json` must exist (step 5); without it the server falls back to `serving_mock\` (fake
CVE ids, development only).

## 2. Daily commands

### Play (Windows, PowerShell, repo root)

| Step | Command |
|---|---|
| Start server + web client | `scripts\start.ps1` (prints the URL: http://localhost:5173) |
| Faster rehearsal ticks | `scripts\start.ps1 -TickSeconds 1` (sets `PT_TICK_SECONDS`) |
| With BAND / Telem | `scripts\start.ps1 -Band -Telem` (sets `BAND_ENABLED=1`, `TELEM_ENABLED=1`) |
| Stop both | `scripts\stop.ps1` (refuses while an advance runs; `-Force` stops it anyway) |
| Server only | `.venv\Scripts\python -m uvicorn game.server:app --port 8000 --ws-max-size 4096` |
| Headless balance check | `.venv\Scripts\python -m game.simulate --serving serving --seeds 30 --seed 1 --fast --drop-tick -1` |
| Game tests | `.venv\Scripts\python -m pytest tests/game -q` |
| End-to-end test | `.venv\Scripts\python -m pytest tests/e2e -q -s` (real server, two WebSocket players, about 30 s) |

Logs and process ids of `start.ps1` go to `logs\run\`.

In the browser: the briefing card explains the goal; **Start** on it begins a 150-tick match
(5 minutes at 2 s a tick). Player 1 uses W A S D, E patch, Q monitor, R restore, F isolate. Player 2
uses the arrows, Enter patch, right Shift monitor, `/` restore, `.` isolate. How to play is in
[PLAY.md](PLAY.md); what each part of the screen means is in [UI_GUIDE.md](UI_GUIDE.md).

**Advance week** runs `scripts/advance.py` through WSL in the background (about 20-30 s for one
week). Ticks keep running and the screen shows the pipeline's progress step by step. When the new
`world.json` lands, the gate verdict is shown, the week's cards drop onto the board and the red
agents open a campaign.

After a rehearsal, put the world back to day 0 with `bash scripts/reset.sh` (WSL, about 2 minutes).
A running server keeps its current week while `reset.sh` has deleted `serving/*.json` (it never
falls back to the mock data once real data is loaded), and a world that is not newer than the board,
such as the rebuilt day 0, drops nothing onto a running match: the next match deals from it.

### Pipeline (WSL, repo root)

All Bruin commands run inside WSL from the repo root with `--workers 1`. From Windows, prefix with
`wsl -e bash -lc "cd <repo-in-wsl> && export PATH=$HOME/.local/bin:$PATH && ..."`.

| Step | Command | Time measured |
|---|---|---|
| One-off data pull | `bash scripts/pull_data.sh` (WSL) | (see the script) |
| Config | `mkdir -p ~/.patch-tuesday`, then `cp .bruin.yml.example .bruin.yml` and set the DuckDB path | |
| Validate | `bruin validate pipelines/patch_tuesday` | `26 assets ... all good` |
| Day 0 (as of 2026-03-22) | `python scripts/advance.py --day0` or `bash scripts/reset.sh` | about 2 min (NVD parse 69 s, EPSS 53 files 53 s) |
| Advance one week | `python scripts/advance.py` (Windows or WSL Python) | about 20-25 s |
| Re-run the current week | `python scripts/advance.py --rerun` | about 30 s |
| Lock state | `python scripts/advance.py --lock-status` | |
| Advance decision tests | `python tests/pipeline/test_advance.py` (fallback rule, lock, re-run; no Bruin) | under 1 s |
| NVD enrichment report | `python3 tests/pipeline/nvd_enrichment_report.py` (no run active) | about 10 s |
| Rehearse all 12 weeks | the backfill command below, then `bash scripts/reset.sh` | 4 min + 2 min |
| Serving contract test | `python3 tests/pipeline/test_serving_contract.py` | about 1 s |
| Leakage audit | `python3 tests/pipeline/leakage_audit.py` (no run active) | about 20 s |
| Ledger into the warehouse | `bruin run --workers 1 pipelines/game_ledger/pipeline.yml` (between matches) | about 2 s |
| Ad hoc query | `bruin query --connection duckdb-default --query "select * from ml.eval_metrics"` | |
| Dependency graph | `bruin lineage pipelines/patch_tuesday/assets/serve/export_world.sql` (add `--full`) | |

The backfill command (results in [MODEL_AND_GATE.md](MODEL_AND_GATE.md)):

```bash
bruin backfill pipelines/patch_tuesday --start-date 2026-03-23 --end-date 2026-06-14 \
  --partition weekly --timezone UTC --on-failure continue --workers 1
```

What `advance.py` does step by step, and its lock, are described in
[PIPELINE.md](PIPELINE.md#one-simulated-week). After a manual `bruin run` whose gate failed, always
run `bruin run --workers 1 --tag serve` for the same window, or use `advance.py --rerun` instead
(why: [MODEL_AND_GATE.md](MODEL_AND_GATE.md#proving-the-gate-blocks)). Never set `PT_DEGRADE_MODEL`
in play; it is a test switch.

## 3. Share with teammates (ngrok)

Teammates on other networks can join the same match through an [ngrok](https://ngrok.com) tunnel to
the web client. ngrok is a separate tool with its own install and sign-in.

1. Start the tunnel and copy the `https://...` address it prints:

   ```powershell
   ngrok http 5173 --host-header=rewrite
   ```

2. Start the game with that address allowed. The server refuses browsers from any other address
   (see [What the server accepts](#what-the-server-accepts)):

   ```powershell
   $env:PT_ALLOWED_ORIGINS = "https://<your-ngrok-address>"
   scripts\start.ps1
   ```

   If the game was already running, stop it first with `scripts\stop.ps1`. You can also put
   `PT_ALLOWED_ORIGINS=https://<your-ngrok-address>` in `.env` instead.
3. Send teammates the https address.

Everyone joins the same single match and sees the same board. Each remote browser can drive
Player 1 (W A S D keys) or Player 2 (arrow keys): agree who takes which. Anyone can press Start,
Reset or Advance week. The server serves at most 8 browser connections.

On the same local network no tunnel is needed: add the LAN address teammates will type (for
example `http://192.168.1.20:5173`) to `PT_ALLOWED_ORIGINS`.

## 4. Optional switches

All read from `.env` or the environment (secrets stay in `.env`):

| Variable | Effect |
|---|---|
| `LLM_REASONS=0` | Template reasons only, even when `LLM_API_KEY` is set |
| `BAND_ENABLED=1` | Mirror highlights into the BAND room as events (needs a remote **agent** key, see below) |
| `BAND_CHAT_ID` | The room to post to; otherwise the agent's room whose title contains "Patch Tuesday" |
| `TELEM_ENABLED=1` | The blue Hunter may spend a tick on INTEL: one Telem search for a board CVE, bounded to the world week |
| `TELEM_MAX_PER_MATCH` | Cap on Telem searches per match (default 3); `TELEM_PROVIDERS` (default `brave`) |
| `TELEM_MAX_PER_PROCESS` | Cap on Telem searches for the whole server process (default 10); then INTEL answers "budget spent" |
| `BAND_MAX_PER_HOUR`, `BAND_MAX_EVENTS` | BAND posts per rolling hour (default 120, extra events dropped) and per process (default 300, then the mirror switches off) |
| `PT_TICK_SECONDS` | Tick length override (default 2.0); the screen's time-left clock reads it from `/api/status` |
| `PT_ALLOWED_ORIGINS` | Extra browser origins (comma-separated) allowed to drive the server, e.g. a LAN address or an ngrok address |
| `PT_SERVER_PORT`, `PT_WEB_PORT` | Read by `web/vite.config.js` only: the server port the web client proxies to (default 8000) and the web client's own port (default 5173). For testing a second copy next to a running one |

Page options such as `?mock=1` (the screen on built-in fake data, no server) are listed in
[UI_GUIDE.md](UI_GUIDE.md).

### BAND setup

Once, in https://app.band.ai: **Agents** > **Connect remote agent**, name it `Patch Tuesday`,
**Connect Agent**, and copy the `band_a_...` key shown once into `.env` as `BAND_API_KEY` (a
`band_u_...` user key is refused by the Agent API and the mirror switches itself off). Then
**Rooms** > **+** > **Create room**, select that agent, and give the room a title containing
`Patch Tuesday` (or put the room id in `.env` as `BAND_CHAT_ID`). The game never posts into any
other room.

The mirror posts at most one event every 3 s and at most `BAND_MAX_PER_HOUR` an hour, drops events
when its queue is full, never blocks a tick, and switches itself off on HTTP 401, 403 or 429 and
after `BAND_MAX_EVENTS` posts.

### Telem INTEL

Telem INTEL results are reduced to a count, domains and dates ("Hunter found 3 public reports on
CVE-... this world week (nvd.nist.gov 2026-03-17, ...)"). Headlines are never shown, because
advisory titles carry affected versions and weakness descriptions. Results whose headline looks
like exploit material, and results from exploit archives, exploit-intelligence sites or code
hosts, are dropped.

## 5. Good to know

### What the server accepts

Any web page the player has open could otherwise talk to `localhost:8000` (browsers apply no
same-origin policy to WebSockets, and a form POST needs no preflight). So `/ws` closes with code
1008 and every POST answers 403 when the request carries an `Origin` other than
`http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:8000` or
`http://127.0.0.1:8000` (plus `PT_ALLOWED_ORIGINS`), or `Sec-Fetch-Site: cross-site`. Requests
without an `Origin` (curl, scripts, tests) are allowed. WebSocket frames over 4 KiB are refused by
uvicorn (`--ws-max-size 4096`) and anything over 1 KiB is ignored by the server; at most 8 sockets
are served. Each socket has its own send queue: a client that stops reading is closed (code 1011),
so the browser reconnects, and the tick loop never waits on a socket.

### What the humans see

The defenders, human or agent, see a red agent only while it is revealed (after a breach, a
detected failed exploit, or while it stands on a monitored machine). While a match runs, an
unrevealed red is drawn faint at the machine where it was last seen, tagged "LAST SEEN", and its
log rows arrive as `UNSEEN` with machine, CVE and breach chance blanked. The full rows are in the
ledger files from the start and in `/api/ledger` once the match ends. A key pressed while that
player's PATCH, MONITOR, ISOLATE or RESTORE is still running is kept and applied on the first free
tick (the player panel shows "Queued: … runs when the current action finishes.").

## 6. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| PowerShell refuses to run `start.ps1` | Execution policy `Restricted`, or a zip download with Mark-of-the-Web. Use `powershell -ExecutionPolicy Bypass -File scripts\start.ps1` (and the same for `stop.ps1`) |
| `Port 8000 (or 5173) is already in use` | The game is already running. `scripts\stop.ps1`, then start again |
| Warning `serving\world.json is missing`, cards show `CVE-2099-...` | Day 0 was never built, so the server uses the mock data. Run setup step 5 |
| `bruin validate` or day 0 fails opening the database | `~/.patch-tuesday` does not exist (DuckDB does not create it), or `.bruin.yml` points under `/mnt/c`. See setup step 3 |
| Advance week does nothing; `advance.py` says the lock is held | Another advance is running, or one crashed. `python scripts/advance.py --lock-status`. A lock whose process is gone, or older than 2 hours, is stale and the next advance (or `reset.sh`) removes it; `bash scripts/reset.sh --force` removes it at once |
| Advance returns `ok: false` after 2026-06-14 | That is the last available week. `bash scripts/reset.sh` goes back to day 0 |
| Advance returns `ok: false` with check names | A check other than the gate failed. `serving/` keeps the last good week. Read the newest file in `logs/advance/` |
| Advance pressed late in a match and no cards drop | No drop happens in the last 25 ticks of a match; the new week is dealt in the next match |
| A teammate's browser shows no live connection, or buttons answer 403 | Their address is not in `PT_ALLOWED_ORIGINS`. See [Share with teammates](#3-share-with-teammates-ngrok) |
| BAND posts nothing | A `band_u_...` user key was used, or the mirror switched itself off after HTTP 401, 403 or 429. Use a `band_a_...` agent key and restart |
| `stop.ps1` refuses | An advance is running. Wait for it, or `scripts\stop.ps1 -Force` |
