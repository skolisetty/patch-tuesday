"""Call for backup: the Coach's analysis, the three specialists and the flow. Pure game logic.

No network, no disk, no wall clock, no sleeping. Everything here works on the in-memory match
state, the ledger rows of the match so far and state.tick. game/bandteam.py turns the `Say`
objects this module returns into BAND messages; with no BandTeam at all (tests, the headless
simulator) the Says are simply collected and the match plays the same.

What is in here:
  analyse(state, ledger_rows) -> Analysis      which of three weaknesses is worst, with computed evidence
  spawn(state, role) -> Actor                  the specialist joins as actor "blue4" at JUMP-01
  responder / hardener / sentinel              the three specialist policies (registered in policy.POLICIES)
  metrics_before_after(state, rows, tick)      Red's income, breaches ... before and after the join
  BackupFlow                                   when things are said and decided, from state.tick only

Fog of war: the analysis reads blue rows, the board, the score and state.track (breaches, restores
and detections, all public to the defenders when they happened). It never reads a red's position,
action, memory or scan intel, and the specialists decide from policy.blue_view like any blue agent.
"""
from __future__ import annotations

import copy
import logging
import math
import random
import re
from dataclasses import dataclass, field

from . import config as game_config
from . import policy
from .policy import BlueView, _claimed, _move, exposure
from .rules import Action
from .state import (BACKUP_ID, BACKUP_NODE, HUMAN_IDS, SPECIALISTS, Actor, MatchState, Node, dist,
                    new_memory)

log = logging.getLogger("game.backup")

WEAKNESSES = ("slow_response", "exposure", "blind_spots")
TITLES = {"slow_response": "Slow response", "exposure": "Exposure", "blind_spots": "Blind spots"}
SPECIALIST_FOR = {"slow_response": "responder", "exposure": "hardener", "blind_spots": "sentinel"}
OPTIONS = ["responder", "hardener", "sentinel"]
WHAT_IT_DOES = {
    "responder": "it goes straight to breached machines and restores them, highest value first",
    "hardener": "it patches the open cards worth the most, known-exploited first",
    "sentinel": "it keeps the most valuable exposed machines monitored, which halves breach chance there",
}
ROOM_NAME = "Patch Tuesday - Blue team"

# A weakness "stands out" when its score is above its threshold. The scores are 0..1 mixes of the
# evidence figures (see _scores). Weights and thresholds were set on simulated matches on the real
# deck (seeds 101-220) so that the weakness Coach names is the one whose specialist helped most
# there, and checked on seeds 1-100; Coach picks the weakness furthest above its threshold.
THRESHOLDS = {"slow_response": 0.50, "exposure": 0.35, "blind_spots": 0.75}
HOT_DANGER = 0.30          # "open cards with danger >= 30%"
HOT_VALUE = 2              # "... on machines of value >= 2"
LONG_OPEN_TICKS = 10       # "open and unattempted for 10+ ticks"


# =========================================================================== specialist policies
def _mate_restores(v: BlueView) -> dict[int, int]:
    """Breached machines a team-mate has claimed to restore -> how many steps that mate is away."""
    out: dict[int, int] = {}
    for m in v.mates.values():
        tg = m.get("target") or {}
        if tg.get("what") == "RESTORE" and tg.get("node") is not None:
            d = dist(m["node"], tg["node"])
            out[tg["node"]] = min(d, out.get(tg["node"], 99))
    return out


HARDENER_STEP_COST = 0.25      # how much one step of walking discounts a card's worth for the Hardener


def _red_in_sight(v: BlueView) -> set[int]:
    """Healthy machines a *revealed* attacker is standing on (all a blue agent may know of them)."""
    return {nid for _, nid in v.revealed_reds if v.nodes[nid].state == "healthy"}


def _watched_by_mates(v: BlueView) -> set[int]:
    return {(m.get("target") or {}).get("node") for m in v.mates.values()
            if (m.get("target") or {}).get("what") == "MONITOR"}


def responder(v: BlueView, mem: dict, rng: random.Random) -> Action:
    """Goes straight to the highest-value breached machine and restores it. With nothing breached
    it stands on the highest-value exposed machine and keeps it monitored. No random draw."""
    t = v.tuning
    me, here = v.me, v.node_id
    name = "Responder"
    claimed = _mate_restores(v)
    comp = [n for n in v.nodes if n.is_compromised]
    # Mine unless a team-mate is already on it or will reach it no later than I do.
    mine = [n for n in comp if claimed.get(n.id, 99) > dist(here, n.id)]
    if mine:
        n = max(mine, key=lambda n: (n.value / (1 + 0.15 * dist(here, n.id)), -n.id))
        d = dist(here, n.id)
        mem["target"] = {"node": n.id, "card": None, "what": "RESTORE"}
        facts = {"target": n.name, "node_value": n.value, "distance": d, "compromised_nodes": len(comp),
                 "specialist": "responder"}
        if d == 0:
            return Action(me, "RESTORE", here, policy="responder",
                          reason=f"{name} restores {n.name} (value {n.value}): Red earns {n.value} every tick it stays breached.",
                          facts=facts)
        return _move(me, here, n.id, "responder",
                     f"{name} goes straight to {n.name}: breached, value {n.value}, {d} step(s) away.", facts)

    cands = [n for n in v.nodes if n.state == "healthy" and n.unpatched()]
    if not cands:
        mem["target"] = None
        return Action(me, "WAIT", policy="responder",
                      reason=f"{name} waits: nothing is breached and nothing is exposed.",
                      facts={"compromised_nodes": len(comp), "exposed_nodes": 0, "specialist": "responder"})
    cur = (mem.get("target") or {}).get("node") if (mem.get("target") or {}).get("what") == "MONITOR" else None
    sight = _red_in_sight(v)           # an attacker in sight marks the machine most likely to fall next
    n = max(cands, key=lambda n: (exposure(n, t) * (1.25 if n.id in (cur, here) else 1.0)
                                  * (3.0 if n.id in sight else 1.0) / (1 + 0.1 * dist(here, n.id)), -n.id))
    facts = {"target": n.name, "node_value": n.value, "exposure": round(exposure(n, t), 2),
             "distance": dist(here, n.id), "compromised_nodes": len(comp), "standby": True,
             "red_in_sight": n.id in sight, "specialist": "responder"}
    mem["target"] = {"node": n.id, "card": None, "what": "MONITOR"}
    if n.id != here:
        why = ("where an attacker is in sight" if n.id in sight else "the most valuable exposed machine")
        return _move(me, here, n.id, "responder",
                     f"{name} has nothing to restore, so it moves to {n.name} (value {n.value}), {why}.",
                     facts)
    if n.monitored_ticks <= 1:
        return Action(me, "MONITOR", here, policy="responder",
                      reason=f"{name} monitors {n.name} (value {n.value}) while nothing is breached.", facts=facts)
    return Action(me, "WAIT", policy="responder",
                  reason=f"{name} stands by on {n.name}: nothing is breached and it is already monitored.", facts=facts)


def hardener(v: BlueView, mem: dict, rng: random.Random) -> Action:
    """Patches the open cards worth the most (machine value x danger, known-exploited first),
    wherever they are. It never restores or monitors. No random draw."""
    t = v.tuning
    me, here = v.me, v.node_id
    name = "Hardener"
    claimed, node_claims, _ = _claimed(v)
    pool = []
    for n in v.nodes:
        if n.state == "isolated":         # shut for now
            continue
        oc = [c for c in n.unpatched() if c.card_id not in claimed]
        if not oc or (node_claims.get(n.id, 0) >= 1 and len(oc) <= 1):
            continue
        for c in oc:
            worth = n.value * c.score
            u = n.value * (c.score + (t.kev_bonus if c.in_kev else 0.0)) / (1 + HARDENER_STEP_COST * dist(here, n.id))
            if n.is_compromised:
                u *= 0.3                  # a breached machine cannot be attacked again until it is restored
            pool.append((u, -c.card_id, n, c, worth))
    if not pool:
        mem["target"] = None
        return Action(me, "WAIT", policy="hardener", reason=f"{name} waits: no open card is left to patch.",
                      facts={"open_cards": 0, "specialist": "hardener"})
    cur = mem.get("target") or {}
    pick = next((x for x in pool if x[3].card_id == cur.get("card")), None) or max(pool, key=lambda x: (x[0], x[1]))
    _, _, n, c, worth = pick
    mem["target"] = {"node": n.id, "card": c.card_id, "what": "PATCH"}
    facts = {"target": n.name, "node_value": n.value, "cve": c.cve_id, "score": round(c.score, 3),
             "worth": round(worth, 2), "in_kev": c.in_kev, "open_cards": len(pool),
             "distance": dist(here, n.id), "specialist": "hardener"}
    kev = ", known exploited" if c.in_kev else ""
    if n.id == here:
        return Action(me, "PATCH", here, c.card_id, "hardener",
                      f"{name} patches {c.cve_id} on {n.name}: worth {worth:.2f} (value {n.value} x danger {c.score:.2f}{kev}), the most of {len(pool)} open cards.",
                      facts)
    return _move(me, here, n.id, "hardener",
                 f"{name} walks to {n.name} for {c.cve_id}, the open card worth the most ({worth:.2f}{kev}).", facts)


def _beat(v: BlueView, mem: dict) -> list[Node]:
    """The Sentinel's rotation: the most exposed healthy machine nobody else is watching, plus the
    best machine next to it when that one is worth the walk (two neighbours can each be kept
    monitored three ticks out of four; one machine alone, all the time)."""
    t = v.tuning
    taken = _watched_by_mates(v)
    cands = [n for n in v.nodes if n.state == "healthy" and n.unpatched() and n.id not in taken]
    if not cands:
        return []
    old = mem.get("beat") or []
    sight = _red_in_sight(v)           # an attacker in sight: that machine is the one to watch
    a = max(cands, key=lambda n: (n.id in sight, exposure(n, t) * (1.2 if n.id in old else 1.0),
                                  -dist(v.node_id, n.id), -n.id))
    if a.id in sight:
        return [a]
    near = [n for n in cands if dist(a.id, n.id) == 1 and exposure(n, t) >= 0.34 * exposure(a, t)]
    if not near:
        return [a]
    b = max(near, key=lambda n: (exposure(n, t) * (1.2 if n.id in old else 1.0), -n.id))
    return [a, b]


def sentinel(v: BlueView, mem: dict, rng: random.Random) -> Action:
    """Keeps the highest-value exposed machines monitored in rotation, and restores the machine it
    stands on if that one is breached. No random draw."""
    t = v.tuning
    me, here = v.me, v.node_id
    name = "Sentinel"
    node_here = v.nodes[here]
    if node_here.is_compromised and _mate_restores(v).get(here, 99) > 0:
        mem["target"] = {"node": here, "card": None, "what": "RESTORE"}
        return Action(me, "RESTORE", here, policy="sentinel",
                      reason=f"{name} restores {node_here.name} (value {node_here.value}): it was breached under its feet.",
                      facts={"target": node_here.name, "node_value": node_here.value, "specialist": "sentinel"})
    beat = _beat(v, mem)
    mem["beat"] = [n.id for n in beat]
    if not beat:
        mem["target"] = None
        return Action(me, "WAIT", policy="sentinel", reason=f"{name} waits: no exposed machine is left to watch.",
                      facts={"exposed_nodes": 0, "specialist": "sentinel"})
    names = " and ".join(n.name for n in beat)
    facts = {"beat": [n.name for n in beat], "beat_values": [n.value for n in beat],
             "exposure": round(sum(exposure(n, t) for n in beat), 2), "specialist": "sentinel"}
    on_beat = here in mem["beat"]
    if on_beat and node_here.monitored_ticks <= 1:
        mem["target"] = {"node": here, "card": None, "what": "MONITOR"}
        return Action(me, "MONITOR", here, policy="sentinel",
                      reason=f"{name} monitors {node_here.name} (value {node_here.value}): breach chance is halved here while it watches.",
                      facts={**facts, "target": node_here.name, "node_value": node_here.value})
    others = [n for n in beat if n.id != here]
    if not others:                                    # one machine on the beat and it is still watched
        mem["target"] = {"node": here, "card": None, "what": "MONITOR"}
        return Action(me, "WAIT", policy="sentinel",
                      reason=f"{name} holds {node_here.name}: it is monitored for {node_here.monitored_ticks} more tick(s).",
                      facts={**facts, "target": node_here.name, "node_value": node_here.value})
    n = min(others, key=lambda n: (n.monitored_ticks, dist(here, n.id), n.id))
    mem["target"] = {"node": n.id, "card": None, "what": "MONITOR"}
    return _move(me, here, n.id, "sentinel",
                 f"{name} rotates to {n.name} (value {n.value}); its beat is {names}.",
                 {**facts, "target": n.name, "node_value": n.value, "distance": dist(here, n.id)})


SPECIALIST_POLICIES = {"responder": responder, "hardener": hardener, "sentinel": sentinel}
# Registered beside the existing five. The existing policy functions are not touched: they see the
# specialist as one more blue team-mate through policy.blue_view, and it sees them the same way.
for _role, _fn in SPECIALIST_POLICIES.items():
    policy.POLICIES[_role] = (_fn, "blue")


def spawn(state: MatchState, role: str) -> Actor:
    """The specialist walks onto the board: actor "blue4" at JUMP-01 (machine 19), a normal blue
    agent with that role as its policy. One per match: a second call, an unknown role or a match
    that is not running is refused with ValueError."""
    if role not in SPECIALISTS:
        raise ValueError(f"unknown specialist: {role!r}")
    if BACKUP_ID in state.actors:
        raise ValueError("a specialist has already joined this match")
    if state.phase != "running":
        raise ValueError("a specialist can only join a running match")
    actor = Actor(id=BACKUP_ID, name=SPECIALISTS[role], team="blue", kind="agent", role=role,
                  node_id=BACKUP_NODE, home=BACKUP_NODE, last_seen_node=BACKUP_NODE, reinforcement=True)
    state.actors[BACKUP_ID] = actor
    state.memory[BACKUP_ID] = new_memory(BACKUP_ID)
    state.track["backup"] = {"role": role, "tick": state.tick}
    return actor


# =========================================================================== analysis
@dataclass
class Analysis:
    weakness_key: str                 # "slow_response" | "exposure" | "blind_spots"
    title: str
    evidence: list[str]               # the evidence sentences of the chosen weakness, numbers computed
    scores: dict[str, float]          # weakness key -> 0..1
    agent_lines: dict[str, str]       # blue1/blue2/blue3 -> one first-person sentence
    player_lines: list[dict]          # [{"id": "p1", "line": "..."}]
    recommended: str                  # "responder" | "hardener" | "sentinel"
    team_ok: bool                     # Blue ahead and no weakness score above its threshold
    numbers: dict = field(default_factory=dict)        # every figure the sentences quote
    all_evidence: dict = field(default_factory=dict)   # weakness key -> its evidence sentences
    text: str = ""                    # Coach's analysis message (final wording)
    ask_text: str = ""                # Coach's recommendation message (final wording)
    tick: int = 0


def _s(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def _num(x: float) -> str:
    """A score as the scoreboard shows it: 31 or 31.4."""
    x = round(float(x), 1)
    return f"{x:.0f}" if float(x).is_integer() else f"{x:.1f}"


def breach_episodes(state: MatchState) -> list[dict]:
    """One entry per breach: {"node","start","end","by","ticks"}. `end` is the tick its restore
    finished (None while it is still breached) and `ticks` how many ticks it stayed breached, which
    is also how many times Red was paid for it. Blue resolves before Red inside a tick, so a
    restore in the same tick as a breach came first and does not end that breach."""
    out = []
    restores = state.track.get("restores", [])
    for b in state.track.get("breaches", []):
        r = next((r for r in restores if r["node"] == b["node"] and r["tick"] > b["tick"]), None)
        end = r["tick"] if r else None
        out.append({"node": b["node"], "start": b["tick"], "end": end, "by": r["actor"] if r else None,
                    "ticks": (end if end is not None else state.tick) - b["tick"]})
    return out


def actor_stats(state: MatchState, rows: list[dict], actor_id: str) -> dict:
    """What one blue actor did so far, from its ledger rows, the cards and the restore history."""
    mine = [r for r in rows if r.get("actor_id") == actor_id]
    patched = [c for n in state.nodes for c in n.cards if c.patched_by_id == actor_id]
    return {
        "rows": len(mine),
        "waits": sum(1 for r in mine if r.get("action") == "WAIT"),
        "patches": len(patched),
        "worth": round(sum(c.patch_gain or 0.0 for c in patched), 2),
        "restores": sum(1 for r in state.track.get("restores", []) if r["actor"] == actor_id),
        "restore_starts": sum(1 for r in mine if r.get("action") == "RESTORE" and r.get("outcome") == "restoring"),
        "monitors": sum(1 for r in mine if r.get("action") == "MONITOR" and r.get("outcome") == "monitoring"),
        "isolates": sum(1 for r in mine if r.get("action") == "ISOLATE" and r.get("outcome") == "isolated"),
    }


def walked_past(state: MatchState, rows: list[dict], actor_id: str) -> int:
    """How many breaches this actor stood within one step of, while the machine was breached,
    without being the one who restored it (or restoring it right now)."""
    where = {r["tick"]: r["node_id"] for r in rows if r.get("actor_id") == actor_id and r.get("node_id") is not None}
    actor = state.actors.get(actor_id)
    doing = actor.busy if (actor is not None and actor.busy_ticks > 0 and actor.busy) else {}
    n = 0
    for e in breach_episodes(state):
        if e["by"] == actor_id:
            continue
        if e["end"] is None and doing.get("action") == "RESTORE" and doing.get("node") == e["node"]:
            continue
        last = e["end"] if e["end"] is not None else state.tick
        if any(t in where and dist(where[t], e["node"]) <= 1 for t in range(e["start"] + 1, last + 1)):
            n += 1
    return n


def _player_line(state: MatchState, rows: list[dict], pid: str) -> tuple[str, dict]:
    a = state.actors[pid]
    st_ = actor_stats(state, rows, pid)
    st_["walked_past"] = walked_past(state, rows, pid)
    st_["wait_share"] = round(st_["waits"] / st_["rows"], 3) if st_["rows"] else 0.0
    if st_["rows"] == st_["waits"]:
        return f"{a.name} has not acted yet.", st_
    did = (f"patched {st_['patches']} {_s(st_['patches'], 'card')} worth {st_['worth']:.1f}"
           if st_["patches"] else "patched nothing")
    did += (f" and restored {st_['restores']} {_s(st_['restores'], 'machine')}"
            if st_["restores"] else " and restored nothing")
    line = f"{a.name} {did}"
    if st_["walked_past"]:
        line += f", but walked past {st_['walked_past']} breached {_s(st_['walked_past'], 'machine')}"
    line += f"; waited {st_['wait_share']:.0%} of the ticks."
    return line, st_


def _agent_lines(state: MatchState, rows: list[dict], detections: int) -> tuple[dict[str, str], dict]:
    lines, stats = {}, {}
    for aid, job in (("blue1", "the priority list"), ("blue2", "patching")):
        s = actor_stats(state, rows, aid)
        stats[aid] = s
        lines[aid] = (f"I patched {s['patches']} {_s(s['patches'], 'card')} worth {s['worth']:.1f} and was pulled off "
                      f"{job} {s['restore_starts']} {_s(s['restore_starts'], 'time')} to restore a machine.")
    s = actor_stats(state, rows, "blue3")
    stats["blue3"] = s
    lines["blue3"] = (f"I restored {s['restores']} {_s(s['restores'], 'machine')} and monitored "
                      f"{s['monitors']} {_s(s['monitors'], 'time')}; an attacker was detected "
                      f"{detections} {_s(detections, 'time')}.")
    return lines, stats


def _scores(n: dict) -> dict[str, float]:
    """Three 0..1 scores from the evidence figures. Higher = weaker."""
    b = n["breaches"]
    # Slow response: a machine breached right now weighs most (that is what a Responder fixes),
    # then how long breaches lasted (3 ticks is the fastest possible restore), then Red's held share.
    slow = (0.40 * min(1.0, n["breached_now"]) + 0.35 * min(1.0, n["avg_breach_ticks"] / 8.0)
            + 0.25 * n["held_share"])
    # Exposure: valuable high-danger cards still open weigh most (four or more = the full 0.70).
    expo = (0.70 * min(1.0, n["open_hot"] / 4.0) + 0.15 * (n["breaches_long_open"] / b if b else 0.0)
            + 0.15 * (n["breaches_top50"] / b if b else 0.0))
    # Blind spots: attackers unseen for more than half of their active time, breaches on machines
    # nobody watched, and few detections (three or more detections = none of the last 0.30).
    blind = (0.40 * max(0.0, min(1.0, (n["hidden_share"] - 0.5) / 0.5))
             + 0.30 * (n["breaches_unmonitored"] / b if b else 0.0)
             + 0.30 * (1.0 - min(1.0, n["detections"] / 3.0) if n["red_ticks"] else 0.0))
    return {"slow_response": round(slow, 3), "exposure": round(expo, 3), "blind_spots": round(blind, 3)}


def analyse(state: MatchState, ledger_rows: list[dict]) -> Analysis:
    """Work out, from the match so far, what the Blue team is weakest at. Every number in the
    sentences is computed here and kept in Analysis.numbers."""
    rows = [r for r in (ledger_rows or []) if r.get("team") == "blue"]   # red rows are never read
    tr = state.track
    sc = state.score
    eps = breach_episodes(state)
    now = [n for n in state.nodes if n.is_compromised]
    hot = [(n, c) for n in state.nodes if n.value >= HOT_VALUE
           for c in n.cards if c.patch_state == "unpatched" and c.score >= HOT_DANGER]
    breaches = tr.get("breaches", [])
    nb = len(breaches)
    numbers = {
        "tick": state.tick, "red": round(sc.red, 2), "blue": round(sc.blue, 2),
        "held": round(sc.held, 2), "held_share": round(sc.held / sc.red, 3) if sc.red > 0 else 0.0,
        "breaches": nb,
        "avg_breach_ticks": round(sum(e["ticks"] for e in eps) / len(eps), 1) if eps else 0.0,
        "longest_breach_ticks": max((e["ticks"] for e in eps), default=0),
        "breached_now": len(now), "breached_now_names": [n.name for n in now],
        "open_hot": len(hot),
        "breaches_long_open": sum(1 for b in breaches if b["untouched"] and b["open_ticks"] >= LONG_OPEN_TICKS),
        "breaches_top50": sum(1 for b in breaches if b["rank"] is not None and b["rank"] <= 50),
        "red_ticks": tr.get("red_ticks", 0), "hidden_ticks": tr.get("hidden_ticks", 0),
        "hidden_share": round(tr.get("hidden_ticks", 0) / tr["red_ticks"], 3) if tr.get("red_ticks") else 0.0,
        "breaches_unmonitored": sum(1 for b in breaches if not b["monitored"]),
        "detections": len(tr.get("detections", [])),
    }
    scores = _scores(numbers)

    n = numbers
    ev = {"slow_response": [], "exposure": [], "blind_spots": []}
    ev["slow_response"].append(
        f"Red earned {_num(n['held'])} of its {_num(n['red'])} points from machines left breached."
        if sc.red > 0 else "Red has not scored yet.")
    ev["slow_response"].append(
        f"A breached machine stayed breached for {n['avg_breach_ticks']:.1f} ticks on average (longest {n['longest_breach_ticks']})."
        if eps else "No machine has been breached yet.")
    ev["slow_response"].append(
        f"{len(now)} {_s(len(now), 'machine is', 'machines are')} breached right now ({', '.join(x.name for x in now)})."
        if now else "No machine is breached right now.")
    ev["exposure"].append(
        f"{len(hot)} open {_s(len(hot), 'card')} with danger of 30% or more {_s(len(hot), 'sits', 'sit')} on machines worth 2 or more.")
    ev["exposure"].append(
        f"{n['breaches_long_open']} of {nb} {_s(nb, 'breach', 'breaches')} came through a card that had been open and untouched for 10 ticks or more."
        if nb else "No breach yet, so no card has been used against us.")
    if nb:
        ev["exposure"].append(f"{n['breaches_top50']} of {nb} {_s(nb, 'breach', 'breaches')} came through a top-50 priority card.")
    ev["blind_spots"].append(
        f"The attackers were out of sight for {n['hidden_share']:.0%} of the time since they became active."
        if n["red_ticks"] else "The attackers have not become active yet.")
    ev["blind_spots"].append(
        f"{n['breaches_unmonitored']} of {nb} {_s(nb, 'breach', 'breaches')} happened on a machine nobody was monitoring."
        if nb else "No breach yet on an unmonitored machine.")
    ev["blind_spots"].append(f"An attacker has been detected {n['detections']} {_s(n['detections'], 'time')}.")

    key = max(WEAKNESSES, key=lambda k: (scores[k] - THRESHOLDS[k], -WEAKNESSES.index(k)))
    team_ok = sc.blue > sc.red and all(scores[k] <= THRESHOLDS[k] for k in WEAKNESSES)
    rec = SPECIALIST_FOR[key]

    agent_lines, agent_stats = _agent_lines(state, rows, n["detections"])
    player_lines, player_stats = [], {}
    for pid in HUMAN_IDS:
        line, st_ = _player_line(state, rows, pid)
        player_lines.append({"id": pid, "line": line})
        player_stats[pid] = st_
    numbers["agents"] = agent_stats
    numbers["players"] = player_stats

    head = f"Analysis at tick {state.tick}: Red {_num(sc.red)}, Blue {_num(sc.blue)}."
    if team_ok:
        verdict = f"The team is holding up and nothing stands out; the nearest thing to a weak point is {TITLES[key].lower()}."
    else:
        verdict = f"Our weakest point is {TITLES[key].lower()}."
    text = " ".join([head, verdict] + ev[key][:2])
    ask = " ".join(p["line"] for p in player_lines)
    name = SPECIALISTS[rec]
    others = [o for o in OPTIONS if o != rec]
    ask += (f" I recommend calling in {name}: {WHAT_IT_DOES[rec]}. Reply yes, or name another: "
            f"{others[0]} or {others[1]}. Reply no to carry on without backup.")
    return Analysis(weakness_key=key, title=TITLES[key], evidence=list(ev[key]), scores=scores,
                    agent_lines=agent_lines, player_lines=player_lines, recommended=rec, team_ok=team_ok,
                    numbers=numbers, all_evidence=ev, text=text, ask_text=ask, tick=state.tick)


# =========================================================================== before / after
def metrics_before_after(state: MatchState, ledger_rows: list[dict] | None, joined_tick: int) -> tuple[dict, dict]:
    """(before, after): the ticks before the specialist joined and the ticks since.

      ticks             length of the window
      red_per_tick      Red's points gained in the window / ticks
      blue_per_tick     Blue's points gained in the window / ticks
      breaches          successful exploits in the window
      avg_breach_ticks  for breaches that began in the window: average ticks the machine stayed
                        breached (until its restore, or until now); None when there was none
      peak_breached     most machines breached at the same time in the window

    The specialist's first move is tick `joined_tick`, so that tick belongs to "after". The figures
    come from state.track (written by the rules); `ledger_rows` is accepted for the seam and not needed."""
    tr = state.track
    scores = tr.get("scores", [])
    j = max(0, min(int(joined_tick), state.tick))
    at_join = scores[j - 1] if 0 < j <= len(scores) else (0.0, 0.0)
    final = scores[-1] if scores else (0.0, 0.0)
    eps = breach_episodes(state)
    held = tr.get("breached", [])

    def window(lo: int, hi: int, red: float, blue: float) -> dict:
        ticks = hi - lo
        mine = [e for e in eps if lo <= e["start"] < hi]
        return {"ticks": ticks,
                "red_per_tick": round(red / ticks, 2) if ticks else 0.0,
                "blue_per_tick": round(blue / ticks, 2) if ticks else 0.0,
                "breaches": len(mine),
                "avg_breach_ticks": round(sum(e["ticks"] for e in mine) / len(mine), 1) if mine else None,
                "peak_breached": max(held[lo:hi], default=0)}

    before = window(0, j, at_join[0], at_join[1])
    after = window(j, state.tick, final[0] - at_join[0], final[1] - at_join[1])
    return before, after


# =========================================================================== the flow
@dataclass
class Say:
    """One message for the room. `text` is the final wording (plain, no mention tokens); the
    transport adds the mention objects named by `mention`."""
    speaker: str                      # coach | triage | patcher | hunter | responder | hardener | sentinel
    text: str
    mention: str                      # blue_agents | humans | coach | team
    kind: str                         # analysis | agent_line | ask | decision | join | report | summary
    role: str | None = None           # kind "join" only: the specialist to add to the room before it speaks


@dataclass
class BackupConfig:
    enabled: bool = True
    checkpoint_tick: int = 40         # BAND_BACKUP_TICK
    wait_ticks: int = 15              # BAND_BACKUP_WAIT_TICKS: the answer window
    auto: bool = True                 # BAND_BACKUP_AUTO: on timeout Coach brings its own pick (0 = stand down)
    gap_ticks: int = 2                # the 3 s between two lines of the conversation, in ticks
    report_gap_ticks: int = 8         # the 15 s between two specialist reports, in ticks
    max_reports: int = 8
    early_lead: float = 15.0          # Red leading by this much ...
    early_after_tick: int = 20        # ... after this tick triggers the analysis early
    last_ticks: int = 30              # no checkpoint in the last N ticks of a match
    human_min_tick: int = 10          # a human request before the attackers move waits until this tick
    room: str = ROOM_NAME

    @classmethod
    def from_env(cls, tick_seconds: float | None = None) -> "BackupConfig":
        def num(name: str, default: int) -> int:
            try:
                v = int(float(game_config.env(name) or default))
            except ValueError:
                return default
            return v if v > 0 else default

        secs = tick_seconds if (tick_seconds and tick_seconds > 0) else game_config.tick_seconds()
        return cls(checkpoint_tick=num("BAND_BACKUP_TICK", 40), wait_ticks=num("BAND_BACKUP_WAIT_TICKS", 15),
                   auto=game_config.env("BAND_BACKUP_AUTO", "1") != "0",
                   gap_ticks=max(1, math.ceil(3.0 / secs)), report_gap_ticks=max(1, math.ceil(15.0 / secs)))


SPEAKERS = {"blue1": "triage", "blue2": "patcher", "blue3": "hunter"}
YES_WORDS = {"yes", "ok", "go"}
NO_WORDS = {"no", "none"}
TRIGGER_WORDS = {"analyze", "analyse", "backup", "help"}
MAX_HUMAN_TEXT = 200
_TOKEN = re.compile(r"@\[\[[0-9a-fA-F-]{36}\]\]")
_WORDS = re.compile(r"[a-z]+")


def _words(text) -> list[str]:
    """Untrusted human text -> lower-case words. Length-capped, mention tokens removed."""
    return _WORDS.findall(_TOKEN.sub(" ", str(text or ""))[:MAX_HUMAN_TEXT].lower())


def parse_reply(text) -> str | None:
    """The answer to Coach's question, by fixed keywords only: a specialist's name (wins over
    everything), else "yes" (yes / ok / go / do it) or "no" (no / none), whichever word comes
    first. None when the text holds none of them."""
    w = _words(text)
    for x in w:
        if x in SPECIALISTS:
            return x
    for i, x in enumerate(w):
        if x in YES_WORDS or (x == "do" and i + 1 < len(w) and w[i + 1] == "it"):
            return "yes"
        if x in NO_WORDS:
            return "no"
    return None


def is_trigger(text) -> bool:
    """A human asking Coach for the analysis: analyze / backup / help."""
    return any(x in TRIGGER_WORDS for x in _words(text))


def clean_name(name) -> str:
    """A display name from the room, made safe to show: printable, single line, at most 40 characters."""
    s = "".join(ch for ch in str(name or "") if ch.isprintable())
    s = re.sub(r"\s+", " ", s).strip()[:40].strip()
    return s or "a player"


def intro_line(state: MatchState, role: str) -> str:
    """The specialist's first message: what it sees and where it is going. Computed by running its
    own policy once on a copy of its memory (nothing on the board changes)."""
    name = SPECIALISTS[role]
    try:
        act = SPECIALIST_POLICIES[role](policy.blue_view(state, BACKUP_ID),
                                        copy.deepcopy(state.memory[BACKUP_ID]), random.Random(0))
        f = act.facts
    except Exception:                  # never let a sentence break the join
        log.exception("intro line failed")
        return f"{name} here, joining the Blue team at {state.nodes[BACKUP_NODE].name}."
    if role == "responder":
        if f.get("standby"):
            return (f"{name} here. Nothing is breached right now: I will hold {f['target']} (worth "
                    f"{f['node_value']}) and restore whatever falls.")
        if f.get("target"):
            return f"{name} here. {f['target']} is breached and worth {f['node_value']}: going there now."
        return f"{name} here. Nothing is breached and nothing is exposed: standing by."
    if role == "hardener":
        if f.get("cve"):
            kev = ", known exploited" if f.get("in_kev") else ""
            return (f"{name} here. The open card worth the most is {f['cve']} on {f['target']} (worth "
                    f"{f['node_value']}, danger {f['score']:.0%}{kev}): going to patch it.")
        return f"{name} here. No open card is left to patch: I will take new ones as they drop."
    if f.get("beat"):
        return (f"{name} here. I will keep {' and '.join(f['beat'])} monitored: breach chance is halved "
                f"there and attackers show up.")
    return f"{name} here. No exposed machine is left to watch: standing by."


class BackupFlow:
    """The whole "Call for backup" state machine, with no I/O.

        flow = BackupFlow(config)             # a BackupConfig, a dict of its fields, or None (= from env)
        flow.reset(state)                     # at match start and at reset (state may be None)
        says = flow.on_tick(state, rows_this_tick, all_rows)   # once per tick, after the rules ran
        says = flow.on_human(state, text, by_name)             # each human message addressed to Coach
        says = flow.on_match_end(state, all_rows)              # once, when the match has ended
        flow.public(state)                    # the state.backup object (None when the feature is off)
        flow.status                           # watching | analysing | asked | joined | declined | unavailable

    The flow decides when things are said, from state.tick only. It performs the game-side effects
    itself: it calls spawn() on approval, appends the "coach"/"backup" events to
    state.pending_events and keeps state.backup equal to public(state), so st.snapshot() carries the
    object without further wiring. No method raises: an internal error is logged and the flow goes
    to "unavailable" for the rest of the match.
    """

    def __init__(self, config=None):
        if isinstance(config, BackupConfig):
            self.cfg = config
        elif isinstance(config, dict):
            self.cfg = BackupConfig(**config)
        else:                         # None, or the game.config module
            self.cfg = BackupConfig.from_env()
        self.says: list[Say] = []     # everything said this match, in order (tests, simulator)
        self.reset(None)

    # ------------------------------------------------------------------ life cycle
    def reset(self, state: MatchState | None = None) -> None:
        self._status = "watching"
        self.match_id = state.match_id if state is not None else None
        self.analysis: Analysis | None = None
        self.checkpoint_tick: int | None = None      # the tick the analysis actually ran
        self.asked_tick: int | None = None
        self.expires_tick: int | None = None
        self.chosen: str | None = None
        self.by: str | None = None
        self.joined_tick: int | None = None
        self.before: dict | None = None
        self.after: dict | None = None
        self.unavailable_reason: str | None = None
        self._queue: list[tuple[int, Say | str]] = []    # (due tick, Say) or (due tick, "ask")
        self._human_asked = False
        self._too_late_said = False
        self._ended = False
        self._last_report_tick = -10**6
        self._reports = 0
        self._pending_report: list[str] = []
        self._detections_seen = 0
        self.says = []
        if state is not None:
            state.backup = self.public(state)

    @property
    def status(self) -> str:
        return self._status

    def set_unavailable(self, reason: str = "") -> None:
        """The transport lost BAND (or the feature was switched off mid-match): nothing more is
        said or decided this match. A specialist that already joined keeps playing."""
        if self._status not in ("joined", "declined"):
            self._status = "unavailable"
        self.unavailable_reason = (reason or "")[:120] or None
        self._queue = []

    # ------------------------------------------------------------------ helpers
    def _sync(self, state: MatchState) -> None:
        if state is not None and state.match_id != self.match_id:
            self.reset(state)

    def _emit(self, out: list[Say], say: Say) -> None:
        out.append(say)
        self.says.append(say)

    def _event(self, state: MatchState, kind: str, text: str) -> None:
        state.pending_events.append({"kind": kind, "text": text})

    def _done(self, state: MatchState, out: list[Say]) -> list[Say]:
        state.backup = self.public(state)
        return out

    def _fail(self, state: MatchState, where: str) -> list[Say]:
        log.exception("backup flow failed in %s; switching off for this match", where)
        self.set_unavailable("internal error")
        try:
            state.backup = self.public(state)
        except Exception:
            pass
        return []

    def _may_start(self, state: MatchState) -> bool:
        return state.phase == "running" and state.tick <= state.ticks_total - self.cfg.last_ticks

    def _should_trigger(self, state: MatchState) -> bool:
        c = self.cfg
        if not self._may_start(state):
            return False
        if self._human_asked and state.tick >= c.human_min_tick:
            return True
        if state.tick >= c.checkpoint_tick:
            return True
        return state.tick > c.early_after_tick and state.score.red - state.score.blue >= c.early_lead

    def _start_analysis(self, state: MatchState, all_rows: list[dict], out: list[Say]) -> None:
        c = self.cfg
        a = analyse(state, all_rows)
        self.analysis = a
        self.checkpoint_tick = state.tick
        self._status = "analysing"
        self._emit(out, Say("coach", a.text, "blue_agents", "analysis"))
        t = state.tick
        for i, aid in enumerate(("blue1", "blue2", "blue3"), start=1):
            self._queue.append((t + i * c.gap_ticks, Say(SPEAKERS[aid], a.agent_lines[aid], "coach", "agent_line")))
        self._queue.append((t + 4 * c.gap_ticks, "ask"))
        lead = "The team is holding up" if a.team_ok else f"Weakest point: {a.title.lower()}"
        self._event(state, "coach", f"Coach: {lead}. {a.evidence[0]}")

    def _ask(self, state: MatchState, out: list[Say]) -> None:
        a = self.analysis
        self._status = "asked"
        self.asked_tick = state.tick
        self.expires_tick = state.tick + self.cfg.wait_ticks
        self._emit(out, Say("coach", a.ask_text, "humans", "ask"))
        self._event(state, "coach", f"Coach recommends calling in {SPECIALISTS[a.recommended]}. Reply in BAND within "
                                    f"{self.cfg.wait_ticks} ticks: yes, {' or '.join(o for o in OPTIONS if o != a.recommended)}, or no.")

    def _join(self, state: MatchState, role: str, by: str, human: bool, out: list[Say]) -> None:
        name = SPECIALISTS[role]
        rec = self.analysis.recommended if self.analysis else role
        try:
            spawn(state, role)
        except ValueError as exc:      # match ended in the meantime, or one is already there
            log.info("specialist not spawned: %s", exc)
            self._decline(state, by, f"Too late to bring {name} in: {exc}.", out)
            return
        self.chosen, self.by, self.joined_tick = role, by, state.tick
        self._status = "joined"
        self._last_report_tick = state.tick
        self._detections_seen = len(state.track.get("detections", []))
        self.before, _ = metrics_before_after(state, None, self.joined_tick)
        first = by.split(" ")[0]
        if human and role == rec:
            said = f"{first} approved: {name} is joining the Blue team now."
        elif human:
            said = f"{first} chose {name} instead of my pick ({SPECIALISTS[rec]}): {name} is joining the Blue team now."
        else:
            said = f"No reply in {self.cfg.wait_ticks} ticks, so I am calling in {name} myself."
        self._emit(out, Say("coach", said, "humans", "decision"))
        self._emit(out, Say(role, intro_line(state, role), "team", "join", role=role))
        how = f"approved by {first} via BAND" if human else "no reply in BAND, Coach's own call"
        self._event(state, "backup", f"{name} joined the Blue team ({how}).")

    def _decline(self, state: MatchState, by: str, said: str, out: list[Say]) -> None:
        self._status = "declined"
        self.by = by
        self._emit(out, Say("coach", said, "humans", "decision"))
        self._event(state, "backup", "Backup declined.")

    def _specialist_reports(self, state: MatchState, rows: list[dict], out: list[Say]) -> None:
        c = self.cfg
        actor = state.actors.get(BACKUP_ID)
        if actor is None:
            return
        for r in rows or []:
            if r.get("actor_id") != BACKUP_ID:
                continue
            node = state.nodes[r["node_id"]] if r.get("node_id") is not None else None
            if r.get("outcome") == "restored" and node is not None:
                self._pending_report.append(f"restored {node.name} (Red was earning {node.value} a tick there)")
            elif r.get("outcome") == "patched" and node is not None:
                card = node.card_by_cve(r.get("cve_id"))
                gain = (card.patch_gain or 0.0) if card else 0.0
                if gain >= 0.3:
                    self._pending_report.append(f"patched {r.get('cve_id')} on {node.name} (+{gain:.1f}) before Red tried it")
        dets = state.track.get("detections", [])
        mon = state.memory.get(BACKUP_ID, {}).get("monitored_at", {})
        for d in dets[self._detections_seen:]:
            if d["tick"] - mon.get(d["node"], -99) <= state.tuning.monitor_ticks:
                self._pending_report.append(f"an attacker was detected at {state.nodes[d['node']].name} on my watch")
        self._detections_seen = len(dets)
        if (self._pending_report and self._reports < c.max_reports
                and state.tick - self._last_report_tick >= c.report_gap_ticks):
            items = list(dict.fromkeys(self._pending_report))[-3:]     # no phrase twice, the newest three
            text = "; ".join(items)
            self._emit(out, Say(actor.role, f"{actor.name} report: {text}.", "coach", "report"))
            self._pending_report = []
            self._last_report_tick = state.tick
            self._reports += 1

    # ------------------------------------------------------------------ the three entry points
    def on_tick(self, state: MatchState, rows_this_tick: list[dict] | None, all_rows: list[dict] | None) -> list[Say]:
        if not self.cfg.enabled or state is None:
            return []
        try:
            self._sync(state)
            out: list[Say] = []
            if self._status == "unavailable" or state.phase != "running":
                return self._done(state, out)
            if self._status == "watching" and self._should_trigger(state):
                self._start_analysis(state, all_rows or [], out)
            while self._queue and self._queue[0][0] <= state.tick:
                _, item = self._queue.pop(0)
                if item == "ask":
                    self._ask(state, out)
                else:
                    self._emit(out, item)
            if self._status == "asked" and state.tick >= self.expires_tick:
                if self.cfg.auto:
                    self._join(state, self.analysis.recommended, "Coach (no reply)", False, out)
                else:
                    self._decline(state, "Coach (no reply)",
                                  f"No reply in {self.cfg.wait_ticks} ticks. Standing down: no backup this match.", out)
            elif self._status == "joined":
                self._specialist_reports(state, rows_this_tick or [], out)
            return self._done(state, out)
        except Exception:
            return self._fail(state, "on_tick")

    def on_human(self, state: MatchState, text, by_name) -> list[Say]:
        """One human message addressed to Coach. Untrusted: fixed keywords only."""
        if not self.cfg.enabled or state is None:
            return []
        try:
            self._sync(state)
            out: list[Say] = []
            if self._status == "unavailable" or state.phase != "running":
                return self._done(state, out)
            who = clean_name(by_name)
            if self._status == "watching":
                if is_trigger(text) and not self._human_asked:
                    if not self._may_start(state):
                        if not self._too_late_said:
                            self._too_late_said = True
                            self._emit(out, Say("coach", f"Too late in the match to bring backup in: fewer than "
                                                         f"{self.cfg.last_ticks} ticks are left.", "humans", "decision"))
                    else:
                        self._human_asked = True
                        if state.tick < self.cfg.human_min_tick:
                            self._emit(out, Say("coach", f"Noted. The attackers have not moved yet: I will analyse at tick "
                                                         f"{self.cfg.human_min_tick}.", "humans", "decision"))
            elif self._status == "asked":
                ans = parse_reply(text)
                if ans in SPECIALISTS:
                    self._join(state, ans, who, True, out)
                elif ans == "yes":
                    self._join(state, self.analysis.recommended, who, True, out)
                elif ans == "no":
                    self._decline(state, who, f"Understood, {who.split(' ')[0]}: no backup. The team carries on as it is.", out)
            return self._done(state, out)
        except Exception:
            return self._fail(state, "on_human")

    def on_match_end(self, state: MatchState, all_rows: list[dict] | None) -> list[Say]:
        """Once, after the last tick: fills `after` and returns Coach's closing message."""
        if not self.cfg.enabled or state is None:
            return []
        try:
            self._sync(state)
            out: list[Say] = []
            if self._ended or self._status == "unavailable":
                return self._done(state, out)
            self._ended = True
            self._queue = []
            sc = state.score
            verdict = "Blue wins" if sc.blue > sc.red else "Red wins" if sc.red > sc.blue else "a draw"
            head = f"Match over: Blue {_num(sc.blue)}, Red {_num(sc.red)} ({verdict})."
            if self._status == "joined":
                self.before, self.after = metrics_before_after(state, all_rows, self.joined_tick)
                name = SPECIALISTS[self.chosen]
                b, a = self.before, self.after
                s = actor_stats(state, all_rows or [], BACKUP_ID)

                def part(m: dict) -> str:
                    avg = (f", machines stayed breached {m['avg_breach_ticks']:.1f} ticks on average"
                           if m["avg_breach_ticks"] is not None else "")
                    return (f"Red +{m['red_per_tick']:.2f} a tick, Blue +{m['blue_per_tick']:.2f} a tick, "
                            f"{m['breaches']} {_s(m['breaches'], 'breach', 'breaches')}{avg}")

                did = (f"{name} restored {s['restores']} {_s(s['restores'], 'machine')}, patched {s['patches']} "
                       f"{_s(s['patches'], 'card')} worth {s['worth']:.1f} and monitored {s['monitors']} {_s(s['monitors'], 'time')}.")
                self._emit(out, Say("coach", f"{head} Before {name} joined ({b['ticks']} ticks): {part(b)}. "
                                             f"After ({a['ticks']} ticks): {part(a)}. {did}", "humans", "summary"))
            elif self._status in ("analysing", "asked", "declined"):
                self._emit(out, Say("coach", f"{head} No backup was called in this match.", "humans", "summary"))
            return self._done(state, out)
        except Exception:
            return self._fail(state, "on_match_end")

    # ------------------------------------------------------------------ the wire object
    def public(self, state: MatchState | None = None) -> dict | None:
        """The `state.backup` object of the wire spec; None when the feature is off."""
        if not self.cfg.enabled:
            return None
        a = self.analysis
        return {
            "status": self._status,
            "checkpoint_tick": self.checkpoint_tick if self.checkpoint_tick is not None else self.cfg.checkpoint_tick,
            "weakness": ({"key": a.weakness_key, "title": a.title, "evidence": list(a.evidence)} if a else None),
            "players": [dict(p) for p in a.player_lines] if a else [],
            "recommended": a.recommended if a else None,
            "options": list(OPTIONS),
            "asked_tick": self.asked_tick, "expires_tick": self.expires_tick,
            "chosen": self.chosen, "by": self.by,
            "joined_tick": self.joined_tick,
            "actor_id": BACKUP_ID if self.joined_tick is not None else None,
            "room": self.cfg.room,
            "before": dict(self.before) if self.before else None,
            "after": dict(self.after) if self.after else None,
            "team_ok": bool(a.team_ok) if a else None,
            "reason": self.unavailable_reason,
        }
