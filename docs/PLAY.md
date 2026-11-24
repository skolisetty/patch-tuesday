# How to play

Everything you need for a first match. What each part of the screen means is in
[UI_GUIDE.md](UI_GUIDE.md). Every term is in [GLOSSARY.md](GLOSSARY.md).

## The goal

You are the **Blue team**: two human players and three AI defenders. Two hidden **Red** AI
attackers try to break into a network of 20 **machines**. When the clock runs out, the team with
the higher score wins.

A match is 150 **ticks**. A tick is one step of the game clock, 2 seconds by default, so a match
takes 5 minutes. Everyone on the board, human or AI, does one thing per tick.

Each machine carries **cards**. A card is one real, publicly listed software vulnerability (a
CVE). Each card shows a **danger**: the predicted chance, from 0 to 100%, that this bug really gets
exploited. Attackers break in through cards. You make cards safe by patching them.

## How points move

| What happens | Who scores | Points |
|---|---|---|
| Red holds a breached machine | Red | the machine's value (1, 2 or 3) every tick |
| Red breaches a machine for the first time in the match | Red | +5 |
| Blue patches a card before Red has attacked it | Blue | machine value x danger |
| Blue restores a breached machine | Blue | +3, and Red's income from it stops |
| Blue keeps a machine isolated | Blue | -1 every tick |

Three things follow from this table:

- **A red machine is the most expensive thing on the board.** A value-3 machine left breached for
  20 ticks gives Red 60 points. Restoring it is nearly always the best move.
- **A patch is worth machine value x danger.** A 94% card on a value-3 machine is worth 2.82. A 1%
  card on a value-1 machine is worth 0.01. Patch high danger on high value first.
- **A patch only pays before Red attacks the card.** Once Red has tried a card, patching it still
  closes the hole but scores nothing. The card table shows such a card as "attacked".

The machine value is the "×1", "×2" or "×3" next to the machine's name on the board.

## The five actions

Each player has five actions. The keys are also in the "? How to play" card on the screen.

| Action | Player 1 | Player 2 | Takes | What it does | When to use it |
|---|---|---|---|---|---|
| Move | W A S D | arrow keys | 1 tick per step | Walk to the next machine. Hold the key to keep walking | To reach a breached machine or a valuable card |
| Patch | E | Enter | 2 ticks | Makes one card on your machine safe. Scores machine value x danger if Red has not attacked that card | On high-danger cards on high-value machines |
| Restore | R | / | 3 ticks | Takes back a breached (red) machine. Scores +3, stops Red's income, and closes the card Red came in through | Whenever you stand on a red machine |
| Monitor | Q | Right Shift | lasts 3 ticks | Watches your machine: halves the breach chance there and shows any attacker standing on it | When nothing here is worth patching, on a value-3 machine |
| Isolate | F | . | lasts 5 ticks | Cuts your machine off: no attack can land, and an attacker standing on it is thrown out. Costs Blue 1 point per tick | Rarely. To stop an attack you cannot stop in time any other way |

You can also walk with the mouse: click a machine on the board and your player walks there, one
step per tick. A click on a player panel chooses which player your mouse moves; Ctrl+click on a
machine moves the other player. Pressing any of that player's keys stops the walk.

Patch always acts on the card your panel marks with the patch key: the open card with the best
priority that still pays.

While a patch or a restore is running you cannot do anything else. A key you press in that time is
kept; the panel says "Queued: … runs when the current action finishes." and it runs on the first
free tick.

## What the attackers do

Both attackers wait 10 ticks before their first move. After that:

- **Opportunist** goes straight for cards that are new this week and have a high danger. It
  attacks without looking first.
- **Patient** scans a machine first, then attacks only when the **breach chance** is good. It
  prefers older cards and jumps from a machine it already holds to its neighbours.

The breach chance is the chance that one attack attempt succeeds. It rises with the card's danger,
and a little more when the card is **known exploited (KEV)** or the attacker has scanned the
machine. It is cut to 40% while the card is being patched and halved while the machine is
monitored. A patched card and an isolated machine cannot be breached. It never exceeds 95%.

You do not see the attackers. An attacker shows up only for 2 ticks after a breach, for 3 ticks
when a failed attack on a monitored machine gives it away, and while it stands on a monitored
machine. Otherwise the board shows a faint figure tagged "LAST SEEN" where it was last revealed.

## Reading your panel

Each player has a panel under the board. From top to bottom:

1. **Where you are**: the machine, its state (HEALTHY, BREACHED or ISOLATED) and its value.
2. **NEXT**: the best move right now, in one sentence, with the keys to press. For example
   "Press E: patch CVE-2026-33660 (+1.38 = value 3 x danger 46%)." or "MAIL-01 is breached, 2 steps
   (← ↑), costing 2 per tick. Go there and press R."
3. **What your last key did**, for 4 seconds: green when it worked ("Patched CVE-2026-33660: +1.38
   (machine value 3 x danger 46%)."), red when it did not ("Nothing to restore: this machine is not
   breached.").
4. **The cards on your machine**: Priority, CVE, What it is, Danger, Worth and Status. "Worth" is
   what a patch would score now. The row marked with your patch key is the card that key acts on.
   Scroll the list when a machine has more cards than fit.

If you only ever do what the NEXT line says, you are playing well.

## A guided first match

1. **Open the page.** The "How to play" card is on top. Read the four rows of "How points move",
   then press **Start the match** on the card.
2. **Ticks 0 to 10: patch.** No attacker moves yet. Look at your panel's NEXT line. It names the
   most valuable card you can reach. Walk there with your move keys and press your patch key. A
   blue "+… patch" floats up from the machine when the patch finishes, and Blue's score rises.
3. **Split up.** The two players start on different machines. Stay apart: the panels already send
   you to different cards.
4. **A machine turns red.** The events strip at the bottom says who breached what. The machine
   glows red and shows "Red +N / tick" above it. Under Red's score the top bar shows the same
   income. If your NEXT line says "… is breached … Go there and press R", go. Restoring takes 3
   ticks; stay on the machine until "+3 restore" floats up.
5. **Watch the Agents tab** on the right. Each of the five AI agents has a card that says what it
   is doing and why. If Hunter is already walking to the red machine, your panel sends you
   elsewhere.
6. **Optional: press Advance week.** A pipeline strip appears over the board and lights up step by step
   while Bruin replays the next real week of data. This takes about 20 to 30 seconds and the match
   keeps running. When it finishes, the gate verdict shows for 8 seconds, up to 12 new cards drop
   onto the machines and the attackers go after them for 30 ticks. Patch the new high-danger cards
   first. In the last 25 ticks of a match no cards drop; the next match starts from the new week.
7. **The match ends.** The debrief shows the verdict, where each team's points came from, and
   "What really happened": which cards on your board were added to CISA's exploited list in the 91
   days after the board's date, and whether you patched them in time. Often there are none. Real
   exploitation is rare, and that is why the order you patch in matters.

## Calling for backup (only with BAND on)

When the game was started with BAND, a **Coach** agent watches the Blue team. At tick 40 it posts
in the team's BAND chat room what the team is weakest at (slow response, exposure or blind spots)
with numbers from your match, and recommends one **specialist**. Reply in the room with
`@Coach yes`, with another specialist's name, or with `@Coach no`. On yes, the specialist joins the
room and walks onto the board at JUMP-01 as a fourth blue AI agent, marked BACKUP on the Agents
tab. It plays by the same rules as everyone else. The debrief then shows what changed after it
joined.

On the game screen the Coach box at the top of the right panel shows the analysis and how many
ticks you have left to answer. You can ask for the analysis earlier with `@Coach analyze`. All of
it is in [BAND.md](BAND.md).

## Where the danger comes from

A data pipeline computes the danger of every card from real public data, one real week at a time.
Two scorers compete: **our model**, retrained every week, and the **EPSS baseline**, a public
industry-standard score. **The gate** is a check in the pipeline: each scorer names its 50 most
dangerous bugs, and the pipeline counts how many of them really became known exploited within 91
days. Our model sets the danger only when it has strictly more hits. A tie keeps the EPSS baseline.

The data panel in the top bar says who won this week, for example "Gate passed: our model found 2,
EPSS baseline 0, of 32 bugs that really became exploited". The badge next to the date says MODEL or
EPSS. More in [HOW_IT_WORKS.md](HOW_IT_WORKS.md) and [MODEL_AND_GATE.md](MODEL_AND_GATE.md).

## Quick answers

| Question | Answer |
|---|---|
| Why did my patch score nothing? | Red had already attacked that card. Its Worth cell showed "—" |
| Why can I not restore? | The machine is not breached. Restore only works on a red machine |
| Is isolating a breached machine enough? | No. After 5 ticks it turns red again. Restore it while it is isolated |
| Why did my key do nothing? | A patch or restore was still running. The key was queued and runs when it finishes |
| Why does a card show "KEV"? | It is on CISA's list of bugs that attackers have really used. Its breach chance is higher |
| Why does a card show "NEW"? | It arrived with this week's data. Opportunist goes after these first |
| Who is the extra blue character marked BACKUP? | A specialist that Coach called in through BAND after a human approved it. See [BAND.md](BAND.md) |
| Can I pause? | No. Reset stops the match and goes back to the lobby |
