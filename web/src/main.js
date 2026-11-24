import './style.css';
import './presentation.css';
import { createPresentation } from './presentation.js';
import { createScene } from './scene.js';
import { createHud } from './hud.js';
import { createInput } from './input.js';
import { createNet, postControl, fetchLedger, fetchStatus } from './net.js';
import { createMock } from './mock.js';

const params = new URLSearchParams(location.search);
const MOCK = params.get('mock') === '1';

let link = null; // net or mock: { send, isOpen }
let lastState = null;
let lastStateAt = 0;
let lastTick = null;
let tickMs = 2000;

const hud = createHud({
  onControl(cmd) {
    const sent = link && link.send({ t: 'control', cmd });
    if (!sent && !MOCK) postControl(cmd); // socket down: use the HTTP route
  },
});

// ---- mouse: click a machine and "your" player walks there.
// Each browser picks which player its clicks move (the seat): ?player=2 in the address, a click on
// a player panel, or the last choice remembered on this computer. Ctrl+click (or Alt+click) moves the other one. Shift is not
// used for this: Right Shift is player 2's MONITOR key, and the keyboard ignores Ctrl and Alt.
let seat = 1;
try {
  const q = params.get('player');
  const saved = q || window.localStorage.getItem('pt-seat');
  if (saved === '2') seat = 2;
} catch (err) {
  /* storage blocked: stay on player 1 */
}
const seatPanels = { 1: document.getElementById('hint-p1'), 2: document.getElementById('hint-p2') };

function setSeat(n) {
  seat = n === 2 ? 2 : 1;
  for (const no of [1, 2]) {
    const box = seatPanels[no];
    if (!box) continue;
    box.classList.toggle('is-seat', no === seat);
    box.title = no === seat
      ? 'Your mouse controls this player: click a machine on the board to walk there. Ctrl+click moves the other player.'
      : 'Click this panel to control this player with the mouse.';
  }
  try {
    window.localStorage.setItem('pt-seat', String(seat));
  } catch (err) {
    /* ignore */
  }
}
for (const no of [1, 2]) {
  if (seatPanels[no]) seatPanels[no].addEventListener('click', () => { if (seat !== no) setSeat(no); });
}

function machineName(id) {
  const n = lastState && (lastState.nodes || []).find((x) => x.id === id);
  return (n && n.name) || `machine ${id}`;
}

function onPick(nodeId, e) {
  const player = e && (e.ctrlKey || e.altKey || e.metaKey) ? (seat === 1 ? 2 : 1) : seat;
  const res = input.walkTo(player, nodeId);
  if (res === 'idle') hud.setInputFeedback(player, 'The match is not running. Press Start.', 'note');
  else if (res === 'here') hud.setInputFeedback(player, `You are already on ${machineName(nodeId)}.`, 'note');
}

const scene = createScene(document.getElementById('scene'), document.getElementById('stage'), {
  onNote: (text) => hud.setSceneNote(text),
  onPick,
});

const input = createInput({
  send: (msg) => !!link && link.send(msg),
  getPatchCve: (player) => hud.getPatchCve(player),
  onFeedback: (player, text, kind) => hud.setInputFeedback(player, text, kind),
  onDestination(player, nodeId, why) {
    scene.setDestination(player === 2 ? 'p2' : 'p1', nodeId);
    if (why === 'start') hud.setInputFeedback(player, `Walking to ${machineName(nodeId)}. Press any of your keys to stop.`, 'note');
    else if (why === 'stuck') hud.setInputFeedback(player, 'Stopped walking: the way is blocked.', 'bad');
  },
});
setSeat(seat);
const presentation = createPresentation();

function onState(state) {
  const now = performance.now();
  // Measure the real tick length so movement lasts exactly one tick.
  if (lastTick !== null && state.tick === lastTick + 1 && lastStateAt) {
    const dt = now - lastStateAt;
    if (dt > 150 && dt < 6000) {
      tickMs = tickMs * 0.5 + dt * 0.5;
      scene.setTickMs(tickMs);
      input.setTickMs(tickMs);
      hud.setTickSeconds(tickMs / 1000);
    }
  }
  lastTick = state.tick;
  lastStateAt = now;
  lastState = state;
  scene.applyState(state);
  hud.applyState(state);
  input.onState(state);
  // Presentation observes the completed update; it never changes the authoritative state.
  try { presentation.applyState(state); } catch (err) { console.warn('Presentation unavailable', err); }
}

function onMessage(msg) {
  try {
    if (msg.t === 'state') onState(msg);
    else if (msg.t === 'reason') hud.applyReason(msg);
  } catch (err) {
    console.error('message handling failed', err);
  }
}

function onStatus(status, text) {
  hud.setConn(status, text);
  if (status === 'open') {
    // Fill the ledger with what happened before this page connected.
    // (The mock has no server: asking one would only show another game's rows or a proxy error.)
    // 1500 rows cover a whole match (150 ticks x 7 actors), so a page opened late still knows
    // each agent's last decision and the breaches, patches and restores so far.
    if (!MOCK) fetchLedger(1500).then((rows) => hud.backfill(rows));
    // The server's configured tick (PT_TICK_SECONDS / start.ps1 -TickSeconds) for the time-left clock.
    if (!MOCK) fetchStatus().then((s) => {
      if (s && Number(s.tick_seconds) > 0) {
        hud.setTickSeconds(Number(s.tick_seconds));
        if (lastTick === null) {
          tickMs = Number(s.tick_seconds) * 1000;
          scene.setTickMs(tickMs);
          input.setTickMs(tickMs);
        }
      }
    });
  }
}

if (MOCK) {
  const tick = Number(params.get('tick'));
  const ticks = Number(params.get('ticks'));
  hud.setTickSeconds((tick >= 100 ? tick : 2000) / 1000);
  link = createMock({
    onMessage,
    onStatus,
    tickMs: tick >= 100 ? tick : 2000,
    ticksTotal: ticks >= 5 ? Math.floor(ticks) : 150,
    autostart: params.get('autostart') !== '0',
    seed: Number(params.get('seed')) || undefined,
  });
} else {
  link = createNet({ onMessage, onStatus });
}

// Deep links: ?tab=log opens the full log, ?help=1 opens the briefing card.
if (params.get('tab') === 'log') hud.showTab('log');
if (params.get('help') === '1') hud.openBriefing();

// Handy when poking at the page from the console.
window.__pt = { scene, hud, get state() { return lastState; }, mock: MOCK };

window.addEventListener('beforeunload', () => {
  presentation.dispose();
  if (scene.dispose) scene.dispose();
  if (input.dispose) input.dispose();
});
