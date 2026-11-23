"""One plain sentence per human action, for `state.player_feedback`. Pure: no I/O, no rng.

Every outcome rules.step can give a human has a sentence here. WAIT (no key pressed) and the
middle ticks of a multi-tick action ("working") produce no entry.
"""
from __future__ import annotations

from .config import Tuning

OK_OUTCOMES = {"moved", "patching", "patched", "monitoring", "isolated", "restoring", "restored"}


def _ticks(n: int) -> str:
    return f"{n} tick{'s' if n != 1 else ''}"


def _pct(score: float) -> str:
    return f"{score * 100:.0f}%"


def sentence(action: str, outcome: str, node, card, gained: float | None, t: Tuning) -> str:
    """`node` is the machine the action applied to (for MOVE: where the player now stands);
    `card` the card involved or None; `gained` the patch points or None when it earned nothing."""
    name = node.name
    cve = card.cve_id if card is not None else None
    if action == "MOVE":
        if outcome == "moved":
            return f"Moved to {name}."
        return "Can't move: that is the edge of the network."
    if action == "PATCH":
        if outcome == "patching":
            return f"Patching {cve} ({_ticks(t.patch_ticks)})."
        if outcome == "patched":
            if gained is None:
                return f"Patched {cve}: already attacked, no points."
            return f"Patched {cve}: +{gained:.2f} (machine value {node.value} x danger {_pct(card.score)})."
        if outcome == "noop":
            return (f"Nothing left to patch: {cve} was already closed." if cve
                    else "Nothing left to patch on this machine.")
        # invalid
        if card is not None and card.patch_state == "patching":
            return f"{cve} is already being patched."
        if card is not None and card.patch_state == "patched":
            return f"{cve} is already patched."
        if not node.unpatched():
            return "Nothing left to patch on this machine."
        return "Can't patch: that card is not on this machine."
    if action == "MONITOR":
        return (f"Monitoring {name} for {_ticks(t.monitor_ticks)}: attackers here are revealed "
                f"and their breach chance is cut.")
    if action == "ISOLATE":
        if outcome == "isolated":
            return (f"Isolated {name} for {_ticks(t.isolate_ticks)}: no attack can land, "
                    f"but it costs {t.isolate_cost:g} point per tick.")
        return "Can't isolate: this machine is already isolated."
    if action == "RESTORE":
        if outcome == "restoring":
            return f"Restoring {name} ({_ticks(t.restore_ticks)})."
        if outcome == "restored":
            return f"Restored {name}: +{t.restore_points:g}. Red no longer earns from it."
        if outcome == "noop":
            return f"Nothing to restore: {name} was already clean."
        return "Nothing to restore: this machine is not breached."
    return f"{action}: {outcome}."


def entry(actor, action: str, outcome: str, node, card, gained: float | None, t: Tuning) -> dict:
    """{"player": 1|2, "action", "outcome", "ok", "text"} for a human actor (id "p1" or "p2")."""
    return {"player": int(actor.id[1:]) if actor.id[1:].isdigit() else actor.id,
            "action": action, "outcome": outcome, "ok": outcome in OK_OUTCOMES,
            "text": sentence(action, outcome, node, card, gained, t)}
