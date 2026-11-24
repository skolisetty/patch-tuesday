# Patch Tuesday: submission sheet

Text for the hackathon form, and a checklist of how the project meets the brief.

## One line

A 3D co-op game where two humans and AI defenders hold a network against AI attackers, and every
threat on the board is a real vulnerability scored by a Bruin pipeline that replays real history
week by week.

## Track

Track 2: Co-op Multiplayer Game.

## What it is (short)

You and a partner are the security team for a 20-machine network. Three AI defenders work beside
you and two hidden AI attackers work against you. Every card on a machine is a real CVE. How
dangerous it is comes from a Bruin pipeline over real public data (NVD, CISA KEV, FIRST EPSS,
MITRE ATT&CK): each simulated week it retrains an exploit-prediction model, and a blocking check
lets that model set the danger only if it beats the industry baseline (EPSS) on held-out data.
Press **Advance week** and the real pipeline runs on screen; new cards drop and the attackers go
after them. At the end the game shows which of the board's bugs were exploited in the real world.

A **Coach** agent watches the match. It runs the match log through a second Bruin pipeline, tells
the team in a BAND room what it is weakest at, and, when a human approves in the room, a
specialist agent joins the room and walks onto the board as a fourth defender.

## The brief, item by item

| Required | Where |
|---|---|
| Characters with a visual identity | Seven 3D robots (eight with the specialist), each with its own colour and name tag |
| Visual output | A playable 3D game in the browser |
| Movement and action | Characters walk the network, patch, restore, isolate, scan and exploit on screen |
| At least three agents with their own goals | Five AI agents with different roles (two attackers, three defenders), plus Coach and a specialist |
| Visible decisions | The Agents tab (what each agent is doing and why) and the full decision log |
| Track 2: 2 humans + 3 agents in one world | Player 1, Player 2, Triage, Patcher, Hunter, Opportunist, Patient |
| Track 2: agents act on their own, with memory | Rule-based policies that run every tick; attackers remember what they scanned and where they failed |
| Track 2: a world event caused by agents alone | The red campaign after each new week; the specialist joining on Coach's analysis |

| Scores higher | Where |
|---|---|
| 3D | three.js scene |
| ML and trained agents | A model retrained every simulated week decides what both sides go for; it must pass a gate against EPSS (it passed 11 of 12 weeks and was blocked once, plus a tie on day 0). The agents themselves are rule-based |
| Voice | Each agent says its notable moves aloud in its own voice (the **Voices** button) |

## Sponsor tools

| Sponsor | How it is used | Where to look |
|---|---|---|
| **Bruin** (mandatory) | The weekly pipeline: 26 assets, incremental replay of real data cut at a simulated date, Python and SQL assets, blocking checks including the model-versus-EPSS gate, a 12-week backfill, leakage checks. It runs live from the game and its progress is shown on screen. A second pipeline analyses each match for the Coach, with its own checks | `pipelines/`, `docs/PIPELINE.md`, `docs/MODEL_AND_GATE.md` |
| **BAND** | "Call for backup": Coach, the three defenders and a specialist are separate BAND agents in one room; they report, a human approves in the room, and the specialist joins the room and the game | `docs/BAND.md`, `game/bandteam.py`, `scripts/band_setup.py` |
| **Telem** | The Hunter agent can pull the week's public reporting on a card through Telem search (start with `-Telem`) | `game/intel.py` |

The agents' one-sentence reasons are written by an LLM through an OpenAI-compatible endpoint.

## Run it

See `docs/RUNNING.md`. Short version: `scripts\start.ps1 -Band -Telem`, then open
http://localhost:5173. A two-minute demo script is in `docs/DEMO.md`.

## Honest limits

- The model's margin over EPSS is small (1 to 5 bugs in its top 50) and measured on its own task.
- Card details come from today's NVD records, which can flatter the model; the write-up quantifies it.
- The AI agents follow fixed rules. What is learned is the model that sets the danger.
