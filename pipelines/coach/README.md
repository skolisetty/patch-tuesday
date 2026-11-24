# The coach pipeline

A small [Bruin](https://getbruin.com) pipeline that does the Coach's analysis for "Call for backup"
(see `docs/BAND.md`): at a checkpoint of a match it reads the match's decision ledger and the board
the Blue team sees, works out the evidence figures for the three weaknesses (slow response,
exposure, blind spots) and the per-agent and per-player figures, checks them, and writes one JSON
file. The game reads only that file.

- Pipeline name `patch-tuesday-coach`, 16 SQL assets in the schema `coaching`, 261 quality checks.
- Its own DuckDB connection, `duckdb-coach` (see `.bruin.yml.example`). It never opens the weekly
  pipeline's database and never takes `data/.advance.lock`, so it can run during a match while
  "Advance week" is running.
- A run takes about 2 to 3 seconds from Windows through WSL (Bruin itself reports 1.2 to 1.5 s).

The schema is called `coaching`, not `coach`, because the database file is `coach.duckdb`: DuckDB
names the catalog after the file, and a schema with the catalog's name makes every `coach.x`
reference ambiguous ("Ambiguous reference to catalog or schema").

## How it is run

```
python scripts/coach.py --match <id> --ledger <ledger.jsonl> --state <state.json> --out <metrics.json>
python scripts/coach.py --match <id> --ledger <...> --state <...> --dry-run     # print the commands only
```

`scripts/coach.py` (standard library only; from Windows it runs Bruin through WSL, inside WSL
directly) copies the two inputs into the run folder `data/coach/<match>-<tick>/` and into the fixed
folder `data/coach/current/`, runs `bruin run --workers 1 pipelines/coach/pipeline.yml` from the
repo root, and copies `data/coach/current/metrics.json` to the run folder and to `--out`. The run
folder also keeps `bruin.log`. Its last stdout line is one JSON object:

```
{"ok": true, "metrics_path": "...", "pipeline_run_id": "2026_11_23_16_11_27", "checks_passed": 261,
 "checks_failed": 0, "failed_checks": [], "seconds": 2.4, "message": "..."}
```

`ok` is true only when Bruin exited 0, nothing was reported as failed and a fresh metrics file for
this match exists. On a failed check `failed_checks` names it, for example
`coaching.score_reconciliation:custom-check:red_income_from_the_ledger_reconciles_with_the_score_breakdown`,
and no metrics file is written. The script holds `data/.coach.lock` while it runs.

The inputs reach the SQL through the fixed folder `data/coach/current/` (relative paths in Bruin
SQL resolve against the folder Bruin is started from, the repo root). Pipeline variables were not
used for the paths: a string value would have to survive `wsl -e bash -lc` quoting, and one
writer at a time is enforced by the lock anyway. The pipeline variables (`pipeline.yml`) hold the
game constants and the Coach's thresholds instead.

The game does not call the script itself: `game/coach_bruin.py` does, in a background thread
(`available()`, `request(match_id, tick, ledger_rows, public_state)`, `poll(handle)`).

## Inputs

**`ledger.jsonl`**: this match's ledger rows up to the checkpoint, one JSON object per line, the
game's ledger row shape (`docs/CONTRACTS.md`). Read: `ledger_id, match_id, tick, actor_id, team,
kind, action, node_id, cve_id, score_used, outcome, policy` and the optional `seen`. Required: one
row per actor per tick for `p1, p2, blue1, blue2, blue3, red1, red2`, ticks 0 to `tick - 1`, one
match id. Optional `seen` (boolean, attackers' rows): true when that attacker was in sight of the
defenders at the end of the tick. In the game that is `not state.is_unseen(state, row)` evaluated
right after the tick was resolved. When every attacker row carries it, the sighting figures are exact.

**`state.json`**: the board the Blue team sees at the checkpoint. The game's state message works
as it is (extra keys are ignored); the fields read are:

```
{"match_id": "...", "tick": 40,
 "nodes": [{"id": 0, "name": "MAIL-01", "value": 2, "state": "healthy|compromised|isolated", "monitored": false,
            "cards": [{"cve_id": "...", "score": 0.94, "in_kev": true, "priority_rank": 12,
                       "patch_state": "unpatched|patching|patched", "dropped_tick": null}]}],
 "score": {"red": 26.0, "blue": 16.0, "breaches_total": 2, "breaches_in_top50": 1,
           "breakdown": {"blue": {"patch": 10.0, "restore": 6.0, "isolate_cost": 0.0},
                         "red": {"first_breach": 10.0, "held": 16.0}}},
 "tuning": {"first_breach_points": 5.0, "...": "optional, see pipeline.yml"}}
```

`tick` N means N ticks have been played. `danger` / `known_exploited` / `priority` are accepted as
other names for `score` / `in_kev` / `priority_rank`. `dropped_tick` (the tick a card landed in a
mid-match "Advance week") and `tuning` are optional.

## Assets

| Layer | Asset | Rows | Purpose |
|---|---|---|---|
| load | `coaching.match_state` | 1 | The checkpoint: match, tick, scores with their breakdown, game constants, this run's id |
| load | `coaching.machines` | 20 | The board: value, state, monitored now, grid position |
| load | `coaching.cards` | one per card | Danger, known-exploited flag, priority, patch state |
| load | `coaching.ledger_rows` | 7 per tick | The ledger snapshot. The only table that holds the attackers' rows; never exported |
| derive | `coaching.monitor_windows` | one per MONITOR or restore | When each machine was monitored, and thanks to whom |
| derive | `coaching.isolation_ticks` | one per machine per isolated tick | Ticks Red earns nothing and Blue pays |
| derive | `coaching.patches` | one per finished patch | Worth (machine value x danger) and points earned |
| derive | `coaching.breach_spans` | one per breach | Machine, tick breached, tick restored or still open, length, ticks paid, flags |
| derive | `coaching.attacker_sightings` | one per attacker per tick | In sight or not, newly detected or not, who gets the credit |
| metrics | `coaching.score_reconciliation` | 1 | **The gate**: the five score parts rebuilt from the ledger next to the scoreboard's |
| metrics | `coaching.team_metrics` | 1 | The evidence figures of the three weaknesses |
| metrics | `coaching.blue_activity` | 5 | What each of blue1-3, p1, p2 did |
| metrics | `coaching.agent_metrics` | 3 | blue1, blue2, blue3 |
| metrics | `coaching.player_metrics` | 2 | p1, p2 |
| out | `coaching.summary` | 1 | One-row summary of the analysis |
| out | `coaching.export_metrics` | file | Single `COPY` to `data/coach/current/metrics.json`, last |

Every asset has a description, owner, domain, tags and described, typed, checked columns.
`bruin lineage pipelines/coach/assets/out/export_metrics.sql --full` prints the graph.

## Checks

261 checks in a run (column checks and custom checks). The blocking custom checks that carry the weight:

| Asset | Blocking check | What it guards |
|---|---|---|
| `score_reconciliation` | red income from the ledger reconciles with the score breakdown | Holding (machine value x ticks paid) and first breaches derived from the ledger equal the scoreboard's `held`, `first_breach` and Red's score within `reconcile_tolerance` (0.05) |
| `score_reconciliation` | blue points from the ledger reconcile with the score breakdown | The same for patches, restores, isolation cost and Blue's score |
| `score_reconciliation` | breaches in the ledger equal the scoreboard's breach count | Every breach is a span and the other way round |
| `ledger_rows` | the match id is single-valued; ledger and board are the same match | No mixed snapshot |
| `ledger_rows` | ledger ticks do not exceed the state's tick | The board was not captured before the ledger |
| `ledger_rows` | every actor has one row for every tick played | No gap that would silently lower a figure |
| `machines`, `cards` | the board has 20 machines; every card sits on a machine; a CVE appears once per machine | A whole board |
| `breach_spans` | a restore comes after its breach; spans on one machine do not overlap; the board agrees with the open breaches | The spans are real spans |
| `attacker_sightings` | no machine is kept for an attacker that was not detected | Fog of war |
| `team_metrics` | the two income shares add up; breach counts are consistent; sighting bounds are ordered | Shares within 0..1 and consistent (column checks do the 0..1 and non-negative part) |
| `blue_activity`, `agent_metrics`, `player_metrics` | five actors, three agents, two players | Always a figure for everyone |
| `summary` | exactly one summary row; the summary is this run's | One row, not a stale one |

A failed blocking check skips everything downstream, so `export_metrics` does not run and no
metrics file exists: the game then uses its local figures.

## The metrics file

One JSON object. `test_coach.py` checks the field list is exactly this.

```
{"match_id": "m-...", "tick": 40, "generated_by": "bruin", "pipeline_run_id": "2026_11_23_16_11_27",
 "team": {...}, "agents": {"blue1": {...}, "blue2": {...}, "blue3": {...}}, "players": {"p1": {...}, "p2": {...}}}
```

### `team`

| Field | Type | Definition |
|---|---|---|
| `red_points`, `blue_points` | number | The scoreboard at the checkpoint |
| `red_points_from_holding` | number | Machine value x ticks paid, summed over all breaches (derived from the ledger; equals the scoreboard's `held`) |
| `red_points_from_first_breaches` | number | `first_breach_points` x machines breached for the first time |
| `red_share_from_holding`, `red_share_from_first_breaches` | number 0..1 | Each over their sum, 3 decimals; both 0 when Red has no points |
| `breaches` | integer | Breaches so far |
| `machines_ever_breached` | integer | Different machines breached at least once |
| `avg_ticks_breached` | number | Average ticks a breach lasted (restore tick, or the checkpoint tick for an open one, minus breach tick), 2 decimals; 0 with no breach |
| `longest_ticks_breached` | integer | The longest; 0 with no breach |
| `machines_breached_now` | integer | Open breaches (a breached machine that is isolated counts) |
| `machines_breached_now_names` | list of strings | Their names, by machine id; `[]` when none |
| `open_hot_cards` | integer | Unpatched cards with danger >= 0.30 on machines of value >= 2 |
| `breaches_through_long_open_cards` | integer | Breaches through a card on which no patch had been started and that had been on the board 10 ticks or more |
| `breaches_through_top50_cards` | integer | The scoreboard's `breaches_in_top50` (priority 50 or better at the moment of the breach) |
| `breaches_on_unmonitored_machines` | integer | Breaches of a machine not monitored at that moment |
| `attacker_ticks` | integer | 2 x ticks played from `red_entry_delay` (10) on |
| `attacker_ticks_unseen`, `attackers_unseen_share` | integer, number 0..1 | Attacker time out of sight, counting only sightings that are certain |
| `attacker_ticks_unseen_if_all_detected`, `attackers_unseen_share_if_all_detected` | integer, number 0..1 | The same if every failed exploit on a monitored machine was detected |
| `detections`, `detections_if_all_detected` | integer | Times an attacker went from out of sight to in sight without breaching, in the two scenarios |
| `sightings_exact` | boolean | True when the two scenarios coincide, so the six fields above are exact |
| `sightings_basis` | `"derived"` or `"seen_flags"` | `seen_flags` when the ledger carried `seen` on every attacker row |
| `cards_patched`, `restores` | integer | Team totals |
| `score_reconciliation_max_diff` | number | Largest difference between a derived score part and the scoreboard (always below the tolerance) |

### `agents.blue1 | blue2 | blue3` and `players.p1 | p2`

| Field | Type | Definition |
|---|---|---|
| `role` (agents only) | string or null | The agent's policy: `triage`, `patcher`, `hunter`. Null only if the agent has no ledger row |
| `ticks` | integer | Ledger rows of the actor |
| `ticks_waiting`, `wait_share` | integer, number 0..1 | Rows whose action is WAIT, and their share (3 decimals). A player with 1.0 has not acted yet |
| `cards_patched` | integer | Patches finished |
| `patch_worth` | number | Machine value x danger of those cards, 2 decimals |
| `patch_points` | number | Points those patches earned: a card attacked before its patch finished earns 0 |
| `restores` | integer | Restores finished |
| `restores_started` | integer | Restores started. For Triage and Patcher: the times they broke off their own job to restore |
| `monitors`, `isolates` | integer | MONITOR actions, ISOLATE actions that took effect |
| `attackers_revealed` | integer | Certain detections credited to the actor's monitoring (its MONITOR, or the watch after its restore, was the latest to cover the machine) |
| `near_breached_without_restoring` | integer | Breaches the actor stood on or one step from while breached, without restoring them (and not restoring them at the checkpoint). Counts breaches, not ticks |

### Null rules and what could not be derived

No figure is guessed. Three things are not in the two inputs:

1. **Whether a failed exploit on a monitored machine was detected.** The game rolls a die (0.8)
   and the ledger does not record it. The sighting figures are therefore given for both outcomes
   (`..._unseen` counts only what is certain and is the upper bound; `..._if_all_detected` is the
   lower bound) with `sightings_exact` saying whether they coincide. Over 30 simulated matches
   cut at tick 40 the engine's own count was inside the bounds every time, and the bounds
   coincided (exact) in 22; where they differed it was by 1 to 3 attacker-ticks out of 60. With the
   optional `seen` flags in the ledger the figures are exact. They are never null.
2. **When a card arrived on the board** (`dropped_tick`). The state message does not carry it.
   Without it a card counts as on the board since tick 0, which is exact for a match with no
   mid-match "Advance week" before the checkpoint and can overcount
   `breaches_through_long_open_cards` after one.
3. **A card's priority at the moment it was breached.** Priorities change with the world, so
   `breaches_through_top50_cards` is the scoreboard's own counter, not a derived figure.

The only field that can be null is `agents.*.role`. A share or an average with nothing to divide
by is 0, not null, so the consumer needs no null handling for numbers.

## Fog of war

The ledger snapshot holds every actor's rows. `coaching.ledger_rows` is the only table with the
attackers' positions and attempts and it is never exported. Breach spans carry a machine and
ticks (a breach is announced to the defenders) but no attacker and no CVE. Sightings carry a
machine only on the tick an attacker was detected there, which a blocking check enforces. The
metrics file holds counts, shares and the names of machines that are breached now (the board
shows those). `game/coach_bruin.py` drops the state message's `actors` before writing `state.json`.

## Tests

```
python tests/pipeline/test_coach.py                 # 3 hand-checked fixtures, tampered breakdown, bad ledgers, seen flags, the game seam
python tests/pipeline/coach_sim_check.py            # freshly simulated matches against the engine's own bookkeeping
python tests/pipeline/coach_compare.py --all        # pipeline next to a local metrics function, if game/backup.py has one
python tests/pipeline/coach_real_cut.py --ledger ledger/ledger.jsonl     # one real match cut from a ledger file
python -m pytest tests/game/test_coach_bruin.py -q  # offline, no WSL
```
