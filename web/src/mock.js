// Mock mode (?mock=1): a small local simulation that emits messages in the
// exact shapes of docs/CONTRACTS.md, so the client can be developed and
// shown without the game server. All CVE ids, vendors and titles here are
// invented placeholders, not real vulnerability data, and every number
// (hits, counts, reveal dates) is made up.
//
// ?mock=1&backup=1 also plays "Call for backup": a Coach watches, names the team's weakness at a
// checkpoint, asks in a (pretend) BAND room, and a specialist joins as the eighth actor "blue4".
// Without backup=1 nothing of that exists: state.backup is null and there are seven actors.

const COLS = 5;
const ROWS = 4;

const NODE_NAMES = [
  'MAIL-01', 'WEB-01', 'WEB-02', 'VPN-01', 'DNS-01',
  'DB-01', 'DB-02', 'APP-01', 'APP-02', 'FILE-01',
  'AUTH-01', 'CI-01', 'GIT-01', 'HR-01', 'ERP-01',
  'BKP-01', 'LOG-01', 'WIKI-01', 'PRINT-01', 'KIOSK-01',
];
const VENDORS = ['Acme', 'Globex', 'Initech', 'Umbra', 'Vandelay', 'Hooli', 'Soylent', 'Tyrell'];
const PRODUCTS = ['Gateway', 'Mail Server', 'Web Portal', 'Hypervisor', 'Directory', 'File Share', 'Build Agent', 'Firewall'];
const WEAKNESS = ['injection', 'memory safety', 'auth bypass', 'path traversal', 'deserialization', 'access control'];
const TECHNIQUES = [
  ['T1190', 'initial-access'],
  ['T1068', 'privilege-escalation'],
  ['T1203', 'execution'],
  ['T1210', 'lateral-movement'],
  ['T1133', 'persistence'],
];

// The assets one mock "Advance week" walks through, in pipeline order. The schema prefix is
// the stage shown on the pipeline strip (sim.* counts as serve, ml.eval_metrics is the gate).
const ADVANCE_ASSETS = [
  'landing.nvd_cves', 'landing.cisa_kev', 'landing.epss_scores', 'landing.attack_techniques', 'landing.cwe_catalog', 'landing.vendor_advisories',
  'raw.nvd_cves', 'raw.cisa_kev', 'raw.epss_scores', 'raw.attack_techniques', 'raw.cwe_catalog',
  'stg.cves', 'stg.kev', 'stg.epss', 'stg.techniques',
  'feat.cve_text', 'feat.vendor_history', 'feat.snapshot_features', 'feat.labels',
  'ml.train_challenger', 'ml.score_candidates', 'ml.eval_metrics',
  'serve.patch_priority', 'serve.cards', 'sim.world_clock', 'sim.reveal',
];
const STAGES = ['landing', 'raw', 'stg', 'feat', 'ml', 'gate', 'serve'];

function stageOf(asset) {
  if (asset === 'ml.eval_metrics') return 'gate';
  const prefix = asset.split('.')[0];
  return prefix === 'sim' ? 'serve' : prefix;
}

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// "Call for backup": the three weaknesses and the specialist that answers each.
const SPECIALISTS = ['responder', 'hardener', 'sentinel'];
const WEAKNESS_OF = { responder: 'slow_response', hardener: 'exposure', sentinel: 'blind_spots' };
const WEAKNESS_TITLE = { slow_response: 'Slow response', exposure: 'Exposure', blind_spots: 'Blind spots' };
const BACKUP_ROOM = 'Patch Tuesday - Blue team';
const BACKUP_SPAWN_NODE = 19; // the specialist walks in from the last machine of the board
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const r1 = (v) => Number(v.toFixed(1));
const pct = (v) => `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%`;
const pts = (v) => (Number.isInteger(v) ? String(v) : v.toFixed(2));

function addDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// backup: null (feature off) or { role, reply, replyAfter }:
//   role        force Coach's recommendation: 'responder' | 'hardener' | 'sentinel' (else worked out)
//   reply       what the pretend human answers in BAND: 'yes' (default), 'no', a specialist's name,
//               'none' (nobody answers: Coach brings its own pick), 'standdown' (nobody answers and
//               Coach stands down), 'unavailable' (BAND cannot be reached at the checkpoint)
//   replyAfter  ticks between the question and the answer
export function createMock({ onMessage, onStatus, tickMs = 2000, ticksTotal = 150, autostart = true, seed = 20260322, backup = null }) {
  const rng = mulberry32(seed);
  const pick = (arr) => arr[Math.floor(rng() * arr.length)];
  let cardSeq = 0;
  let ledgerSeq = 0;
  let matchSeq = 0;
  let timer = null;
  let closed = false;
  const pendingActs = { 1: null, 2: null };

  // Before the first advance the mock world has no gate result, like old serving files.
  const world = {
    asof_date: '2026-03-22',
    score_source: 'epss',
    model_version: null,
    challenger_p_at_50: null,
    epss_p_at_50: null,
    advancing: false,
    pipeline_run_id: null,
    gate_passed: false,
    challenger_hits_at_50: null,
    epss_hits_at_50: null,
    n_positives: null,
    n_candidates: null,
    n_cves_visible: 187217,
    n_new_cves_this_week: null,
  };
  let adv = null; // { startedAt, done } while an advance runs
  let advTimer = null;
  let advLanded = false; // every asset done; lands on the next tick of a running match
  let campaignAtTick = null;
  let campaignLeft = 0;

  let st = null;
  let bk = null; // state.backup, null when the feature is off
  let bkx = null; // what the mock tracks to fill it
  // Filled while one tick is computed.
  let deltas = [];
  let feedback = [];

  function newCard(isNew) {
    cardSeq += 1;
    const tech = pick(TECHNIQUES);
    const hot = isNew ? rng() < 0.7 : rng() < 0.18;
    const score = hot ? 0.12 + rng() * 0.5 : Math.pow(rng(), 3) * 0.12 + 0.002;
    return {
      card_id: cardSeq,
      cve_id: `CVE-2026-${String(90000 + cardSeq)}`,
      title: `${pick(VENDORS)} ${pick(PRODUCTS)}: ${pick(WEAKNESS)}`,
      score: Number(score.toFixed(4)),
      in_kev: rng() < (hot ? 0.3 : 0.05),
      patch_state: 'unpatched',
      is_new: !!isNew,
      technique_id: tech[0],
      tactic: tech[1],
      priority_rank: null,
      attempted: false,
      fate: null, // { fate, by, tick } once patched or used in a breach
    };
  }

  function rerank() {
    const all = [];
    for (const n of st.nodes) for (const c of n.cards) all.push(c);
    all.sort((a, b) => b.score - a.score);
    all.forEach((c, i) => {
      // Spread ranks so some fall outside the top 50, as in the real feed.
      c.priority_rank = i < 70 ? 1 + i * 2 : null;
    });
  }

  function newMatch() {
    matchSeq += 1;
    cardSeq = 0;
    const nodes = [];
    for (let id = 0; id < COLS * ROWS; id++) {
      const count = 2 + (rng() < 0.45 ? 1 : 0);
      nodes.push({
        id,
        x: id % COLS,
        y: Math.floor(id / COLS),
        name: NODE_NAMES[id],
        value: 1 + Math.floor(rng() * 3),
        state: 'healthy',
        monitored: false,
        monitorLeft: 0,
        isolateLeft: 0,
        wasBreached: false, // state to return to when isolation ends
        breachedOnce: false,
        cards: Array.from({ length: count }, () => newCard(false)),
      });
    }
    const actor = (id, name, team, kind, role, node_id) => ({
      id, name, team, kind, role, node_id,
      action: 'WAIT', target_node: null, revealed: true, busy_ticks: 0,
      job: null, revealLeft: 0, scanned: new Set(), lastSeen: node_id,
    });
    st = {
      match_id: `m-mock-${matchSeq}`,
      phase: 'lobby',
      tick: 0,
      ticks_total: ticksTotal,
      nodes,
      // Same ids, names and roles as the real server (game/state.py ACTOR_DEFS).
      actors: [
        actor('red1', 'Opportunist', 'red', 'agent', 'opportunist', 4),
        actor('red2', 'Patient', 'red', 'agent', 'patient', 15),
        actor('blue1', 'Triage', 'blue', 'agent', 'triage', 7),
        actor('blue2', 'Patcher', 'blue', 'agent', 'patcher', 11),
        actor('blue3', 'Hunter', 'blue', 'agent', 'hunter', 13),
        actor('p1', 'Player 1', 'blue', 'human', 'human', 6),
        actor('p2', 'Player 2', 'blue', 'human', 'human', 8),
      ],
      score: { red: 0, blue: 0, breaches_total: 0, breaches_in_top50: 0 },
      breakdown: { blue: { patch: 0, restore: 0, isolate_cost: 0 }, red: { first_breach: 0, held: 0 } },
      reveal: null,
    };
    campaignAtTick = null;
    campaignLeft = 0;
    rerank();
    newBackup();
  }

  // ------------------------------------------------------ call for backup

  function newBackup() {
    bk = null;
    bkx = null;
    if (!backup) return;
    const wait = clamp(Math.round(ticksTotal * 0.1), 6, 15);
    bk = {
      status: 'watching',
      checkpoint_tick: clamp(Math.round(ticksTotal * 0.27), 4, 40),
      weakness: null,
      players: [],
      recommended: null,
      options: [...SPECIALISTS],
      asked_tick: null,
      expires_tick: null,
      chosen: null,
      by: null,
      joined_tick: null,
      actor_id: null,
      room: BACKUP_ROOM,
      before: null,
      after: null,
    };
    bkx = {
      wait,
      replyAfter: clamp(Number(backup.replyAfter) || Math.min(5, wait - 2), 1, wait),
      red: [0], blue: [0], breaches: [0], // totals at the end of each tick
      spans: [], // { node, start, end } : how long each breach lasted
      redTicks: 0, redHidden: 0, spotted: 0, unwatchedBreaches: 0,
      humans: { p1: { acts: 0, patches: 0, worth: 0, restores: 0 }, p2: { acts: 0, patches: 0, worth: 0, restores: 0 } },
    };
  }

  // Called at the end of every tick: the numbers Coach quotes are counted, not made up on the spot.
  function trackBackup() {
    if (!bkx) return;
    bkx.red[st.tick] = st.score.red;
    bkx.blue[st.tick] = st.score.blue;
    bkx.breaches[st.tick] = st.score.breaches_total;
    for (const n of st.nodes) {
      const bad = n.state === 'compromised' || (n.state === 'isolated' && n.wasBreached);
      const open = bkx.spans.find((s) => s.node === n.id && s.end === null);
      if (bad && !open) bkx.spans.push({ node: n.id, start: st.tick, end: null });
      else if (!bad && open) open.end = st.tick;
    }
    for (const a of st.actors) {
      if (a.team !== 'red') continue;
      bkx.redTicks += 1;
      if (!a.revealed) bkx.redHidden += 1;
    }
  }

  // Before/after numbers for the ticks in (from, to].
  function windowStats(from, to) {
    const ticks = Math.max(1, to - from);
    const spans = bkx.spans.filter((s) => s.start > from && s.start <= to);
    const total = spans.reduce((sum, s) => sum + (Math.min(s.end ?? to, to) - s.start), 0);
    return {
      ticks: to - from,
      red_per_tick: r1(((bkx.red[to] ?? 0) - (bkx.red[from] ?? 0)) / ticks),
      blue_per_tick: r1(((bkx.blue[to] ?? 0) - (bkx.blue[from] ?? 0)) / ticks),
      breaches: (bkx.breaches[to] ?? 0) - (bkx.breaches[from] ?? 0),
      avg_breach_ticks: spans.length ? r1(total / spans.length) : null,
    };
  }

  function analyse() {
    const red = st.score.red;
    const held = st.breakdown.red.held;
    const lens = bkx.spans.map((s) => (s.end ?? st.tick) - s.start);
    const breachedNow = st.nodes.filter((n) => n.state === 'compromised').length;
    const breaches = st.score.breaches_total;
    let risky = 0;
    for (const n of st.nodes) {
      if (n.value < 2) continue;
      for (const c of n.cards) if (c.patch_state === 'unpatched' && c.score >= 0.3) risky += 1;
    }
    const hiddenShare = bkx.redTicks ? bkx.redHidden / bkx.redTicks : 0;
    const scores = {
      slow_response: (red > 0 ? held / red : 0) + breachedNow * 0.08,
      exposure: Math.min(1, risky / 8),
      blind_spots: hiddenShare * 0.7,
    };
    const evidence = {
      slow_response: [
        `Red earned ${pts(held)} of its ${pts(red)} points from machines left breached.`,
        lens.length
          ? `A breached machine stayed breached for ${r1(lens.reduce((a, b) => a + b, 0) / lens.length)} ticks on average (longest: ${Math.max(...lens)}).`
          : 'No machine has been breached yet.',
        `${breachedNow} ${breachedNow === 1 ? 'machine is' : 'machines are'} breached right now.`,
      ],
      exposure: [
        `${risky} open ${risky === 1 ? 'card with danger of 30% or more sits' : 'cards with danger of 30% or more sit'} on machines of value 2 or 3.`,
        `${st.score.breaches_in_top50} of ${breaches} ${breaches === 1 ? 'breach' : 'breaches'} came through top-50 priority cards.`,
      ],
      blind_spots: [
        `The attackers were hidden for ${Math.round(hiddenShare * 100)}% of the match so far.`,
        `${bkx.unwatchedBreaches} of ${breaches} ${breaches === 1 ? 'breach' : 'breaches'} hit machines nobody was monitoring.`,
        `An attacker was spotted on a monitored machine ${bkx.spotted} ${bkx.spotted === 1 ? 'time' : 'times'}.`,
      ],
    };
    let key = Object.keys(scores).reduce((a, b) => (scores[b] > scores[a] ? b : a));
    if (SPECIALISTS.includes(backup.role)) key = WEAKNESS_OF[backup.role];
    bk.weakness = { key, title: WEAKNESS_TITLE[key], evidence: evidence[key] };
    bk.recommended = SPECIALISTS.find((r) => WEAKNESS_OF[r] === key);
    bk.players = ['p1', 'p2'].map((id) => {
      const h = bkx.humans[id];
      const name = id === 'p1' ? 'Player 1' : 'Player 2';
      if (!h.acts) return { id, line: `${name} has not acted yet.` };
      return {
        id,
        line: `${name} patched ${h.patches} ${h.patches === 1 ? 'card' : 'cards'} worth ${pts(Number(h.worth.toFixed(2)))} and restored ${h.restores} ${h.restores === 1 ? 'machine' : 'machines'}.`,
      };
    });
  }

  // Coach's part of one tick: watch, analyse at the checkpoint, ask, then act on the answer.
  function backupStep(events) {
    if (!bk) return;
    const t = st.tick;
    if (bk.status === 'watching' && t >= bk.checkpoint_tick) {
      if (backup.reply === 'unavailable') {
        bk.status = 'unavailable';
        return;
      }
      analyse();
      bk.status = 'analysing';
      events.push({ kind: 'coach', text: `Coach: the team's weakness is ${bk.weakness.title.toLowerCase()}. ${bk.weakness.evidence[0]}` });
    } else if (bk.status === 'analysing') {
      bk.status = 'asked';
      bk.asked_tick = t;
      bk.expires_tick = t + bkx.wait;
      const others = bk.options.filter((o) => o !== bk.recommended).join(' or ');
      events.push({ kind: 'coach', text: `Coach recommends calling in ${cap(bk.recommended)}. Reply in BAND: yes, or name another (${others}), or no.` });
    } else if (bk.status === 'asked' && bk.chosen) {
      // Approved last tick: the specialist steps onto the board now and acts from the next tick.
      const role = bk.chosen;
      st.actors.push({
        id: 'blue4', name: cap(role), team: 'blue', kind: 'agent', role, node_id: BACKUP_SPAWN_NODE,
        action: 'WAIT', target_node: null, revealed: true, busy_ticks: 0,
        job: null, revealLeft: 0, scanned: new Set(), lastSeen: BACKUP_SPAWN_NODE,
        reinforcement: true, joinedAt: t,
      });
      bk.status = 'joined';
      bk.joined_tick = t;
      bk.actor_id = 'blue4';
      const how = bk.by && bk.by.startsWith('Coach') ? 'no reply in BAND, called in by Coach' : `approved by ${bk.by} via BAND`;
      events.push({ kind: 'backup', text: `${cap(role)} joined the Blue team (${how}).` });
    } else if (bk.status === 'asked') {
      const reply = backup.reply || 'yes';
      const silent = reply === 'none' || reply === 'standdown';
      if (!silent && t >= bk.asked_tick + bkx.replyAfter) {
        bk.by = 'Alex';
        if (reply === 'no') {
          bk.status = 'declined';
          events.push({ kind: 'backup', text: 'Backup declined.' });
        } else {
          bk.chosen = SPECIALISTS.includes(reply) ? reply : bk.recommended;
        }
      } else if (t >= bk.expires_tick) {
        bk.by = 'Coach (no reply)';
        if (reply === 'standdown') {
          bk.status = 'declined';
          events.push({ kind: 'backup', text: 'Backup declined.' });
        } else {
          bk.chosen = bk.recommended;
        }
      }
    }
  }

  // The specialist's move: one step per tick and the same actions as any blue agent.
  // Returns the sentence for its ledger row.
  function specialistTurn(a, node, { startJob, monitor, move }) {
    const dist = (n) => Math.abs((n.id % COLS) - (a.node_id % COLS)) + Math.abs(Math.floor(n.id / COLS) - Math.floor(a.node_id / COLS));
    const openCards = (n) => n.cards.filter((c) => c.patch_state === 'unpatched');
    const exposed = st.nodes
      .filter((n) => n.state === 'healthy' && openCards(n).length)
      .sort((x, y) => y.value - x.value || dist(x) - dist(y));
    const goTo = (n) => move(stepToward(a.node_id, n.id));

    if (a.role !== 'hardener' && node.state === 'compromised') {
      startJob('RESTORE', 3);
      return `${node.name} is breached and worth ${node.value}: restoring it stops Red's income here.`;
    }
    if (a.role === 'responder') {
      const bad = st.nodes.filter((n) => n.state === 'compromised').sort((x, y) => y.value - x.value || dist(x) - dist(y))[0];
      if (bad) {
        goTo(bad);
        return `${bad.name} is breached and worth ${bad.value}: going there to restore it.`;
      }
      const post = exposed[0];
      if (!post) return 'Nothing is breached and no card is open.';
      if (post.id !== node.id) {
        goTo(post);
        return `Nothing is breached: waiting on ${post.name}, the highest-value machine with open cards.`;
      }
      if (!node.monitored) {
        monitor();
        return `Nothing is breached: monitoring ${node.name} until something is.`;
      }
      return `Nothing is breached: standing by on ${node.name}.`;
    }
    if (a.role === 'hardener') {
      let best = null;
      for (const n of st.nodes) {
        if (n.state !== 'healthy') continue;
        for (const c of openCards(n)) {
          if (c.attempted) continue;
          const w = (n.value * c.score * (c.in_kev ? 1.5 : 1)) / (1 + 0.25 * dist(n));
          if (!best || w > best.w) best = { w, n, c };
        }
      }
      if (!best) return 'No open card is worth patching any more.';
      const worth = pts(Number((best.n.value * best.c.score).toFixed(2)));
      if (best.n.id !== node.id) {
        goTo(best.n);
        return `Heading for ${best.c.cve_id} on ${best.n.name}: worth ${worth} (machine value ${best.n.value} x danger ${pct(best.c.score)}).`;
      }
      startJob('PATCH', 2, best.c);
      return `Patching the open card worth the most: ${worth} (machine value ${node.value} x danger ${pct(best.c.score)}).`;
    }
    // Sentinel: keep the highest-value exposed machines monitored, in rotation.
    const round = exposed.slice(0, 3);
    const post = round.find((n) => !n.monitored) || round[0];
    if (!post) return 'No machine with open cards is left to watch.';
    if (post.id !== node.id) {
      goTo(post);
      return `${post.name} (value ${post.value}) is not monitored: going there.`;
    }
    if (!node.monitored) {
      monitor();
      return `Monitoring ${node.name} (value ${node.value}): halves the breach chance here and can reveal an attacker.`;
    }
    return `${node.name} is already monitored: holding position.`;
  }

  // Every score change goes through here, so score, breakdown and score_deltas always agree.
  function addScore(team, amount, cause, node_id, actor_id = null, cve_id = null) {
    if (!amount) return;
    st.score[team] += amount;
    st.breakdown[team][cause] += amount;
    deltas.push({ team, amount: Number(amount.toFixed(4)), cause, node_id, actor_id, cve_id });
  }

  function say(a, action, outcome, ok, text) {
    if (a.kind === 'human') feedback.push({ player: a.id === 'p1' ? 1 : 2, action, outcome, ok, text });
  }

  function neighbours(id) {
    const x = id % COLS;
    const y = Math.floor(id / COLS);
    const out = [];
    if (y > 0) out.push(id - COLS);
    if (y < ROWS - 1) out.push(id + COLS);
    if (x > 0) out.push(id - 1);
    if (x < COLS - 1) out.push(id + 1);
    return out;
  }

  function stepToward(from, to) {
    const fx = from % COLS;
    const fy = Math.floor(from / COLS);
    const tx = to % COLS;
    const ty = Math.floor(to / COLS);
    if (fx !== tx && (fy === ty || rng() < 0.5)) return from + Math.sign(tx - fx);
    if (fy !== ty) return from + Math.sign(ty - fy) * COLS;
    return from;
  }

  function pBreach(node, card, red) {
    if (node.state === 'isolated') return 0;
    const s = clamp(card.score, 0, 1);
    const base = 1 - Math.pow(1 - s, 6);
    const kev = card.in_kev ? 0.15 : 0;
    const patchM = { unpatched: 1, patching: 0.4, patched: 0 }[card.patch_state] ?? 1;
    const defense = node.monitored ? 0.5 : 1;
    const recon = red.scanned.has(node.id) ? 0.1 : 0;
    return clamp((base + kev + recon) * patchM * defense, 0, 0.95);
  }

  function sortedCards(node) {
    return [...node.cards].sort((a, b) => {
      const ra = a.priority_rank ?? Infinity;
      const rb = b.priority_rank ?? Infinity;
      return ra !== rb ? ra - rb : b.score - a.score;
    });
  }

  const REASONS = {
    EXPLOIT: (c) => `Chose the card with the best breach chance on this machine (danger ${pct(c.score)}).`,
    SCAN: () => 'Scanning first raises the breach chance of the next attempt here.',
    PATCH: (c) => `Highest-priority unpatched card on this machine${c.priority_rank ? ` (priority #${c.priority_rank})` : ''}.`,
    RESTORE: () => 'This machine is breached and bleeding points every tick.',
    MONITOR: () => 'High-value machine with risky cards: halve the breach chance.',
    ISOLATE: () => 'Cutting the machine off while the patch backlog is cleared.',
    MOVE: () => 'Heading for the machine with the most risk left on it.',
    PIVOT: () => 'Using the breached machine as a stepping stone.',
    WAIT: () => 'Nothing useful to do this tick.',
  };

  function step() {
    st.tick += 1;
    deltas = [];
    feedback = [];
    const rows = [];
    const events = [];
    const lateReasons = [];
    const nodeById = (id) => st.nodes[id];

    // Node timers.
    for (const n of st.nodes) {
      if (n.monitorLeft > 0 && --n.monitorLeft === 0) n.monitored = false;
      if (n.isolateLeft > 0 && --n.isolateLeft === 0 && n.state === 'isolated') {
        n.state = n.wasBreached ? 'compromised' : 'healthy';
      }
    }

    // Scripted world advance: gate, drop, then a red campaign.
    if (st.tick === 8 && !world.advancing) startAdvance(events);
    if (advLanded) landAdvance(events);
    if (campaignAtTick !== null && st.tick >= campaignAtTick) {
      campaignAtTick = null;
      campaignLeft = 12;
      events.push({ kind: 'campaign', text: 'Red agents pivot onto this week\'s freshest high-danger cards.' });
    }
    if (campaignLeft > 0) campaignLeft -= 1;
    backupStep(events);

    for (const a of st.actors) {
      const node = nodeById(a.node_id);
      let action = 'WAIT';
      let card = null;
      let outcome = 'waiting';
      let p = null;
      let roll = null;
      let why = null; // a specialist's own sentence for this row
      a.target_node = null;
      if (a.revealLeft > 0 && --a.revealLeft === 0) a.revealed = false;

      const finishJob = () => {
        const job = a.job;
        a.job = null;
        if (job.action === 'PATCH' && job.card) {
          const c = job.card;
          c.patch_state = 'patched';
          if (!c.fate) c.fate = { fate: 'patched', by: a.name, tick: st.tick };
          outcome = 'patched';
          if (!c.attempted) {
            const gain = node.value * c.score;
            addScore('blue', gain, 'patch', node.id, a.id, c.cve_id);
            if (bkx && bkx.humans[a.id]) bkx.humans[a.id].worth += gain;
            say(a, 'PATCH', 'patched', true, `Patched ${c.cve_id}: +${pts(gain)} (machine value ${node.value} x danger ${pct(c.score)}).`);
          } else {
            say(a, 'PATCH', 'patched', true, `Patched ${c.cve_id}: Red had already attacked it, so no points. It can no longer be used.`);
          }
          if (bkx && bkx.humans[a.id]) bkx.humans[a.id].patches += 1;
          events.push({ kind: 'patched', text: `${a.name} patched ${c.cve_id} on ${node.name}.` });
        } else if (job.action === 'RESTORE') {
          if (node.state === 'compromised') {
            node.state = 'healthy';
            addScore('blue', 3, 'restore', node.id, a.id);
            if (bkx && bkx.humans[a.id]) bkx.humans[a.id].restores += 1;
            events.push({ kind: 'restored', text: `${a.name} restored ${node.name}.` });
            say(a, 'RESTORE', 'restored', true, `Restored ${node.name}: +3, and Red stops earning ${node.value} per tick here.`);
            outcome = 'restored';
          } else {
            say(a, 'RESTORE', 'noop', false, `${node.name} was no longer breached when the restore finished. No points.`);
            outcome = 'noop';
          }
        }
      };

      const startJob = (act, ticks, c) => {
        action = act;
        card = c || null;
        a.job = { action: act, left: ticks - 1, card: c || null };
        a.target_node = node.id;
        if (act === 'PATCH' && c) {
          c.patch_state = 'patching';
          outcome = 'patching';
          say(a, 'PATCH', 'patching', true, `Patching ${c.cve_id} (${ticks} ticks).`);
        }
        if (act === 'RESTORE') {
          outcome = 'restoring';
          say(a, 'RESTORE', 'restoring', true, `Restoring ${node.name} (${ticks} ticks).`);
        }
        if (a.job.left <= 0) finishJob();
      };

      const monitor = () => {
        action = 'MONITOR';
        a.target_node = node.id;
        node.monitored = true;
        node.monitorLeft = 3;
        outcome = 'monitoring';
        say(a, 'MONITOR', 'monitoring', true, `Monitoring ${node.name} for 3 ticks: breach chance here is halved and a failed attack can reveal the attacker.`);
      };

      const isolate = () => {
        action = 'ISOLATE';
        a.target_node = node.id;
        if (node.state === 'isolated') {
          outcome = 'invalid';
          say(a, 'ISOLATE', 'invalid', false, `${node.name} is already isolated.`);
          return;
        }
        node.wasBreached = node.state === 'compromised';
        node.state = 'isolated';
        node.isolateLeft = 5;
        outcome = 'isolated';
        say(a, 'ISOLATE', 'isolated', true, `Isolated ${node.name} for 5 ticks: nothing can breach it, but it costs 1 point per tick.`);
      };

      const move = (to) => {
        action = 'MOVE';
        a.target_node = to;
        if (to !== null && to !== a.node_id && neighbours(a.node_id).includes(to)) {
          a.node_id = to;
          outcome = 'moved';
          say(a, 'MOVE', 'moved', true, `Moved to ${nodeById(to).name}.`);
        } else {
          a.target_node = null;
          outcome = 'blocked';
          say(a, 'MOVE', 'blocked', false, "Can't move: that is the edge of the network.");
        }
      };

      if (a.job) {
        // The server keeps a busy player's newest key press; the mock simply drops it.
        if (a.kind === 'human') pendingActs[a.id === 'p1' ? 1 : 2] = null;
        action = a.job.action;
        card = a.job.card;
        a.target_node = node.id;
        a.job.left -= 1;
        outcome = 'working';
        if (a.job.left <= 0) finishJob();
      } else if (a.team === 'red') {
        const open = node.cards.filter((c) => c.patch_state !== 'patched');
        const canHit = node.state === 'healthy' && open.length > 0;
        const r = rng();
        const eager = campaignLeft > 0 ? 0.8 : 0.5;
        if (canHit && r < eager) {
          action = 'EXPLOIT';
          card = open.reduce((best, c) => (pBreach(node, c, a) > pBreach(node, best, a) ? c : best), open[0]);
          p = pBreach(node, card, a);
          roll = rng();
          card.attempted = true;
          if (roll < p) {
            node.state = 'compromised';
            outcome = 'breach';
            card.fate = { fate: 'breached', by: a.name, tick: st.tick };
            st.score.breaches_total += 1;
            if (bkx && !node.monitored) bkx.unwatchedBreaches += 1;
            if (card.priority_rank !== null && card.priority_rank <= 50) st.score.breaches_in_top50 += 1;
            if (!node.breachedOnce) {
              node.breachedOnce = true;
              addScore('red', 5, 'first_breach', node.id, a.id, card.cve_id);
            }
            a.revealed = true;
            a.revealLeft = 3;
            events.push({ kind: 'breach', text: `${a.name} breached ${node.name} via ${card.cve_id}.` });
          } else {
            outcome = 'failed';
            if (node.monitored && rng() < 0.8) {
              a.revealed = true;
              a.revealLeft = 3;
              if (bkx) bkx.spotted += 1;
              events.push({ kind: 'intel', text: `${a.name} was spotted on monitored ${node.name}.` });
            }
          }
        } else if (canHit && !a.scanned.has(node.id) && r < eager + 0.2) {
          action = 'SCAN';
          a.scanned.add(node.id);
          outcome = 'scanned';
        } else {
          // Head for fresh, risky cards during a campaign; wander otherwise.
          let goal = null;
          if (campaignLeft > 0) {
            const fresh = st.nodes.filter((n) => n.state === 'healthy' && n.cards.some((c) => c.is_new && c.patch_state !== 'patched'));
            if (fresh.length) goal = pick(fresh).id;
          }
          const to = goal !== null && goal !== a.node_id ? stepToward(a.node_id, goal) : pick(neighbours(a.node_id));
          if (rng() < 0.12) {
            action = 'WAIT';
            outcome = 'waiting';
          } else if (node.state === 'compromised' && rng() < 0.3) {
            action = 'PIVOT';
            a.target_node = to;
            a.node_id = to;
            outcome = 'pivoted';
          } else {
            move(to);
          }
        }
      } else if (a.reinforcement) {
        // It steps onto the board on the tick it joins and acts from the next one.
        if (a.joinedAt === st.tick) why = 'Just joined the Blue team: getting its bearings.';
        else why = specialistTurn(a, node, { startJob, monitor, move });
      } else if (a.kind === 'agent') {
        const best = sortedCards(node).find((c) => c.patch_state === 'unpatched');
        const r = rng();
        if (node.state === 'compromised') {
          startJob('RESTORE', 3);
        } else if (best && node.state === 'healthy' && r < 0.65) {
          startJob('PATCH', 2, best);
        } else if (node.state === 'healthy' && !node.monitored && node.value >= 3 && r < 0.75) {
          monitor();
        } else {
          const bad = st.nodes.filter((n) => n.state === 'compromised');
          const risky = st.nodes.filter((n) => n.state === 'healthy' && n.cards.some((c) => c.patch_state === 'unpatched' && c.score > 0.1));
          const goalNode = a.role === 'hunter' && bad.length ? pick(bad) : risky.length ? pick(risky) : null;
          if (goalNode && goalNode.id !== a.node_id) move(stepToward(a.node_id, goalNode.id));
          else if (rng() < 0.6) move(pick(neighbours(a.node_id)));
        }
      } else {
        // Human: the latest key press for this tick, if any.
        const no = a.id === 'p1' ? 1 : 2;
        const act = pendingActs[no];
        pendingActs[no] = null;
        if (act && bkx) bkx.humans[a.id].acts += 1;
        if (act) {
          if (act.action === 'MOVE') {
            const delta = { N: -COLS, S: COLS, W: -1, E: 1 }[act.dir];
            const x = a.node_id % COLS;
            const okX = !(act.dir === 'W' && x === 0) && !(act.dir === 'E' && x === COLS - 1);
            const to = a.node_id + (delta || 0);
            move(delta && okX && to >= 0 && to < COLS * ROWS ? to : null);
          } else if (act.action === 'PATCH') {
            const list = sortedCards(node).filter((c) => c.patch_state === 'unpatched');
            const chosen = (act.cve && list.find((c) => c.cve_id === act.cve)) || list[0];
            if (chosen) startJob('PATCH', 2, chosen);
            else {
              action = 'PATCH';
              outcome = 'invalid';
              say(a, 'PATCH', 'invalid', false, 'Nothing left to patch on this machine.');
            }
          } else if (act.action === 'MONITOR') {
            monitor();
          } else if (act.action === 'ISOLATE') {
            isolate();
          } else if (act.action === 'RESTORE') {
            if (node.state === 'compromised') startJob('RESTORE', 3);
            else {
              action = 'RESTORE';
              outcome = 'invalid';
              say(a, 'RESTORE', 'invalid', false, 'Nothing to restore: this machine is not breached.');
            }
          }
        }
      }

      a.action = action;
      a.busy_ticks = a.job ? a.job.left : 0;
      if (a.team !== 'red' || a.revealed) a.lastSeen = a.node_id;

      ledgerSeq += 1;
      const ledger_id = `${st.match_id}-${st.tick}-${a.id}-${ledgerSeq}`;
      const reason = why || (REASONS[action] || REASONS.WAIT)(card || { score: 0 });
      const hiddenRed = a.team === 'red' && !a.revealed;
      const late = a.kind === 'agent' && action !== 'WAIT' && !hiddenRed && rng() < 0.6;
      if (late) lateReasons.push({ ledger_id, reason_text: reason });
      const row = {
        ledger_id,
        match_id: st.match_id,
        world_asof: world.asof_date,
        pipeline_run_id: world.pipeline_run_id || 'mock-run',
        model_version: world.model_version,
        tick: st.tick,
        ts: new Date().toISOString(),
        actor_id: a.id,
        team: a.team,
        kind: a.kind,
        action,
        node_id: a.node_id,
        cve_id: card ? card.cve_id : null,
        score_used: card ? card.score : null,
        score_source: world.score_source,
        p_breach: p,
        roll,
        outcome,
        policy: a.kind === 'human' ? 'human' : a.reinforcement ? a.role : 'rule',
        reason_text: late ? null : a.kind === 'human' ? 'Player input.' : reason,
        llm_model: late ? 'mock-llm' : null,
        llm_latency_ms: late ? 800 : null,
      };
      // Fog of war, as on the real server: an attacker nobody has seen leaves a blank row.
      rows.push(hiddenRed ? {
        ...row, action: 'UNSEEN', node_id: null, cve_id: null, score_used: null, p_breach: null, roll: null,
        outcome: 'undetected', policy: null, llm_model: null, llm_latency_ms: null, reason_text: 'Not seen by the defenders.',
      } : row);
    }

    // Per-tick scoring.
    for (const n of st.nodes) {
      if (n.state === 'compromised') addScore('red', n.value, 'held', n.id);
      if (n.state === 'isolated') addScore('blue', -1, 'isolate_cost', n.id);
    }

    trackBackup();
    if (bk && bk.status === 'joined' && !bk.before) bk.before = windowStats(0, bk.joined_tick);

    if (st.tick >= st.ticks_total) {
      st.phase = 'ended';
      if (bk && bk.status === 'joined') bk.after = windowStats(bk.joined_tick, st.tick);
      st.reveal = buildReveal();
      for (const a of st.actors) a.revealed = true;
      events.push({ kind: 'info', text: 'Match over.' });
      if (st.reveal.n_real) {
        events.push({ kind: 'info', text: `Reveal: ${st.reveal.n_real} of this board's ${st.reveal.n_cards} cards became known exploited within 91 days.` });
      }
      stopTimer();
    }

    emitState(rows, events);
    for (const r of lateReasons) {
      setTimeout(() => {
        if (!closed) emit({ t: 'reason', ledger_id: r.ledger_id, reason_text: r.reason_text });
      }, 500 + Math.floor(rng() * 1200));
    }
  }

  // "What really happened": which board cards "became known exploited" after this date (invented).
  function buildReveal() {
    const items = [];
    let nCards = 0;
    for (const n of st.nodes) {
      for (const c of n.cards) {
        nCards += 1;
        if (c.in_kev || c.score < 0.12 || (c.card_id * 7) % 10 >= 4) continue;
        const days = 5 + ((c.card_id * 13) % 80);
        const f = c.fate || { fate: 'open', by: null, tick: null };
        items.push({
          cve_id: c.cve_id, title: c.title, node_id: n.id, node_name: n.name,
          kev_date_added: addDays(world.asof_date, days), days_after_asof: days,
          priority_rank: c.priority_rank, score: c.score, score_source: world.score_source,
          in_kev_at_asof: false, fate: f.fate, by: f.by, tick: f.tick,
        });
      }
    }
    items.sort((a, b) => a.kev_date_added.localeCompare(b.kev_date_added));
    return { n_cards: nCards, n_real: items.length, horizon_days: 91, items };
  }

  // -------------------------------------------------------- world advance

  function advancePublic() {
    if (!adv) return null;
    const total = ADVANCE_ASSETS.length;
    const current = ADVANCE_ASSETS[Math.min(adv.done, total - 1)];
    const done = adv.done >= total;
    const stage = done ? 'done' : stageOf(current);
    return {
      stage,
      stage_index: done ? STAGES.length - 1 : STAGES.indexOf(stage),
      assets_done: Math.min(adv.done, total),
      assets_total: total,
      current: done ? null : current,
      seconds: Number(((performance.now() - adv.startedAt) / 1000).toFixed(1)),
      message: done ? 'Pipeline run finished.' : `Running ${current}`,
    };
  }

  function startAdvance(events) {
    if (world.advancing) return;
    world.advancing = true;
    advLanded = false;
    adv = { startedAt: performance.now(), done: 0 };
    events.push({ kind: 'info', text: 'Advance week: Bruin started the pipeline run for the next week.' });
    clearInterval(advTimer);
    // The real run takes about a minute; the mock walks one asset per beat.
    advTimer = setInterval(() => {
      if (closed || !adv) return;
      adv.done += 1;
      if (adv.done >= ADVANCE_ASSETS.length) {
        clearInterval(advTimer);
        advTimer = null;
        if (st.phase === 'running') {
          advLanded = true; // the next tick lands it
        } else {
          const ev = [];
          landAdvance(ev);
          emitState([], ev);
        }
      } else if (st.phase !== 'running') {
        emitState(); // no ticks outside a match: publish the progress ourselves
      }
    }, clamp(Math.round(tickMs / 4), 80, 500));
  }

  function landAdvance(events) {
    advLanded = false;
    adv = null;
    world.advancing = false;
    world.asof_date = addDays(world.asof_date, 7);
    const positives = 28 + Math.floor(rng() * 14);
    const epssHits = Math.floor(rng() * 3);
    const passed = rng() < 0.75;
    const modelHits = passed ? epssHits + 1 + Math.floor(rng() * 3) : Math.max(0, epssHits - Math.floor(rng() * 2));
    world.epss_hits_at_50 = epssHits;
    world.challenger_hits_at_50 = modelHits;
    world.epss_p_at_50 = epssHits / 50;
    world.challenger_p_at_50 = modelHits / 50;
    world.n_positives = positives;
    world.n_candidates = 172000 + Math.floor(rng() * 2000);
    world.n_new_cves_this_week = 1200 + Math.floor(rng() * 900);
    world.n_cves_visible += world.n_new_cves_this_week;
    world.pipeline_run_id = `mock_${world.asof_date.replace(/-/g, '_')}_13_24_43`;
    world.gate_passed = passed;
    world.score_source = passed ? 'model' : 'epss';
    world.model_version = passed ? `hgb-${world.asof_date}` : null;
    events.push({
      kind: 'gate',
      text: passed
        ? `Gate passed: our model put ${modelHits} of the ${positives} soon-to-be-exploited bugs in its top 50; the EPSS baseline put ${epssHits}. Our model (${world.model_version}) now sets the danger scores.`
        : `Gate blocked: our model ${modelHits}, EPSS baseline ${epssHits} (of ${positives}). EPSS stays.`,
    });
    if (st.phase === 'running') {
      for (const n of st.nodes) for (const c of n.cards) c.is_new = false;
      let dropped = 0;
      const targets = [...st.nodes].sort(() => rng() - 0.5).slice(0, 6);
      for (const n of targets) {
        n.cards.push(newCard(true));
        dropped += 1;
      }
      rerank();
      events.push({ kind: 'drop', text: `Patch Tuesday: ${dropped} new cards for week ending ${world.asof_date} dropped onto the network.` });
      campaignAtTick = st.tick + 2;
    }
  }

  // --------------------------------------------------------------- output

  function publicState(rows, events) {
    const counts = { healthy: 0, compromised: 0, isolated: 0 };
    let redRate = 0;
    for (const n of st.nodes) {
      counts[n.state] += 1;
      if (n.state === 'compromised') redRate += n.value;
    }
    const fog = st.phase === 'running';
    const round = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, Number(v.toFixed(2))]));
    return {
      t: 'state',
      match_id: st.match_id,
      phase: st.phase,
      tick: st.tick,
      ticks_total: st.ticks_total,
      world: { ...world, advance: advancePublic() },
      nodes: st.nodes.map((n) => ({
        id: n.id, x: n.x, y: n.y, name: n.name, value: n.value, state: n.state, monitored: n.monitored,
        cards: n.cards.map((c) => ({
          card_id: c.card_id, cve_id: c.cve_id, title: c.title, score: c.score, in_kev: c.in_kev,
          patch_state: c.patch_state, is_new: c.is_new, technique_id: c.technique_id, tactic: c.tactic,
          priority_rank: c.priority_rank,
        })),
      })),
      // With fog, an unrevealed attacker shows only where it was last seen, doing nothing.
      actors: st.actors.map((a) => (fog && a.team === 'red' && !a.revealed
        ? { id: a.id, name: a.name, team: a.team, kind: a.kind, role: a.role, node_id: a.lastSeen, action: 'WAIT', target_node: null, revealed: false, busy_ticks: 0 }
        : {
          id: a.id, name: a.name, team: a.team, kind: a.kind, role: a.role, node_id: a.node_id, action: a.action,
          target_node: a.target_node, revealed: a.revealed, busy_ticks: a.busy_ticks,
          ...(a.reinforcement ? { reinforcement: true } : {}),
        })),
      score: {
        red: Number(st.score.red.toFixed(2)),
        blue: Number(st.score.blue.toFixed(2)),
        healthy: counts.healthy,
        compromised: counts.compromised,
        isolated: counts.isolated,
        breaches_total: st.score.breaches_total,
        breaches_in_top50: st.score.breaches_in_top50,
        rate: { red: redRate, blue: -counts.isolated },
        breakdown: { blue: round(st.breakdown.blue), red: round(st.breakdown.red) },
      },
      score_deltas: deltas,
      player_feedback: feedback,
      reveal: st.phase === 'ended' ? st.reveal : null,
      backup: bk,
      ledger: rows,
      events,
    };
  }

  function emit(msg) {
    if (closed) return;
    // Round-trip through JSON so the client sees exactly what a socket would deliver.
    onMessage(JSON.parse(JSON.stringify(msg)));
  }

  // Deltas and feedback belong to one tick only: they go out once.
  function emitState(rows = [], events = []) {
    emit(publicState(rows, events));
    deltas = [];
    feedback = [];
  }

  function stopTimer() {
    clearInterval(timer);
    timer = null;
  }

  function start() {
    if (st.phase === 'running') return;
    if (st.phase === 'ended') newMatch();
    st.phase = 'running';
    for (const a of st.actors) if (a.team === 'red') a.revealed = false;
    emitState([], [{ kind: 'info', text: 'Match started.' }]);
    stopTimer();
    timer = setInterval(step, tickMs);
  }

  function reset() {
    stopTimer();
    newMatch();
    emitState([], [{ kind: 'info', text: 'Back to lobby.' }]);
  }

  function advance() {
    if (world.advancing) return;
    const events = [];
    startAdvance(events);
    if (st.phase !== 'running') emitState([], events);
  }

  function send(msg) {
    if (closed || !msg) return false;
    if (msg.t === 'act' && (msg.player === 1 || msg.player === 2)) {
      pendingActs[msg.player] = msg; // only the latest per tick is kept
      return true;
    }
    if (msg.t === 'control') {
      if (msg.cmd === 'start') start();
      else if (msg.cmd === 'reset') reset();
      else if (msg.cmd === 'advance') advance();
      return true;
    }
    return false;
  }

  newMatch();
  onStatus('mock', 'MOCK DATA');
  setTimeout(() => {
    emitState();
    if (autostart) setTimeout(start, 1200);
  }, 0);

  return {
    send,
    isOpen: () => !closed,
    close() {
      closed = true;
      stopTimer();
      clearInterval(advTimer);
    },
    mode: 'mock',
  };
}
