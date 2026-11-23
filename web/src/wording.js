// Plain-language sentences built from the state message. One name per concept:
// machine, card, danger, priority, breach chance, our model, EPSS baseline, the gate.

import { fmtDanger, fmtInt, fmtPct, fmtPoints, isNum, plural } from './dom.js';

export function sourceName(source, modelVersion) {
  if (source === 'model') return modelVersion ? `our model ${modelVersion}` : 'our model';
  return 'the EPSS baseline';
}

// Tooltip of the gate sentence: what the gate is and what the two numbers count.
export const GATE_HELP =
  'The gate: each week the pipeline tests our model against the EPSS baseline. Each of them picks the 50 bugs it '
  + 'thinks are most dangerous; the numbers say how many of those picks really became known exploited within 91 days. '
  + 'Only a strict win lets our model set the danger. A tie keeps the EPSS baseline.';

// The data panel's one-sentence gate verdict.
export function gateSentence(w) {
  if (!w) return '';
  const passed = w.gate_passed === true || (w.gate_passed == null && w.score_source === 'model');
  const c = w.challenger_hits_at_50;
  const e = w.epss_hits_at_50;
  if (isNum(c) && isNum(e)) {
    const of = isNum(w.n_positives) ? `, of ${fmtInt(w.n_positives)} bugs that really became exploited` : ' in its top 50';
    if (passed) return `Gate passed: our model found ${c}, EPSS baseline ${e}${of}`;
    if (Number(c) === Number(e)) return `Gate blocked (tie): our model found ${c}, EPSS baseline ${e}${of}`;
    return `Gate blocked: our model found ${c}, EPSS baseline ${e}${of}. EPSS baseline sets the danger`;
  }
  // Older serving files: only the share of hits in the top 50 is known.
  if (isNum(w.challenger_p_at_50) && isNum(w.epss_p_at_50)) {
    const nums = `hits in top 50: our model ${fmtPct(w.challenger_p_at_50)}, EPSS baseline ${fmtPct(w.epss_p_at_50)}`;
    return passed ? `Gate passed (${nums})` : `Gate blocked (${nums}): EPSS baseline sets the danger`;
  }
  return passed ? 'Gate passed: our model sets the danger' : 'No model has passed the gate yet: EPSS baseline sets the danger';
}

export function dataLine(w) {
  if (!w) return '';
  const parts = [];
  if (isNum(w.n_cves_visible)) parts.push(`${fmtInt(w.n_cves_visible)} real CVEs visible`);
  if (isNum(w.n_new_cves_this_week)) parts.push(`${fmtInt(w.n_new_cves_this_week)} new this week`);
  if (w.pipeline_run_id) parts.push(`run ${w.pipeline_run_id}`);
  return parts.join(' · ');
}

// Per-tick income under each score. Falls back to the board when score.rate is missing.
export function rates(state) {
  const nodes = state.nodes || [];
  const bad = nodes.filter((n) => n.state === 'compromised');
  const iso = nodes.filter((n) => n.state === 'isolated');
  const rate = (state.score && state.score.rate) || {};
  const red = isNum(rate.red) ? Number(rate.red) : bad.reduce((s, n) => s + (Number(n.value) || 0), 0);
  const blue = isNum(rate.blue) ? Number(rate.blue) : -iso.length;
  return {
    red: red > 0 ? `${fmtPoints(red)} / tick from ${plural(bad.length, 'breached machine')}` : '+0 / tick: nothing breached',
    blue: blue < 0 ? `${fmtPoints(blue)} / tick: ${iso.length} isolated` : 'earns by patching and restoring',
    redHot: red > 0,
    blueHot: blue < 0,
  };
}

// The trace line behind a ledger row's source chip.
export function traceSentence(r) {
  const parts = [];
  const where = [];
  if (r.pipeline_run_id) where.push(`pipeline run ${r.pipeline_run_id}`);
  if (r.world_asof) where.push(`data as of ${r.world_asof}`);
  parts.push(`danger ${fmtDanger(r.score_used)} from ${sourceName(r.score_source, r.model_version)}${where.length ? ` (${where.join(', ')})` : ''}`);
  if (isNum(r.p_breach)) {
    // The server gives exactly 0 only for a card that is already patched or a machine that is isolated.
    const why = Number(r.p_breach) === 0 && r.action === 'EXPLOIT' ? ' (card already patched or machine isolated)' : '';
    parts.push(`breach chance ${fmtPct(r.p_breach)}${why}`);
  }
  if (isNum(r.roll)) parts.push(`roll ${Number(r.roll).toFixed(2)}`);
  if (r.outcome) parts.push(String(r.outcome));
  return parts.join(' → ');
}

// The stored reason sentences use the code's words and raw fractions (node, score 0.94, est. odds
// 0.10, rank 336, compromised). The agent cards and the Full log show them in the screen's words,
// with the same percentages as the card table; the stored text stays in the row's tooltip.
export function plainReason(text) {
  const danger = (_, v) => `danger ${fmtDanger(Number(v))}`;
  const chance = (v) => fmtPct(Number(v));
  return String(text || '')
    .replace(/\best\. odds (\d(?:\.\d+)?)(?!\d|%)/g, (_, v) => `estimated breach chance ${chance(v)}`)
    .replace(/\best\. odds\b/g, 'estimated breach chance')
    .replace(/\bworst odds\b/g, 'worst breach chance')
    .replace(/\bover its bar of (\d(?:\.\d+)?)(?!\d|%)/g, (_, v) => `over its threshold of ${chance(v)}`)
    .replace(/\bclears its bar of (\d(?:\.\d+)?)(?!\d|%)/g, (_, v) => `reaches its threshold of ${chance(v)} breach chance`)
    .replace(/\bstill breaches at (\d(?:\.\d+)?)(?!\d|%)/g, (_, v) => `still has a ${chance(v)} breach chance`)
    .replace(/\bhighest score of (\d+) open cards? here, (\d(?:\.\d+)?)(?!\d|%)/g,
      (_, n, v) => `highest danger of ${n} open ${Number(n) === 1 ? 'card' : 'cards'} here, ${fmtDanger(Number(v))}`)
    .replace(/\bscore (\d(?:\.\d+)?)(?!\d|%)/g, danger)
    .replace(/\bscore (\d+(?:\.\d+)?%)/g, 'danger $1')
    .replace(/\bhigh-score\b/g, 'high-danger')
    .replace(/\bhighest score\b/g, 'highest danger')
    .replace(/, exposure \d+(?:\.\d+)?/g, '')
    .replace(/\bthe most exposed node\b/g, 'the machine with the most open danger')
    .replace(/\bno exposed node is left\b/g, 'no machine with an open card is left')
    .replace(/\bnothing is exposed\b/g, 'no card is open')
    .replace(/\b1 open cards\b/g, '1 open card')
    .replace(/\b1 ticks\b/g, '1 tick')
    .replace(/\bnode\(s\)/g, 'machine(s)')
    .replace(/\bnodes\b/g, 'machines')
    .replace(/\bnode\b/g, 'machine')
    .replace(/\bcompromised\b/g, 'breached')
    .replace(/\b(\d+) red in sight\b/g, (_, n) => `${n} ${Number(n) === 1 ? 'attacker' : 'attackers'} in sight`)
    .replace(/\ba red\b/g, 'an attacker')
    .replace(/\bwas detected\b/g, 'was spotted')
    .replace(/\brank (\d+) on the (\w+ )?priority list\b/g, 'priority #$1')
    .replace(/\bunranked on the (\w+ )?priority list\b/g, 'not on the priority list');
}

const ROLES = {
  triage: 'follows the priority list',
  patcher: 'cleans the highest-value machines',
  hunter: 'monitors, restores, isolates',
  opportunist: 'chases fresh high-danger cards',
  patient: 'scans first, waits for a good breach chance',
};

export function roleText(actor) {
  const side = actor.team === 'red' ? 'Red AI attacker' : 'Blue AI defender';
  const what = ROLES[actor.role];
  return what ? `${side}: ${what}` : side;
}

// "Patching CVE-… on DB-01 (1 tick left)" for an agent card.
export function actionPhrase(actor, nodeName, targetName, cve) {
  const at = nodeName || 'the network';
  let s;
  switch (actor.action) {
    case 'MOVE': s = targetName && targetName !== nodeName ? `Moving to ${targetName}` : `Moving, now at ${at}`; break;
    case 'PATCH': s = `Patching ${cve || 'a card'} on ${at}`; break;
    case 'RESTORE': s = `Restoring ${at}`; break;
    case 'MONITOR': s = `Monitoring ${at}`; break;
    case 'ISOLATE': s = `Isolating ${at}`; break;
    case 'SCAN': s = `Scanning ${at}`; break;
    case 'EXPLOIT': s = `Attacking ${at}${cve ? ` through ${cve}` : ''}`; break;
    case 'PIVOT': s = `Pivoting to ${targetName || at}`; break;
    case 'INTEL': s = 'Checking public reports on a CVE'; break;
    default: s = `Waiting at ${at}`;
  }
  const busy = Number(actor.busy_ticks) || 0;
  return busy > 0 ? `${s} (${plural(busy, 'tick')} left)` : s;
}

// One line of the "latest" feed, or null when the row is not notable.
export function feedLine(r, actorName, nodeName) {
  const who = actorName || r.actor_id || 'Someone';
  const at = nodeName || 'a machine';
  if (r.action === 'EXPLOIT' && r.outcome === 'breach') {
    return { kind: 'breach', text: `${who} breached ${at}${r.cve_id ? ` through ${r.cve_id}` : ''}` };
  }
  if (r.action === 'EXPLOIT' && r.outcome === 'failed') {
    const p = isNum(r.p_breach) ? ` (breach chance ${fmtPct(r.p_breach)})` : '';
    return { kind: 'failed', text: `${who}'s attack on ${at} failed${p}` };
  }
  if (r.outcome === 'patched') return { kind: 'patched', text: `${who} patched ${r.cve_id || 'a card'} on ${at}` };
  if (r.outcome === 'restored') return { kind: 'restored', text: `${who} restored ${at}` };
  return null;
}

// "What really happened" sentence for one card of state.reveal.
export function revealSentence(it) {
  const name = it.title ? `${it.cve_id} (${it.title})` : String(it.cve_id || 'A card');
  let when;
  if (it.kev_date_added && isNum(it.days_after_asof) && Number(it.days_after_asof) > 0) {
    when = `was added to CISA's exploited list on ${it.kev_date_added}, ${plural(it.days_after_asof, 'day')} after this board's date`;
  } else if (it.kev_date_added) {
    when = `was added to CISA's exploited list on ${it.kev_date_added}`;
  } else {
    when = "was added to CISA's exploited list";
  }
  const by = it.score_source === 'model' ? 'our model' : 'the EPSS baseline';
  const prio = isNum(it.priority_rank) ? `Priority #${it.priority_rank} by ${by}` : `Not in the priority list (${by})`;
  const where = it.node_name ? ` It sat on ${it.node_name}.` : '';
  return `${name} ${when}. ${prio}.${where}`;
}

export function fateSentence(it) {
  const tick = isNum(it.tick) ? ` at tick ${it.tick}` : '';
  const by = it.by ? ` (${it.by})` : '';
  if (it.fate === 'patched') return { kind: 'patched', text: `Blue patched it${tick}${by}.` };
  if (it.fate === 'breached') return { kind: 'breached', text: `It was breached${tick}${by}.` };
  return { kind: 'open', text: 'It was left open.' };
}
