// The two player panels: who you are, the next best move, what your last key did,
// and the cards on the machine you stand on.

import { el, fmtDanger, plural } from './dom.js';
import { KEYS, fmtWorth, isAttacked, nextMove, patchChoice, sortCards, worth } from './advisor.js';

const FEEDBACK_MS = 4000;
const STATE_LABEL = { healthy: 'HEALTHY', compromised: 'BREACHED', isolated: 'ISOLATED' };
const BUSY_LABEL = { PATCH: 'patching', RESTORE: 'restoring', MONITOR: 'monitoring', ISOLATE: 'isolating' };

export function createPlayerPanels(boxes) {
  // feedback[pid]: { text, kind: 'ok' | 'bad' | 'note', at }
  const feedback = { p1: null, p2: null };
  const timers = { p1: null, p2: null };
  let last = null; // { state, ctx }
  // The card list is a scroll container that is rebuilt on every state message: remember where
  // each player had scrolled to, and on which machine, so reading card 5 of 6 is possible.
  const scroll = { p1: { nodeId: null, top: 0 }, p2: { nodeId: null, top: 0 } };

  function setFeedback(pid, text, kind) {
    feedback[pid] = { text, kind, at: performance.now() };
    clearTimeout(timers[pid]);
    timers[pid] = setTimeout(() => {
      if (last) render(pid, last.state, last.ctx);
    }, FEEDBACK_MS + 50);
    if (last) render(pid, last.state, last.ctx);
  }

  function keyLine(keys) {
    const line = el('div', 'hint-fb fb-keys');
    const add = (key, what) => line.append(el('kbd', 'kbd-s', key), el('span', null, what));
    line.append(el('kbd', 'kbd-s', Object.values(keys.dirs).join(' ')), el('span', null, 'move'));
    add(keys.patch, 'patch');
    add(keys.restore, 'restore');
    add(keys.monitor, 'monitor');
    add(keys.isolate, 'isolate');
    return line;
  }

  function cardRow(node, c, choice, keys, attacked) {
    const row = el('div', `hcard ps-${c.patch_state}`);
    const isChoice = !!choice && c.card_id === choice.card_id;
    if (isChoice) row.classList.add('is-best');

    // A title is "Vendor Product: kind of weakness". The weakness is what the bug is, so it comes
    // first and is never the part that gets cut off; the product follows in a quieter colour.
    const title = el('span', 'hc-title');
    if (c.in_kev) title.append(el('span', 'tagb tag-kev', 'KEV'));
    if (c.is_new) title.append(el('span', 'tagb tag-new', 'NEW'));
    const full = String(c.title || '');
    const cut = full.lastIndexOf(': ');
    if (cut > 0 && cut < full.length - 2) {
      const what = full.slice(cut + 2);
      title.append(el('span', 'hc-what', what.charAt(0).toUpperCase() + what.slice(1)), el('span', 'hc-where', ` in ${full.slice(0, cut)}`));
    } else {
      title.append(document.createTextNode(full));
    }
    title.title = `${full}${c.in_kev ? ' (known exploited: on the CISA KEV list)' : ''}${c.is_new ? ' (new this week)' : ''}`;

    const hit = isAttacked(node, c, attacked);
    const w = worth(node, c, attacked);
    const worthCell = el('span', 'hc-worth', c.patch_state === 'unpatched' && !hit ? fmtWorth(w) : '—');
    if (hit && c.patch_state === 'unpatched') worthCell.title = 'Red already attacked this card: patching it still blocks it, but scores nothing.';

    const status = el('span', 'hc-state');
    if (isChoice) status.append(el('kbd', null, keys.patch), document.createTextNode(' patch'));
    else status.textContent = c.patch_state === 'unpatched' && hit ? 'attacked' : c.patch_state || '';

    row.append(
      el('span', 'hc-rank', Number.isFinite(c.priority_rank) ? `#${c.priority_rank}` : '—'),
      el('span', 'hc-cve', c.cve_id || ''),
      title,
      el('span', 'hc-score', fmtDanger(c.score)),
      worthCell,
      status,
    );
    return row;
  }

  function render(pid, state, ctx) {
    const box = boxes[pid];
    const keys = KEYS[pid];
    const actor = (state.actors || []).find((a) => a.id === pid);
    const frag = document.createDocumentFragment();

    const oldList = box.querySelector('.hint-cards');
    if (oldList) scroll[pid].top = oldList.scrollTop;

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
      head.append(
        el('span', 'hint-at', 'on'),
        el('span', 'hint-node', node.name ?? `#${node.id}`),
        el('span', `pill pill-${node.state}`, STATE_LABEL[node.state] || String(node.state || '').toUpperCase()),
        el('span', 'hint-value', `machine value ×${node.value ?? '?'}`),
      );
      if (node.monitored) head.append(el('span', 'pill pill-mon', 'MONITORED'));
    }
    if (actor.busy_ticks > 0) {
      const what = BUSY_LABEL[actor.action] || 'busy';
      head.append(el('span', 'hint-busy', `${what}: ${plural(actor.busy_ticks, 'tick')} left`));
    }
    frag.append(head);

    const move = nextMove(state, pid, ctx);
    const advice = el('div', `hint-advice adv-${move.tone}`);
    advice.append(el('span', 'adv-mark', 'NEXT'), el('span', 'adv-text', move.text));
    frag.append(advice);

    const fb = feedback[pid];
    if (fb && performance.now() - fb.at < FEEDBACK_MS) frag.append(el('div', `hint-fb fb-${fb.kind}`, fb.text));
    else frag.append(keyLine(keys));

    if (node) {
      const head2 = el('div', 'hcard hcard-head');
      for (const h of ['Priority', 'CVE', 'What it is', 'Danger', 'Worth', 'Status']) head2.append(el('span', null, h));
      head2.children[3].title = 'Danger: predicted chance this bug gets exploited';
      head2.children[4].title = 'Worth: points for patching it now = machine value x danger';
      const list = el('div', 'hint-cards');
      const cards = sortCards(node.cards);
      const choice = patchChoice(node, ctx.attacked);
      if (!cards.length) list.append(el('div', 'hint-empty', 'No cards on this machine.'));
      for (const c of cards) list.append(cardRow(node, c, choice, keys, ctx.attacked));
      frag.append(head2, list);
      box.replaceChildren(frag);
      // Same machine as before: keep the scroll position. A new machine starts at the top.
      if (scroll[pid].nodeId === node.id && scroll[pid].top > 0) list.scrollTop = scroll[pid].top;
      else scroll[pid].top = 0;
      scroll[pid].nodeId = node.id;
      return;
    }
    box.replaceChildren(frag);
  }

  function renderAll(state, ctx) {
    last = { state, ctx };
    render('p1', state, ctx);
    render('p2', state, ctx);
  }

  function placeholder() {
    for (const pid of ['p1', 'p2']) {
      const head = el('div', 'hint-head');
      head.append(el('span', 'hint-tag', KEYS[pid].tag), el('span', 'hint-name', 'waiting for match state'));
      boxes[pid].replaceChildren(head);
    }
  }

  function reset() {
    feedback.p1 = null;
    feedback.p2 = null;
  }

  placeholder();
  return { renderAll, setFeedback, reset };
}
