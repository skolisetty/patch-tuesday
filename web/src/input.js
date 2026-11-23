// Two players on one keyboard. Keys are read with KeyboardEvent.code
// (physical keys), exactly as listed in docs/CONTRACTS.md.

const KEYMAP = {
  // Player 1
  KeyW: { player: 1, action: 'MOVE', dir: 'N' },
  KeyS: { player: 1, action: 'MOVE', dir: 'S' },
  KeyA: { player: 1, action: 'MOVE', dir: 'W' },
  KeyD: { player: 1, action: 'MOVE', dir: 'E' },
  KeyE: { player: 1, action: 'PATCH' },
  KeyQ: { player: 1, action: 'MONITOR' },
  KeyR: { player: 1, action: 'RESTORE' },
  KeyF: { player: 1, action: 'ISOLATE' },
  // Player 2
  ArrowUp: { player: 2, action: 'MOVE', dir: 'N' },
  ArrowDown: { player: 2, action: 'MOVE', dir: 'S' },
  ArrowLeft: { player: 2, action: 'MOVE', dir: 'W' },
  ArrowRight: { player: 2, action: 'MOVE', dir: 'E' },
  Enter: { player: 2, action: 'PATCH' },
  ShiftRight: { player: 2, action: 'MONITOR' },
  Slash: { player: 2, action: 'RESTORE' },
  Period: { player: 2, action: 'ISOLATE' },
};

const COLS = 5; // the grid is 5 columns by 4 rows; id = y * 5 + x, and N is y - 1

// One step from machine `from` toward machine `to`: across first, then up or down.
function stepToward(from, to) {
  const fx = from % COLS;
  const tx = to % COLS;
  if (tx !== fx) return tx > fx ? 'E' : 'W';
  const fy = Math.floor(from / COLS);
  const ty = Math.floor(to / COLS);
  if (ty !== fy) return ty > fy ? 'S' : 'N';
  return null;
}

function label(a) {
  return a.action === 'MOVE' ? `MOVE ${a.dir}` : a.action;
}

/**
 * send(msg) -> bool         sends one contract "act" message
 * getPatchCve(player)       CVE the hint card marks for this player, or null
 * onFeedback(player, text, kind)   kind: 'note' | 'bad'. Only for what the server cannot
 *                                  say (queued, not running, not connected); what an action
 *                                  did comes back in state.player_feedback.
 *
 * Rate limit: at most one action per player per tick window. A tick window
 * opens when a state message with a new tick arrives (or, if none arrives,
 * after one tick length). A key pressed after that player's action was
 * already sent is buffered and sent when the next window opens. A held
 * movement key repeats once per window.
 *
 * walkTo(player, machineId) makes that player walk to a machine by itself, one step per
 * window, until it arrives, the player presses one of their keys, or it stops making progress.
 * onDestination(player, machineId | null, why) reports the walk starting and ending
 * (why: 'start' | 'arrived' | 'cancelled' | 'stuck' | 'ended').
 */
export function createInput({ send, getPatchCve, onFeedback, onDestination }) {
  let phase = 'lobby';
  let tickMs = 2000;
  let lastTick = null;
  let lastMatch = null;
  const players = {
    1: { sentThisWindow: false, sentAt: 0, pending: null },
    2: { sentThisWindow: false, sentAt: 0, pending: null },
  };
  const held = new Map(); // code -> mapping, for movement keys being held
  // busy_ticks of p1/p2 in the last state: while a PATCH/RESTORE/... runs, the server keeps
  // the newest key press and applies it on the first free tick.
  const busy = { 1: 0, 2: 0 };
  const pos = { 1: null, 2: null }; // machine each human stands on, from the last state
  const dest = { 1: null, 2: null }; // machine each human is walking to, or null
  const walk = { 1: { from: null, tries: 0 }, 2: { from: null, tries: 0 } };

  function endWalk(no, why) {
    if (dest[no] === null) return;
    dest[no] = null;
    walk[no].tries = 0;
    if (onDestination) onDestination(no, null, why);
  }

  // The next step of a walk, or null when there is none to send this window.
  function walkStep(no) {
    if (dest[no] === null || pos[no] === null || busy[no] > 0) return null;
    if (pos[no] === dest[no]) {
      endWalk(no, 'arrived');
      return null;
    }
    // The same machine three windows running means the steps are not landing: give up.
    if (walk[no].from === pos[no]) walk[no].tries += 1;
    else walk[no].tries = 0;
    walk[no].from = pos[no];
    if (walk[no].tries >= 3) {
      endWalk(no, 'stuck');
      return null;
    }
    const dir = stepToward(pos[no], dest[no]);
    return dir ? { player: no, action: 'MOVE', dir } : null;
  }

  function canSend(p) {
    if (!p.sentThisWindow) return true;
    // Safety net when no state message opens a new window.
    return performance.now() - p.sentAt >= tickMs;
  }

  function dispatch(playerNo, a) {
    const p = players[playerNo];
    const msg = { t: 'act', player: playerNo, action: a.action };
    if (a.action === 'MOVE') msg.dir = a.dir;
    if (a.action === 'PATCH') {
      const cve = getPatchCve ? getPatchCve(playerNo) : null;
      if (cve) msg.cve = cve;
    }
    const ok = send(msg);
    if (ok) {
      p.sentThisWindow = true;
      p.sentAt = performance.now();
      p.pending = null;
      if (busy[playerNo] > 0) onFeedback(playerNo, `Queued: ${label(a)} runs when the current action finishes.`, 'note');
    } else {
      onFeedback(playerNo, 'Not connected to the game server.', 'bad');
    }
    return ok;
  }

  function heldMoveFor(playerNo) {
    let last = null;
    for (const a of held.values()) if (a.player === playerNo) last = a;
    return last;
  }

  function openWindow() {
    for (const no of [1, 2]) {
      const p = players[no];
      p.sentThisWindow = false;
      if (phase !== 'running') {
        p.pending = null;
        endWalk(no, 'ended');
        continue;
      }
      const next = p.pending || heldMoveFor(no) || walkStep(no);
      if (next) dispatch(no, next);
    }
  }

  function onKeyDown(e) {
    const a = KEYMAP[e.code];
    if (!a) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return; // leave browser shortcuts alone
    const tgt = e.target;
    if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA' || tgt.isContentEditable)) return;
    // Stops arrow keys scrolling the page, "/" opening quick find and
    // Enter re-triggering a focused button.
    e.preventDefault();
    if (a.action === 'MOVE') held.set(e.code, a);
    if (e.repeat) return; // held keys are handled once per window
    if (phase !== 'running') {
      onFeedback(a.player, 'The match is not running. Press Start.', 'note');
      return;
    }
    endWalk(a.player, 'cancelled'); // the keyboard always wins over a click
    const p = players[a.player];
    if (canSend(p)) {
      dispatch(a.player, a);
    } else {
      p.pending = a;
      onFeedback(a.player, `Queued for the next tick: ${label(a)}.`, 'note');
    }
  }

  function onKeyUp(e) {
    held.delete(e.code);
  }

  function onBlur() {
    held.clear();
  }

  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  window.addEventListener('blur', onBlur);

  return {
    // Call for every state message.
    onState(state) {
      phase = state.phase || 'lobby';
      for (const a of state.actors || []) {
        const no = a.id === 'p1' ? 1 : a.id === 'p2' ? 2 : 0;
        if (!no) continue;
        busy[no] = Number(a.busy_ticks) || 0;
        pos[no] = Number.isInteger(a.node_id) ? a.node_id : null;
      }
      for (const no of [1, 2]) if (dest[no] !== null && pos[no] === dest[no]) endWalk(no, 'arrived');
      const changed = state.tick !== lastTick || state.match_id !== lastMatch;
      lastTick = state.tick;
      lastMatch = state.match_id;
      if (changed) openWindow();
    },
    setTickMs(ms) {
      if (ms > 100) tickMs = ms;
    },
    // Start walking `playerNo` (1 or 2) to a machine. Returns 'walking', 'here' or 'idle'.
    walkTo(playerNo, nodeId) {
      const no = playerNo === 2 ? 2 : 1;
      if (phase !== 'running') return 'idle';
      if (!Number.isInteger(nodeId) || nodeId < 0) return 'idle';
      if (pos[no] === nodeId) {
        endWalk(no, 'arrived');
        return 'here';
      }
      dest[no] = nodeId;
      walk[no].from = null;
      walk[no].tries = 0;
      players[no].pending = null;
      if (onDestination) onDestination(no, nodeId, 'start');
      if (canSend(players[no])) {
        const step = walkStep(no);
        if (step) dispatch(no, step);
      }
      return 'walking';
    },
    dispose() {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    },
  };
}
