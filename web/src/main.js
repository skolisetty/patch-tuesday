import './style.css';
import { createScene } from './scene.js';
import { createHud, bestPatchCard } from './hud.js';
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

const scene = createScene(document.getElementById('scene'), document.getElementById('stage'), {
  onNote: (text) => hud.setSceneNote(text),
});

const input = createInput({
  send: (msg) => !!link && link.send(msg),
  getPatchCve(player) {
    if (!lastState) return null;
    const id = player === 2 ? 'p2' : 'p1';
    const actor = (lastState.actors || []).find((a) => a.id === id);
    if (!actor) return null;
    const node = (lastState.nodes || []).find((n) => n.id === actor.node_id);
    const card = node ? bestPatchCard(node.cards) : null;
    return card ? card.cve_id : null;
  },
  onFeedback: (player, text, kind) => hud.setInputFeedback(player, text, kind),
});

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
    fetchLedger(200).then((rows) => hud.backfill(rows));
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
  });
} else {
  link = createNet({ onMessage, onStatus });
}

// Handy when poking at the page from the console.
window.__pt = { scene, hud, get state() { return lastState; }, mock: MOCK };
