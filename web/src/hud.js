// DOM overlays: top bar, events strip, banners, and the wiring between the HUD modules.
// Every string that comes from the server is written with textContent, never as HTML.
//
//   players.js     the two player panels (advisor line, feedback, card table)
//   advisor.js     the "next best move" sentence
//   rightpanel.js  Agents tab and Full log tab
//   pipeline.js    the pipeline strip shown during "Advance week"
//   overlays.js    briefing card and end-of-match debrief
//   wording.js     plain-language sentences shared by the above

import { $, el, fmtDuration, fmtScore, fmtTime, onClick, setText } from './dom.js';
import { cardKey, patchChoice } from './advisor.js';
import { GATE_HELP, dataLine, feedLine, gateSentence, rates } from './wording.js';
import { createPlayerPanels } from './players.js';
import { createRightPanel } from './rightpanel.js';
import { createPipelineStrip } from './pipeline.js';
import { createBriefing, createDebrief } from './overlays.js';

const TICKER_CAP = 40;
const TICKER_KINDS = new Set(['drop', 'campaign', 'gate', 'breach', 'patched', 'restored', 'intel', 'info']);
const LOUD_KINDS = new Set(['drop', 'campaign', 'gate', 'breach']);
const BANNER_MS = 8000;
// Tick length for the time-left clock: the contract's 2 s until main.js reports the server's
// own value (/api/status tick_seconds, then the measured gap between state messages).
const DEFAULT_TICK_SECONDS = 2;

export function createHud({ onControl }) {
  let tickSeconds = DEFAULT_TICK_SECONDS;
  const dom = {
    phase: $('phase'),
    conn: $('conn'),
    connText: $('conn-text'),
    scoreBlue: $('score-blue'),
    scoreRed: $('score-red'),
    rateBlue: $('rate-blue'),
    rateRed: $('rate-red'),
    nHealthy: $('n-healthy'),
    nCompromised: $('n-compromised'),
    nIsolated: $('n-isolated'),
    breachesTotal: $('breaches-total'),
    breachesNoun: $('breaches-noun'),
    briefLength: $('brief-length'),
    breachesTop50: $('breaches-top50'),
    ticksLeft: $('ticks-left'),
    timeLeft: $('time-left'),
    tickBar: $('tick-bar'),
    asof: $('asof'),
    srcBadge: $('src-badge'),
    modelVersion: $('model-version'),
    gateLine: $('gate-line'),
    dataLine: $('data-line'),
    btnStart: $('btn-start'),
    btnReset: $('btn-reset'),
    btnAdvance: $('btn-advance'),
    ticker: $('ticker'),
    banner: $('banner'),
    stageMsg: $('stage-msg'),
    stageMsgTitle: $('stage-msg-title'),
    stageMsgSub: $('stage-msg-sub'),
    sceneNote: $('scene-note'),
    flash: $('flash'),
    flashText: $('flash-text'),
  };

  const players = createPlayerPanels({ p1: $('hint-p1'), p2: $('hint-p2') });
  const right = createRightPanel();
  const pipeline = createPipelineStrip();
  const briefing = createBriefing({ onStart: () => onControl('start'), onChange: () => renderStageMsg() });
  const debrief = createDebrief({ onStart: () => onControl('start'), onChange: () => renderStageMsg() });

  // What the advisor needs beyond one state message.
  const ctx = {
    attacked: new Set(), // cardKey() of cards Red was seen attacking
    isolatedSince: new Map(), // node id -> tick first seen isolated
  };
  let matchId = null;
  let lastState = null;
  let lastAsof = null;
  let bannerTimer = null;
  let flashTimer = null;
  let lastEventKey = '';
  const liveLedgerIds = new Set(); // rows that came with a state message (their events were shown live)
  let lastFeedbackKey = '';

  for (const [btn, cmd] of [
    [dom.btnStart, 'start'],
    [dom.btnReset, 'reset'],
    [dom.btnAdvance, 'advance'],
  ]) {
    onClick(btn, () => onControl(cmd));
  }

  // --------------------------------------------------------- connection

  function setConn(status, text) {
    dom.conn.className = `conn conn-${status}`;
    setText(dom.connText, text);
    dom.conn.title = status === 'open' ? 'Connected to the game server' : text;
    if (!lastState) {
      // Nothing received yet: say what the page is waiting for.
      dom.stageMsg.hidden = false;
      dom.stageMsg.className = 'stage-msg msg-lobby';
      setText(dom.stageMsgTitle, status === 'down' ? 'WAITING FOR GAME SERVER' : 'CONNECTING');
      setText(dom.stageMsgSub, 'Start the game server, or open this page with ?mock=1 for a demo');
    }
  }

  // ------------------------------------------------------------- events

  function showBanner(kind, text) {
    dom.banner.className = `banner banner-${kind}`;
    dom.banner.textContent = text;
    dom.banner.hidden = false;
    clearTimeout(bannerTimer);
    bannerTimer = setTimeout(() => {
      dom.banner.hidden = true;
    }, BANNER_MS);
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
      else if (kind === 'drop') showBanner(kind, text);
      else if (kind === 'gate' && !pipeline.showVerdict(text)) showBanner(kind, text);
      if (!TICKER_KINDS.has(kind)) continue;
      const item = el('div', `tk tk-${kind}`);
      if (LOUD_KINDS.has(kind)) item.classList.add('tk-loud');
      item.append(el('span', 'tk-kind', kind.toUpperCase()), el('span', 'tk-tick', `t${state.tick ?? 0}`), el('span', 'tk-text', text));
      dom.ticker.prepend(item);
    }
    while (dom.ticker.childElementCount > TICKER_CAP) dom.ticker.lastElementChild.remove();
  }

  // What the server says each human action did (state.player_feedback).
  function addPlayerFeedback(state) {
    const list = Array.isArray(state.player_feedback) ? state.player_feedback : [];
    if (!list.length) return;
    const key = `${state.match_id}|${state.tick}|${list.length}`;
    if (key === lastFeedbackKey) return;
    lastFeedbackKey = key;
    for (const f of list) {
      if (!f || !f.text) continue;
      const pid = f.player === 2 || f.player === 'p2' ? 'p2' : f.player === 1 || f.player === 'p1' ? 'p1' : null;
      if (pid) players.setFeedback(pid, String(f.text), f.ok === false ? 'bad' : 'ok');
    }
  }

  // Feedback from the keyboard itself, before the server has answered.
  function setInputFeedback(player, text, kind) {
    players.setFeedback(player === 2 ? 'p2' : 'p1', text, kind === 'bad' ? 'bad' : 'note');
  }

  // ------------------------------------------------------------ top bar

  function renderScoreboard(state, phase) {
    const sc = state.score || {};
    setText(dom.scoreBlue, fmtScore(sc.blue));
    setText(dom.scoreRed, fmtScore(sc.red));
    const rate = rates(state);
    setText(dom.rateBlue, phase === 'lobby' ? 'you + 3 AI defenders' : rate.blue);
    setText(dom.rateRed, phase === 'lobby' ? '2 hidden AI attackers' : rate.red);
    dom.rateBlue.classList.toggle('is-hot', phase === 'running' && rate.blueHot);
    dom.rateRed.classList.toggle('is-hot', phase === 'running' && rate.redHot);
    setText(dom.nHealthy, sc.healthy ?? 0);
    setText(dom.nCompromised, sc.compromised ?? 0);
    setText(dom.nIsolated, sc.isolated ?? 0);
    setText(dom.breachesTotal, sc.breaches_total ?? 0);
    setText(dom.breachesNoun, Number(sc.breaches_total) === 1 ? 'breach' : 'breaches');
    setText(dom.breachesTop50, sc.breaches_in_top50 ?? 0);
    const total = Number(state.ticks_total) || 150;
    const tick = Number(state.tick) || 0;
    const left = Math.max(0, total - tick);
    setText(dom.ticksLeft, left);
    setText(dom.timeLeft, fmtTime(left * tickSeconds));
    dom.tickBar.style.width = `${Math.min(100, (tick / total) * 100).toFixed(1)}%`;
    // The match length in words, from the server's own tick count and tick length.
    const length = matchLength(state);
    setText(dom.briefLength, length);
    dom.btnStart.title = `Start a match (${length})`;
  }

  function matchLength(state) {
    return fmtDuration((Number(state && state.ticks_total) || 150) * tickSeconds);
  }

  function renderDataPanel(w) {
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
    dom.srcBadge.title = isModel ? 'Our model sets the danger scores this week' : 'The EPSS baseline sets the danger scores this week';
    setText(dom.modelVersion, isModel ? w.model_version || 'our model' : 'baseline');
    const gate = gateSentence(w);
    setText(dom.gateLine, gate);
    dom.gateLine.title = GATE_HELP;
    dom.gateLine.className = `gate-line ${isModel ? 'is-pass' : 'is-block'}`;
    setText(dom.dataLine, dataLine(w) || 'Every card is a real CVE');
  }

  // -------------------------------------------------------------- state

  // The server marks a card as attacked (no patch points any more) only when the attempt was
  // really rolled: outcome "breach" or "failed". An EXPLOIT that resolved as "invalid" (the
  // machine was already breached) leaves the card worth patching.
  function wasAttacked(r) {
    return !!r && r.action === 'EXPLOIT' && !!r.cve_id && r.node_id !== null && r.node_id !== undefined
      && (r.outcome === 'breach' || r.outcome === 'failed');
  }

  function trackBoard(state) {
    for (const r of Array.isArray(state.ledger) ? state.ledger : []) {
      if (r && r.ledger_id != null) liveLedgerIds.add(String(r.ledger_id));
      if (wasAttacked(r)) ctx.attacked.add(cardKey(r.node_id, r.cve_id));
    }
    for (const n of state.nodes || []) {
      if (n.state !== 'isolated') ctx.isolatedSince.delete(n.id);
      else if (!ctx.isolatedSince.has(n.id)) ctx.isolatedSince.set(n.id, Number(state.tick) || 0);
    }
  }

  function applyState(state) {
    if (!state) return;
    lastState = state;

    if (state.match_id !== matchId) {
      if (matchId !== null) {
        right.clear();
        dom.ticker.replaceChildren();
        players.reset();
      }
      ctx.attacked.clear();
      ctx.isolatedSince.clear();
      liveLedgerIds.clear();
      matchId = state.match_id;
    }

    const phase = ['lobby', 'running', 'ended'].includes(state.phase) ? state.phase : 'lobby';
    dom.phase.className = `phase phase-${phase}`;
    setText(dom.phase, phase.toUpperCase());

    const w = state.world || {};
    trackBoard(state);
    renderScoreboard(state, phase);
    renderDataPanel(w);
    pipeline.applyWorld(w);

    const advancing = !!w.advancing || (!!w.advance && w.advance.stage !== 'done' && w.advance.stage !== 'failed');
    dom.btnStart.disabled = phase === 'running';
    dom.btnAdvance.disabled = advancing;
    setText(dom.btnAdvance, advancing ? 'Advancing…' : 'Advance week');

    right.applyState(state);
    addPlayerFeedback(state);
    players.renderAll(state, ctx);
    addEvents(state);
    briefing.applyState(state);
    debrief.applyState(state);

    renderStageMsg();
  }

  // What the board shows when neither card is open.
  function renderStageMsg() {
    const state = lastState;
    if (!state) return;
    if (state.phase === 'lobby' && !briefing.isOpen()) {
      dom.stageMsg.hidden = false;
      dom.stageMsg.className = 'stage-msg msg-lobby';
      setText(dom.stageMsgTitle, 'READY');
      setText(dom.stageMsgSub, `Press Start to begin a match (${matchLength(state)}), or ? for how to play`);
    } else if (state.phase === 'ended' && !debrief.isOpen()) {
      const sc = state.score || {};
      const blue = Number(sc.blue) || 0;
      const red = Number(sc.red) || 0;
      dom.stageMsg.hidden = false;
      dom.stageMsg.className = `stage-msg ${blue >= red ? 'msg-blue' : 'msg-red'}`;
      setText(dom.stageMsgTitle, blue === red ? 'DRAW' : blue > red ? 'BLUE HOLDS THE NETWORK' : 'RED WINS');
      setText(dom.stageMsgSub, `Blue ${fmtScore(blue)}  |  Red ${fmtScore(red)}`);
    } else {
      dom.stageMsg.hidden = true;
    }
  }

  function applyReason(msg) {
    right.applyReason(msg);
  }

  // Rows fetched from GET /api/ledger on connect.
  function backfill(rows) {
    right.backfill(rows, matchId);
    const mine = (Array.isArray(rows) ? rows : []).filter((r) => r && (!matchId || !r.match_id || r.match_id === matchId));
    for (const r of mine) {
      if (wasAttacked(r)) ctx.attacked.add(cardKey(r.node_id, r.cve_id));
    }
    backfillTicker(mine);
    if (lastState) players.renderAll(lastState, ctx);
  }

  // A page opened in the middle of a match missed the events so far: rebuild the breaches,
  // patches and restores of this match from the ledger rows, older than what came live.
  function backfillTicker(rows) {
    const names = new Map();
    const actors = new Map();
    if (lastState) {
      for (const n of lastState.nodes || []) names.set(n.id, n.name);
      for (const a of lastState.actors || []) actors.set(a.id, a.name);
    }
    const kindOf = { breach: 'breach', patched: 'patched', restored: 'restored' };
    const items = [];
    for (const r of rows) {
      if (r.ledger_id == null || liveLedgerIds.has(String(r.ledger_id))) continue;
      const line = feedLine(r, actors.get(r.actor_id), names.get(r.node_id));
      if (!line || !kindOf[line.kind]) continue;
      items.push({ tick: Number(r.tick) || 0, kind: kindOf[line.kind], text: line.text });
    }
    items.sort((a, b) => b.tick - a.tick);
    for (const it of items.slice(0, TICKER_CAP)) {
      if (dom.ticker.childElementCount >= TICKER_CAP) break;
      const item = el('div', `tk tk-${it.kind}`);
      if (LOUD_KINDS.has(it.kind)) item.classList.add('tk-loud');
      // The event is shown with the tick after the action, like the live ones.
      item.append(el('span', 'tk-kind', it.kind.toUpperCase()), el('span', 'tk-tick', `t${it.tick + 1}`), el('span', 'tk-text', `${it.text}.`));
      dom.ticker.append(item);
    }
  }

  // The CVE the patch key acts on for this player: the card its panel marks.
  function getPatchCve(player) {
    if (!lastState) return null;
    const id = player === 2 ? 'p2' : 'p1';
    const actor = (lastState.actors || []).find((a) => a.id === id);
    const node = actor && (lastState.nodes || []).find((n) => n.id === actor.node_id);
    const card = patchChoice(node, ctx.attacked);
    return card ? card.cve_id : null;
  }

  function setSceneNote(text) {
    dom.sceneNote.textContent = text;
    dom.sceneNote.hidden = !text;
  }

  function setTickSeconds(sec) {
    const n = Number(sec);
    if (!Number.isFinite(n) || n < 0.01 || n > 60 || n === tickSeconds) return;
    tickSeconds = n;
    // In the lobby no further message comes, so redraw the clock with the real tick length now.
    if (lastState) renderScoreboard(lastState, ['lobby', 'running', 'ended'].includes(lastState.phase) ? lastState.phase : 'lobby');
  }

  return {
    applyState,
    applyReason,
    backfill,
    getPatchCve,
    setConn,
    setInputFeedback,
    setSceneNote,
    setTickSeconds,
    showTab: right.showTab,
    openBriefing: briefing.open,
  };
}
