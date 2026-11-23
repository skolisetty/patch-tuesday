// The "next best move" sentence of a player panel. Pure functions over one state message;
// nothing here touches the page.

import { fmtDanger, fmtPoints, plural } from './dom.js';

export const KEYS = {
  p1: { tag: 'P1', patch: 'E', monitor: 'Q', restore: 'R', isolate: 'F', dirs: { N: 'W', S: 'S', W: 'A', E: 'D' } },
  p2: { tag: 'P2', patch: 'Enter', monitor: 'Right Shift', restore: '/', isolate: '.', dirs: { N: '↑', S: '↓', W: '←', E: '→' } },
};

const RESTORE_TICKS = 3;
const ISOLATE_TICKS = 5;
const WORTH_HERE = 0.3; // a card on your own machine worth at least this is patched before walking

// Cards in the order the server patches them: best priority_rank first,
// unranked cards last (highest danger first among those).
export function sortCards(cards) {
  return [...(cards || [])].sort((a, b) => {
    const ra = Number.isFinite(a.priority_rank) ? a.priority_rank : Infinity;
    const rb = Number.isFinite(b.priority_rank) ? b.priority_rank : Infinity;
    if (ra !== rb) return ra - rb;
    return (Number(b.score) || 0) - (Number(a.score) || 0);
  });
}

export const cardKey = (nodeId, cveId) => `${nodeId}|${cveId}`;

export function isAttacked(node, card, attacked) {
  return !!card.attempted || !!(attacked && attacked.has(cardKey(node.id, card.cve_id)));
}

// Points for patching this card now: machine value x danger, nothing once Red has tried it.
export function worth(node, card, attacked) {
  if (card.patch_state !== 'unpatched' || isAttacked(node, card, attacked)) return 0;
  return (Number(node.value) || 0) * (Number(card.score) || 0);
}

// The card the patch key acts on: the best-priority unpatched card that still pays,
// else the best-priority unpatched card.
export function patchChoice(node, attacked) {
  if (!node) return null;
  const open = sortCards(node.cards).filter((c) => c.patch_state === 'unpatched');
  return open.find((c) => !isAttacked(node, c, attacked)) || open[0] || null;
}

export function steps(a, b) {
  return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
}

// "2 steps (← ↑)" in the player's own keys. y grows to the south, as on the server.
export function route(from, to, pid) {
  const d = KEYS[pid].dirs;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const part = (n, key) => (n === 0 ? [] : n >= 3 ? [`${key}×${n}`] : Array(n).fill(key));
  const keys = [...part(Math.abs(dx), dx > 0 ? d.E : d.W), ...part(Math.abs(dy), dy > 0 ? d.S : d.N)];
  const n = Math.abs(dx) + Math.abs(dy);
  return `${plural(n, 'step')} (${keys.join(' ')})`;
}

// "+1.4", "+0.36", and "+0.01" for anything smaller that still pays.
export function fmtWorth(w) {
  if (w > 0 && w < 0.01) return '+0.01';
  return fmtPoints(Number(w.toFixed(w >= 1 ? 1 : 2)));
}

// The unpatched card with the most points per step for the player standing on `here`.
function bestPatchTarget(nodes, blues, pid, here, attacked, skipNodeId) {
  let best = null;
  for (const n of nodes) {
    if (n.state === 'compromised' || n.id === skipNodeId) continue;
    const dist = steps(here, n);
    // The other human already stands there and is told to patch it: do not send both.
    if (dist > 0 && blues.some((a) => a.id !== pid && a.kind === 'human' && a.node_id === n.id)) continue;
    const crowded = dist > 0 && blues.some((a) => a.id !== pid && a.kind !== 'human' && (a.node_id === n.id || a.target_node === n.id));
    for (const c of n.cards || []) {
      const w = worth(n, c, attacked);
      if (w <= 0) continue;
      const rank = (w / (1 + dist)) * (crowded ? 0.5 : 1);
      if (!best || rank > best.rank) best = { node: n, card: c, w, dist, rank };
    }
  }
  return best;
}

function patchSentence(keys, node, card) {
  const w = worth(node, card);
  return `Press ${keys.patch}: patch ${card.cve_id} (${fmtWorth(w)} = value ${node.value} x danger ${fmtDanger(card.score)}).`;
}

/**
 * state: a state message. pid: 'p1' | 'p2'.
 * ctx.attacked: Set of cardKey() for cards Red was seen attacking.
 * ctx.isolatedSince: Map node id -> tick the machine was first seen isolated.
 * Returns { tone: 'warn' | 'go' | 'ok' | 'idle', text }.
 */
export function nextMove(state, pid, ctx = {}) {
  const keys = KEYS[pid];
  const actors = state.actors || [];
  const nodes = state.nodes || [];
  const me = actors.find((a) => a.id === pid);
  const here = me && nodes.find((n) => n.id === me.node_id);
  if (!me || !here) return { tone: 'idle', text: 'Not in this match.' };
  if (state.phase === 'lobby') {
    return { tone: 'idle', text: `Press Start. You begin on ${here.name}; your keys are in the "How to play" card.` };
  }
  if (state.phase === 'ended') return { tone: 'idle', text: 'Match over. Press Start to play again.' };

  const attacked = ctx.attacked;
  const blues = actors.filter((a) => a.team === 'blue');

  // A PATCH or RESTORE of this player is still running: say so instead of repeating the advice
  // that started it. A key pressed now is kept by the server and runs on the first free tick.
  const busy = Number(me.busy_ticks) || 0;
  if (busy > 0 && (me.action === 'RESTORE' || me.action === 'PATCH')) {
    const left = `${plural(busy, 'tick')} left`;
    return {
      tone: 'ok',
      text: me.action === 'RESTORE'
        ? `Restoring ${here.name}: ${left}. Stay here; no key is needed.`
        : `Patching on ${here.name}: ${left}. No key is needed.`,
    };
  }

  // (a) standing on a breached (or isolated) machine
  const helper = blues.find((a) => a.id !== pid && a.node_id === here.id && a.action === 'RESTORE');
  if (here.state === 'compromised' && !helper) {
    return {
      tone: 'warn',
      text: `Press ${keys.restore}: restore this machine (${RESTORE_TICKS} ticks). Red earns ${here.value} every tick it stays red.`,
    };
  }
  if (here.state === 'isolated') {
    const since = ctx.isolatedSince && ctx.isolatedSince.get(here.id);
    const left = since === undefined ? null : Math.max(1, ISOLATE_TICKS - ((Number(state.tick) || 0) - since));
    const span = left === null ? 'a few more ticks' : `${plural(left, 'more tick')}`;
    return {
      tone: 'idle',
      text: `Isolated for ${span}. If it was breached it turns red again: press ${keys.restore} to restore.`,
    };
  }

  // (b) a breached machine nobody on the Blue team is handling, and this player is the nearest human
  const other = actors.find((a) => a.kind === 'human' && a.id !== pid);
  const otherNode = other && nodes.find((n) => n.id === other.node_id);
  const otherFree = otherNode && otherNode.state !== 'compromised';
  let go = null;
  for (const n of nodes) {
    if (n.state !== 'compromised') continue;
    if (blues.some((a) => a.node_id === n.id || a.target_node === n.id)) continue;
    const mine = steps(here, n);
    if (otherFree) {
      const theirs = steps(otherNode, n);
      if (theirs < mine || (theirs === mine && other.id < pid)) continue;
    }
    const rank = (Number(n.value) || 1) / (1 + mine);
    if (!go || rank > go.rank) go = { node: n, rank };
  }
  if (go) {
    return {
      tone: 'warn',
      text: `${go.node.name} is breached, ${route(here, go.node, pid)}, costing ${go.node.value} per tick. Go there and press ${keys.restore}.`,
    };
  }

  // (c) a card here that is worth patching right now
  const top = patchChoice(here, attacked);
  if (top && worth(here, top, attacked) >= WORTH_HERE) {
    return { tone: 'go', text: patchSentence(keys, here, top) };
  }

  // (d) the best unpatched card on the board, by points per step. Player 1 picks first, so the
  // two humans are not sent to the same machine.
  const taken = other && other.id < pid && otherFree ? bestPatchTarget(nodes, blues, other.id, otherNode, attacked, null) : null;
  const best = bestPatchTarget(nodes, blues, pid, here, attacked, taken && taken.node.id !== here.id ? taken.node.id : null);
  // On this machine the patch key always takes patchChoice(), so name that card.
  if (best && best.dist === 0) return { tone: 'go', text: patchSentence(keys, here, top || best.card) };
  if (best) {
    return {
      tone: 'go',
      text: `Best patch: ${best.card.cve_id} on ${best.node.name}, ${fmtWorth(best.w)}, ${route(here, best.node, pid)}.`,
    };
  }

  // (e) nothing left that pays
  if (here.state === 'compromised' && helper) {
    const left = Number(helper.busy_ticks) > 0 ? ` (${plural(helper.busy_ticks, 'tick')} left)` : '';
    return { tone: 'ok', text: `${helper.name || 'A defender'} is already restoring this machine${left}. Nothing else needs you right now.` };
  }
  return { tone: 'ok', text: `All clear here. Monitor (${keys.monitor}) a high-value machine to spot attackers.` };
}
