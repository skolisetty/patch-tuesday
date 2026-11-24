# Glossary

One line per term. The screen and the docs use one name per idea. Older names you may still meet in
code, logs or file names are listed with an arrow (→) to the name used now.

## The game

| Term | Meaning |
|---|---|
| machine | One of the 20 computers on the board. In code: node |
| machine value | How much a machine is worth, 1 to 3. Shown as ×1, ×2, ×3 next to its name |
| card | One real CVE sitting on a machine. Attackers break in through cards; defenders patch them |
| danger | A card's predicted chance (0-100%) of being exploited. Set by our model or by the EPSS baseline |
| priority #N | A card's position in the week's patch-priority list. #1 is the most urgent |
| breach chance | The chance that one specific attack attempt succeeds. It rises with danger and falls when the card is being patched or the machine is monitored |
| breach | A successful attack. The machine turns red and Red scores its value every tick until it is restored |
| patch | Blue action, 2 ticks. Makes one card safe. Scores machine value x danger if no attack was tried on it first |
| restore | Blue action, 3 ticks. Cleans a breached machine. Scores +3 and stops Red's income from it |
| monitor | Blue action, 3 ticks. Watches a machine: halves the breach chance there and reveals an attacker standing on it |
| isolate | Blue action, 5 ticks. Cuts a machine off so it cannot be attacked. Costs Blue 1 point per tick |
| tick | One step of the game clock, 2 seconds by default. Every player and agent acts once per tick |
| match | One game: 150 ticks, 5 minutes |
| Blue team | The defenders: Player 1, Player 2 and the AI agents Triage, Patcher and Hunter |
| Red team | The attackers: the AI agents Opportunist and Patient. Hidden unless revealed |
| agent | A character played by the computer. There are five on the board, and a sixth when a specialist is called in |
| LAST SEEN | Tag on a faint attacker figure: where a hidden attacker was last revealed |
| Advance week | Button. Replays the next real week of data through the pipeline while the match runs |
| drop | The new week's cards landing on machines after an advance (at most 12) |
| campaign | After a drop, the two attackers agree to go after the freshest high-danger cards (at most 6, for 30 ticks) |
| decision log | The on-screen list of every move every character made, each with a one-sentence reason. In code and files: ledger |
| debrief | The end-of-match screen: the verdict, where each team's points came from, and "what really happened" |
| what really happened | The reveal at the end of a match: which cards on the board really became known exploited within 91 days of the board's date |

## Call for backup (BAND)

Only when the game runs with BAND on. The full page is [BAND.md](BAND.md).

| Term | Meaning |
|---|---|
| BAND | A chat service where AI agents and people talk in shared rooms. The game uses it for Call for backup |
| Call for backup | The feature: Coach finds the Blue team's weakness, a human approves in BAND, and a specialist joins the team |
| room | A BAND chat room. Here: "Patch Tuesday - Blue team", where Coach, the blue agents and the humans talk |
| mention | Naming someone in a BAND message with `@`, for example `@Coach yes`. BAND delivers a message to an agent only when the message mentions it |
| Coach | The agent that watches the match, measures the team's weaknesses, and asks in the room whether to call in a specialist. It does not play on the board |
| weakness | What Coach measures: slow response, exposure or blind spots. It picks the worst of the three |
| checkpoint | The moment Coach analyses the match: tick 40 by default, once per match |
| specialist | The extra blue AI agent Coach can call in: Responder (restores), Hardener (patches) or Sentinel (monitors). At most one per match. In code: `blue4` |
| backup | The specialist once it has joined. Marked BACKUP on the Agents tab |

## The data

| Term | Meaning |
|---|---|
| CVE | A publicly listed software vulnerability with an id such as CVE-2026-33660 |
| NVD | The US National Vulnerability Database. The source of the CVE list |
| CVSS | A 0-10 severity score for a CVE. It says how bad a bug would be, not how likely an attack is |
| CWE | A weakness class, such as "SQL injection". Shown on a card as "what it is" |
| CISA | The US Cybersecurity and Infrastructure Security Agency |
| known exploited (KEV) | On CISA's Known Exploited Vulnerabilities list: attackers have really used this bug. In code: `in_kev` |
| EPSS | Exploit Prediction Scoring System, a public daily score from FIRST.org for every CVE |
| EPSS baseline | The EPSS score used as the standard our model must beat |
| MITRE ATT&CK | A public catalogue of attack techniques. Each card carries one technique id |
| data as of | The simulated "today". Nothing dated later is visible to the model or the game. Starts at 2026-03-22 |
| day 0 | The starting date of the replay, 2026-03-22 |
| mock data | Built-in fake data (CVE-2099-... ids) for trying the screen without a pipeline |

## The pipeline and the model

| Term | Meaning |
|---|---|
| Bruin | The command-line tool that runs the data pipeline and its checks |
| pipeline | The chain of steps that turns the downloaded public data into the files the game reads |
| asset | One step of the pipeline: a SQL or Python file that builds one table or file. There are 27 |
| layer | A group of assets. In order: landing, raw, stg, feat, ml, serve |
| blocking check | A test on an asset. If it fails, Bruin does not run the assets that depend on it |
| DuckDB | The database file the pipeline works in |
| serving files | The four JSON files in `serving/` that the pipeline writes and the game reads |
| pipeline run id | The stamp of one pipeline run, such as `2026_11_23_13_24_43`. Shown so a danger score can be traced to its run |
| our model | The prediction model the pipeline retrains every simulated week |
| the gate | The blocking check that lets our model replace EPSS only if it has strictly more hits in top 50 |
| hits in top 50 | Of a scorer's 50 top picks, how many really became known exploited within 91 days |
| positives | The bugs in the test set that really became known exploited within the 91 days |
| candidates | All the CVEs a scorer must rank (about 172,000 to 185,000) |
| snapshot | The state of the world at one date, used to train, test or serve |
| horizon | The look-ahead window for "became known exploited": 91 days |
| leakage | Information from the future reaching the model, which would make its test result too good |
| backfill | Running the pipeline for many past weeks in one go. Used to rehearse all 12 weeks |

## Older names → the name used now

| You may see | Read it as |
|---|---|
| node | → machine |
| score, EPSS% (on a card) | → danger |
| rank | → priority #N |
| odds, `p_breach` | → breach chance |
| `in_kev` | → known exploited (KEV) |
| challenger, `hgb-2026-03-29` and similar | → our model (the second form is one week's version of it) |
| EPSS (as a score source) | → EPSS baseline |
| promotion gate | → the gate |
| P@50, precision@50, hits@50 | → hits in top 50 (precision@50 is hits divided by 50) |
| world clock, asof, `asof_date` | → data as of |
| ledger, decision ledger | → decision log |
| compromised | → breached |
| promoted | → our model passed the gate for that week |
