"""In-memory match state and the contract-shaped state message. No I/O here."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .config import TUNING, Tuning

GRID_W, GRID_H = 5, 4
N_NODES = GRID_W * GRID_H

# (name, value) by node id; id = y * 5 + x
NODE_LAYOUT = [
    ("MAIL-01", 2), ("WEB-01", 2), ("WEB-02", 1), ("VPN-01", 3), ("DNS-01", 1),
    ("APP-01", 2), ("DB-01", 3), ("FILE-01", 2), ("HR-01", 1), ("DEV-01", 1),
    ("CI-01", 2), ("DC-01", 3), ("ERP-01", 3), ("WIKI-01", 1), ("PRINT-01", 1),
    ("BKP-01", 2), ("IOT-01", 1), ("POS-01", 2), ("LAB-01", 1), ("JUMP-01", 2),
]

# id, name, team, kind, role, start node
ACTOR_DEFS = [
    ("red1", "Opportunist", "red", "agent", "opportunist", 0),
    ("red2", "Patient", "red", "agent", "patient", 19),
    ("blue1", "Triage", "blue", "agent", "triage", 6),
    ("blue2", "Patcher", "blue", "agent", "patcher", 12),
    ("blue3", "Hunter", "blue", "agent", "hunter", 7),
    ("p1", "Player 1", "blue", "human", "human", 11),
    ("p2", "Player 2", "blue", "human", "human", 8),
]
ACTOR_IDS = [a[0] for a in ACTOR_DEFS]
RED_IDS = ["red1", "red2"]
BLUE_AGENT_IDS = ["blue1", "blue2", "blue3"]
HUMAN_IDS = ["p1", "p2"]
# Resolution order inside a tick: defenders first, then attackers.
RESOLVE_ORDER = ["p1", "p2", "blue1", "blue2", "blue3", "red1", "red2"]

# "Call for backup": at most one specialist joins a match, as an optional eighth actor. It exists
# in state.actors only after backup.spawn(); every loop over actors goes through the three
# functions below, which return the fixed lists above while it is absent.
BACKUP_ID = "blue4"
BACKUP_NODE = 19                       # JUMP-01: where the specialist walks onto the board
SPECIALISTS = {"responder": "Responder", "hardener": "Hardener", "sentinel": "Sentinel"}


def actor_ids(state: "MatchState") -> list[str]:
    """ACTOR_IDS, plus the specialist once it has joined."""
    return ACTOR_IDS + [BACKUP_ID] if BACKUP_ID in state.actors else ACTOR_IDS


def blue_agent_ids(state: "MatchState") -> list[str]:
    """BLUE_AGENT_IDS, plus the specialist once it has joined (it decides last of the blue agents)."""
    return BLUE_AGENT_IDS + [BACKUP_ID] if BACKUP_ID in state.actors else BLUE_AGENT_IDS


def resolve_order(state: "MatchState") -> list[str]:
    """RESOLVE_ORDER with the specialist after the other defenders and before the attackers."""
    if BACKUP_ID not in state.actors:
        return RESOLVE_ORDER
    return ["p1", "p2", "blue1", "blue2", "blue3", BACKUP_ID, "red1", "red2"]

LEDGER_FIELDS = [
    "ledger_id", "match_id", "world_asof", "pipeline_run_id", "model_version", "tick", "ts",
    "actor_id", "team", "kind", "action", "node_id", "cve_id", "score_used", "score_source",
    "p_breach", "roll", "outcome", "policy", "reason_text", "llm_model", "llm_latency_ms",
]
EVENT_KINDS = {"breach", "patched", "restored", "drop", "campaign", "gate", "intel", "info",
               "coach", "backup"}


def node_xy(node_id: int) -> tuple[int, int]:
    return node_id % GRID_W, node_id // GRID_W


def node_at(x: int, y: int) -> int | None:
    if 0 <= x < GRID_W and 0 <= y < GRID_H:
        return y * GRID_W + x
    return None


def dist(a: int, b: int) -> int:
    ax, ay = node_xy(a)
    bx, by = node_xy(b)
    return abs(ax - bx) + abs(ay - by)


def neighbours(node_id: int) -> list[int]:
    x, y = node_xy(node_id)
    out = []
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        n = node_at(x + dx, y + dy)
        if n is not None:
            out.append(n)
    return out


def step_toward(src: int, dst: int) -> int:
    """Next node on a shortest path (larger axis gap first, x on ties). Deterministic."""
    if src == dst:
        return src
    sx, sy = node_xy(src)
    dx_, dy_ = node_xy(dst)
    gx, gy = dx_ - sx, dy_ - sy
    if abs(gx) >= abs(gy) and gx != 0:
        return node_at(sx + (1 if gx > 0 else -1), sy)  # type: ignore[return-value]
    return node_at(sx, sy + (1 if gy > 0 else -1))  # type: ignore[return-value]


DIRS = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}


@dataclass
class Card:
    card_id: int
    cve_id: str
    title: str
    score: float
    in_kev: bool = False
    technique_id: str | None = None
    tactic: str | None = None
    priority_rank: int | None = None
    is_new: bool = False
    age_days: int = 0
    score_source: str = "epss"
    patch_state: str = "unpatched"      # unpatched | patching | patched
    attempted: bool = False             # any exploit attempt so far
    breached: bool = False              # this card was the way in
    dropped_tick: int | None = None     # set when it arrived in a mid-match drop
    # Who closed or used this card, for the end-of-match reveal only (never in the state message
    # while a match runs).
    patched_by: str | None = None       # actor name whose PATCH finished (None for a RESTORE clean-up)
    patched_tick: int | None = None
    breached_by: str | None = None      # actor name of the first successful exploit through it
    breached_tick: int | None = None
    patched_by_id: str | None = None    # actor id of that PATCH (for the Coach's per-player numbers)
    patch_gain: float | None = None     # points that PATCH earned (0.0 when the card had been attacked)

    def public(self) -> dict:
        return {
            "card_id": self.card_id, "cve_id": self.cve_id, "title": self.title,
            "score": round(self.score, 4), "in_kev": self.in_kev,
            "patch_state": self.patch_state, "is_new": self.is_new,
            "technique_id": self.technique_id, "tactic": self.tactic,
            "priority_rank": self.priority_rank,
        }


def card_from_serving(c: dict) -> Card:
    return Card(card_id=c["card_id"], cve_id=c["cve_id"], title=c["title"], score=float(c["score"]),
                in_kev=bool(c["in_kev"]), technique_id=c.get("technique_id"), tactic=c.get("tactic"),
                priority_rank=c.get("priority_rank"), is_new=bool(c.get("is_new_this_week")),
                age_days=int(c.get("age_days") or 0), score_source=c.get("score_source") or "epss")


@dataclass
class Node:
    id: int
    x: int
    y: int
    name: str
    value: int
    state: str = "healthy"              # healthy | compromised | isolated
    prior_state: str = "healthy"        # state to return to when isolation ends
    monitored_ticks: int = 0
    isolated_ticks: int = 0
    ever_breached: bool = False
    cards: list[Card] = field(default_factory=list)

    @property
    def monitored(self) -> bool:
        return self.monitored_ticks > 0

    @property
    def is_compromised(self) -> bool:
        """True also for an isolated node that is still compromised underneath."""
        return self.state == "compromised" or (self.state == "isolated" and self.prior_state == "compromised")

    def card(self, card_id: int | None) -> Card | None:
        for c in self.cards:
            if c.card_id == card_id:
                return c
        return None

    def card_by_cve(self, cve: str | None) -> Card | None:
        for c in self.cards:
            if c.cve_id == cve:
                return c
        return None

    def unpatched(self) -> list[Card]:
        return [c for c in self.cards if c.patch_state == "unpatched"]

    def public(self) -> dict:
        return {"id": self.id, "x": self.x, "y": self.y, "name": self.name, "value": self.value,
                "state": self.state, "monitored": self.monitored,
                "cards": [c.public() for c in self.cards]}


@dataclass
class Actor:
    id: str
    name: str
    team: str
    kind: str
    role: str
    node_id: int
    home: int
    action: str = "WAIT"
    target_node: int | None = None
    revealed_ticks: int = 0
    busy_ticks: int = 0
    busy: dict | None = None            # {"action","node","card"} while a multi-tick action runs
    last_seen_node: int | None = None   # where the defenders last saw this red (fog of war)
    reinforcement: bool = False         # True only for the specialist called in mid-match ("blue4")

    @property
    def revealed(self) -> bool:
        # Blue actors are always visible; a red only while it has been detected.
        return self.team == "blue" or self.revealed_ticks > 0

    def public(self, fog: bool = False) -> dict:
        """With fog (a running match), an unrevealed red shows only where it was last seen,
        doing nothing: the humans get no more than the blue agents' view (policy.blue_view)."""
        if fog and not self.revealed:
            seen = self.last_seen_node if self.last_seen_node is not None else self.home
            return {"id": self.id, "name": self.name, "team": self.team, "kind": self.kind,
                    "role": self.role, "node_id": seen, "action": "WAIT", "target_node": None,
                    "revealed": False, "busy_ticks": 0}
        out = {"id": self.id, "name": self.name, "team": self.team, "kind": self.kind,
               "role": self.role, "node_id": self.node_id, "action": self.action,
               "target_node": self.target_node, "revealed": self.revealed,
               "busy_ticks": self.busy_ticks}
        if self.reinforcement:           # the key exists only on the specialist's entry
            out["reinforcement"] = True
        return out


@dataclass
class Score:
    red: float = 0.0
    blue: float = 0.0
    breaches_total: int = 0
    breaches_in_top50: int = 0
    # Cumulative causes this match. blue == patch + restore + isolate_cost; red == first_breach + held.
    patch: float = 0.0
    restore: float = 0.0
    isolate_cost: float = 0.0           # zero or negative
    first_breach: float = 0.0
    held: float = 0.0


def new_track() -> dict:
    """Match history the Coach analyses (game/backup.py). Written by rules.step, read only by the
    analysis; it never influences a rule, a policy or the random stream. Everything in it was
    public to the defenders when it was recorded: a breach is announced, a restore is a blue
    move, a detection is a red becoming visible.

      breaches    [{"tick","node","cve","rank","monitored","open_ticks","untouched","first"}]
      restores    [{"tick","node","actor"}]
      detections  [{"tick","node"}]   a red went from unseen to seen through monitoring (not a breach)
      red_ticks / hidden_ticks        attacker-ticks since the attackers became active / of those, unseen
      scores      [(red, blue)]       cumulative, after each tick (index i = after tick i)
      breached    [int]               machines breached at the end of each tick
    """
    return {"breaches": [], "restores": [], "detections": [], "red_ticks": 0, "hidden_ticks": 0,
            "scores": [], "breached": []}


@dataclass
class MatchState:
    match_id: str
    seed: int
    phase: str                           # lobby | running | ended
    tick: int
    world: dict                          # normalised world.json object
    nodes: list[Node]
    actors: dict[str, Actor]
    score: Score = field(default_factory=Score)
    tuning: Tuning = TUNING
    # red_intel[red_id][node_id] = {"tick": int, "cards": {card_id: patch_state}, "recon": bool}
    red_intel: dict[str, dict[int, dict]] = field(default_factory=dict)
    memory: dict[str, dict] = field(default_factory=dict)
    campaign: dict | None = None
    next_card_id: int = 0
    track: dict = field(default_factory=new_track)
    # "Call for backup" hooks (None / empty while the feature is off):
    backup: dict | None = None           # the state.backup wire object, kept current by backup.BackupFlow
    pending_events: list[dict] = field(default_factory=list)   # "coach"/"backup" events not yet sent

    @property
    def ticks_total(self) -> int:
        return self.tuning.ticks_total

    def node_of(self, actor_id: str) -> Node:
        return self.nodes[self.actors[actor_id].node_id]

    def counts(self) -> dict[str, int]:
        c = {"healthy": 0, "compromised": 0, "isolated": 0}
        for n in self.nodes:
            c[n.state] += 1
        return c

    def find_card(self, card_id: int | None) -> tuple[Node, Card] | None:
        if card_id is None:
            return None
        for n in self.nodes:
            c = n.card(card_id)
            if c is not None:
                return n, c
        return None

    def board_cves(self) -> set[str]:
        return {c.cve_id for n in self.nodes for c in n.cards}


def new_memory(actor_id: str) -> dict:
    return {"visited": [], "visit_tick": {}, "scanned": {}, "failed": {}, "target": None,
            "last_outcome": None, "idle": 0, "breaches": 0}


def new_match(serving: Any, seed: int, match_id: str, tuning: Tuning = TUNING,
              phase: str = "running") -> MatchState:
    """Deal a board from the serving deck. `serving` needs .world and .cards (see world.ServingData)."""
    rng = random.Random(f"deal-{seed}")
    nodes = [Node(id=i, x=i % GRID_W, y=i // GRID_W, name=NODE_LAYOUT[i][0], value=NODE_LAYOUT[i][1])
             for i in range(N_NODES)]
    deck = [card_from_serving(c) for c in serving.cards]
    rng.shuffle(deck)
    want = [3 if n.value == 3 else (2 + (1 if rng.random() < 0.4 else 0)) for n in nodes]
    # With a small deck, give every node one card before anyone gets a second.
    for round_no in range(3):
        for n in nodes:
            if want[n.id] > round_no and deck:
                n.cards.append(deck.pop())
    actors = {a[0]: Actor(id=a[0], name=a[1], team=a[2], kind=a[3], role=a[4], node_id=a[5], home=a[5],
                          last_seen_node=a[5])
              for a in ACTOR_DEFS}
    st = MatchState(match_id=match_id, seed=seed, phase=phase, tick=0, world=dict(serving.world),
                    nodes=nodes, actors=actors, tuning=tuning)
    st.red_intel = {r: {} for r in RED_IDS}
    st.memory = {a: new_memory(a) for a in ACTOR_IDS}
    st.next_card_id = max([c["card_id"] for c in serving.cards] + [0]) + 1
    if phase == "running":
        for r in RED_IDS:
            st.actors[r].busy_ticks = tuning.red_entry_delay
            st.actors[r].busy = {"action": "WAIT", "node": None, "card": None, "why": "infiltrating"}
    return st


def world_public(world: dict, advancing: bool, advance: dict | None = None) -> dict:
    """The `world` object of the state message. Every field after `advancing` is an addition and
    may be null when the serving files lack it (mock or hand-made data)."""
    run_id = world.get("pipeline_run_id")
    visible = world.get("n_cves_visible") or None       # the loader stores a missing counter as 0
    src = world.get("score_source")
    return {"asof_date": world.get("asof_date"), "score_source": src,
            "model_version": world.get("model_version"),
            "challenger_p_at_50": world.get("challenger_p_at_50"),
            "epss_p_at_50": world.get("epss_p_at_50"), "advancing": bool(advancing),
            "pipeline_run_id": run_id if run_id and run_id != "unknown" else None,
            "gate_passed": (src == "model") if src in ("model", "epss") else None,
            "challenger_hits_at_50": world.get("challenger_hits_at_50"),
            "epss_hits_at_50": world.get("epss_hits_at_50"),
            "n_positives": world.get("n_positives"), "n_candidates": world.get("n_candidates"),
            "n_cves_visible": visible,
            "n_new_cves_this_week": world.get("n_new_cves_this_week") if visible else None,
            "advance": advance}


def score_rate(state: MatchState) -> dict:
    """Points per tick right now: red earns the value of every compromised machine, blue pays
    isolate_cost for every isolated one. An isolated machine earns red nothing, breached or not."""
    red = sum(n.value for n in state.nodes if n.state == "compromised")
    iso = sum(1 for n in state.nodes if n.state == "isolated")
    cost = iso * state.tuning.isolate_cost
    blue = -cost if cost else 0
    return {"red": red, "blue": int(blue) if float(blue).is_integer() else round(blue, 2)}


def score_public(state: MatchState) -> dict:
    c = state.counts()
    s = state.score
    return {"red": round(s.red, 2), "blue": round(s.blue, 2),
            "healthy": c["healthy"], "compromised": c["compromised"], "isolated": c["isolated"],
            "breaches_total": s.breaches_total,
            "breaches_in_top50": s.breaches_in_top50,
            "rate": score_rate(state),
            "breakdown": {"blue": {"patch": round(s.patch, 2), "restore": round(s.restore, 2),
                                   "isolate_cost": round(s.isolate_cost, 2)},
                          "red": {"first_breach": round(s.first_breach, 2), "held": round(s.held, 2)}}}


def take_events(state: MatchState) -> list[dict]:
    """Remove and return the events the backup flow queued for the state message ("coach" and
    "backup" kinds). The server may call this right after flow.on_tick to send them with the
    current tick; whatever is still queued when the next tick starts is delivered by
    match.run_tick in that tick's events, so nothing is lost either way."""
    out, state.pending_events = list(state.pending_events), []
    return out


DELTA_CAUSES = {"patch", "restore", "isolate_cost", "first_breach", "held"}


def delta(team: str, amount: float, cause: str, node_id: int | None, actor_id: str | None = None,
          cve_id: str | None = None) -> dict:
    """One entry of `score_deltas`: a single change to one team's score, with its cause."""
    return {"team": team, "amount": round(float(amount), 4), "cause": cause, "node_id": node_id,
            "actor_id": actor_id, "cve_id": cve_id}


def deltas_public(state: MatchState, deltas: list[dict] | None) -> list[dict]:
    """While a match runs, a delta never names an unrevealed red or the card it used. (A breach
    reveals its red for breach_reveal_ticks, so this only bites if that is tuned to 0.)"""
    out = []
    for d in deltas or []:
        a = state.actors.get(d.get("actor_id"))
        if state.phase == "running" and a is not None and a.team == "red" and not a.revealed:
            d = {**d, "actor_id": None, "cve_id": None}
        out.append(dict(d))
    return out


# Ledger fields that would tell the humans what an undetected red did.
_HIDDEN_ROW = {"action": "UNSEEN", "node_id": None, "cve_id": None, "score_used": None, "score_source": None,
               "p_breach": None,
               "roll": None, "outcome": "undetected", "policy": None, "llm_model": None,
               "llm_latency_ms": None, "reason_text": "Not seen by the defenders."}


def hidden_row(row: dict) -> dict:
    """A ledger row with every field that gives away an undetected red's move blanked."""
    return {**row, **_HIDDEN_ROW}


def is_unseen(state: MatchState, row: dict) -> bool:
    """True for a running match's row by a red that is not revealed now."""
    a = state.actors.get(row.get("actor_id"))
    return state.phase == "running" and a is not None and a.team == "red" and not a.revealed


def snapshot(state: MatchState, rows: list[dict] | None = None, events: list[dict] | None = None,
             advancing: bool = False, advance: dict | None = None, deltas: list[dict] | None = None,
             feedback: list[dict] | None = None, reveal: dict | None = None,
             backup: dict | None = None) -> dict:
    """The {"t":"state"} message of docs/CONTRACTS.md. While a match runs, an unrevealed red's
    position, action and ledger row are fogged (the full rows stay in the ledger files and
    become visible through /api/ledger when the match ends).

    `deltas` and `feedback` are this tick's rules.StepResult lists; `reveal` (match.reveal_data)
    is only ever sent once the match has ended.

    `backup` (or, when not passed, state.backup, which backup.BackupFlow keeps current) is the
    "Call for backup" object. The key is present only when there is one: with the feature off the
    message has exactly the keys it had before."""
    fog = state.phase == "running"
    rows = [hidden_row(r) if is_unseen(state, r) else r for r in (rows or [])]
    backup = backup if backup is not None else state.backup
    msg = {
        "t": "state", "match_id": state.match_id, "phase": state.phase, "tick": state.tick,
        "ticks_total": state.ticks_total,
        "world": world_public(state.world, advancing, advance),
        "nodes": [n.public() for n in state.nodes],
        "actors": [state.actors[a].public(fog) for a in actor_ids(state)],
        "score": score_public(state),
        "ledger": rows,
        "events": [{"kind": e["kind"], "text": e["text"]} for e in (events or [])],
        "score_deltas": deltas_public(state, deltas),
        "player_feedback": [dict(f) for f in (feedback or [])],
        "reveal": reveal if state.phase == "ended" else None,
    }
    if backup is not None:
        msg["backup"] = backup
    return msg
