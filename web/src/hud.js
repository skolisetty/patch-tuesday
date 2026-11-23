// DOM overlays. Every string that comes from the server is written with
// textContent, never as HTML.

const LEDGER_CAP = 200;
const TICKER_CAP = 40;
const TICKER_KINDS = new Set(['drop', 'campaign', 'gate', 'breach', 'restored', 'intel', 'info']);
const LOUD_KINDS = new Set(['drop', 'campaign', 'gate', 'breach']);
// Tick length for the time-left clock: the contract's 2 s until main.js reports the server's
// own value (/api/status tick_seconds, then the measured gap between state messages).
const DEFAULT_TICK_SECONDS = 2;

const KEYS = {
  p1: { tag: 'P1', move: 'W A S D', patch: 'E', monitor: 'Q', restore: 'R', isolate: 'F' },
  p2: { tag: 'P2', move: 'Arrows', patch: 'Enter', monitor: 'R-Shift', restore: '/', isolate: '.' },
};

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function setText(node, value) {
  const s = String(value);
  if (node.textContent !== s) node.textContent = s;
}

function fmtScore(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '0';
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function fmtPct(v, digits = 0) {
  const n = Number(v);
  if (v === null || v === undefined || !Number.isFinite(n)) return '--';
  return (n * 100).toFixed(digits) + '%';
}

function fmtP50(v) {
  const n = Number(v);
  if (v === null || v === undefined || !Number.isFinite(n)) return '--';
  return n.toFixed(2);
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Cards in the order the server patches them: best priority_rank first,
// unranked cards last (highest score first among those).
export function sortCards(cards) {
  return [...(cards || [])].sort((a, b) => {
    const ra = Number.isFinite(a.priority_rank) ? a.priority_rank : Infinity;
    const rb = Number.isFinite(b.priority_rank) ? b.priority_rank : Infinity;
    if (ra !== rb) return ra - rb;
    return (Number(b.score) || 0) - (Number(a.score) || 0);
  });
}

export function bestPatchCard(cards) {
  return sortCards(cards).find((c) => c.patch_state === 'unpatched') || null;
}

export function createHud({ onControl }) {
  let tickSeconds = DEFAULT_TICK_SECONDS;
  const dom = {
    phase: $('phase'),
    conn: $('conn'),
    connText: $('conn-text'),
    scoreBlue: $('score-blue'),
    scoreRed: $('score-red'),
    nHealthy: $('n-healthy'),
    nCompromised: $('n-compromised'),
    nIsolated: $('n-isolated'),
    breachesTotal: $('breaches-total'),
    breachesTop50: $('breaches-top50'),
    ticksLeft: $('ticks-left'),
    timeLeft: $('time-left'),
    tickBar: $('tick-bar'),
    asof: $('asof'),
    srcBadge: $('src-badge'),
    modelVersion: $('model-version'),
    adv: $('adv'),
    p50Model: $('p50-model'),
    p50Epss: $('p50-epss'),
    btnStart: $('btn-start'),
    btnReset: $('btn-reset'),
    btnAdvance: $('btn-advance'),
    btnWaits: $('btn-waits'),
    ledger: $('ledger'),
    ledgerCount: $('ledger-count'),
    ticker: $('ticker'),
    banner: $('banner'),
    stageMsg: $('stage-msg'),
    stageMsgTitle: $('stage-msg-title'),
    stageMsgSub: $('stage-msg-sub'),
    sceneNote: $('scene-note'),
    flash: $('flash'),
    flashText: $('flash-text'),
    hint: { p1: $('hint-p1'), p2: $('hint-p2') },
  };

  const ledgerRows = new Map(); // ledger_id -> { row, reason }
  const actorNames = new Map();
  const nodeNames = new Map();
  const feedback = { p1: { text: '', kind: 'idle', at: 0 }, p2: { text: '', kind: 'idle', at: 0 } };
  let matchId = null;
  let lastState = null;
  let lastAsof = null;
  let bannerTimer = null;
  let flashTimer = null;
  let lastEventKey = '';

  // ------------------------------------------------------------ buttons

  for (const [btn, cmd] of [
    [dom.btnStart, 'start'],
    [dom.btnReset, 'reset'],
    [dom.btnAdvance, 'advance'],
  ]) {
    btn.addEventListener('click', () => {
      btn.blur(); // so Enter (player 2 patch) cannot press it again
      onControl(cmd);
    });
  }
  dom.btnWaits.addEventListener('click', () => {
    const on = dom.ledger.classList.toggle('hide-waits');
    dom.btnWaits.setAttribute('aria-pressed', String(on));
    dom.btnWaits.blur();
  });

  // --------------------------------------------------------- connection

  function setConn(status, text) {
    dom.conn.className = `conn conn-${status}`;
    setText(dom.connText, text);
    if (!lastState) {
      // Nothing received yet: say what the page is waiting for.
      dom.stageMsg.hidden = false;
      dom.stageMsg.className = 'stage-msg msg-lobby';
      setText(dom.stageMsgTitle, status === 'down' ? 'WAITING FOR GAME SERVER' : 'CONNECTING');
      setText(dom.stageMsgSub, 'Start the game server on port 8000, or open this page with ?mock=1 for a demo');
    }
  }

  for (const pid of ['p1', 'p2']) {
    const head = el('div', 'hint-head');
    head.append(el('span', 'hint-tag', KEYS[pid].tag), el('span', 'hint-name', 'waiting for match state'));
    dom.hint[pid].replaceChildren(head);
  }

  // ------------------------------------------------------------- ledger

  function buildRow(r) {
    const team = r.team === 'red' ? 'red' : 'blue';
    const row = el('div', `lrow team-${team}`);
    const action = String(r.action ?? '');
    if (action === 'WAIT') row.classList.add('is-wait');
    const outcome = r.outcome === null || r.outcome === undefined ? '' : String(r.outcome);
    if (/breach/i.test(outcome) && !/no|fail|block|miss/i.test(outcome)) row.classList.add('is-breach');

    const l1 = el('div', 'l1');
    l1.append(
      el('span', 'c-tick', r.tick ?? ''),
      el('span', 'c-actor', actorNames.get(r.actor_id) || r.actor_id || '?'),
      el('span', 'c-action', action),
      el('span', 'c-node', r.node_id === null || r.node_id === undefined ? '' : nodeNames.get(r.node_id) || `#${r.node_id}`),
      el('span', 'c-pb', r.p_breach === null || r.p_breach === undefined ? '' : fmtPct(r.p_breach)),
      el('span', 'c-outcome', outcome),
    );
    const l2 = el('div', 'l2');
    if (r.cve_id) l2.append(el('span', 'c-cve', r.cve_id));
    const reason = el('span', 'c-reason');
    if (r.reason_text) reason.textContent = String(r.reason_text);
    else reason.classList.add('pending');
    l2.append(reason);
    row.append(l1, l2);
    return { row, reason };
  }

  function trimLedger() {
    while (dom.ledger.childElementCount > LEDGER_CAP) {
      const last = dom.ledger.lastElementChild;
      const id = last.dataset.lid;
      if (id !== undefined) ledgerRows.delete(id);
      last.remove();
    }
    setText(dom.ledgerCount, `${dom.ledger.childElementCount} rows`);
  }

  // newest = true puts the row at the top; false appends (history backfill).
  function addLedgerRow(r, newest) {
    if (!r || r.ledger_id === null || r.ledger_id === undefined) return;
    const id = String(r.ledger_id);
    if (ledgerRows.has(id)) return;
    const built = buildRow(r);
    built.row.dataset.lid = id;
    ledgerRows.set(id, built);
    if (newest) dom.ledger.prepend(built.row);
    else dom.ledger.append(built.row);
  }

  function clearLedger() {
    dom.ledger.replaceChildren();
    ledgerRows.clear();
    setText(dom.ledgerCount, '0 rows');
  }

  function applyReason(msg) {
    if (!msg || msg.ledger_id === null || msg.ledger_id === undefined) return;
    const entry = ledgerRows.get(String(msg.ledger_id));
    if (!entry) return; // row already scrolled out of the cap
    entry.reason.textContent = String(msg.reason_text ?? '');
    entry.reason.classList.remove('pending');
    entry.reason.classList.remove('fresh');
    void entry.reason.offsetWidth; // restart the highlight animation
    entry.reason.classList.add('fresh');
  }

  // Rows fetched from GET /api/ledger on connect.
  function backfill(rows) {
    if (!Array.isArray(rows) || !rows.length) return;
    const list = rows
      .filter((r) => r && (!matchId || !r.match_id || r.match_id === matchId))
      .sort((a, b) => (b.tick ?? 0) - (a.tick ?? 0));
    for (const r of list) addLedgerRow(r, false);
    trimLedger();
  }

  // -------------------------------------------------------------- hints

  function renderHint(pid, state) {
    const box = dom.hint[pid];
    const keys = KEYS[pid];
    const actor = (state.actors || []).find((a) => a.id === pid);
    const frag = document.createDocumentFragment();

    const head = el('div', 'hint-head');
    head.append(el('span', 'hint-tag', keys.tag));
    if (!actor) {
      head.append(el('span', 'hint-name', 'not in this match'));
      frag.append(head);
      box.replaceChildren(frag);
      return;
    }
    const node = (state.nodes || []).find((n) => n.id === actor.node_id);
    head.append(el('span', 'hint-name', actor.name || pid));
    if (node) {
      head.append(el('span', 'hint-at', 'on'));
      head.append(el('span', 'hint-node', node.name ?? `#${node.id}`));
      head.append(el('span', `pill pill-${node.state}`, String(node.state || '').toUpperCase()));
      head.append(el('span', 'hint-value', `value ${node.value ?? '?'}`));
      if (node.monitored) head.append(el('span', 'pill pill-mon', 'MONITORED'));
    }
    const fb = feedback[pid];
    if (actor.busy_ticks > 0) {
      head.append(el('span', 'hint-busy', `${actor.action || 'BUSY'} ${actor.busy_ticks}t`));
    } else if (fb.text && performance.now() - fb.at < 4000) {
      head.append(el('span', `hint-fb fb-${fb.kind}`, `${fb.kind === 'queued' ? 'queued' : fb.kind === 'sent' ? 'sent' : ''} ${fb.text}`.trim()));
    }
    frag.append(head);

    if (!node) {
      box.replaceChildren(frag);
      return;
    }

    const cards = sortCards(node.cards);
    const best = bestPatchCard(node.cards);
    const tip = el('div', 'hint-tip');
    if (node.state === 'compromised') {
      tip.append(el('kbd', null, keys.restore), el('span', 'tip-text tip-warn', 'RESTORE this node (3 ticks)'));
    } else if (node.state === 'isolated') {
      tip.append(el('span', 'tip-text', 'Node is isolated: safe, but costs 1 point per tick'));
    } else if (best) {
      tip.append(el('kbd', null, keys.patch), el('span', 'tip-text', 'PATCH the top card (2 ticks)'));
    } else {
      tip.append(el('span', 'tip-text tip-ok', 'Nothing left to patch here. Move on.'));
    }
    tip.append(
      el('span', 'tip-alt', ''),
      el('kbd', 'kbd-s', keys.monitor),
      el('span', 'tip-small', 'monitor'),
      el('kbd', 'kbd-s', keys.isolate),
      el('span', 'tip-small', 'isolate'),
    );
    frag.append(tip);

    const list = el('div', 'hint-cards');
    if (!cards.length) list.append(el('div', 'hint-empty', 'No cards on this node.'));
    for (const c of cards) {
      const row = el('div', `hcard ps-${c.patch_state}`);
      if (best && c.card_id === best.card_id) row.classList.add('is-best');
      const key = el('span', 'hc-key');
      if (best && c.card_id === best.card_id && node.state !== 'isolated') key.append(el('kbd', null, keys.patch));
      row.append(
        key,
        el('span', 'hc-rank', Number.isFinite(c.priority_rank) ? `#${c.priority_rank}` : '#--'),
        el('span', 'hc-cve', c.cve_id || ''),
        el('span', 'hc-title', c.title || ''),
        el('span', 'hc-score', fmtPct(c.score, 1)),
      );
      const badges = el('span', 'hc-badges');
      if (c.in_kev) badges.append(el('span', 'tagb tag-kev', 'KEV'));
      if (c.is_new) badges.append(el('span', 'tagb tag-new', 'NEW'));
      row.append(badges, el('span', 'hc-state', c.patch_state || ''));
      list.append(row);
    }
    frag.append(list);
    box.replaceChildren(frag);
  }

  function setInputFeedback(player, text, kind) {
    const pid = player === 2 ? 'p2' : 'p1';
    feedback[pid] = { text, kind, at: performance.now() };
    if (lastState) renderHint(pid, lastState);
  }

  // ------------------------------------------------------------- events

  function showBanner(kind, text) {
    dom.banner.className = `banner banner-${kind}`;
    dom.banner.textContent = text;
    dom.banner.hidden = false;
    clearTimeout(bannerTimer);
    bannerTimer = setTimeout(() => {
      dom.banner.hidden = true;
    }, 5000);
  }

  function showFlash(text) {
    dom.flashText.textContent = text;
    dom.flash.hidden = false;
    dom.flash.classList.remove('run');
    void dom.flash.offsetWidth;
    dom.flash.classList.add('run');
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => {
      dom.flash.hidden = true;
      dom.flash.classList.remove('run');
    }, 3200);
  }

  function addEvents(state) {
    const events = Array.isArray(state.events) ? state.events : [];
    if (!events.length) return;
    // The same state can be delivered twice (connect, then tick).
    const key = `${state.match_id}|${state.tick}|${events.length}|${events[0] && events[0].text}`;
    if (key === lastEventKey) return;
    lastEventKey = key;
    for (const ev of events) {
      if (!ev) continue;
      const kind = String(ev.kind || 'info');
      const text = String(ev.text ?? '');
      if (kind === 'campaign') showFlash(text);
      else if (kind === 'drop' || kind === 'gate') showBanner(kind, text);
      if (!TICKER_KINDS.has(kind)) continue;
      const item = el('div', `tk tk-${kind}`);
      if (LOUD_KINDS.has(kind)) item.classList.add('tk-loud');
      item.append(el('span', 'tk-kind', kind.toUpperCase()), el('span', 'tk-tick', `t${state.tick ?? 0}`), el('span', 'tk-text', text));
      dom.ticker.prepend(item);
    }
    while (dom.ticker.childElementCount > TICKER_CAP) dom.ticker.lastElementChild.remove();
  }

  // -------------------------------------------------------------- state

  function applyState(state) {
    if (!state) return;
    lastState = state;

    if (state.match_id !== matchId) {
      if (matchId !== null) {
        clearLedger();
        dom.ticker.replaceChildren();
      }
      matchId = state.match_id;
    }
    for (const a of state.actors || []) actorNames.set(a.id, a.name || a.id);
    for (const n of state.nodes || []) nodeNames.set(n.id, n.name ?? `#${n.id}`);

    const phase = ['lobby', 'running', 'ended'].includes(state.phase) ? state.phase : 'lobby';
    dom.phase.className = `phase phase-${phase}`;
    setText(dom.phase, phase.toUpperCase());

    // Scoreboard.
    const sc = state.score || {};
    setText(dom.scoreBlue, fmtScore(sc.blue));
    setText(dom.scoreRed, fmtScore(sc.red));
    setText(dom.nHealthy, sc.healthy ?? 0);
    setText(dom.nCompromised, sc.compromised ?? 0);
    setText(dom.nIsolated, sc.isolated ?? 0);
    setText(dom.breachesTotal, sc.breaches_total ?? 0);
    setText(dom.breachesTop50, sc.breaches_in_top50 ?? 0);
    const total = Number(state.ticks_total) || 150;
    const tick = Number(state.tick) || 0;
    const left = Math.max(0, total - tick);
    setText(dom.ticksLeft, left);
    setText(dom.timeLeft, fmtTime(left * tickSeconds));
    dom.tickBar.style.width = `${Math.min(100, (tick / total) * 100).toFixed(1)}%`;

    // World clock.
    const w = state.world || {};
    setText(dom.asof, w.asof_date || '----------');
    if (lastAsof !== null && w.asof_date && w.asof_date !== lastAsof) {
      dom.asof.classList.remove('bump');
      void dom.asof.offsetWidth;
      dom.asof.classList.add('bump');
    }
    if (w.asof_date) lastAsof = w.asof_date;
    const isModel = w.score_source === 'model';
    dom.srcBadge.className = `badge ${isModel ? 'badge-model' : 'badge-epss'}`;
    setText(dom.srcBadge, isModel ? 'MODEL' : 'EPSS');
    setText(dom.modelVersion, w.model_version || (isModel ? 'model' : 'no model promoted'));
    setText(dom.p50Model, fmtP50(w.challenger_p_at_50));
    setText(dom.p50Epss, fmtP50(w.epss_p_at_50));
    const cm = Number(w.challenger_p_at_50);
    const ce = Number(w.epss_p_at_50);
    const both = w.challenger_p_at_50 != null && w.epss_p_at_50 != null && Number.isFinite(cm) && Number.isFinite(ce);
    dom.p50Model.className = both && cm > ce ? 'win' : '';
    dom.p50Epss.className = both && ce >= cm ? 'win' : '';
    dom.adv.hidden = !w.advancing;

    // Buttons.
    dom.btnStart.disabled = phase === 'running';
    dom.btnAdvance.disabled = !!w.advancing;
    setText(dom.btnAdvance, w.advancing ? 'Advancing...' : 'Advance week');

    // Ledger: rows created this tick, newest on top.
    const rows = Array.isArray(state.ledger) ? state.ledger : [];
    for (const r of rows) addLedgerRow(r, true);
    trimLedger();

    renderHint('p1', state);
    renderHint('p2', state);
    addEvents(state);

    // Lobby and end-of-match overlays.
    if (phase === 'lobby') {
      dom.stageMsg.hidden = false;
      dom.stageMsg.className = 'stage-msg msg-lobby';
      setText(dom.stageMsgTitle, 'READY');
      setText(dom.stageMsgSub, 'Press Start to begin a 5 minute match');
    } else if (phase === 'ended') {
      const blue = Number(sc.blue) || 0;
      const red = Number(sc.red) || 0;
      dom.stageMsg.hidden = false;
      dom.stageMsg.className = `stage-msg ${blue >= red ? 'msg-blue' : 'msg-red'}`;
      setText(dom.stageMsgTitle, blue === red ? 'DRAW' : blue > red ? 'BLUE HOLDS THE NETWORK' : 'RED WINS');
      setText(
        dom.stageMsgSub,
        `Blue ${fmtScore(blue)}  |  Red ${fmtScore(red)}  |  ${sc.breaches_total ?? 0} breaches, ${sc.breaches_in_top50 ?? 0} on top-50 priority CVEs`,
      );
    } else {
      dom.stageMsg.hidden = true;
    }
  }

  function setSceneNote(text) {
    dom.sceneNote.textContent = text;
    dom.sceneNote.hidden = !text;
  }

  function setTickSeconds(sec) {
    const n = Number(sec);
    if (Number.isFinite(n) && n >= 0.01 && n <= 60) tickSeconds = n;
  }

  return { applyState, applyReason, backfill, setConn, setInputFeedback, setSceneNote, setTickSeconds };
}
