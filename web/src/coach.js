// "Call for backup": the Coach box at the top of the right panel. It is driven by state.backup
// alone and stays hidden when that is null (feature off, or an older server).
//
//   watching               one slim line: when the analysis comes and what to type in BAND
//   analysing / asked      the weakness, its evidence, the players' lines, the question + countdown
//   joined / declined      the outcome; the details fold away a few ticks later
//   unavailable            one slim line
//
// Every string from the server (and a human's name from BAND) is written with textContent.

import { $, el, onClick, plural, setText } from './dom.js';
import { approvalText, clip, specialistJob, specialistName } from './wording.js';

const FOLD_AFTER_TICKS = 8; // details stay open this long after the answer
const MAX_LINES = 4;

export function createCoach() {
  const root = $('coach');
  if (!root) return { applyState() {}, reset() {}, isFull: () => false, isOpen: () => false };

  const tag = el('span', 'coach-tag', 'COACH');
  const title = el('span', 'coach-title');
  const toggle = el('button', 'coach-toggle', 'hide');
  toggle.type = 'button';
  toggle.title = "Show or hide Coach's evidence";
  const head = el('div', 'coach-head');
  head.append(tag, title, toggle);
  const evidence = el('ul', 'coach-evidence');
  const players = el('ul', 'coach-players');
  const details = el('div', 'coach-details');
  details.append(evidence, players);
  const rec = el('div', 'coach-rec');
  const line = el('div', 'coach-state');
  const bar = el('div', 'coach-bar');
  const barFill = el('i');
  bar.append(barFill);
  root.append(head, details, rec, line, bar);

  let matchId = null;
  let finalSince = null; // tick at which joined/declined was first seen
  let userOpen = null; // the toggle, once used, wins over the automatic folding
  let last = null;

  onClick(toggle, () => {
    userOpen = details.hidden;
    if (last) applyState(last);
  });

  function fillList(node, items, cls) {
    const frag = document.createDocumentFragment();
    for (const text of items.slice(0, MAX_LINES)) frag.append(el('li', cls, clip(text, 240)));
    node.replaceChildren(frag);
    node.hidden = !items.length;
  }

  function reset() {
    finalSince = null;
    userOpen = null;
  }

  function applyState(state) {
    last = state;
    const b = state && state.backup;
    if (!b || typeof b !== 'object') {
      root.hidden = true;
      return;
    }
    if (state.match_id !== matchId) {
      matchId = state.match_id;
      reset();
    }
    const tick = Number(state.tick) || 0;
    const status = String(b.status || 'watching');
    const room = clip(b.room, 60);
    const where = room ? `BAND (${room})` : 'BAND';
    const weakness = b.weakness && typeof b.weakness === 'object' ? b.weakness : null;
    const actor = (state.actors || []).find((a) => a && (a.id === (b.actor_id || 'blue4') || a.reinforcement === true));
    const pick = b.chosen || b.recommended || null;
    const who = clip(actor && actor.name, 30) || specialistName(pick);

    // ---- slim line: nothing to show yet, or nothing will come
    // (An older or partial message may name a later status without the analysis: the box then
    // shows the state alone under a plain title.)
    const known = ['analysing', 'asked', 'joined', 'declined'].includes(status);
    const slim = !known || (status === 'analysing' && !weakness);
    root.hidden = false;
    root.className = `coach coach-${slim ? 'slim' : 'full'} is-${status.replace(/[^a-z]/g, '') || 'watching'}`;
    head.hidden = slim;
    rec.hidden = true;
    bar.hidden = true;
    if (slim) {
      details.hidden = true;
      let text;
      if (status === 'unavailable') text = 'Coach could not reach BAND. No backup this match; the game carries on.';
      else if (status === 'analysing') text = `Coach is analysing the match and posting in ${where}.`;
      else if (state.phase === 'ended') text = 'Coach did not call for backup in this match.';
      else {
        const cp = Number(b.checkpoint_tick);
        const when = Number.isFinite(cp) && cp > tick ? `It steps in if Red starts to dominate, or at tick ${cp}. To ask now, write` : 'For an analysis now, write';
        text = `Coach is ${state.phase === 'lobby' ? 'on the team' : 'watching'}. ${when} @coach analyze in ${where}.`;
      }
      setText(line, text);
      return;
    }

    // ---- full box
    const rescue = b.trigger === 'red_dominating';
    setText(title, weakness && clip(weakness.title, 60) ? `${rescue ? 'Red is dominating. Weakness' : 'Team weakness'}: ${clip(weakness.title, 60)}` : (rescue ? 'Red is dominating: Coach steps in' : 'Call for backup'));
    // Where the figures came from: the Bruin coach pipeline, or the game's own calculation.
    const evLines = (weakness && Array.isArray(weakness.evidence) ? weakness.evidence : []).filter(Boolean).map(String);
    if (weakness && b.analysis_source === 'bruin') {
      const checks = Number.isFinite(Number(b.checks_passed)) && b.checks_passed ? `${b.checks_passed} checks passed` : 'checks passed';
      const secs = Number.isFinite(Number(b.analysis_seconds)) && b.analysis_seconds ? ` in ${Number(b.analysis_seconds).toFixed(1)} s` : '';
      const run = b.pipeline_run_id ? ` (run ${clip(String(b.pipeline_run_id), 40)})` : '';
      evLines.unshift(`Analysed by a Bruin pipeline: ${checks}${secs}${run}.`);
    } else if (weakness && b.analysis_source === 'local') {
      evLines.unshift('Analysed locally (Bruin not available).');
    }
    fillList(evidence, evLines, 'coach-ev');
    fillList(
      players,
      (Array.isArray(b.players) ? b.players : []).map((p) => p && p.line).filter(Boolean).map(String),
      'coach-pl',
    );

    const done = status === 'joined' || status === 'declined';
    if (done && finalSince === null) finalSince = tick;
    const autoOpen = !done || (state.phase === 'running' && tick - finalSince < FOLD_AFTER_TICKS);
    const open = userOpen === null ? autoOpen : userOpen;
    details.hidden = !open || (evidence.hidden && players.hidden);
    toggle.hidden = evidence.hidden && players.hidden;
    setText(toggle, details.hidden ? 'show evidence' : 'hide');
    toggle.setAttribute('aria-expanded', String(!details.hidden));

    const job = specialistJob(pick);
    if (status === 'analysing') {
      setText(line, `Coach is posting this in ${where}. The three AI defenders add their own numbers, then Coach asks for approval.`);
    } else if (status === 'asked' && b.chosen) {
      setText(line, `${who} ${approvalText(b.by)}. It walks onto the board next tick.`);
    } else if (status === 'asked') {
      rec.hidden = !pick;
      setText(rec, pick ? `Coach recommends ${specialistName(pick)}${job ? `: ${job}` : ''}.` : '');
      const others = (Array.isArray(b.options) ? b.options : []).map(String).filter((o) => o && o !== b.recommended).slice(0, 3);
      const words = ['yes', ...others.map((o) => clip(o, 20)), 'no'].join(' / ');
      const left = Number(b.expires_tick) - tick;
      const asked = Number(b.asked_tick);
      let count = '';
      if (Number.isFinite(left)) count = left > 0 ? ` ${plural(left, 'tick')} left.` : " Time is up: waiting for Coach's call.";
      setText(line, `Asking in ${where}: reply ${words}.${count}`);
      if (Number.isFinite(left) && Number.isFinite(asked) && Number(b.expires_tick) > asked) {
        bar.hidden = false;
        barFill.style.width = `${Math.max(0, Math.min(100, (left / (Number(b.expires_tick) - asked)) * 100)).toFixed(0)}%`;
      }
    } else if (status === 'joined') {
      const at = Number.isFinite(Number(b.joined_tick)) && b.joined_tick !== null ? ` at tick ${Number(b.joined_tick)}` : '';
      setText(line, `${who} joined${at} (${approvalText(b.by, true)}).${job ? ` It ${job}.` : ''}`);
    } else if (status === 'declined') {
      const by = clip(b.by, 40);
      setText(line, !by ? 'Backup declined. No specialist joins this match.'
        : by.startsWith('Coach') ? 'No reply in BAND, so Coach stood down. No specialist joins this match.'
          : `Backup declined by ${by}. No specialist joins this match.`);
    } else {
      setText(line, '');
    }
    line.hidden = !line.textContent;
  }

  // True while the box shows more than its one slim line.
  const isFull = () => !root.hidden && root.classList.contains('coach-full');
  // isOpen: the evidence is showing too, so the box is at its tallest.
  return { applyState, reset, isFull, isOpen: () => isFull() && !details.hidden };
}
