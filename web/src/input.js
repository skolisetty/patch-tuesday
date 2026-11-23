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

function label(a) {
  return a.action === 'MOVE' ? `MOVE ${a.dir}` : a.action;
}

/**
 * send(msg) -> bool         sends one contract "act" message
 * getPatchCve(player)       CVE the hint card marks for this player, or null
 * onFeedback(player, text, kind)   kind: 'sent' | 'queued' | 'idle'
 *
 * Rate limit: at most one action per player per tick window. A tick window
 * opens when a state message with a new tick arrives (or, if none arrives,
 * after one tick length). A key pressed after that player's action was
 * already sent is buffered and sent when the next window opens. A held
 * movement key repeats once per window.
 */
export function createInput({ send, getPatchCve, onFeedback }) {
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
      if (busy[playerNo] > 0) onFeedback(playerNo, `${label(a)} after current action`, 'queued');
      else onFeedback(playerNo, label(a), 'sent');
    } else {
      onFeedback(playerNo, 'not connected', 'idle');
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
        continue;
      }
      const next = p.pending || heldMoveFor(no);
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
      onFeedback(a.player, 'match not running', 'idle');
      return;
    }
    const p = players[a.player];
    if (canSend(p)) {
      dispatch(a.player, a);
    } else {
      p.pending = a;
      onFeedback(a.player, label(a), 'queued');
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
        if (a.id === 'p1') busy[1] = Number(a.busy_ticks) || 0;
        if (a.id === 'p2') busy[2] = Number(a.busy_ticks) || 0;
      }
      const changed = state.tick !== lastTick || state.match_id !== lastMatch;
      lastTick = state.tick;
      lastMatch = state.match_id;
      if (changed) openWindow();
    },
    setTickMs(ms) {
      if (ms > 100) tickMs = ms;
    },
    dispose() {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    },
  };
}
