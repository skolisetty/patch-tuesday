# Demo script and judges' sheet

A 2-minute demo that uses only what the system really does, and a one-table sheet that maps each
hackathon criterion to the screen and to the repo. Terms are in the [glossary](GLOSSARY.md).

## What the demo shows

1. On day 0 the gate **blocks** our model (a 0-0 tie), so the EPSS baseline sets the danger.
2. One click replays the next real week through Bruin. This time the gate **passes**, 2 against 0,
   and our model takes over.
3. The week's new cards drop, and the red attackers open a campaign against them.
4. The match ends with "what really happened": which cards really became known exploited later.

An optional second beat of about 60 seconds shows BAND: [Call for backup](#the-band-beat-call-for-backup-about-60-seconds).

## Before you speak (5 minutes)

| Step | Command or action | Why |
|---|---|---|
| 1 | In WSL: `bash scripts/reset.sh` (about 2 minutes) | Puts the data back to day 0, 2026-03-22. The 2-against-0 result belongs to the first advance only |
| 2 | In PowerShell: `scripts\start.ps1 -TickSeconds 1` | 1-second ticks: a whole match takes 2 minutes 30 seconds instead of 5 minutes |
| 3 | Open http://localhost:5173 | The briefing card is showing. The data panel reads DATA AS OF 2026-03-22 with the EPSS badge |
| 4 | **45 seconds before your slot, press Start** | The match must end inside your 2 minutes. Until you take the keys the five AI agents play on without you |

Setup of the machine itself is in [RUNNING.md](RUNNING.md). Advance week needs WSL and Bruin on
this machine.

## The script (2:00)

Times are from the moment you start speaking. The match is then at about tick 45 of 150.

| Time | Do | Say | What the screen really shows |
|---|---|---|---|
| 0:00 | Press `?` in the top bar | "Two of us and three AI defenders protect 20 machines from two hidden AI attackers. Every card is a real vulnerability. Its danger is the predicted chance it gets exploited." | The briefing card: goal, how points move, keys. Closing it does not affect the match |
| 0:12 | Close the card. Point at the data panel | "The danger comes from a Bruin pipeline. Today is 22 March 2026 in the data. Our model tied the industry baseline 0-0, so Bruin's gate blocked it and the public EPSS score is in charge." | DATA AS OF 2026-03-22, EPSS badge, "Gate blocked (tie): our model found 0, EPSS baseline 0, of N bugs that really became exploited" |
| 0:20 | Click **Advance week** | "Now we replay the next real week. This is not an animation: Bruin is re-running 26 assets on real data and retraining the model." | The pipeline strip: `landing › raw › stg › feat › ml › GATE › serve`, steps lighting up, "asset N of 26". The match keeps ticking |
| 0:25 | While it runs, move and patch: Player 1 presses E on the card its panel recommends | "Meanwhile we play. My panel tells me the best move and what it is worth: machine value times danger." | The player panel's "next best move" line; a green feedback line; a floating "+… patch" over the machine |
| about 0:45 | Point at the banner | "The gate passed. Our model found 2 of the 32 bugs that really became exploited. The baseline found none. Bruin promoted the model, and every danger on the board is now the model's." | Banner for 8 seconds: "Gate passed: our model put 2 of the 32 soon-to-be-exploited bugs in its top 50; the EPSS baseline put 0." Data panel: DATA AS OF 2026-03-29, MODEL badge, "188,844 real CVEs visible" |
| 0:55 | Point at the board and the events strip | "The week's new vulnerabilities drop onto our machines, and the attackers go straight for them." | Events strip: "Patch Tuesday 2026-03-29 (was 2026-03-22): N new cards dropped on M machines; danger set by our model." and "Red campaign on N fresh cards (top: CVE-…, danger …%, on …)". New cards appear on machines |
| 1:05 | Point at the Agents tab | "Five agents, each with its own policy, each explaining itself in one sentence. Hidden attackers show only where they were last seen." | One card per agent (Triage, Patcher, Hunter, Opportunist, Patient) with its current action and latest reason |
| 1:20 | Open the Full log tab. Click the source chip on an EXPLOIT or PATCH row | "Every decision is logged. And each one traces back to the pipeline run that produced its danger score." | Trace line: danger from our model `hgb-2026-03-29`, pipeline run id, data as of 2026-03-29, breach chance, roll, outcome |
| 1:35 | If a machine is red, walk there and press R | "Red earns points every tick it holds a machine. Restoring it stops that." | "Red +N / tick" above the breached machine; after 3 ticks "+3 restore" |
| 1:45 | The match ends by itself | "Because these weeks are real and in the past, we can check the prediction against what really happened." | The debrief: verdict, where each team's points came from, and "What really happened" |
| 2:00 | Stop | "Bruin is not beside the game. Bruin's check decides which model the game is allowed to use." | |

### What can differ, and what to say

- **The advance takes longer than 25 seconds.** Measured time is about 20 to 30 seconds. Keep
  playing and talking. The drop still happens as long as the new week lands before tick 125 (the
  last 25 ticks of a match take no drop). With the timing above you have until about 1:20.
- **No machine is breached.** Whether an attack succeeds is a dice roll against the breach chance.
  Skip the 1:35 step and stay on the log.
- **"What really happened" lists nothing.** That is the honest result most of the time. Of the 120
  cards in this week's deck, 1 really became known exploited within 91 days, and a board holds only
  part of the deck. The debrief then says "None of this board's cards became known exploited within
  91 days." Say: "Real exploitation is rare, about 0.02% of bugs per 91 days. That is exactly why
  ranking matters."
- **The numbers "2", "0", "32" and "188,844"** are the real output of the 2026-03-29 run. They only
  appear if you started from day 0.

### Longer version (5 minutes, no early start)

Run `scripts\start.ps1` with normal 2-second ticks. Read the briefing card with the audience, press
Start on it, play for about a minute, then click Advance week. Everything else is the same.

### After the demo

`scripts\stop.ps1`, then `bash scripts/reset.sh` in WSL to return to day 0.

## The BAND beat: Call for backup (about 60 seconds)

An optional second beat, played as its own match. It shows the game's agents as separate BAND
agents talking in a room, a human approving, and a new agent joining the team through the room.
The feature is explained in [BAND.md](BAND.md).

**Layout:** the game on the left half of the screen, the BAND room "Patch Tuesday - Blue team" on
the right half (Band Desktop or https://app.band.ai).

### Before the beat

| Step | Command or action | Why |
|---|---|---|
| 1 | Once: `.venv\Scripts\python scripts\band_setup.py` | Registers the seven BAND agents and creates the room. See [BAND.md](BAND.md#set-up-once) |
| 2 | In `.env`: `BAND_BACKUP_TICK=80` and `BAND_BACKUP_WAIT_TICKS=30` | With 1-second ticks the default answer window is only 15 seconds. These two settings give you 30 seconds to reply and put the match end about a minute after Coach starts |
| 3 | `scripts\start.ps1 -Band -TickSeconds 1` | BAND on, 1-second ticks |
| 4 | Open the game and the room side by side. Press Start **75 seconds before the beat** | Coach starts its analysis at tick 80, a few seconds after you start speaking |

The times below follow from these settings and from the spacing of the messages (3 seconds apart).
Rehearse once. If Red is 15 or more points ahead after tick 20, Coach starts by itself before tick
80: then begin the beat when the Coach box appears.

### The beat

| Time | Do | Say | What the screen really shows |
|---|---|---|---|
| 0:00 | Point at the top of the right panel | "One more agent, Coach, has been watching how we play." | Game: "Coach is watching. Analysis at tick 80, or write @coach analyze in BAND." |
| 0:05 | Point at the room | "At the checkpoint it tells the team, in a BAND room, what we are weakest at. These numbers are computed from this match." | Room: Coach's analysis, 2 to 4 sentences with numbers. Game: the Coach box with the weakness and its evidence |
| 0:10 | Let the lines arrive | "Each blue agent is its own BAND agent and answers from its own side." | Room: one line each from Triage, Patcher and Hunter, 3 seconds apart |
| 0:20 | Point at Coach's question | "Coach recommends a specialist, but it does not decide. It asks us." | Room: "I recommend calling in …. Reply yes, or name another …" with your name mentioned. Game: "Asking in BAND (Patch Tuesday - Blue team): reply yes / … / no. N ticks left." |
| 0:25 | In the room type `@Coach yes` | "I approve it here, in BAND, not in the game." | Your reply in the room |
| 0:30 | Point at the room, then at the board | "The specialist was not in the room. Now it joins, says what it will do, and walks onto the board." | Room: the specialist joins and posts one line. Game: a new white-green character at JUMP-01, a sixth card marked BACKUP on the Agents tab, and "… joined the Blue team (approved by … via BAND)." in the events strip |
| 0:40 | Follow the specialist on the board | "It plays by the same rules as every other agent. Its edge is focus." | The specialist moves and acts; in the room it reports key results, at most one line every 15 seconds |
| about 1:10 | The match ends by itself | "And here is what changed after it joined." | Game: the debrief's Backup section with the before/after table. Room: Coach's before/after summary |

### What can differ, and what to say

- **Which weakness and which specialist** depend on how the match went. Do not promise "Responder".
  Read out what Coach found.
- **You miss the answer window.** Coach brings in its own recommendation and says so in the room.
  Say: "Nobody answered, so Coach went with its own recommendation."
- **BAND is slow or unreachable.** The match continues without backup. Show the flow instead with
  `http://localhost:5173/?mock=1&backup=1`, which plays it on built-in fake data, and say that it
  is the mock.
- **The before/after numbers are small.** The specialist had about 40 ticks. Read the numbers as
  they are.

After the beat, remove the two lines from `.env` again to return to the defaults (tick 40, 15
ticks).

## Judges' sheet

| Hackathon criterion | Where it is on screen | Where it is in the repo |
|---|---|---|
| **Bruin is mandatory** | The data panel (DATA AS OF, the MODEL or EPSS badge, the gate sentence and the pipeline run id). The pipeline strip during **Advance week**, showing Bruin's seven steps and "asset N of 26". The gate banner when it finishes | [`pipelines/patch_tuesday/`](../pipelines/patch_tuesday) (26 assets) and [`pipelines/game_ledger/`](../pipelines/game_ledger) (1 asset). The gate is the blocking checks in [`assets/ml/eval_metrics.sql`](../pipelines/patch_tuesday/assets/ml/eval_metrics.sql). [`scripts/advance.py`](../scripts/advance.py) starts the Bruin run. Explained in [PIPELINE.md](PIPELINE.md), feature by feature in its "Bruin feature map" |
| **Visual multi-agent experience with 3+ agents** | The 3D board with five AI agents (Triage, Patcher, Hunter on Blue; Opportunist, Patient on Red) moving next to the two human players. The **Agents** tab: one card per agent with its role, current action and latest reason. With BAND on, **Call for backup**: a Coach agent and the three blue agents talk in a BAND room as separate agents, a human approves in the room, and a specialist agent joins the room and then the board as a fourth blue AI agent (the Coach box, the BACKUP card, the debrief's Backup section) | Five policies in [`game/policy.py`](../game/policy.py) (`opportunist`, `patient`, `triage`, `patcher`, `hunter`). Reason sentences in [`game/reasons.py`](../game/reasons.py). The red campaign in [`game/match.py`](../game/match.py). The 3D scene in [`web/src/scene.js`](../web/src/scene.js). Call for backup: the analysis and the three specialist policies in `game/backup.py`, the BAND conversation in `game/bandteam.py`, setup in `scripts/band_setup.py`. Explained in [BAND.md](BAND.md) |
| **Visible decision log** | The **Full log** tab: every move by every character with its reason. On EXPLOIT and PATCH rows a source chip opens the trace from danger score to pipeline run to dice roll. The **Agents** tab shows each agent's latest reason | Written by [`game/ledger.py`](../game/ledger.py) to `ledger/ledger.jsonl`, also served at `/api/ledger`. Loaded back into the warehouse by Bruin: [`pipelines/game_ledger/assets/agent_ledger.sql`](../pipelines/game_ledger/assets/agent_ledger.sql). Row format in [CONTRACTS.md](CONTRACTS.md) |

Results and their caveats, for judges who want to check the claim "our model beat the baseline":
[MODEL_AND_GATE.md](MODEL_AND_GATE.md).
