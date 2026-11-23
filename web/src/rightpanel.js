// Right panel. Tab "Agents": one card per AI agent with what it is doing and why, plus a short
// feed of the latest breaches, patches and restores. Tab "Full log": the decision ledger.

import { $, el, fmtPct, isNum, onClick, plural, setText } from './dom.js';
import { actionPhrase, feedLine, plainReason, roleText, traceSentence } from './wording.js';

const LEDGER_CAP = 200;
const FEED_CAP = 6;
// Same colours as the robots on the board (scene.js ACTOR_COLOR).
const CHIP = { red1: '#ff3b4e', red2: '#ff7a2f', blue1: '#3f8cff', blue2: '#6c7bff', blue3: '#38b6ff', p1: '#2ff3e0', p2: '#ffd23f' };
const AGENT_ORDER = ['blue1', 'blue2', 'blue3', 'red1', 'red2'];

export function createRightPanel() {
  const dom = {
    tabAgents: $('tab-agents'),
    tabLog: $('tab-log'),
    viewAgents: $('view-agents'),
    viewLog: $('view-log'),
    cards: $('agent-cards'),
    feed: $('feed'),
    ledger: $('ledger'),
    count: $('ledger-count'),
    btnWaits: $('btn-waits'),
  };

  const ledgerRows = new Map(); // ledger_id -> { row, reason }
  const unseen = new Map(); // tick -> { row, text, n } : hidden attacker moves of one tick
  const agentCards = new Map(); // actor id -> { root, action, reason }
  const reasons = new Map(); // actor id -> { ledger_id, tick, text, cve }
  const reasonOwner = new Map(); // ledger_id -> actor id (for late "reason" messages)
  const feedSeen = new Set();
  let feedItems = []; // { id, tick, kind, text }
  const actorNames = new Map();
  const nodeNames = new Map();
  let matchTick = 0;
  let lastState = null;

  // --------------------------------------------------------------- tabs

  function showTab(name) {
    const log = name === 'log';
    dom.viewAgents.hidden = log;
    dom.viewLog.hidden = !log;
    dom.tabAgents.setAttribute('aria-selected', String(!log));
    dom.tabLog.setAttribute('aria-selected', String(log));
    dom.count.hidden = !log;
    dom.btnWaits.hidden = !log;
  }
  onClick(dom.tabAgents, () => showTab('agents'));
  onClick(dom.tabLog, () => showTab('log'));
  onClick(dom.btnWaits, () => {
    const on = dom.ledger.classList.toggle('hide-waits');
    dom.btnWaits.setAttribute('aria-pressed', String(on));
  });
  showTab('agents');

  const nodeName = (id) => (id === null || id === undefined ? '' : nodeNames.get(id) || `#${id}`);
  const actorName = (id) => actorNames.get(id) || id || '?';

  // ------------------------------------------------------------- ledger

  function sourceChip(r, row) {
    const isModel = r.score_source === 'model';
    const chip = el('button', `src-chip ${isModel ? 'src-model' : 'src-epss'}`, isModel ? `MODEL ${r.model_version || ''}`.trim() : 'EPSS');
    chip.type = 'button';
    chip.title = 'Where this danger score came from. Click for the full trace.';
    chip.setAttribute('aria-expanded', 'false');
    let trace = null;
    onClick(chip, () => {
      if (trace) {
        trace.remove();
        trace = null;
      } else {
        trace = el('div', 'l-trace', traceSentence(r));
        row.append(trace);
      }
      chip.setAttribute('aria-expanded', String(!!trace));
    });
    return chip;
  }

  // The log shows a reason in the screen's words; the stored sentence stays in the tooltip.
  function setLogReason(node, text) {
    const raw = String(text ?? '');
    const plain = plainReason(raw);
    node.textContent = plain;
    if (plain !== raw) node.title = `As stored in the ledger: ${raw}`;
    else node.removeAttribute('title');
  }

  function buildRow(r) {
    const team = r.team === 'red' ? 'red' : 'blue';
    const row = el('div', `lrow team-${team}`);
    const action = String(r.action ?? '');
    if (action === 'WAIT') row.classList.add('is-wait');
    const outcome = r.outcome === null || r.outcome === undefined ? '' : String(r.outcome);
    if (outcome === 'breach') row.classList.add('is-breach');

    const l1 = el('div', 'l1');
    l1.append(
      el('span', 'c-tick', r.tick ?? ''),
      el('span', 'c-actor', actorName(r.actor_id)),
      el('span', 'c-action', action),
      el('span', 'c-node', nodeName(r.node_id)),
      el('span', 'c-pb', isNum(r.p_breach) ? fmtPct(r.p_breach) : ''),
      el('span', 'c-outcome', outcome),
    );
    const l2 = el('div', 'l2');
    if (r.cve_id) l2.append(el('span', 'c-cve', r.cve_id));
    if ((action === 'EXPLOIT' || action === 'PATCH') && r.score_source && isNum(r.score_used)) l2.append(sourceChip(r, row));
    const reason = el('span', 'c-reason');
    if (r.reason_text) setLogReason(reason, r.reason_text);
    else reason.classList.add('pending');
    l2.append(reason);
    row.append(l1, l2);
    return { row, reason };
  }

  // Hidden attacker moves of one tick share a single line.
  function addUnseen(r, newest) {
    const tick = r.tick ?? 0;
    let u = unseen.get(tick);
    if (!u) {
      const row = el('div', 'lrow team-red is-unseen');
      const text = el('span', 'c-unseen');
      row.append(el('span', 'c-tick', tick), text);
      row.dataset.unseen = String(tick);
      u = { row, text, n: 0 };
      unseen.set(tick, u);
      if (newest) dom.ledger.prepend(row);
      else dom.ledger.append(row);
    }
    u.n += 1;
    setText(u.text, `${plural(u.n, 'hidden attacker move')} (not seen by the defenders)`);
  }

  function noteReason(r) {
    if (r.kind !== 'agent' && !AGENT_ORDER.includes(r.actor_id)) return;
    reasonOwner.set(String(r.ledger_id), r.actor_id);
    const action = String(r.action ?? '');
    // A row that only continues an earlier decision says nothing new: a multi-tick action still
    // running ("working") or a cooldown after an attack (WAIT with an outcome other than "waiting").
    // A WAIT the agent chose itself ("waiting": nothing left to do) is a decision and is kept.
    if (action === 'UNSEEN' || r.outcome === 'working') return;
    if (action === 'WAIT' && r.outcome !== 'waiting') return;
    const prev = reasons.get(r.actor_id);
    if (prev && (prev.tick ?? -1) > (r.tick ?? 0)) return;
    reasons.set(r.actor_id, { ledger_id: String(r.ledger_id), tick: r.tick ?? 0, text: r.reason_text || '', cve: r.cve_id || null, action });
  }

  function noteFeed(r) {
    const id = String(r.ledger_id);
    if (feedSeen.has(id)) return;
    const line = feedLine(r, actorName(r.actor_id), nodeName(r.node_id));
    if (!line) return;
    feedSeen.add(id);
    feedItems.push({ id, tick: r.tick ?? 0, team: r.team === 'red' ? 'red' : 'blue', ...line });
    feedItems.sort((a, b) => b.tick - a.tick);
    feedItems = feedItems.slice(0, FEED_CAP);
  }

  function renderFeed() {
    const frag = document.createDocumentFragment();
    if (!feedItems.length) frag.append(el('div', 'feed-empty', 'Breaches, patches and restores show up here.'));
    for (const it of feedItems) {
      const row = el('div', `feed-row feed-${it.kind}`);
      row.append(el('span', 'feed-tick', `t${it.tick}`), el('span', 'feed-text', it.text));
      frag.append(row);
    }
    dom.feed.replaceChildren(frag);
  }

  // newest = true puts the row at the top; false appends (history backfill).
  function addRow(r, newest) {
    if (!r || r.ledger_id === null || r.ledger_id === undefined) return;
    const id = String(r.ledger_id);
    if (ledgerRows.has(id)) return;
    noteReason(r);
    noteFeed(r);
    if (r.action === 'UNSEEN') {
      ledgerRows.set(id, { row: null, reason: null });
      addUnseen(r, newest);
      return;
    }
    const built = buildRow(r);
    built.row.dataset.lid = id;
    ledgerRows.set(id, built);
    if (newest) dom.ledger.prepend(built.row);
    else dom.ledger.append(built.row);
  }

  function trim() {
    while (dom.ledger.childElementCount > LEDGER_CAP) {
      const lastRow = dom.ledger.lastElementChild;
      if (lastRow.dataset.lid !== undefined) ledgerRows.delete(lastRow.dataset.lid);
      if (lastRow.dataset.unseen !== undefined) unseen.delete(Number(lastRow.dataset.unseen));
      lastRow.remove();
    }
    setText(dom.count, plural(dom.ledger.childElementCount, 'row'));
  }

  function clear() {
    dom.ledger.replaceChildren();
    ledgerRows.clear();
    unseen.clear();
    reasons.clear();
    reasonOwner.clear();
    feedSeen.clear();
    feedItems = [];
    renderFeed();
    setText(dom.count, '0 rows');
  }

  // A reason sentence that arrived after its row (LLM-written reasons are late).
  function applyReason(msg) {
    if (!msg || msg.ledger_id === null || msg.ledger_id === undefined) return;
    const id = String(msg.ledger_id);
    const text = String(msg.reason_text ?? '');
    const entry = ledgerRows.get(id);
    if (entry && entry.reason) {
      setLogReason(entry.reason, text);
      entry.reason.classList.remove('pending', 'fresh');
      void entry.reason.offsetWidth; // restart the highlight animation
      entry.reason.classList.add('fresh');
    }
    const owner = reasonOwner.get(id);
    const cur = owner && reasons.get(owner);
    if (cur && cur.ledger_id === id) {
      cur.text = text;
      const card = agentCards.get(owner);
      if (card) setReason(card, cur, false, card.actor);
    }
  }

  // -------------------------------------------------------- agent cards

  function agentCard(a) {
    const root = el('div', `agent agent-${a.team === 'red' ? 'red' : 'blue'}`);
    const chip = el('i', 'agent-chip');
    chip.style.background = CHIP[a.id] || (a.team === 'red' ? CHIP.red1 : CHIP.blue1);
    const head = el('div', 'agent-head');
    head.append(chip, el('span', 'agent-name', a.name || a.id), el('span', 'agent-role', roleText(a)));
    const action = el('div', 'agent-action');
    const reason = el('div', 'agent-reason');
    root.append(head, action, reason);
    return { root, action, reason, actor: a };
  }

  // The reason line must agree with the action line above it. When the agent is waiting now
  // (a cooldown after an attack, or between decisions) but the stored reason belongs to an earlier action, it
  // is shown as that: "Last decision (tick 44): ...".
  function setReason(card, r, hidden, actor) {
    if (hidden) {
      setText(card.reason, 'You only see an attacker when it breaches a machine or is caught on a monitored one.');
      card.reason.className = 'agent-reason is-muted';
    } else if (!r) {
      setText(card.reason, matchTick > 0 ? 'No decision seen since this page was opened.' : 'No decision yet.');
      card.reason.className = 'agent-reason is-muted';
    } else if (!r.text) {
      setText(card.reason, 'Why: thinking…');
      card.reason.className = 'agent-reason is-muted';
    } else {
      const stale = !!actor && String(actor.action || 'WAIT') === 'WAIT' && r.action !== 'WAIT';
      setText(card.reason, stale ? `Last decision (tick ${r.tick}): ${plainReason(r.text)}` : `Why: ${plainReason(r.text)}`);
      card.reason.className = stale ? 'agent-reason is-muted' : 'agent-reason';
    }
  }

  function renderAgents(state) {
    const actors = (state.actors || []).filter((a) => a.kind === 'agent');
    actors.sort((a, b) => {
      const ia = AGENT_ORDER.indexOf(a.id);
      const ib = AGENT_ORDER.indexOf(b.id);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    for (const a of actors) {
      let card = agentCards.get(a.id);
      if (!card) {
        card = agentCard(a);
        agentCards.set(a.id, card);
        dom.cards.append(card.root);
      }
      card.actor = a;
      const hidden = a.team === 'red' && a.revealed === false && state.phase === 'running';
      const r = reasons.get(a.id);
      card.root.classList.toggle('is-hidden', hidden);
      if (hidden) {
        setText(card.action, `Hidden. Last seen at ${nodeName(a.node_id) || 'an unknown machine'}.`);
      } else {
        const cve = r && r.action === a.action && r.cve ? r.cve : null;
        setText(card.action, actionPhrase(a, nodeName(a.node_id), nodeName(a.target_node), cve));
      }
      setReason(card, r, hidden, a);
    }
    // Agents that left the match (never happens on the real server).
    for (const [id, card] of agentCards) {
      if (!actors.some((a) => a.id === id)) {
        card.root.remove();
        agentCards.delete(id);
      }
    }
  }

  // -------------------------------------------------------------- state

  function applyState(state) {
    for (const a of state.actors || []) actorNames.set(a.id, a.name || a.id);
    for (const n of state.nodes || []) nodeNames.set(n.id, n.name ?? `#${n.id}`);
    matchTick = Number(state.tick) || 0;
    lastState = state;
    for (const r of Array.isArray(state.ledger) ? state.ledger : []) addRow(r, true);
    trim();
    renderAgents(state);
    renderFeed();
  }

  // Rows fetched from GET /api/ledger on connect.
  function backfill(rows, matchId) {
    if (!Array.isArray(rows) || !rows.length) return;
    const list = rows
      .filter((r) => r && (!matchId || !r.match_id || r.match_id === matchId))
      .sort((a, b) => (b.tick ?? 0) - (a.tick ?? 0));
    // The whole match is read for the agent cards and the feed; the log itself shows the
    // newest LEDGER_CAP rows, so older ones are not built at all.
    let built = dom.ledger.childElementCount;
    for (const r of list) {
      if (built < LEDGER_CAP) {
        const before = dom.ledger.childElementCount;
        addRow(r, false);
        built += dom.ledger.childElementCount - before;
      } else if (r.ledger_id !== null && r.ledger_id !== undefined && !ledgerRows.has(String(r.ledger_id))) {
        noteReason(r);
        noteFeed(r);
      }
    }
    trim();
    if (lastState) renderAgents(lastState);
    renderFeed();
  }

  return { applyState, applyReason, backfill, clear, showTab };
}
