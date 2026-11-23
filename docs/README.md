# Patch Tuesday documentation

Start with the front page, [../README.md](../README.md). Then pick the path that fits you.

## Reading paths

| You are | Read, in this order |
|---|---|
| **A player** who wants to play in five minutes | [PLAY.md](PLAY.md) → [UI_GUIDE.md](UI_GUIDE.md) when a panel is unclear → [GLOSSARY.md](GLOSSARY.md) |
| **Curious** how a real CVE ends up as a card on a machine | [HOW_IT_WORKS.md](HOW_IT_WORKS.md) → [GLOSSARY.md](GLOSSARY.md) |
| **A presenter or a judge** | [DEMO.md](DEMO.md) (2-minute script and the criteria sheet) → [MODEL_AND_GATE.md](MODEL_AND_GATE.md) for the result and its caveats |
| **Running it** on your machine or sharing it | [RUNNING.md](RUNNING.md) |
| **A data engineer** interested in the Bruin pipeline | [PIPELINE.md](PIPELINE.md) → [MODEL_AND_GATE.md](MODEL_AND_GATE.md) → [BRUIN_NOTES.md](BRUIN_NOTES.md) |
| **An ML or security reader** checking the claims | [MODEL_AND_GATE.md](MODEL_AND_GATE.md) → [DATA_SOURCES.md](DATA_SOURCES.md) |
| **A developer** changing the code | [CONTRACTS.md](CONTRACTS.md) → [HOW_IT_WORKS.md](HOW_IT_WORKS.md) → [RUNNING.md](RUNNING.md) (tests) |

## All documents

| Document | What it answers |
|---|---|
| [../README.md](../README.md) | What is this, how do you win, what is the one idea, how do I start it |
| [PLAY.md](PLAY.md) | What is the goal, how do points move, what does each action do, what do I do first |
| [UI_GUIDE.md](UI_GUIDE.md) | What does this panel, colour or icon mean |
| [HOW_IT_WORKS.md](HOW_IT_WORKS.md) | What happens each tick, each match and each week; one real CVE followed end to end |
| [DEMO.md](DEMO.md) | How do I show it in 2 minutes; where is each hackathon criterion |
| [RUNNING.md](RUNNING.md) | Setup, daily commands, sharing with teammates (ngrok), switches, troubleshooting |
| [PIPELINE.md](PIPELINE.md) | How Bruin is used: layers, the 27 assets, one simulated week, the Bruin feature map |
| [MODEL_AND_GATE.md](MODEL_AND_GATE.md) | The model, the gate, the 12-week results, proof the gate blocks, the leakage audit, known limits |
| [DATA_SOURCES.md](DATA_SOURCES.md) | Where the data comes from, notices and licences |
| [GLOSSARY.md](GLOSSARY.md) | Every term in one line, and old names mapped to the names used now |
| [CONTRACTS.md](CONTRACTS.md) | The fixed interfaces: serving files, server routes, WebSocket messages, rules |
| [BRUIN_NOTES.md](BRUIN_NOTES.md) | A lab notebook: what Bruin v0.11.831 really does, tested before the build |

## One name per idea

The screen and these documents use the same words: machine, card, danger, priority #N, breach
chance, known exploited (KEV), our model, EPSS baseline, the gate, hits in top 50, data as of. Code
and file names keep their older names (node, challenger, `p_breach`, `asof_date`, ledger). The
[glossary](GLOSSARY.md#older-names--the-name-used-now) maps one to the other.
