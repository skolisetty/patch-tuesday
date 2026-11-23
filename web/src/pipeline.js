// Pipeline strip: shown while the server reports world.advance (an "Advance week" run in
// flight), then keeps the gate verdict on screen for a few seconds.

import { $, el, isNum, setText } from './dom.js';
import { gateSentence } from './wording.js';

const STEPS = [
  ['landing', 'landing', 'download'],
  ['raw', 'raw', 'load'],
  ['stg', 'stg', 'clean'],
  ['feat', 'feat', 'features'],
  ['ml', 'ml', 'train + score'],
  ['gate', 'GATE', 'beat EPSS?'],
  ['serve', 'serve', 'new cards'],
];
const VERDICT_MS = 8000;
const CAPTION = 'Bruin is re-running the real pipeline for the next week.';

export function createPipelineStrip() {
  const root = $('pipe');
  const caption = $('pipe-cap');
  const list = $('pipe-steps');
  const detail = $('pipe-detail');
  const steps = STEPS.map(([key, label, sub], i) => {
    const li = el('li', 'pipe-step');
    li.append(el('b', null, label), el('small', null, sub));
    li.dataset.stage = key;
    list.append(li);
    if (i < STEPS.length - 1) list.append(el('li', 'pipe-sep', '›'));
    return li;
  });
  root.title = CAPTION;

  let active = false;
  let failed = false;
  let failMessage = '';
  let lastIndex = -1;
  let verdict = null; // gate event text received during the run
  let hideTimer = null;

  function paint(index, mode) {
    steps.forEach((li, i) => {
      const cls = mode === 'done' || i < index ? 'is-done' : i === index ? (mode === 'failed' ? 'is-failed' : 'is-now') : '';
      li.className = `pipe-step ${cls}`.trim();
    });
  }

  function stageIndex(adv) {
    const byName = STEPS.findIndex((s) => s[0] === adv.stage);
    if (byName >= 0) return byName;
    return isNum(adv.stage_index) ? Math.max(0, Math.min(STEPS.length - 1, Number(adv.stage_index))) : -1;
  }

  function progressText(adv) {
    const parts = [];
    // After a blocked gate the server runs a second, short Bruin command; its counts restart.
    if (adv.message && /EPSS baseline/.test(String(adv.message))) parts.push('gate blocked · rebuilding with the EPSS baseline');
    // assets_done counts finished assets, so the one running now is the next one.
    if (isNum(adv.assets_done) && isNum(adv.assets_total)) {
      parts.push(`asset ${Math.min(Number(adv.assets_done) + 1, Number(adv.assets_total))} of ${adv.assets_total}`);
    } else if (isNum(adv.assets_done) && Number(adv.assets_done) > 0) {
      parts.push(`${adv.assets_done} assets done`);
    }
    if (adv.current) parts.push(String(adv.current));
    else if (adv.message && parts.length === 0) parts.push(String(adv.message)); // e.g. "Waiting for the pipeline log."
    if (isNum(adv.seconds)) parts.push(`${Math.round(Number(adv.seconds))} s`);
    if (!parts.length && adv.message) parts.push(String(adv.message));
    return parts.join(' · ') || 'starting…';
  }

  function holdThenHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      root.hidden = true;
    }, VERDICT_MS);
  }

  function finish(world) {
    active = false;
    root.classList.remove('is-running');
    root.classList.toggle('is-failed', failed);
    if (failed) {
      paint(lastIndex, 'failed');
      setText(detail, `The pipeline run failed, so the board keeps last week's data.${failMessage ? ` (${failMessage})` : ''}`);
    } else {
      paint(STEPS.length, 'done');
      setText(detail, verdict || gateSentence(world));
    }
    detail.classList.add('is-verdict');
    holdThenHide();
  }

  // Call with every state message.
  function applyWorld(world) {
    const adv = world && world.advance && typeof world.advance === 'object' ? world.advance : null;
    const over = !!adv && (adv.stage === 'done' || adv.stage === 'failed');
    const running = !!(world && world.advancing) || (!!adv && !over);
    if (active && adv && adv.stage === 'failed') {
      failed = true;
      failMessage = adv.message ? String(adv.message) : '';
      finish(world);
      return;
    }
    if (running) {
      if (!active) {
        if (over) return; // the tail of a run this page already showed (or never saw)
        active = true;
        failed = false;
        failMessage = '';
        verdict = null;
        lastIndex = -1;
        clearTimeout(hideTimer);
        root.hidden = false;
        root.classList.add('is-running');
        root.classList.remove('is-failed');
        detail.classList.remove('is-verdict');
        setText(caption, CAPTION);
      }
      if (over) {
        // Every asset ran; the server is loading the new week.
        paint(STEPS.length, 'done');
        setText(detail, 'pipeline finished · loading the new week…');
        return;
      }
      const index = adv ? stageIndex(adv) : -1;
      if (index >= 0) lastIndex = index;
      paint(lastIndex, 'run');
      setText(detail, adv ? progressText(adv) : 'running…');
    } else if (active) {
      finish(world);
    }
  }

  // A gate event: returns true when the strip shows it (so no banner is needed).
  function showVerdict(text) {
    if (active) {
      verdict = text;
      return true;
    }
    if (!root.hidden && !failed) {
      setText(detail, text);
      holdThenHide();
      return true;
    }
    return false;
  }

  return { applyWorld, showVerdict };
}
