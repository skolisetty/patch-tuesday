"""One match tick end to end (still pure: memory and seeded rng only), plus the mid-match
world change: the new week's cards drop onto nodes and the reds open a campaign."""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from typing import Any

from . import policy, rules
from .rules import Action, breach_probability
from .state import RED_IDS, Card, MatchState, card_from_serving, dist, take_events


@dataclass
class TickResult:
    rows: list[dict]
    events: list[dict]
    facts: dict[str, dict] = field(default_factory=dict)
    campaign_started: bool = False
    deltas: list[dict] = field(default_factory=list)     # rules.StepResult.deltas
    feedback: list[dict] = field(default_factory=list)   # rules.StepResult.feedback


def gate_text_legacy(world: dict) -> str:
    """The wording used when world.json carries no hit counts (old or hand-made serving files)."""
    c, e = world.get("challenger_p_at_50"), world.get("epss_p_at_50")
    nums = (f"hits in top 50: our model {c:.0%}, EPSS baseline {e:.0%}" if c is not None and e is not None
            else "hit counts not available")
    if world.get("score_source") == "model":
        version = world.get("model_version")
        return (f"Gate passed for {world.get('asof_date')}: our model{f' ({version})' if version else ''} "
                f"now sets the danger scores ({nums}).")
    return f"Gate blocked for {world.get('asof_date')}: the EPSS baseline sets the danger scores ({nums})."


def source_name(world: dict) -> str:
    """Who sets the danger scores, in the screen's words."""
    return "our model" if world.get("score_source") == "model" else "the EPSS baseline"


def pct(x: float) -> str:
    """A 0..1 value as the screen shows it: 94%, and one decimal below 10% so 0.4% is not 0%."""
    p = float(x) * 100.0
    return f"{p:.1f}%" if p < 10 else f"{p:.0f}%"


def gate_text(world: dict) -> str:
    """The `gate` event, in plain words: how many of the bugs that really became known exploited
    each scorer put in its top 50, and who sets the danger scores now."""
    ch, ep, n = world.get("challenger_hits_at_50"), world.get("epss_hits_at_50"), world.get("n_positives")
    if ch is None or ep is None or n is None:
        return gate_text_legacy(world)
    if world.get("score_source") == "model":
        version = world.get("model_version")
        return (f"Gate passed: our model put {ch} of the {n} soon-to-be-exploited bugs in its top 50; "
                f"the EPSS baseline put {ep}. Our model{f' ({version})' if version else ''} now sets "
                f"the danger scores.")
    return f"Gate blocked: our model {ch}, EPSS baseline {ep} (of {n}). EPSS stays."


def apply_world_change(state: MatchState, serving: Any, rng: random.Random) -> list[dict]:
    """The pipeline published a new week. Refresh scores, drop the new cards, start the campaign."""
    t = state.tuning
    events: list[dict] = []
    old_asof = state.world.get("asof_date")
    state.world = dict(serving.world)
    by_cve = {c["cve_id"]: c for c in serving.cards}
    events.append({"kind": "gate", "text": gate_text(state.world)})

    # cards already on the board take the new week's score, rank and source
    on_board = state.board_cves()
    for n in state.nodes:
        for c in n.cards:
            new = by_cve.get(c.cve_id)
            if new is not None:
                c.score, c.priority_rank = float(new["score"]), new.get("priority_rank")
                c.in_kev, c.score_source = bool(new["in_kev"]), new.get("score_source") or c.score_source
            c.is_new = False              # last week's advisories are no longer news

    fresh = [c for c in serving.cards if c["cve_id"] not in on_board and c.get("is_new_this_week")]
    if not fresh:   # a deck with no flagged cards: anything not on the board yet counts as the drop
        fresh = sorted((c for c in serving.cards if c["cve_id"] not in on_board),
                       key=lambda c: (c.get("age_days") or 0, c["card_id"]))
    fresh.sort(key=lambda c: (-float(c["score"]), c["card_id"]))
    fresh = fresh[: t.drop_max_cards]

    dropped: list[tuple[int, Card]] = []
    for raw in fresh:
        room = [n for n in state.nodes if len(n.cards) < t.max_cards_per_node and n.state != "isolated"]
        if not room:
            break
        fewest = min(len(n.cards) for n in room)
        node = rng.choice([n for n in room if len(n.cards) <= fewest + 1])
        card = card_from_serving(raw)
        card.card_id = state.next_card_id
        state.next_card_id += 1
        card.is_new = True
        card.dropped_tick = state.tick
        node.cards.append(card)
        dropped.append((node.id, card))
    if dropped:
        names = sorted({state.nodes[nid].name for nid, _ in dropped})
        by = source_name(state.world)
        events.append({"kind": "drop",
                       "text": f"Patch Tuesday {state.world.get('asof_date')} (was {old_asof}): {len(dropped)} new "
                               f"card{'s' if len(dropped) != 1 else ''} dropped on {len(names)} "
                               f"machine{'s' if len(names) != 1 else ''}; danger set by {by}."})
        events.extend(start_campaign(state, dropped))
    else:
        events.append({"kind": "info", "text": f"Data is now as of {state.world.get('asof_date')}; no new cards to drop."})
    return events


def start_campaign(state: MatchState, dropped: list[tuple[int, Card]]) -> list[dict]:
    """Both reds agree on the freshest high-score cards and split them between themselves.

    The dropped cards are public advisories, so the reds may know their ids and scores.
    They do not know patch state or monitoring, and the estimate below uses neither.
    """
    t = state.tuning
    ranked = []
    for nid, c in dropped:
        n = state.nodes[nid]
        if n.state != "healthy":          # node state is public
            continue
        est = breach_probability(c.score, c.in_kev, "unpatched", False, False, tuning=t)
        ranked.append((est * n.value, -c.card_id, nid, c, est))
    ranked.sort(reverse=True)
    ranked = ranked[: t.campaign_targets]
    if not ranked:
        return []
    targets: dict[str, list[list[int]]] = {r: [] for r in RED_IDS}
    pos = {r: state.actors[r].node_id for r in RED_IDS}
    for _, _, nid, c, _ in ranked:        # each target goes to the red that will be nearer, load-balanced
        r = min(RED_IDS, key=lambda r: (len(targets[r]) * 2 + dist(pos[r], nid), r))
        targets[r].append([nid, c.card_id])
        pos[r] = nid
    state.campaign = {"start": state.tick, "until": state.tick + t.campaign_ticks, "targets": targets}
    for r in RED_IDS:
        state.memory[r]["target"] = None
        state.memory[r]["campaigns"] = state.memory[r].get("campaigns", 0) + 1
    top = ranked[0]
    parts = []
    for r in RED_IDS:
        if targets[r]:
            parts.append(f"{state.actors[r].name} takes " + ", ".join(state.nodes[n].name for n, _ in targets[r]))
    return [{"kind": "campaign",
             "text": f"Red campaign on {len(ranked)} fresh card{'s' if len(ranked) != 1 else ''} "
                     f"(top: {top[3].cve_id}, danger {pct(top[3].score)}, "
                     f"on {state.nodes[top[2]].name}). " + "; ".join(parts) + "."}]


def run_tick(state: MatchState, human_actions: dict[str, Action] | None, rng: random.Random,
             new_world: Any = None) -> TickResult:
    """Apply a pending world change, let every agent choose, resolve the tick, update memories."""
    if state.phase != "running":
        return TickResult(rows=[], events=[])
    # "coach"/"backup" events the backup flow queued since the last tick and the server did not
    # take itself (state.take_events); empty while the feature is off.
    events: list[dict] = take_events(state)
    campaign = False
    if new_world is not None and state.tick < state.ticks_total - state.tuning.world_change_cutoff:
        events.extend(apply_world_change(state, new_world, rng))
        campaign = any(e["kind"] == "campaign" for e in events)
    actions = policy.choose_all(state, rng)
    for aid, act in (human_actions or {}).items():
        if act is not None and aid in ("p1", "p2"):
            actions[aid] = act
    res = rules.step(state, actions, rng)
    policy.learn(state, res.rows)
    events.extend(res.events)
    if state.tick >= state.ticks_total:
        state.phase = "ended"
        s = state.score
        verdict = "Red wins" if s.red > s.blue else "Blue wins" if s.blue > s.red else "Draw"
        events.append({"kind": "info",
                       "text": f"Match over. {verdict}: red {s.red:.1f}, blue {s.blue:.1f}, "
                               f"{s.breaches_total} breaches ({s.breaches_in_top50} on top-50 priority cards)."})
    return TickResult(rows=res.rows, events=events, facts=res.facts, campaign_started=campaign,
                      deltas=res.deltas, feedback=res.feedback)


def reveal_text(state: MatchState, reveal: dict[str, dict]) -> str | None:
    """End-of-match reveal: how many cards on the board really entered KEV within 91 days."""
    if not reveal:
        return None
    cards = [c for n in state.nodes for c in n.cards if c.cve_id in reveal]
    if not cards:
        return None
    real = [c for c in cards if reveal[c.cve_id].get("entered_kev_within_91d")]
    patched = sum(1 for c in real if c.patch_state == "patched" and not c.breached)
    hit = sum(1 for c in real if c.breached)
    return (f"Reveal: {len(real)} of {len(cards)} cards on the board really entered KEV within 91 days; "
            f"blue patched {patched} of them in time, red breached through {hit}.")


REVEAL_HORIZON_DAYS = 91


def _days_between(later: str | None, earlier: str | None) -> int | None:
    try:
        return (dt.date.fromisoformat(str(later)[:10]) - dt.date.fromisoformat(str(earlier)[:10])).days
    except (TypeError, ValueError):
        return None


def reveal_data(state: MatchState, reveal: dict[str, dict]) -> dict | None:
    """`state.reveal`: the board cards that really entered KEV within 91 days of the board's date,
    and what happened to each in this match. None when there is no reveal file (or the match is
    still running: the future is never shown early).

    A reveal entry may carry its own "asof_date" (the week it was computed for; the server stamps
    it); without one the match's current world date is used."""
    if not reveal or state.phase != "ended":
        return None
    board_asof = state.world.get("asof_date")
    n_cards = 0
    items = []
    for n in state.nodes:
        for c in n.cards:
            r = reveal.get(c.cve_id)
            if r is None:
                continue
            n_cards += 1
            if not r.get("entered_kev_within_91d"):
                continue
            if c.breached:
                fate, by, tick = "breached", c.breached_by, c.breached_tick
            elif c.patch_state == "patched":
                fate, by, tick = "patched", c.patched_by, c.patched_tick
            else:
                fate, by, tick = "open", None, None
            added = r.get("kev_date_added")
            items.append({
                "cve_id": c.cve_id, "title": c.title, "node_id": n.id, "node_name": n.name,
                "kev_date_added": added,
                "days_after_asof": _days_between(added, r.get("asof_date") or board_asof),
                "priority_rank": c.priority_rank, "score": round(c.score, 4),
                "score_source": c.score_source, "in_kev_at_asof": c.in_kev,
                "fate": fate, "by": by, "tick": tick,
            })
    items.sort(key=lambda i: (i["kev_date_added"] is None, i["kev_date_added"] or "", i["cve_id"]))
    return {"n_cards": n_cards, "n_real": len(items), "horizon_days": REVEAL_HORIZON_DAYS, "items": items}
