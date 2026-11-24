// The two cards that explain the game: the briefing (lobby, or the "?" button at any time)
// and the end-of-match debrief. Opening or closing either never changes the match.

import { $, el, fmtInt, fmtScore, isNum, onClick, plural, setText } from './dom.js';
import { fateSentence, revealSentence } from './wording.js';

// --------------------------------------------------------------- briefing

export function createBriefing({ onStart, onChange }) {
  const root = $('briefing');
  const source = $('brief-source');
  const start = $('brief-start');
  const close = $('brief-close');
  const helpBtn = $('btn-help');
  let phase = 'lobby';
  let auto = false; // opened by the lobby, not by the "?" button
  let dismissedMatch = null; // lobby briefing closed by hand for this match
  let matchId = null;

  function open(byLobby) {
    auto = !!byLobby;
    root.hidden = false;
    if (onChange) onChange();
  }
  function shut() {
    root.hidden = true;
    auto = false;
    if (onChange) onChange();
  }

  onClick(helpBtn, () => (root.hidden ? open(false) : shut()));
  onClick(close, () => {
    if (phase === 'lobby') dismissedMatch = matchId;
    shut();
  });
  onClick(start, () => {
    shut();
    if (phase !== 'running') onStart();
  });
  window.addEventListener('keydown', (e) => {
    if (e.code === 'Escape' && !root.hidden) {
      if (phase === 'lobby') dismissedMatch = matchId;
      shut();
    }
  });

  function applyState(state) {
    phase = state.phase;
    matchId = state.match_id;
    const w = state.world || {};
    setText(source, w.score_source === 'model' ? `our model${w.model_version ? ` (${w.model_version})` : ''}` : 'the EPSS baseline');
    setText(start, phase === 'running' ? 'Back to the match' : phase === 'ended' ? 'Play again' : 'Start the match');
    if (phase === 'lobby' && root.hidden && dismissedMatch !== matchId) open(true);
    else if (phase !== 'lobby' && auto) shut(); // someone else pressed Start
  }

  return { applyState, open: () => open(false), isOpen: () => !root.hidden };
}

// ---------------------------------------------------------------- debrief

const BLUE_PARTS = [
  ['patch', 'patches'],
  ['restore', 'restores'],
  ['isolate_cost', 'isolation cost'],
];
const RED_PARTS = [
  ['first_breach', 'first breaches'],
  ['held', 'holding breached machines'],
];

function breakdownBar(team, total, parts, values, scale) {
  const wrap = el('div', `bd bd-${team}`);
  const head = el('div', 'bd-head');
  head.append(el('span', 'bd-team', team === 'blue' ? 'BLUE' : 'RED'), el('span', 'bd-total', fmtScore(total)));
  const bar = el('div', 'bd-bar');
  const legend = el('div', 'bd-legend');
  parts.forEach(([key, label], i) => {
    const v = Number(values[key]) || 0;
    if (v > 0) {
      const seg = el('i', `bd-seg seg-${i}`);
      seg.style.width = `${Math.max(1.5, (v / scale) * 100).toFixed(1)}%`;
      seg.title = `${label}: ${fmtScore(v)}`;
      bar.append(seg);
    }
    const item = el('span', `bd-item${v < 0 ? ' is-neg' : ''}`);
    item.append(el('i', `bd-dot seg-${i}`), el('span', null, `${label} `), el('b', null, `${v > 0 ? '+' : ''}${fmtScore(v)}`));
    legend.append(item);
  });
  wrap.append(head, bar, legend);
  return wrap;
}

export function createDebrief({ onStart, onChange }) {
  const root = $('debrief');
  const title = $('debrief-title');
  const sub = $('debrief-sub');
  const body = $('debrief-body');
  const again = $('debrief-again');
  const close = $('debrief-close');
  let shownFor = null; // match id the card was built for
  let closedFor = null;
  let lastSig = '';

  function shut() {
    closedFor = shownFor;
    root.hidden = true;
    if (onChange) onChange();
  }
  onClick(close, shut);
  onClick(again, () => {
    shut();
    onStart();
  });
  window.addEventListener('keydown', (e) => {
    if (e.code === 'Escape' && !root.hidden) shut();
  });

  function build(state) {
    const sc = state.score || {};
    const blue = Number(sc.blue) || 0;
    const red = Number(sc.red) || 0;
    root.className = `modal debrief ${blue === red ? 'is-draw' : blue > red ? 'is-blue' : 'is-red'}`;
    setText(title, blue === red ? 'A MATCH OF EQUALS' : blue > red ? 'DEFENDERS VICTORIOUS' : 'ATTACKERS VICTORIOUS');
    setText(
      sub,
      `Blue ${fmtScore(blue)} · Red ${fmtScore(red)} · ${plural(sc.breaches_total ?? 0, 'breach', 'breaches')}, ` +
        `${sc.breaches_in_top50 ?? 0} through top-50 priority cards`,
    );

    const frag = document.createDocumentFragment();
    const bd = sc.breakdown;
    if (bd && (bd.blue || bd.red)) {
      const b = bd.blue || {};
      const r = bd.red || {};
      const pos = (o) => Object.values(o).reduce((s, v) => s + Math.max(0, Number(v) || 0), 0);
      const scale = Math.max(pos(b), pos(r), 1);
      const sec = el('section', 'db-sec');
      sec.append(el('h3', null, 'Where the points came from'), breakdownBar('blue', blue, BLUE_PARTS, b, scale), breakdownBar('red', red, RED_PARTS, r, scale));
      frag.append(sec);
    }

    const rv = state.reveal;
    if (rv && typeof rv === 'object') {
      const sec = el('section', 'db-sec');
      sec.append(el('h3', null, 'What really happened'));
      const items = Array.isArray(rv.items) ? rv.items : [];
      const horizon = isNum(rv.horizon_days) ? rv.horizon_days : 91;
      const nReal = isNum(rv.n_real) ? Number(rv.n_real) : items.length;
      if (nReal === 0 || !items.length) {
        sec.append(el('p', 'db-none', `None of this board's cards became known exploited within ${horizon} days.`));
      } else {
        const of = isNum(rv.n_cards) ? ` of this board's ${fmtInt(rv.n_cards)} cards` : " of this board's cards";
        sec.append(
          el('p', 'db-lead', `The data stops at this board's date, but history went on: ${nReal}${of} really became known exploited within ${horizon} days.`),
        );
        const list = el('ul', 'db-list');
        for (const it of items) {
          const fate = fateSentence(it);
          const li = el('li', `db-item fate-${fate.kind}`);
          li.append(el('span', 'db-fate', fate.kind === 'patched' ? 'PATCHED' : fate.kind === 'breached' ? 'BREACHED' : 'LEFT OPEN'));
          const text = el('span', 'db-text', revealSentence(it));
          text.append(el('b', null, ` ${fate.text}`));
          li.append(text);
          list.append(li);
        }
        sec.append(list);
      }
      frag.append(sec);
    }
    body.replaceChildren(frag);
  }

  function applyState(state) {
    if (state.phase !== 'ended') {
      root.hidden = true;
      return;
    }
    // Rebuild when the content changes: reveal can arrive a moment after the final tick.
    const sig = JSON.stringify([state.match_id, state.score, state.reveal ?? null]);
    shownFor = state.match_id;
    if (sig !== lastSig) build(state);
    lastSig = sig;
    root.hidden = closedFor === shownFor;
  }

  return { applyState, isOpen: () => !root.hidden };
}
