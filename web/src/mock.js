// Mock mode (?mock=1): a small local simulation that emits messages in the
// exact shapes of docs/CONTRACTS.md, so the client can be developed and
// shown without the game server. All CVE ids, vendors and titles here are
// invented placeholders, not real vulnerability data.

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

function addDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function createMock({ onMessage, onStatus, tickMs = 2000, ticksTotal = 150, autostart = true }) {
  let rng = mulberry32(20260322);
  const pick = (arr) => arr[Math.floor(rng() * arr.length)];
  let cardSeq = 0;
  let ledgerSeq = 0;
  let matchSeq = 0;
  let timer = null;
  let closed = false;
  const pendingActs = { 1: null, 2: null };

  const world = {
    asof_date: '2026-03-22',
    score_source: 'epss',
    model_version: null,
    challenger_p_at_50: null,
    epss_p_at_50: null,
    advancing: false,
  };
  let advanceAtTick = null; // tick at which a running advance lands
  let campaignAtTick = null;
  let campaignLeft = 0;
  let priorityPool = [];

  let st = null;

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
        breachedOnce: false,
        cards: Array.from({ length: count }, () => newCard(false)),
      });
    }
    const actor = (id, name, team, kind, role, node_id) => ({
      id, name, team, kind, role, node_id,
      action: 'WAIT', target_node: null, revealed: true, busy_ticks: 0,
      job: null, revealLeft: 0, scanned: new Set(),
    });
    st = {
      match_id: `m-mock-${matchSeq}`,
      phase: 'lobby',
      tick: 0,
      ticks_total: ticksTotal,
      nodes,
      actors: [
        actor('red1', 'Opportunist', 'red', 'agent', 'opportunist', 4),
        actor('red2', 'Stalker', 'red', 'agent', 'targeted', 15),
        actor('blue1', 'Triage', 'blue', 'agent', 'patcher', 7),
        actor('blue2', 'Sentinel', 'blue', 'agent', 'monitor', 11),
        actor('blue3', 'Medic', 'blue', 'agent', 'responder', 13),
        actor('p1', 'Player 1', 'blue', 'human', 'human', 6),
        actor('p2', 'Player 2', 'blue', 'human', 'human', 8),
      ],
      score: { red: 0, blue: 0, breaches_total: 0, breaches_in_top50: 0 },
    };
    advanceAtTick = null;
    campaignAtTick = null;
    campaignLeft = 0;
    rerank();
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
    EXPLOIT: (c) => `Chose the card with the best breach odds on this node (score ${(c.score * 100).toFixed(1)}%).`,
    SCAN: () => 'Scanning first raises the odds of the next attempt here.',
    PATCH: (c) => `Highest-priority unpatched card on this node${c.priority_rank ? ` (rank ${c.priority_rank})` : ''}.`,
    RESTORE: () => 'This node is compromised and bleeding points every tick.',
    MONITOR: () => 'High-value node with risky cards: halve the breach odds.',
    ISOLATE: () => 'Cutting the node off while the patch backlog is cleared.',
    MOVE: () => 'Heading for the node with the most risk left on it.',
    PIVOT: () => 'Using the compromised node as a stepping stone.',
    WAIT: () => 'Nothing useful to do this tick.',
  };

  function step() {
    st.tick += 1;
    const rows = [];
    const events = [];
    const lateReasons = [];
    const nodeById = (id) => st.nodes[id];

    // Node timers.
    for (const n of st.nodes) {
      if (n.monitorLeft > 0 && --n.monitorLeft === 0) n.monitored = false;
      if (n.isolateLeft > 0 && --n.isolateLeft === 0 && n.state === 'isolated') n.state = 'healthy';
    }

    // Scripted world advance: gate, drop, then a red campaign.
    if (st.tick === 8 && advanceAtTick === null && !world.advancing) startAdvance(events);
    if (advanceAtTick !== null && st.tick >= advanceAtTick) landAdvance(events);
    if (campaignAtTick !== null && st.tick >= campaignAtTick) {
      campaignAtTick = null;
      campaignLeft = 12;
      events.push({ kind: 'campaign', text: 'Red agents pivot onto this week\'s freshest high-score cards.' });
    }
    if (campaignLeft > 0) campaignLeft -= 1;

    for (const a of st.actors) {
      const node = nodeById(a.node_id);
      let action = 'WAIT';
      let card = null;
      let outcome = 'ok';
      let p = null;
      let roll = null;
      a.target_node = null;
      if (a.revealLeft > 0 && --a.revealLeft === 0) a.revealed = false;

      const finishJob = () => {
        const job = a.job;
        a.job = null;
        if (job.action === 'PATCH' && job.card) {
          job.card.patch_state = 'patched';
          if (!job.card.attempted) st.score.blue += node.value * job.card.score;
          outcome = 'patched';
          events.push({ kind: 'patched', text: `${a.name} patched ${job.card.cve_id} on ${node.name}.` });
        } else if (job.action === 'RESTORE') {
          if (node.state === 'compromised') {
            node.state = 'healthy';
            st.score.blue += 3;
            events.push({ kind: 'restored', text: `${a.name} restored ${node.name}.` });
          }
          outcome = 'restored';
        } else if (job.action === 'MONITOR') {
          outcome = 'monitoring';
        } else if (job.action === 'ISOLATE') {
          outcome = 'isolated';
        }
      };

      const startJob = (act, ticks, c) => {
        action = act;
        card = c || null;
        a.job = { action: act, left: ticks - 1, card: c || null };
        outcome = 'started';
        if (act === 'PATCH' && c) c.patch_state = 'patching';
        if (act === 'MONITOR') {
          node.monitored = true;
          node.monitorLeft = 8;
        }
        if (act === 'ISOLATE') {
          node.state = 'isolated';
          node.isolateLeft = 5;
        }
        if (a.job.left <= 0) finishJob();
      };

      const move = (to) => {
        action = 'MOVE';
        a.target_node = to;
        if (to !== a.node_id && neighbours(a.node_id).includes(to)) {
          a.node_id = to;
          outcome = 'moved';
        } else {
          outcome = 'blocked';
        }
      };

      if (a.job) {
        // A busy player cannot act; drop any key press for this tick.
        if (a.kind === 'human') pendingActs[a.id === 'p1' ? 1 : 2] = null;
        action = a.job.action;
        card = a.job.card;
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
            st.score.breaches_total += 1;
            if (card.priority_rank !== null && card.priority_rank <= 50) st.score.breaches_in_top50 += 1;
            if (!node.breachedOnce) {
              node.breachedOnce = true;
              st.score.red += 5;
            }
            a.revealed = true;
            a.revealLeft = 3;
            events.push({ kind: 'breach', text: `${a.name} breached ${node.name} via ${card.cve_id}.` });
          } else {
            outcome = 'failed';
            if (node.monitored && rng() < 0.6) {
              a.revealed = true;
              a.revealLeft = 3;
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
            outcome = 'ok';
          } else if (node.state === 'compromised' && rng() < 0.3) {
            action = 'PIVOT';
            a.target_node = to;
            a.node_id = to;
            outcome = 'moved';
          } else {
            move(to);
          }
        }
      } else if (a.kind === 'agent') {
        const best = sortedCards(node).find((c) => c.patch_state === 'unpatched');
        const r = rng();
        if (node.state === 'compromised') {
          startJob('RESTORE', 3);
        } else if (best && node.state === 'healthy' && r < 0.65) {
          startJob('PATCH', 2, best);
        } else if (node.state === 'healthy' && !node.monitored && node.value >= 3 && r < 0.75) {
          startJob('MONITOR', 3);
        } else {
          const bad = st.nodes.filter((n) => n.state === 'compromised');
          const risky = st.nodes.filter((n) => n.state === 'healthy' && n.cards.some((c) => c.patch_state === 'unpatched' && c.score > 0.1));
          const goalNode = a.role === 'responder' && bad.length ? pick(bad) : risky.length ? pick(risky) : null;
          if (goalNode && goalNode.id !== a.node_id) move(stepToward(a.node_id, goalNode.id));
          else if (rng() < 0.6) move(pick(neighbours(a.node_id)));
        }
      } else {
        // Human: the latest key press for this tick, if any.
        const no = a.id === 'p1' ? 1 : 2;
        const act = pendingActs[no];
        pendingActs[no] = null;
        if (act) {
          if (act.action === 'MOVE') {
            const delta = { N: -COLS, S: COLS, W: -1, E: 1 }[act.dir];
            const x = a.node_id % COLS;
            const okX = !(act.dir === 'W' && x === 0) && !(act.dir === 'E' && x === COLS - 1);
            const to = a.node_id + (delta || 0);
            if (delta && okX && to >= 0 && to < COLS * ROWS) move(to);
            else {
              action = 'MOVE';
              outcome = 'blocked';
            }
          } else if (act.action === 'PATCH') {
            const list = sortedCards(node).filter((c) => c.patch_state === 'unpatched');
            const chosen = (act.cve && list.find((c) => c.cve_id === act.cve)) || list[0];
            if (chosen && node.state !== 'isolated') startJob('PATCH', 2, chosen);
            else {
              action = 'PATCH';
              outcome = 'nothing to patch';
            }
          } else if (act.action === 'MONITOR') {
            startJob('MONITOR', 3);
          } else if (act.action === 'ISOLATE') {
            startJob('ISOLATE', 5);
          } else if (act.action === 'RESTORE') {
            if (node.state === 'compromised') startJob('RESTORE', 3);
            else {
              action = 'RESTORE';
              outcome = 'not compromised';
            }
          }
        }
      }

      a.action = action;
      a.busy_ticks = a.job ? a.job.left : 0;

      ledgerSeq += 1;
      const ledger_id = `${st.match_id}-${st.tick}-${a.id}-${ledgerSeq}`;
      const reason = (REASONS[action] || REASONS.WAIT)(card || { score: 0 });
      const late = a.kind === 'agent' && action !== 'WAIT' && rng() < 0.6;
      if (late) lateReasons.push({ ledger_id, reason_text: reason });
      rows.push({
        ledger_id,
        match_id: st.match_id,
        world_asof: world.asof_date,
        pipeline_run_id: 'mock-run',
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
        policy: a.kind === 'human' ? 'human' : 'rule',
        reason_text: late ? null : a.kind === 'human' ? 'Player input.' : reason,
        llm_model: late ? 'mock-llm' : null,
        llm_latency_ms: late ? 800 : null,
      });
    }

    // Per-tick scoring.
    for (const n of st.nodes) {
      if (n.state === 'compromised') st.score.red += n.value;
      if (n.state === 'isolated') st.score.blue -= 1;
    }

    if (st.tick >= st.ticks_total) {
      st.phase = 'ended';
      events.push({ kind: 'info', text: 'Match over.' });
      stopTimer();
    }

    emitState(rows, events);
    for (const r of lateReasons) {
      setTimeout(() => {
        if (!closed) emit({ t: 'reason', ledger_id: r.ledger_id, reason_text: r.reason_text });
      }, 500 + Math.floor(rng() * 1200));
    }
  }

  function startAdvance(events) {
    if (world.advancing) return;
    world.advancing = true;
    advanceAtTick = st.phase === 'running' ? st.tick + 5 : null;
    events.push({ kind: 'info', text: 'Pipeline run started for the next world week.' });
  }

  function landAdvance(events) {
    advanceAtTick = null;
    world.advancing = false;
    world.asof_date = addDays(world.asof_date, 7);
    const epss = Number((0.14 + rng() * 0.06).toFixed(2));
    const passed = rng() < 0.75;
    const model = Number((passed ? epss + 0.04 + rng() * 0.08 : epss - rng() * 0.04).toFixed(2));
    world.epss_p_at_50 = epss;
    world.challenger_p_at_50 = model;
    world.score_source = passed ? 'model' : 'epss';
    world.model_version = passed ? `hgb-${world.asof_date}` : null;
    events.push({
      kind: 'gate',
      text: passed
        ? `Gate passed: challenger P@50 ${model.toFixed(2)} beats EPSS ${epss.toFixed(2)}. Model promoted.`
        : `Gate failed: challenger P@50 ${model.toFixed(2)} vs EPSS ${epss.toFixed(2)}. Staying on EPSS.`,
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

  function publicState(rows, events) {
    const counts = { healthy: 0, compromised: 0, isolated: 0 };
    for (const n of st.nodes) counts[n.state] += 1;
    return {
      t: 'state',
      match_id: st.match_id,
      phase: st.phase,
      tick: st.tick,
      ticks_total: st.ticks_total,
      world: { ...world },
      nodes: st.nodes.map((n) => ({
        id: n.id, x: n.x, y: n.y, name: n.name, value: n.value, state: n.state, monitored: n.monitored,
        cards: n.cards.map((c) => ({
          card_id: c.card_id, cve_id: c.cve_id, title: c.title, score: c.score, in_kev: c.in_kev,
          patch_state: c.patch_state, is_new: c.is_new, technique_id: c.technique_id, tactic: c.tactic,
          priority_rank: c.priority_rank,
        })),
      })),
      actors: st.actors.map((a) => ({
        id: a.id, name: a.name, team: a.team, kind: a.kind, role: a.role, node_id: a.node_id,
        action: a.action, target_node: a.target_node, revealed: a.revealed, busy_ticks: a.busy_ticks,
      })),
      score: {
        red: st.score.red,
        blue: Number(st.score.blue.toFixed(2)),
        healthy: counts.healthy,
        compromised: counts.compromised,
        isolated: counts.isolated,
        breaches_total: st.score.breaches_total,
        breaches_in_top50: st.score.breaches_in_top50,
      },
      ledger: rows,
      events,
    };
  }

  function emit(msg) {
    if (closed) return;
    // Round-trip through JSON so the client sees exactly what a socket would deliver.
    onMessage(JSON.parse(JSON.stringify(msg)));
  }

  function emitState(rows = [], events = []) {
    emit(publicState(rows, events));
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
    if (st.phase !== 'running') {
      emitState([], events);
      setTimeout(() => {
        if (closed || !world.advancing) return;
        const ev = [];
        landAdvance(ev);
        emitState([], ev);
      }, Math.max(1500, tickMs * 2));
    }
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
    },
    mode: 'mock',
  };
}
