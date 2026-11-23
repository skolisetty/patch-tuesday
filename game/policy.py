"""Agent policies. Local, rule-based, no network call in the tick.

Every policy gets a *view* holding only what the rules let that actor see, plus its own
memory dict, and returns a rules.Action with a structured reason (the facts it used) and a
template sentence rendered from those facts.

What each side can see:
  red   node layout, values and public state (healthy/compromised/isolated); the cards on nodes it
        has scanned (a snapshot from scan time, including patch state as it was then); cards flagged
        new this week (public advisories: id, score, KEV flag, but not their patch state); its
        team-mate's position and target. It never sees monitoring, blue positions or live patch state.
  blue  every node, card and patch state, priority ranks, team-mates and their targets; red
        positions only while a red is revealed. It never sees what a red has scanned.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .config import Tuning
from .rules import Action, breach_base, breach_probability
from .state import (BLUE_AGENT_IDS, RED_IDS, MatchState, Node, dist, neighbours, step_toward)


# ------------------------------------------------------------------ views
@dataclass(frozen=True)
class PubNode:
    id: int
    name: str
    value: int
    state: str
    ever_breached: bool


@dataclass(frozen=True)
class KnownCard:
    node_id: int
    card_id: int
    cve_id: str
    score: float
    in_kev: bool
    is_new: bool
    age_days: int
    patch_seen: str | None     # patch state when last seen, None if never scanned
    recon: bool                # this red scanned the node (recon bonus applies)
    intel_tick: int | None


@dataclass
class RedView:
    me: str
    tick: int
    node_id: int
    nodes: list[PubNode]
    cards: list[KnownCard]
    intel_tick: dict[int, int]            # node -> tick of my last scan
    mate_node: int
    mate_target: int | None
    campaign: list[tuple[int, int]]       # my (node, card) campaign targets, if any
    tuning: Tuning


@dataclass
class BlueView:
    me: str
    tick: int
    node_id: int
    nodes: list[Node]                     # read only
    revealed_reds: list[tuple[str, int]]
    mates: dict[str, dict]                # id -> {"node": int, "target": dict | None}
    tuning: Tuning


def red_view(state: MatchState, rid: str) -> RedView:
    intel = state.red_intel.get(rid, {})
    cards: list[KnownCard] = []
    for n in state.nodes:
        e = intel.get(n.id)
        seen = e["cards"] if e else {}
        recon = bool(e and e.get("recon"))
        for c in n.cards:
            if c.card_id in seen or c.is_new:
                cards.append(KnownCard(n.id, c.card_id, c.cve_id, c.score, c.in_kev, c.is_new, c.age_days,
                                       seen.get(c.card_id), recon and c.card_id in seen,
                                       e["tick"] if (e and c.card_id in seen) else None))
    mate = next(r for r in RED_IDS if r != rid)
    mt = state.memory[mate].get("target")
    camp = []
    if state.campaign:
        camp = [tuple(x) for x in state.campaign.get("targets", {}).get(rid, [])]
    return RedView(me=rid, tick=state.tick, node_id=state.actors[rid].node_id,
                   nodes=[PubNode(n.id, n.name, n.value, n.state, n.ever_breached) for n in state.nodes],
                   cards=cards, intel_tick={k: v["tick"] for k, v in intel.items() if v.get("recon")},
                   mate_node=state.actors[mate].node_id, mate_target=mt["node"] if mt else None,
                   campaign=camp, tuning=state.tuning)


def blue_view(state: MatchState, bid: str) -> BlueView:
    mates = {}
    for a in state.actors.values():
        if a.team == "blue" and a.id != bid:
            mates[a.id] = {"node": a.node_id, "target": state.memory[a.id].get("target"), "kind": a.kind,
                           "busy": (a.busy or {}).get("action") if a.busy_ticks > 0 else None}
    return BlueView(me=bid, tick=state.tick, node_id=state.actors[bid].node_id, nodes=state.nodes,
                    revealed_reds=[(r, state.actors[r].node_id) for r in RED_IDS if state.actors[r].revealed],
                    mates=mates, tuning=state.tuning)


# ------------------------------------------------------------------ helpers
def _move(aid: str, src: int, dst: int, policy: str, reason: str, facts: dict) -> Action:
    return Action(aid, "MOVE", target_node=step_toward(src, dst), policy=policy, reason=reason, facts=facts)


def _pct(p: float) -> str:
    return f"{p:.2f}"


def exposure(node: Node, t: Tuning) -> float:
    return node.value * sum(breach_base(c.score, t.breach_k) + (t.kev_bonus if c.in_kev else 0.0)
                            for c in node.unpatched())


# ------------------------------------------------------------------ red 1: Opportunist
def opportunist(v: RedView, mem: dict, rng: random.Random) -> Action:
    """Chases fresh, high-score cards it believes are unpatched. Exploits without scanning first."""
    t = v.tuning
    me, here = v.me, v.node_id
    draw = rng.random()
    name = "Opportunist"

    scored = []
    for kc in v.cards:
        n = v.nodes[kc.node_id]
        if n.state != "healthy" or kc.patch_seen == "patched":
            continue
        if mem["failed"].get(kc.card_id, 0) >= t.opportunist_give_up:
            continue
        est = breach_probability(kc.score, kc.in_kev, kc.patch_seen or "unpatched", False, kc.recon, tuning=t)
        if est < t.opportunist_min_odds:
            continue
        gain = n.value + (t.first_breach_points / 2 if not n.ever_breached else 0.0)
        u = est * gain * (1.5 if kc.is_new else 1.0) / (1 + 0.45 * dist(here, kc.node_id))
        if v.mate_target == kc.node_id:
            u *= 0.5                      # do not crowd the Patient's target
        scored.append((u, -kc.card_id, kc, est))

    # campaign orders first
    for node_id, card_id in v.campaign:
        hit = next((s for s in scored if s[2].card_id == card_id), None)
        if hit:
            _, _, kc, est = hit
            n = v.nodes[kc.node_id]
            facts = {"campaign": True, "target": n.name, "cve": kc.cve_id, "score": round(kc.score, 3),
                     "est_odds": round(est, 3), "fresh": kc.is_new, "distance": dist(here, kc.node_id)}
            mem["target"] = {"node": kc.node_id, "card": kc.card_id, "what": "EXPLOIT"}
            if kc.node_id == here:
                return Action(me, "EXPLOIT", here, kc.card_id, "campaign",
                              f"{name} hits {kc.cve_id} on {n.name} for the campaign: new this week, score {_pct(kc.score)}, est. odds {_pct(est)}.", facts)
            return _move(me, here, kc.node_id, "campaign",
                         f"{name} heads to {n.name} for the campaign on {kc.cve_id} (score {_pct(kc.score)}).", facts)

    if draw < t.eps_opportunist and not any(s[2].node_id == here for s in scored):
        nxt = rng.choice(neighbours(here))
        mem["target"] = None
        return Action(me, "MOVE", nxt, policy="opportunist", reason=f"{name} drifts to {v.nodes[nxt].name} on a hunch.",
                      facts={"explore": True, "epsilon": t.eps_opportunist})

    if scored:
        u, _, kc, est = max(scored)
        n = v.nodes[kc.node_id]
        facts = {"target": n.name, "cve": kc.cve_id, "score": round(kc.score, 3), "est_odds": round(est, 3),
                 "fresh": kc.is_new, "node_value": n.value, "scanned": kc.recon, "distance": dist(here, kc.node_id),
                 "failed_before": mem["failed"].get(kc.card_id, 0)}
        mem["target"] = {"node": kc.node_id, "card": kc.card_id, "what": "EXPLOIT"}
        tag = "fresh" if kc.is_new else "known"
        if kc.node_id == here:
            return Action(me, "EXPLOIT", here, kc.card_id, "opportunist",
                          f"{name} exploits {kc.cve_id} on {n.name}: {tag} card, score {_pct(kc.score)}, est. odds {_pct(est)}.", facts)
        return _move(me, here, kc.node_id, "opportunist",
                     f"{name} moves toward {n.name} for {kc.cve_id} ({tag}, score {_pct(kc.score)}).", facts)

    # no leads: look at the nearest node it has not scanned yet
    unseen = [n for n in v.nodes if n.state == "healthy" and n.id not in v.intel_tick]
    pool = unseen or [n for n in v.nodes if n.state == "healthy" and v.tick - v.intel_tick.get(n.id, -99) > 12]
    if not pool:
        mem["target"] = None
        return Action(me, "WAIT", policy="opportunist", reason=f"{name} has no leads and waits.", facts={"leads": 0})
    tgt = min(pool, key=lambda n: (dist(here, n.id), -n.value, n.id))
    facts = {"leads": 0, "target": tgt.name, "distance": dist(here, tgt.id), "unscanned_nodes": len(unseen)}
    mem["target"] = {"node": tgt.id, "card": None, "what": "SCAN"}
    if tgt.id == here:
        return Action(me, "SCAN", here, policy="opportunist",
                      reason=f"{name} has no leads, so it scans {tgt.name}.", facts=facts)
    return _move(me, here, tgt.id, "opportunist", f"{name} has no leads and walks to {tgt.name} to look.", facts)


# ------------------------------------------------------------------ red 2: Patient
def patient(v: RedView, mem: dict, rng: random.Random) -> Action:
    """Scans first, waits for good odds, prefers older cards and pivots from footholds."""
    t = v.tuning
    me, here = v.me, v.node_id
    draw = rng.random()
    name = "Patient"
    thr = mem.setdefault("patience", t.patient_start_odds)
    fail_tick = mem.setdefault("fail_tick", {})
    foothold = v.nodes[here].state == "compromised"

    def fresh_intel(node_id: int) -> bool:
        return node_id in v.intel_tick and v.tick - v.intel_tick[node_id] <= t.patient_stale_ticks

    def reach(dst: int, policy: str, reason_move: str, reason_pivot: str, facts: dict) -> Action:
        if foothold and 0 < dist(here, dst) <= t.pivot_range and v.nodes[dst].state != "isolated":
            return Action(me, "PIVOT", dst, policy=policy, reason=reason_pivot, facts={**facts, "from_foothold": v.nodes[here].name})
        return _move(me, here, dst, policy, reason_move, facts)

    scored = []
    for kc in v.cards:
        if not kc.recon or not fresh_intel(kc.node_id) or kc.patch_seen != "unpatched":
            continue
        n = v.nodes[kc.node_id]
        if n.state != "healthy":
            continue
        fails = mem["failed"].get(kc.card_id, 0)
        if fails >= 2 or (fails >= 1 and (kc.intel_tick or 0) <= fail_tick.get(kc.card_id, -1)):
            continue                      # it does not retry without a new scan
        est = breach_probability(kc.score, kc.in_kev, "unpatched", False, True, tuning=t)
        u = est * (n.value ** 1.3) * (1.25 if kc.age_days >= 30 else 1.0) * (0.8 if kc.is_new else 1.0)
        u /= (1 + 0.25 * dist(here, kc.node_id))
        if v.mate_target == kc.node_id:
            u *= 0.5
        scored.append((u, -kc.card_id, kc, est))

    # campaign orders: same care, but the target is given
    for node_id, card_id in v.campaign:
        n = v.nodes[node_id]
        if n.state != "healthy" or mem["failed"].get(card_id, 0) >= 2:
            continue
        kc = next((k for k in v.cards if k.card_id == card_id), None)
        if kc is None or kc.patch_seen == "patched":
            continue
        facts = {"campaign": True, "target": n.name, "cve": kc.cve_id, "score": round(kc.score, 3),
                 "scanned": kc.recon, "distance": dist(here, node_id)}
        mem["target"] = {"node": node_id, "card": card_id, "what": "EXPLOIT"}
        if node_id != here:
            return reach(node_id, "campaign",
                         f"{name} walks to {n.name} for the campaign on {kc.cve_id}.",
                         f"{name} pivots to {n.name} for the campaign on {kc.cve_id}.", facts)
        if not fresh_intel(here) or (mem["failed"].get(card_id, 0) and (kc.intel_tick or 0) <= fail_tick.get(card_id, -1)):
            return Action(me, "SCAN", here, policy="campaign",
                          reason=f"{name} scans {n.name} before the campaign strike on {kc.cve_id}.", facts=facts)
        est = breach_probability(kc.score, kc.in_kev, kc.patch_seen or "unpatched", False, True, tuning=t)
        facts["est_odds"] = round(est, 3)
        return Action(me, "EXPLOIT", here, card_id, "campaign",
                      f"{name} strikes {kc.cve_id} on {n.name} for the campaign: scanned, est. odds {_pct(est)}.", facts)

    good = [s for s in scored if s[3] >= thr]
    if good:
        u, _, kc, est = max(good)
        n = v.nodes[kc.node_id]
        facts = {"target": n.name, "cve": kc.cve_id, "score": round(kc.score, 3), "est_odds": round(est, 3),
                 "threshold": round(thr, 2), "age_days": kc.age_days, "node_value": n.value,
                 "intel_age_ticks": v.tick - (kc.intel_tick or 0), "distance": dist(here, kc.node_id)}
        mem["target"] = {"node": kc.node_id, "card": kc.card_id, "what": "EXPLOIT"}
        mem["idle"] = 0
        if kc.node_id == here:
            return Action(me, "EXPLOIT", here, kc.card_id, "patient",
                          f"{name} exploits {kc.cve_id} on {n.name}: scanned {facts['intel_age_ticks']} ticks ago, "
                          f"{kc.age_days} days old, est. odds {_pct(est)} over its bar of {_pct(thr)}.", facts)
        return reach(kc.node_id, "patient",
                     f"{name} moves toward {n.name}, where scanned {kc.cve_id} gives est. odds {_pct(est)}.",
                     f"{name} pivots from {v.nodes[here].name} to {n.name} for scanned {kc.cve_id} (est. odds {_pct(est)}).",
                     facts)

    # nothing clears the bar: lower it a little and keep scanning
    mem["patience"] = max(t.patient_min_odds, thr - t.patient_decay)
    stale = [n for n in v.nodes if n.state == "healthy" and not fresh_intel(n.id)]
    if draw < t.eps_patient and stale:
        tgt = rng.choice(stale)
    elif stale:
        tgt = max(stale, key=lambda n: ((n.value + (1.0 if n.id not in v.intel_tick else 0.0))
                                        / (1 + 0.5 * dist(here, n.id)) * (0.5 if v.mate_target == n.id else 1.0), -n.id))
    else:
        mem["target"] = None
        return Action(me, "WAIT", policy="patient",
                      reason=f"{name} waits: nothing scanned clears its bar of {_pct(thr)}.",
                      facts={"threshold": round(thr, 2), "best_odds": round(max([s[3] for s in scored], default=0.0), 3)})
    facts = {"target": tgt.name, "node_value": tgt.value, "threshold": round(thr, 2),
             "best_odds": round(max([s[3] for s in scored], default=0.0), 3),
             "scanned_before": tgt.id in v.intel_tick, "distance": dist(here, tgt.id)}
    mem["target"] = {"node": tgt.id, "card": None, "what": "SCAN"}
    if tgt.id == here:
        return Action(me, "SCAN", here, policy="patient",
                      reason=f"{name} scans {tgt.name} (value {tgt.value}); nothing known clears its bar of {_pct(thr)}.",
                      facts=facts)
    return reach(tgt.id, "patient",
                 f"{name} walks to {tgt.name} (value {tgt.value}) to scan it.",
                 f"{name} pivots from {v.nodes[here].name} into {tgt.name} to map it.", facts)


# ------------------------------------------------------------------ blue helpers
def _claimed(v: BlueView) -> tuple[set[int], dict[int, int], set[int]]:
    """Cards team-mates are after, how many mates target each node, nodes being restored."""
    cards: set[int] = set()
    nodes: dict[int, int] = {}
    restoring: set[int] = set()
    for m in v.mates.values():
        tg = m.get("target")
        if not tg:
            continue
        if tg.get("card") is not None:
            cards.add(tg["card"])
        if tg.get("node") is not None:
            nodes[tg["node"]] = nodes.get(tg["node"], 0) + 1
            if tg.get("what") == "RESTORE":
                restoring.add(tg["node"])
    return cards, nodes, restoring


def _respond(v: BlueView, mem: dict, name: str, policy: str) -> Action | None:
    """Team rule shared by Triage and Patcher: if a compromised node nobody is restoring is within
    reach and I am the nearest free blue agent to it, break off and restore it."""
    t = v.tuning
    here = v.node_id
    _, _, restoring = _claimed(v)
    open_comp = [n for n in v.nodes if n.is_compromised and n.id not in restoring]
    if not open_comp:
        return None
    others = [m["node"] for m in v.mates.values()
              if m.get("kind") == "agent" and m.get("busy") is None
              and not ((m.get("target") or {}).get("what") == "RESTORE")]
    best = None
    for n in open_comp:
        d = dist(here, n.id)
        if d > t.responder_range:
            continue
        if any(dist(o, n.id) < d for o in others):
            continue
        key = (-n.value, d, n.id)
        if best is None or key < best[0]:
            best = (key, n, d)
    if best is None:
        return None
    _, n, d = best
    mem["target"] = {"node": n.id, "card": None, "what": "RESTORE"}
    facts = {"target": n.name, "node_value": n.value, "distance": d, "nearest_responder": True,
             "compromised_nodes": len([x for x in v.nodes if x.is_compromised])}
    if d == 0:
        return Action(v.me, "RESTORE", here, policy=policy,
                      reason=f"{name} restores {n.name} (value {n.value}): it is compromised and {name} is the nearest free defender.",
                      facts=facts)
    return _move(v.me, here, n.id, policy,
                 f"{name} breaks off to restore {n.name} (value {n.value}), {d} step(s) away and nearest free defender.", facts)


def _rank_key(c) -> tuple:
    return (c.priority_rank is None, c.priority_rank or 0, -c.score, c.card_id)


# ------------------------------------------------------------------ blue 1: Triage
def triage(v: BlueView, mem: dict, rng: random.Random) -> Action:
    """Follows the pipeline's patch_priority ranks wherever they lead, with epsilon noise."""
    t = v.tuning
    me, here = v.me, v.node_id
    draw = rng.random()
    name = "Triage"
    claimed, _, _ = _claimed(v)
    r = _respond(v, mem, name, "triage")
    if r is not None:
        return r
    pool =[(n, c) for n in v.nodes if n.state != "isolated" for c in n.unpatched() if c.card_id not in claimed]
    pool.sort(key=lambda nc: _rank_key(nc[1]))
    if not pool:
        mem["target"] = None
        return Action(me, "WAIT", policy="triage", reason=f"{name} waits: the priority list has no open card left.",
                      facts={"open_cards": 0})
    cur = mem.get("target") or {}
    pick = next((nc for nc in pool if nc[1].card_id == cur.get("card")), None)
    noisy = False
    if pick is None:
        if draw < t.eps_triage and len(pool) > 1:
            pick, noisy = rng.choice(pool[1:5]), True
        else:
            pick = pool[0]
    n, c = pick
    mem["target"] = {"node": n.id, "card": c.card_id, "what": "PATCH"}
    rank = c.priority_rank
    rtxt = f"rank {rank}" if rank is not None else "unranked"
    facts = {"target": n.name, "cve": c.cve_id, "priority_rank": rank, "score": round(c.score, 3),
             "score_source": c.score_source, "open_cards": len(pool), "epsilon_pick": noisy,
             "distance": dist(here, n.id)}
    if n.id == here:
        return Action(me, "PATCH", here, c.card_id, "triage",
                      f"{name} patches {c.cve_id} on {n.name}: {rtxt} on the {c.score_source} priority list, score {_pct(c.score)}"
                      + (" (off-list pick)." if noisy else "."), facts)
    return _move(me, here, n.id, "triage",
                 f"{name} walks to {n.name} for {c.cve_id}, {rtxt} on the priority list.", facts)


# ------------------------------------------------------------------ blue 2: Patcher
def patcher(v: BlueView, mem: dict, rng: random.Random) -> Action:
    """Walks to the highest-value exposed node and patches it clean before moving on."""
    t = v.tuning
    me, here = v.me, v.node_id
    draw = rng.random()
    name = "Patcher"
    claimed, node_claims, restoring = _claimed(v)

    def open_cards(n: Node) -> list:
        return [c for c in n.unpatched() if c.card_id not in claimed]

    node_here = v.nodes[here]
    comp = [n for n in v.nodes if n.is_compromised]
    if node_here.is_compromised and here not in restoring and len(comp) >= t.patcher_restore_min:
        mem["target"] = {"node": here, "card": None, "what": "RESTORE"}
        return Action(me, "RESTORE", here, policy="patcher",
                      reason=f"{name} restores {node_here.name}: {len(comp)} nodes are compromised and nobody is on this one.",
                      facts={"target": node_here.name, "compromised_nodes": len(comp), "node_value": node_here.value})
    r = _respond(v, mem, name, "patcher")
    if r is not None:
        return r

    cands = []
    for n in v.nodes:
        if n.state == "isolated":
            continue
        oc = open_cards(n)
        if not oc:
            continue
        if node_claims.get(n.id, 0) >= 1 and len(oc) <= 1:
            continue
        cands.append((exposure(n, t) / (1 + 0.3 * dist(here, n.id)), -n.id, n))
    cur = mem.get("target") or {}
    keep = next((x for x in cands if x[2].id == cur.get("node") and cur.get("what") == "PATCH"), None)
    if not cands:
        mem["target"] = None
        return Action(me, "WAIT", policy="patcher", reason=f"{name} waits: no exposed node is left.",
                      facts={"exposed_nodes": 0})
    cands.sort(reverse=True)
    noisy = False
    if keep:
        pick = keep
    elif draw < t.eps_patcher and len(cands) > 1:
        pick, noisy = rng.choice(cands[1:3]), True
    else:
        pick = cands[0]
    n = pick[2]
    oc = sorted(open_cards(n), key=lambda c: (-c.score, c.card_id))
    c = oc[0]
    mem["target"] = {"node": n.id, "card": c.card_id if n.id == here else None, "what": "PATCH"}
    facts = {"target": n.name, "node_value": n.value, "exposure": round(exposure(n, t), 2),
             "open_cards_on_node": len(oc), "distance": dist(here, n.id), "epsilon_pick": noisy}
    if n.id == here:
        facts.update({"cve": c.cve_id, "score": round(c.score, 3)})
        return Action(me, "PATCH", here, c.card_id, "patcher",
                      f"{name} patches {c.cve_id} on {n.name} (value {n.value}): highest score of {len(oc)} open cards here, {_pct(c.score)}.",
                      facts)
    return _move(me, here, n.id, "patcher",
                 f"{name} walks to {n.name}: value {n.value} with {len(oc)} open cards, the most exposed node in reach.", facts)


# ------------------------------------------------------------------ blue 3: Hunter
def hunter(v: BlueView, mem: dict, rng: random.Random) -> Action:
    """Monitors, restores compromised nodes, and isolates when the pressure is high."""
    t = v.tuning
    me, here = v.me, v.node_id
    draw = rng.random()
    name = "Hunter"
    _, _, restoring = _claimed(v)
    node_here = v.nodes[here]
    comp = [n for n in v.nodes if n.is_compromised]
    reds_here = [r for r, nid in v.revealed_reds if nid == here]
    pressure = len(comp) + len(v.revealed_reds)

    if node_here.is_compromised and here not in restoring:
        facts = {"target": node_here.name, "node_value": node_here.value, "compromised_nodes": len(comp),
                 "revealed_reds": len(v.revealed_reds), "pressure": pressure}
        if node_here.state != "isolated" and pressure >= t.hunter_isolate_pressure and node_here.value >= 2 \
                and v.tick - mem.get("isolated_at", {}).get(here, -99) > 12:
            mem["target"] = {"node": here, "card": None, "what": "ISOLATE"}
            return Action(me, "ISOLATE", here, policy="hunter",
                          reason=f"{name} isolates {node_here.name}: pressure {pressure} "
                                 f"({len(comp)} compromised, {len(v.revealed_reds)} red in sight), so it stops the bleeding first.",
                          facts=facts)
        mem["target"] = {"node": here, "card": None, "what": "RESTORE"}
        return Action(me, "RESTORE", here, policy="hunter",
                      reason=f"{name} restores {node_here.name} (value {node_here.value}); {len(comp)} node(s) compromised.",
                      facts=facts)

    if reds_here and node_here.state == "healthy":
        danger = max((breach_probability(c.score, c.in_kev, c.patch_state, node_here.monitored, True, tuning=t)
                      for c in node_here.cards), default=0.0)
        facts = {"target": node_here.name, "red_in_sight": reds_here, "worst_odds": round(danger, 3),
                 "node_value": node_here.value, "pressure": pressure}
        if danger >= 0.5 and node_here.value >= 2 and pressure >= 2:
            mem["target"] = {"node": here, "card": None, "what": "ISOLATE"}
            return Action(me, "ISOLATE", here, policy="hunter",
                          reason=f"{name} isolates {node_here.name}: a red is on it and the worst open card still breaches at {_pct(danger)}.",
                          facts=facts)
        if node_here.monitored_ticks <= 1:
            mem["target"] = {"node": here, "card": None, "what": "MONITOR"}
            return Action(me, "MONITOR", here, policy="hunter",
                          reason=f"{name} monitors {node_here.name}: a red is standing on it.", facts=facts)

    open_comp = [n for n in comp if n.id not in restoring]
    if open_comp:
        tgt = max(open_comp, key=lambda n: (n.value / (1 + dist(here, n.id)), -n.id))
        mem["target"] = {"node": tgt.id, "card": None, "what": "RESTORE"}
        return _move(me, here, tgt.id, "hunter",
                     f"{name} runs to {tgt.name}: compromised, value {tgt.value}, {dist(here, tgt.id)} step(s) away.",
                     {"target": tgt.name, "node_value": tgt.value, "distance": dist(here, tgt.id),
                      "compromised_nodes": len(comp)})

    elsewhere = [(r, nid) for r, nid in v.revealed_reds if nid != here and v.nodes[nid].state == "healthy"]
    if elsewhere:
        r, nid = min(elsewhere, key=lambda x: (dist(here, x[1]), x[0]))
        mem["target"] = {"node": nid, "card": None, "what": "MONITOR"}
        return _move(me, here, nid, "hunter",
                     f"{name} closes in on {v.nodes[nid].name}, where a red was detected.",
                     {"target": v.nodes[nid].name, "red_in_sight": [r], "distance": dist(here, nid)})

    # quiet moment: ask for public reporting on one board CVE (Telem; only when switched on)
    if t.intel_enabled:
        done = mem.get("intel_cves", [])
        last = mem.get("intel_ticks", [-10**6])[-1] if mem.get("intel_ticks") else -10**6
        if len(done) < t.intel_max_per_match and v.tick - last >= t.intel_cooldown and v.tick >= 3:
            cands = [(n, c) for n in v.nodes for c in n.cards
                     if c.patch_state != "patched" and c.cve_id not in done]
            if cands:
                n, c = min(cands, key=lambda nc: (not nc[1].is_new, -nc[1].score, _rank_key(nc[1])))
                facts = {"target": n.name, "cve": c.cve_id, "score": round(c.score, 3), "fresh": c.is_new,
                         "intel_used": len(done), "intel_budget": t.intel_max_per_match}
                return Action(me, "INTEL", n.id, c.card_id, "hunter",
                              f"{name} pulls this week's public reporting on {c.cve_id} ({'new' if c.is_new else 'open'} card on {n.name}).",
                              facts)

    # patrol: the most exposed node it has not watched lately
    seen = mem["visit_tick"]
    cands = []
    for n in v.nodes:
        if n.state != "healthy" or not n.unpatched():
            continue
        if n.id == here and n.monitored_ticks > 1:
            continue
        stale = min(1.0, (v.tick - seen.get(n.id, -20)) / 12.0) if n.id != here else 0.6
        cands.append((exposure(n, t) * stale / (1 + 0.3 * dist(here, n.id)), -n.id, n))
    if not cands:
        mem["target"] = None
        return Action(me, "WAIT", policy="hunter", reason=f"{name} waits: nothing is exposed and nothing is compromised.",
                      facts={"exposed_nodes": 0})
    cands.sort(reverse=True)
    pick = rng.choice(cands[:3]) if (draw < t.eps_hunter and len(cands) > 1) else cands[0]
    n = pick[2]
    facts = {"target": n.name, "node_value": n.value, "exposure": round(exposure(n, t), 2),
             "ticks_since_visit": v.tick - seen.get(n.id, -20), "distance": dist(here, n.id)}
    mem["target"] = {"node": n.id, "card": None, "what": "MONITOR"}
    if n.id == here:
        return Action(me, "MONITOR", here, policy="hunter",
                      reason=f"{name} monitors {n.name}: value {n.value}, exposure {facts['exposure']}, {len(n.unpatched())} open card(s).",
                      facts=facts)
    return _move(me, here, n.id, "hunter",
                 f"{name} patrols toward {n.name} (value {n.value}, exposure {facts['exposure']}).", facts)


POLICIES = {"opportunist": (opportunist, "red"), "patient": (patient, "red"),
            "triage": (triage, "blue"), "patcher": (patcher, "blue"), "hunter": (hunter, "blue")}


def choose(state: MatchState, actor_id: str, rng: random.Random) -> Action:
    actor = state.actors[actor_id]
    fn, side = POLICIES[actor.role]
    view = red_view(state, actor_id) if side == "red" else blue_view(state, actor_id)
    return fn(view, state.memory[actor_id], rng)


def choose_all(state: MatchState, rng: random.Random) -> dict[str, Action]:
    """One decision per free agent, in a fixed order so a seed replays exactly."""
    out: dict[str, Action] = {}
    for aid in BLUE_AGENT_IDS + RED_IDS:
        if state.actors[aid].busy_ticks > 0:
            continue
        out[aid] = choose(state, aid, rng)
    return out


def learn(state: MatchState, rows: list[dict]) -> None:
    """Fold this tick's outcomes into each actor's memory. state.tick has already advanced."""
    for row in rows:
        aid = row["actor_id"]
        mem = state.memory[aid]
        actor = state.actors[aid]
        tick = row["tick"]
        nid = actor.node_id
        if not mem["visited"] or mem["visited"][-1] != nid:
            mem["visited"].append(nid)
            del mem["visited"][:-40]
        mem["visit_tick"][nid] = tick
        mem["last_outcome"] = (row["action"], row["outcome"])
        action, outcome = row["action"], row["outcome"]
        if actor.team == "red":
            if action == "SCAN" and outcome == "scanned":
                mem["scanned"][nid] = tick
            elif action == "PIVOT" and outcome == "pivoted":
                mem["scanned"][nid] = tick
            elif action == "EXPLOIT":
                card = state.nodes[row["node_id"]].card_by_cve(row["cve_id"])
                if outcome == "failed" and card is not None:
                    mem["failed"][card.card_id] = mem["failed"].get(card.card_id, 0) + 1
                    mem.setdefault("fail_tick", {})[card.card_id] = tick
                    mem["target"] = None
                elif outcome == "breach":
                    mem["breaches"] += 1
                    mem["target"] = None
                    if "patience" in mem:
                        mem["patience"] = state.tuning.patient_start_odds
                elif outcome == "invalid":
                    mem["target"] = None
            if action == "WAIT":
                mem["idle"] += 1
        else:
            if outcome in ("patched", "restored", "noop", "invalid", "isolated", "monitoring"):
                mem["target"] = None
            if outcome == "isolated":
                mem.setdefault("isolated_at", {})[nid] = tick
            if outcome == "monitoring":
                mem.setdefault("monitored_at", {})[nid] = tick
            if action == "INTEL" and outcome == "requested":
                mem.setdefault("intel_ticks", []).append(tick)
                mem.setdefault("intel_cves", []).append(row["cve_id"])
