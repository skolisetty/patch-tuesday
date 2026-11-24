// Agent voices: each AI agent says its notable moves out loud, in the first person, with its
// own voice (browser speech synthesis; nothing is sent anywhere). Built only from what the page
// already shows: fogged ledger rows, the Coach box and the events. Off until the player turns it
// on with the Voices button (browsers also require a click before they will speak).

const KEY = 'pt-voices';
const MIN_GAP_MS = 2500; // silence between two lines
const MAX_QUEUE = 2; // newer lines replace waiting ones instead of piling up

// pitch / rate per speaker so each one is recognisable even when the browser has one voice.
const STYLE = {
  red1: { pitch: 0.55, rate: 1.08, pick: 0 },
  red2: { pitch: 0.4, rate: 0.9, pick: 1 },
  blue1: { pitch: 1.15, rate: 1.05, pick: 2 },
  blue2: { pitch: 1.0, rate: 1.0, pick: 3 },
  blue3: { pitch: 0.85, rate: 1.1, pick: 4 },
  blue4: { pitch: 1.3, rate: 1.12, pick: 5 },
  coach: { pitch: 0.95, rate: 0.98, pick: 6 },
};

function machineName(state, id) {
  const n = (state.nodes || []).find((x) => x.id === id);
  return (n && n.name) || 'a machine';
}

// "CVE-2026-33660" is unpleasant to listen to: say the last four digits.
function sayCve(cve) {
  const m = /(\d{4,})$/.exec(String(cve || ''));
  return m ? `the ${m[1].slice(-4).split('').join(' ')} bug` : 'a bug';
}

function spoken(name) {
  return String(name || '').replace(/-/g, ' ').replace(/0(\d)\b/, '$1');
}

// One first-person line for a ledger row, or null when the row is not worth saying.
function lineFor(row, state) {
  if (!row || row.kind !== 'agent') return null;
  const where = spoken(machineName(state, row.node_id));
  const out = String(row.outcome || '');
  switch (row.action) {
    case 'EXPLOIT':
      if (out === 'breach') return `I'm in. ${where} is ours.`;
      if (out === 'failed') return `${where} held. I'll try again.`;
      return null;
    case 'RESTORE':
      return out === 'restored' ? `${where} is clean again.` : null;
    case 'ISOLATE':
      return out === 'isolated' ? `Cutting ${where} off the network.` : null;
    case 'PATCH':
      return out === 'patched' && Number(row.score_used) >= 0.3 ? `Patched ${sayCve(row.cve_id)} on ${where}.` : null;
    default:
      return null;
  }
}

export function createVoice({ button } = {}) {
  const synth = typeof window !== 'undefined' ? window.speechSynthesis : null;
  let on = false;
  try {
    on = window.localStorage.getItem(KEY) === '1';
  } catch (err) {
    /* storage blocked */
  }
  let voices = [];
  const queue = [];
  let lastEnd = 0;
  let busy = false;
  let matchId = null;
  const seenRows = new Set();
  let backupStatus = null;
  let lastPhase = null;

  function loadVoices() {
    if (!synth) return;
    const all = synth.getVoices() || [];
    const en = all.filter((v) => /^en/i.test(v.lang));
    voices = en.length ? en : all;
  }
  if (synth) {
    loadVoices();
    if (typeof synth.addEventListener === 'function') synth.addEventListener('voiceschanged', loadVoices);
  }

  function paint() {
    if (!button) return;
    button.textContent = on ? 'Voices: on' : 'Voices: off';
    button.setAttribute('aria-pressed', String(on));
    button.classList.toggle('is-on', on);
    button.hidden = !synth;
  }

  function pump() {
    if (!synth || !on || busy || !queue.length) return;
    const wait = MIN_GAP_MS - (performance.now() - lastEnd);
    if (wait > 0) {
      setTimeout(pump, wait);
      return;
    }
    const { who, text } = queue.shift();
    const st = STYLE[who] || STYLE.coach;
    const u = new SpeechSynthesisUtterance(text);
    if (voices.length) u.voice = voices[st.pick % voices.length];
    u.pitch = st.pitch;
    u.rate = st.rate;
    u.volume = 0.9;
    busy = true;
    const done = () => {
      busy = false;
      lastEnd = performance.now();
      pump();
    };
    u.onend = done;
    u.onerror = done;
    try {
      synth.speak(u);
    } catch (err) {
      done();
    }
  }

  // Important lines (Coach, the specialist, the result) are never dropped for routine ones.
  function say(who, text, important = false) {
    if (!synth || !on || !text) return;
    while (queue.length >= MAX_QUEUE) {
      const i = queue.findIndex((q) => !q.important);
      if (i === -1) {
        if (!important) return;
        break;
      }
      queue.splice(i, 1);
    }
    queue.push({ who, text: String(text).slice(0, 220), important });
    pump();
  }

  function setOn(v) {
    on = !!v;
    try {
      window.localStorage.setItem(KEY, on ? '1' : '0');
    } catch (err) {
      /* ignore */
    }
    if (!on && synth) {
      queue.length = 0;
      synth.cancel();
      busy = false;
    }
    paint();
    if (on) say('coach', 'Voices on.');
  }

  if (button) {
    button.addEventListener('click', () => {
      button.blur();
      setOn(!on);
    });
  }
  paint();

  return {
    get on() {
      return on;
    },
    setOn,
    // Call for every state message.
    onState(state) {
      if (!state) return;
      if (state.match_id !== matchId) {
        matchId = state.match_id;
        seenRows.clear();
        backupStatus = null;
        lastPhase = null;
      }
      if (!on) {
        // keep the bookkeeping current so turning voices on mid-match does not replay history
        for (const r of state.ledger || []) seenRows.add(r.ledger_id);
        backupStatus = state.backup ? state.backup.status : null;
        lastPhase = state.phase;
        return;
      }
      for (const r of state.ledger || []) {
        if (!r || seenRows.has(r.ledger_id)) continue;
        seenRows.add(r.ledger_id);
        if (state.phase !== 'running') continue;
        const text = lineFor(r, state);
        if (text) say(r.actor_id, text);
      }
      if (seenRows.size > 4000) seenRows.clear();

      // Coach and the specialist, from the Call-for-backup state.
      const b = state.backup;
      const status = b ? b.status : null;
      if (b && status !== backupStatus) {
        if (status === 'asked' && b.weakness) {
          const rec = b.recommended ? ` I recommend calling in the ${b.recommended}.` : '';
          say('coach', `Coach here. Our weakness is ${String(b.weakness.title || '').toLowerCase()}.${rec}`, true);
        } else if (status === 'joined' && b.chosen) {
          say('blue4', `${b.chosen} here. Joining the Blue team.`, true);
        } else if (status === 'declined') {
          say('coach', 'Understood. No backup this match.', true);
        }
      }
      backupStatus = status;

      if (state.phase === 'ended' && lastPhase === 'running') {
        const sc = state.score || {};
        const blue = Number(sc.blue) || 0;
        const red = Number(sc.red) || 0;
        say('coach', blue > red ? 'Match over. Blue holds the network.' : blue < red ? 'Match over. Red wins this one.' : 'Match over. A draw.', true);
      }
      lastPhase = state.phase;
    },
  };
}
