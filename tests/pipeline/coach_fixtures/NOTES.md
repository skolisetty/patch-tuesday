# Coach pipeline fixtures: how the expected figures were worked out

Three 24-tick matches, cut down to the moves that matter. `make_fixtures.py` only expands the move
lists into ledger rows and writes the boards; it computes nothing. Every number in the three
`expected.json` files was worked out by hand, as shown here. `python tests/pipeline/test_coach.py`
runs the real pipeline on each folder and compares all 94 figures per case.

Rules used (from `game/rules.py` and `game/config.py`):

- Machines: id = y * 5 + x on a 5 by 4 grid. Values: MAIL-01 (0) 2, VPN-01 (3) 3, APP-01 (5) 2,
  DB-01 (6) 3, FILE-01 (7) 2, HR-01 (8) 1, CI-01 (10) 2, DC-01 (11) 3, ERP-01 (12) 3, WIKI-01 (13) 1,
  BKP-01 (15) 2, POS-01 (17) 2, JUMP-01 (19) 2.
- Start: p1 on 11, p2 on 8, blue1 on 6, blue2 on 12, blue3 on 7, red1 on 0, red2 on 19.
- Blue moves before Red inside a tick. Red is paid a breached machine's value at the end of every
  tick from the breach tick to the tick before the restore finishes, but not while it is isolated.
  A first breach is worth 5, a restore 3, an isolated machine costs Blue 1 per tick for 5 ticks.
- MONITOR at tick S: an attacker's move meets it at S, S+1, S+2; an attacker standing there is in
  sight at the end of S and S+1. A restore finished at tick R does the same for R to R+5 / R to R+4.
- A breach shows its attacker at the end of the breach tick only.
- The attackers are active from tick 10, so attacker time is (24 - 10) x 2 = 28 attacker-ticks.
- A patch earns machine value x danger unless the card was attacked before the patch finished.
- "Near a breached machine": on it or one step away, at a tick after the breach tick and up to the
  restore tick (or the checkpoint), unless the actor restored it.

## 1. slow_response (`m-fixture-slow`)

Story: two machines are breached early and left for a long time.

- red1 breaches DB-01 (value 3) at tick 12; blue3 walks over at 18 and its restore finishes at 21.
  Breached 21 - 12 = **9** ticks, paid 9 x 3 = 27.
- red2 breaches POS-01 (value 2) at tick 13; never restored. Breached 24 - 13 = **11** ticks, paid 22.
- Holding 27 + 22 = **49**; first breaches 2 x 5 = **10**; Red 59. Shares 49/59 = **0.831**, 10/59 = **0.169**.
- Average (9 + 11) / 2 = **10.0**, longest **11**, breached now **1** (POS-01).
- Blue: blue1 patches DB-01 0.20 (3 x 0.20 = 0.6) and APP-01 0.50 (2 x 0.50 = 1.0) = 1.6; blue2
  ERP-01 0.10 = 0.3; p2 POS-01 0.40 = 0.8. Patches 2.7 + one restore 3 = Blue **5.7**.
- Open hot cards (danger >= 0.30, machine value >= 2, unpatched): VPN-01 0.30 (the boundary
  counts), ERP-01 0.35, POS-01 0.60 = **3**. Not counted: MAIL-01 0.29 (too low), HR-01 0.95
  (machine value 1), every patched card.
- Both breached cards were untouched and on the board 12 and 13 ticks: long open **2**. Nobody
  monitored anything before tick 21: unmonitored **2**. The board says 1 breach was through a
  top-50 card (DB-01's card is priority 12, POS-01's is 300).
- Sightings: red1 at tick 12 (its breach) and at 21, 22, 23 (it sits on DB-01, which is watched
  after the restore); red2 at 13 (its breach). 5 of 28 in sight, **23** unseen, 23/28 = **0.821**.
  One detection (red1 at tick 21, credited to blue3, whose restore put the watch there). No failed
  exploit, so the two scenarios are the same and `sightings_exact` is true.
- Waiting: blue1 acts at ticks 0-4 (19 of 24 waiting = 0.792); blue2 at 0-1 (22, 0.917); blue3 at
  18-21 (20, 0.833); p1 never (24, 1.0); p2 at 14-18 (19, 0.792).
- Near a breach without restoring: blue1 stands on APP-01, one step from DB-01 (1); blue2 on
  ERP-01, one step from POS-01 (1); blue3 restored DB-01 itself and is far from POS-01 (0); p1 on
  DC-01, one step from DB-01 (1); p2 walks onto POS-01 and patches there instead of restoring (1).

## 2. exposure (`m-fixture-exposure`)

Story: many dangerous cards are open; both breaches come through top-50 cards; response is quick.

- red1 breaches MAIL-01 (value 2) at tick 10; blue3 restores it at 16: **6** ticks, paid 12.
- red2 fails on POS-01 at 12 and breaches it (value 2) at 18. blue2 isolates POS-01 at 20, so it
  is isolated at ticks 20-23 (4 ticks before the checkpoint; the fifth would be tick 24).
  Breached 24 - 18 = **6** ticks, of which 4 isolated, paid 2 x 2 = 4.
- Holding 12 + 4 = **16**; first breaches **10**; Red 26. Shares 16/26 = **0.615**, **0.385**.
- Average **6.0**, longest **6**, breached now **1**: POS-01, which the board shows as isolated
  (it is still breached underneath).
- Blue: patches 3 x 0.05 = 0.15 (blue1, DB-01) + 1 x 0.02 = 0.02 (p2, HR-01) + 0 (blue2 patches
  the POS-01 card at 21-22, but it had been attacked at 12 and 18: worth 2 x 0.50 = 1.0, points 0)
  = 0.17; restore 3; isolation -4. Blue **-0.83**.
- Open hot cards: VPN-01 0.94 and 0.45, DB-01 0.60, CI-01 0.50, DC-01 0.35, ERP-01 0.31 = **6**.
  Not counted: ERP-01 0.29, BKP-01 0.70 (p1 is patching it: not open), HR-01 0.90 (value 1).
- Long open: MAIL-01's card was untouched for exactly 10 ticks (counts). POS-01's card arrived at
  tick 12 (`dropped_tick`), so it was 6 ticks old at the breach (does not count). **1**.
- Unmonitored: MAIL-01 yes. POS-01 no: blue2 monitored it at 16, which covers 16-18. **1**.
- Sightings: red1 at 10 (breach) and 16-20 (on MAIL-01 while it is watched after the restore) = 6;
  red2 at 16, 17 (standing on monitored POS-01) and 18 (breach) = 3. 9 in sight, **19** unseen,
  19/28 = **0.679**. Detections **2**: red1 at 16 (credited to blue3), red2 at 16 (credited to
  blue2). The failed exploit at 12 was on an unmonitored machine, so nothing is uncertain.
- Waiting: blue1 22 (0.917); blue2 acts at 15, 16, 20, 21, 22 (19, 0.792); blue3 acts at 11-16
  (18, 0.75); p1 acts at 20, 21, 23 (21, 0.875); p2 at 2-3 (22, 0.917).
- Near a breach without restoring: only blue2, who stands on POS-01 and isolates and patches
  there (1). blue3 restored MAIL-01 itself; nobody else comes within one step of either machine.

## 3. blind_spots (`m-fixture-blind`)

Story: the attackers are almost never in sight and strike at the very end, on machines nobody watches.

- red2 breaches WIKI-01 (value 1) at tick 22: **2** ticks, paid 2. red1 breaches CI-01 (value 2)
  at tick 23, the last tick played: **1** tick, paid 2.
- Holding **4**; first breaches **10**; Red 14. Shares 4/14 = **0.286**, **0.714**.
- Average 1.5, longest 2, breached now **2** (CI-01, WIKI-01).
- Blue: blue2 patches ERP-01 0.25 = 0.75. p2 starts patching WIKI-01's card at tick 22, the tick
  of the breach, and finishes at 23: worth 1 x 0.40 = 0.4, points 0. Blue **0.75**.
- Open hot cards: APP-01 0.55 and CI-01 0.45 = **2**.
- Long open: CI-01's card was untouched for 23 ticks (counts). WIKI-01's card had a patch started
  on it at the breach tick (does not count). **1**. Both machines were unmonitored: **2**. Top-50: 0.
- Sightings. blue1 monitors APP-01 at tick 9: moves there meet it at 9-11 and it shows who stands
  there at the end of 9 and 10. red1 arrives at 10 (in sight, a detection credited to blue1) and
  fails an exploit there at 11, while the machine is still monitored. Whether that was detected is
  a dice roll the ledger does not record:
  - certain: red1 at 10 and 23 (breach), red2 at 22 (breach): 3 in sight, **25** unseen, **0.893**
  - if it was detected: red1 also at 11 and 12: 5 in sight, **23** unseen, **0.821**
  - detections are **1** either way, and `sightings_exact` is **false**.
  With the optional `seen` flags set as in the second case, the pipeline reports 23 and exact
  (test_coach.py checks that too).
- Waiting: blue1 acts at 8, 9, 10 (21, 0.875); blue2 at 0-1 (22, 0.917); blue3 never (1.0);
  p1 never (1.0); p2 at 20, 22, 23 (21, 0.875).
- Near a breach without restoring: blue2 on ERP-01 is one step from WIKI-01 at tick 23 (1); p2
  stands on WIKI-01 at 23 (1). CI-01 was breached in the last tick, so nobody had a later tick to
  be near it: p1, one step away on DC-01, counts 0.
