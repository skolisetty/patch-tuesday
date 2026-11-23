"""The deterministic core: action set, breach formula, scoring.

rules.step(state, actions, rng) touches only the in-memory state and the seeded rng.
No network, no disk, no wall clock.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .config import TUNING, Tuning
from . import feedback as fb
from .state import (DIRS, RESOLVE_ORDER, Actor, Card, MatchState, Node, delta, dist, neighbours, node_at)

RED_ACTIONS = ("MOVE", "SCAN", "EXPLOIT", "PIVOT", "WAIT")
BLUE_ACTIONS = ("MOVE", "PATCH", "MONITOR", "ISOLATE", "RESTORE", "WAIT")
# INTEL (design.md section 7, Telem) is a blue *agent* move only: the Hunter asks for public
# reporting on one board CVE. It changes nothing on the board; the server runs the search
# off the tick and writes the reduced result back into the row. Never offered to humans.
BLUE_AGENT_ACTIONS = BLUE_ACTIONS + ("INTEL",)
PATCH_MULT_KEYS = ("unpatched", "patching", "patched")


@dataclass
class Action:
    actor_id: str
    kind: str = "WAIT"
    target_node: int | None = None
    card_id: int | None = None
    policy: str = ""
    reason: str = ""
    facts: dict = field(default_factory=dict)


@dataclass
class StepResult:
    rows: list[dict]
    events: list[dict]
    facts: dict[str, dict]   # ledger_id -> structured reason (the facts the policy used)
    deltas: list[dict] = field(default_factory=list)     # every score change of this tick, with its cause
    feedback: list[dict] = field(default_factory=list)   # one plain sentence per human action resolved


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def breach_base(score: float, k: int = TUNING.breach_k) -> float:
    s = clamp(float(score), 0.0, 1.0)
    return 1.0 - (1.0 - s) ** k


def breach_probability(score: float, in_kev: bool = False, patch_state: str = "unpatched",
                       monitored: bool = False, scanned: bool = False, isolated: bool = False,
                       tuning: Tuning = TUNING) -> float:
    """Breach odds for EXPLOIT(node, card), exactly as in docs/CONTRACTS.md."""
    if isolated:
        return 0.0
    base = breach_base(score, tuning.breach_k)
    kev = tuning.kev_bonus if in_kev else 0.0
    patch_m = {"unpatched": 1.0, "patching": tuning.patching_mult, "patched": 0.0}[patch_state]
    defense = tuning.monitor_mult if monitored else 1.0
    recon = tuning.recon_bonus if scanned else 0.0
    return clamp((base + kev + recon) * patch_m * defense, 0.0, tuning.p_cap)


def best_patch_card(node: Node) -> Card | None:
    """The unpatched card with the best priority_rank (nulls last, then highest score)."""
    cands = node.unpatched()
    if not cands:
        return None
    return min(cands, key=lambda c: (c.priority_rank is None, c.priority_rank or 0, -c.score, c.card_id))


def record_intel(state: MatchState, red_id: str, node: Node, recon: bool = True) -> None:
    entry = state.red_intel.setdefault(red_id, {}).get(node.id)
    was_recon = bool(entry and entry.get("recon"))
    state.red_intel[red_id][node.id] = {
        "tick": state.tick, "recon": recon or was_recon,
        "cards": {c.card_id: c.patch_state for c in node.cards},
    }


def has_recon(state: MatchState, red_id: str, node_id: int) -> bool:
    e = state.red_intel.get(red_id, {}).get(node_id)
    return bool(e and e.get("recon"))


def human_action(state: MatchState, player: int, action: str, direction: str | None = None,
                 cve: str | None = None) -> Action | None:
    """Translate a client {"t":"act"} message into an Action. Returns None if it is malformed."""
    aid = f"p{player}"
    actor = state.actors.get(aid)
    if actor is None or action not in BLUE_ACTIONS:
        return None
    act = Action(actor_id=aid, kind=action, policy="human", facts={"input": action})
    if action == "MOVE":
        if direction not in DIRS:
            return None
        x, y = state.nodes[actor.node_id].x, state.nodes[actor.node_id].y
        dx, dy = DIRS[direction]
        tgt = node_at(x + dx, y + dy)
        act.target_node = tgt if tgt is not None else -1   # -1 = off the grid, resolved as blocked
        act.facts["dir"] = direction
    elif action == "PATCH" and cve:
        card = state.nodes[actor.node_id].card_by_cve(cve)
        act.card_id = card.card_id if card else -1
        act.facts["cve"] = cve
    return act


# --------------------------------------------------------------------------- step
def _row(state: MatchState, actor: Actor, action: str, node_id: int | None, outcome: str,
         policy: str, reason: str, card: Card | None = None, p: float | None = None,
         roll: float | None = None, with_score: bool = False) -> dict:
    return {
        "ledger_id": f"{state.match_id}-{state.tick:03d}-{actor.id}",
        "match_id": state.match_id,
        "world_asof": state.world.get("asof_date"),
        "pipeline_run_id": state.world.get("pipeline_run_id"),
        "model_version": state.world.get("model_version"),
        "tick": state.tick,
        "ts": None,                       # stamped by the caller, outside the pure core
        "actor_id": actor.id, "team": actor.team, "kind": actor.kind,
        "action": action, "node_id": node_id,
        "cve_id": card.cve_id if card else None,
        "score_used": round(card.score, 6) if (card and with_score) else None,
        "score_source": card.score_source if (card and with_score) else None,
        "p_breach": None if p is None else round(p, 6),
        "roll": None if roll is None else round(roll, 6),
        "outcome": outcome, "policy": policy or actor.role, "reason_text": reason,
        "llm_model": None, "llm_latency_ms": None,
    }


def _default_reason(actor: Actor, act: Action, node: Node) -> str:
    if act.reason:
        return act.reason
    if actor.kind == "human":
        return f"{actor.name} chose {act.kind} at {node.name}."
    return f"{actor.name} chose {act.kind} at {node.name}."


def step(state: MatchState, actions: dict[str, Action], rng: random.Random) -> StepResult:
    """Advance the match by one tick. Mutates `state`; returns one ledger row per actor."""
    t = state.tuning
    rows: list[dict] = []
    events: list[dict] = []
    facts: dict[str, dict] = {}
    deltas: list[dict] = []
    feedback: list[dict] = []

    def ev(kind: str, text: str) -> None:
        events.append({"kind": kind, "text": text})

    for aid in RESOLVE_ORDER:
        actor = state.actors[aid]
        node = state.nodes[actor.node_id]

        # ---- a multi-tick action or a cooldown is still running
        if actor.busy_ticks > 0:
            actor.busy_ticks -= 1
            b = actor.busy or {"action": "WAIT", "node": None, "card": None}
            kind = b.get("action", "WAIT")
            actor.action = kind
            actor.target_node = b.get("node")
            pol = b.get("policy") or actor.role
            if actor.busy_ticks > 0 or kind == "WAIT":
                why = b.get("why", "busy")
                outcome = "working" if kind != "WAIT" else why
                left = actor.busy_ticks
                text = (f"{actor.name} continues {kind} at {node.name} ({left} tick{'s' if left != 1 else ''} left)."
                        if kind != "WAIT" else f"{actor.name} is {why} ({left} tick{'s' if left != 1 else ''} left).")
                card = node.card(b.get("card")) if b.get("card") is not None else None
                row = _row(state, actor, kind, actor.node_id, outcome, pol, text, card=card)
            else:
                info: dict = {}
                row = _complete(state, actor, b, pol, ev, deltas, info)
                if actor.kind == "human":
                    feedback.append(fb.entry(actor, kind, row["outcome"], node, info.get("card"),
                                             info.get("gained"), t))
            if actor.busy_ticks == 0:
                actor.busy = None
            rows.append(row)
            facts[row["ledger_id"]] = {"actor": actor.name, "action": kind, "continuing": True,
                                       "node": node.name, "outcome": row["outcome"]}
            continue

        act = actions.get(aid) or Action(actor_id=aid, kind="WAIT")
        allowed = (RED_ACTIONS if actor.team == "red"
                   else BLUE_AGENT_ACTIONS if actor.kind == "agent" else BLUE_ACTIONS)
        kind = act.kind if act.kind in allowed else "WAIT"
        pol = act.policy or actor.role
        reason = _default_reason(actor, act, node)
        actor.action = kind
        actor.target_node = None
        card: Card | None = None
        p = roll = None
        with_score = False
        outcome = "waiting"
        info = {}                      # filled by _complete when the action finishes this tick
        acted_on = node                # the machine the action applies to (a MOVE leaves it)

        if kind == "MOVE":
            tgt = act.target_node
            actor.target_node = tgt if (tgt is not None and 0 <= tgt < len(state.nodes)) else None
            if tgt is not None and tgt in neighbours(actor.node_id):
                actor.node_id = tgt
                outcome = "moved"
            else:
                outcome = "blocked"

        elif kind == "PATCH":
            actor.target_node = node.id
            card = node.card(act.card_id) if act.card_id is not None else best_patch_card(node)
            with_score = card is not None
            if card is None or card.patch_state != "unpatched":
                outcome = "invalid"
            else:
                card.patch_state = "patching"
                actor.busy_ticks = t.patch_ticks - 1
                actor.busy = {"action": "PATCH", "node": node.id, "card": card.card_id, "policy": pol}
                outcome = "patching"
                if actor.busy_ticks == 0:   # only if patch_ticks is tuned down to 1
                    r2 = _complete(state, actor, actor.busy, pol, ev, deltas, info)
                    outcome, actor.busy = r2["outcome"], None

        elif kind == "MONITOR":
            actor.target_node = node.id
            node.monitored_ticks = max(node.monitored_ticks, t.monitor_ticks)
            outcome = "monitoring"

        elif kind == "ISOLATE":
            actor.target_node = node.id
            if node.state == "isolated":
                outcome = "invalid"
            else:
                node.prior_state = node.state
                node.state = "isolated"
                node.isolated_ticks = t.isolate_ticks
                outcome = "isolated"
                thrown = []
                for rid in ("red1", "red2"):
                    red = state.actors[rid]
                    if red.node_id == node.id:
                        red.node_id = red.home
                        red.busy_ticks = max(red.busy_ticks, t.eject_cooldown)
                        red.busy = {"action": "WAIT", "node": None, "card": None, "why": "ejected"}
                        thrown.append(red.name)
                ev("info", f"{actor.name} isolated {node.name}" + (f"; {' and '.join(thrown)} thrown out." if thrown else "."))

        elif kind == "RESTORE":
            actor.target_node = node.id
            if not node.is_compromised:
                outcome = "invalid"
            else:
                actor.busy_ticks = t.restore_ticks - 1
                actor.busy = {"action": "RESTORE", "node": node.id, "card": None, "policy": pol}
                outcome = "restoring"
                if actor.busy_ticks == 0:
                    r2 = _complete(state, actor, actor.busy, pol, ev, deltas, info)
                    outcome, actor.busy = r2["outcome"], None

        elif kind == "INTEL":
            found = state.find_card(act.card_id)
            if found is None or not t.intel_enabled:
                outcome = "invalid"
            else:
                tnode, card = found
                actor.target_node = tnode.id
                with_score = True
                outcome = "requested"

        elif kind == "SCAN":
            actor.target_node = node.id
            if node.state == "isolated":
                outcome = "blocked"
            else:
                record_intel(state, aid, node, recon=True)
                outcome = "scanned"

        elif kind == "PIVOT":
            tgt = act.target_node
            actor.target_node = tgt
            ok = (node.state == "compromised" and tgt is not None and 0 <= tgt < len(state.nodes)
                  and tgt != node.id and dist(node.id, tgt) <= t.pivot_range
                  and state.nodes[tgt].state != "isolated")
            if ok:
                actor.node_id = tgt
                record_intel(state, aid, state.nodes[tgt], recon=True)   # lateral movement doubles as recon
                outcome = "pivoted"
            else:
                outcome = "invalid"

        elif kind == "EXPLOIT":
            actor.target_node = node.id
            card = node.card(act.card_id)
            with_score = card is not None
            if card is None or node.state == "compromised":
                outcome = "invalid"
            else:
                p = breach_probability(card.score, card.in_kev, card.patch_state, node.monitored,
                                       has_recon(state, aid, node.id), node.state == "isolated", t)
                roll = rng.random()
                card.attempted = True
                if roll < p:
                    outcome = "breach"
                    node.state = "compromised"
                    card.breached = True
                    state.score.breaches_total += 1
                    if card.priority_rank is not None and card.priority_rank <= 50:
                        state.score.breaches_in_top50 += 1
                    first = not node.ever_breached
                    if first:
                        node.ever_breached = True
                        state.score.red += t.first_breach_points
                        state.score.first_breach += t.first_breach_points
                        deltas.append(delta("red", t.first_breach_points, "first_breach", node.id,
                                            actor.id, card.cve_id))
                    if card.breached_tick is None:
                        card.breached_by, card.breached_tick = actor.name, state.tick
                    actor.revealed_ticks = max(actor.revealed_ticks, t.breach_reveal_ticks)
                    actor.busy_ticks = t.exploit_cooldown_success
                    actor.busy = {"action": "WAIT", "node": None, "card": None, "why": "digging in"}
                    ev("breach", f"{actor.name} breached {node.name} via {card.cve_id}"
                                 f" (breach chance {p:.0%}, roll {roll:.2f})" + (", first breach +5." if first else "."))
                else:
                    outcome = "failed"
                    if card.patch_state == "patched" or node.state == "isolated":
                        # the attempt bounced; the red now knows why
                        e = state.red_intel[aid].setdefault(node.id, {"tick": state.tick, "recon": False, "cards": {}})
                        e["cards"][card.card_id] = card.patch_state
                    if node.monitored and rng.random() < t.detect_prob:
                        actor.revealed_ticks = max(actor.revealed_ticks, t.detect_ticks)
                        ev("info", f"{actor.name} was detected at {node.name} after a failed exploit.")
                    actor.busy_ticks = t.exploit_cooldown_fail
                    actor.busy = {"action": "WAIT", "node": None, "card": None, "why": "regrouping"}

        # node_id is where the actor stands after the action (the destination of a MOVE or PIVOT)
        row = _row(state, actor, kind, actor.node_id, outcome, pol, reason, card=card, p=p, roll=roll,
                   with_score=with_score)
        rows.append(row)
        if actor.kind == "human" and kind != "WAIT":
            feedback.append(fb.entry(actor, kind, outcome, state.nodes[actor.node_id] if kind == "MOVE" else acted_on,
                                     info.get("card", card), info.get("gained"), t))
        f = dict(act.facts)
        f.update({"actor": actor.name, "role": actor.role, "team": actor.team, "action": kind,
                  "node": state.nodes[actor.node_id].name, "node_value": state.nodes[actor.node_id].value,
                  "outcome": outcome})
        if card is not None:
            f.update({"cve": card.cve_id, "card": card.title, "score": round(card.score, 3)})
        if p is not None:
            f["breach_odds"] = round(p, 3)
        facts[row["ledger_id"]] = f

    # ---- end of tick: scoring, then timers
    for node in state.nodes:
        if node.state == "compromised":
            state.score.red += node.value
            state.score.held += node.value
            deltas.append(delta("red", node.value, "held", node.id))
        elif node.state == "isolated":
            state.score.blue -= t.isolate_cost
            state.score.isolate_cost -= t.isolate_cost
            deltas.append(delta("blue", -t.isolate_cost, "isolate_cost", node.id))
    for node in state.nodes:
        if node.monitored_ticks > 0:
            node.monitored_ticks -= 1
        if node.state == "isolated":
            node.isolated_ticks -= 1
            if node.isolated_ticks <= 0:
                node.state = node.prior_state if node.prior_state in ("healthy", "compromised") else "healthy"
                node.isolated_ticks = 0
    for rid in ("red1", "red2"):
        red = state.actors[rid]
        if red.revealed_ticks > 0:
            red.revealed_ticks -= 1
        if state.nodes[red.node_id].monitored:
            red.revealed_ticks = max(red.revealed_ticks, 1)   # a watched node shows who stands on it
        if red.revealed:
            red.last_seen_node = red.node_id
    state.tick += 1
    if state.campaign and state.tick >= state.campaign.get("until", 0):
        state.campaign = None
    return StepResult(rows=rows, events=events, facts=facts, deltas=deltas, feedback=feedback)


def _complete(state: MatchState, actor: Actor, b: dict, pol: str, ev, deltas: list[dict] | None = None,
              info: dict | None = None) -> dict:
    """Finish a PATCH or RESTORE that was started on an earlier tick. Score changes are appended
    to `deltas`; `info` receives {"card", "gained"} for the human feedback sentence (gained is
    None when the patch earned nothing because the card had already been attacked)."""
    deltas = deltas if deltas is not None else []
    info = info if info is not None else {}
    t = state.tuning
    node = state.nodes[b["node"]] if b.get("node") is not None else state.nodes[actor.node_id]
    kind = b["action"]
    if kind == "PATCH":
        card = node.card(b.get("card"))
        info["card"] = card
        if card is None or card.patch_state != "patching":
            return _row(state, actor, "PATCH", node.id, "noop", pol,
                        f"{actor.name} found nothing left to patch at {node.name}.", card=card, with_score=card is not None)
        card.patch_state = "patched"
        card.patched_by, card.patched_tick = actor.name, state.tick
        gained = 0.0
        if not card.attempted:
            gained = node.value * card.score
            state.score.blue += gained
            state.score.patch += gained
            info["gained"] = gained
            if gained:
                deltas.append(delta("blue", gained, "patch", node.id, actor.id, card.cve_id))
        ev("patched", f"{actor.name} patched {card.cve_id} on {node.name}"
                      + (f" (+{gained:.2f})." if gained else " (already attacked, no points)."))
        return _row(state, actor, "PATCH", node.id, "patched", pol,
                    f"{actor.name} finished patching {card.cve_id} on {node.name}.", card=card, with_score=True)
    if kind == "RESTORE":
        if not node.is_compromised:
            return _row(state, actor, "RESTORE", node.id, "noop", pol,
                        f"{actor.name} found {node.name} already clean.")
        if node.state == "isolated":
            node.prior_state = "healthy"
        else:
            node.state = "healthy"
        for c in node.cards:
            if c.breached and c.patch_state != "patched":
                c.patch_state = "patched"       # the way in is closed; no patch points
        node.monitored_ticks = max(node.monitored_ticks, t.restore_monitor_ticks)
        state.score.blue += t.restore_points
        state.score.restore += t.restore_points
        deltas.append(delta("blue", t.restore_points, "restore", node.id, actor.id))
        ev("restored", f"{actor.name} restored {node.name} (+{t.restore_points:g}).")
        return _row(state, actor, "RESTORE", node.id, "restored", pol,
                    f"{actor.name} finished restoring {node.name}.")
    return _row(state, actor, "WAIT", actor.node_id, "waiting", pol, f"{actor.name} waits.")
