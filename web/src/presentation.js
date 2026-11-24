import * as THREE from 'three';
import { createCharacter } from './characters.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

// A passive observer of public state. No game state, inputs or network messages are changed here.
export function createPresentation() {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const prefs = { sound: true, voice: true };
  try { Object.assign(prefs, JSON.parse(localStorage.getItem('pt-audio') || '{}')); } catch { /* optional storage */ }
  let context, master, unlocked = false, lastState = null, lastEvent = '', lastMatch = null;
  let speech = null, speaking = false, hideTimer, lastSpoken = 0, activePriority = 0;
  const cooldown = new Map();
  const controls = document.createElement('div');
  controls.className = 'audio-controls';
  controls.innerHTML = '<button type="button" class="audio-btn" id="pt-sound" aria-label="Toggle game sound effects"></button><button type="button" class="audio-btn" id="pt-voice" aria-label="Toggle announcer voice"></button><button type="button" class="audio-btn audio-preview" title="Hear the robotic announcer" aria-label="Preview announcer voice">Test voice</button>';
  document.getElementById('controls').append(controls);
  const host = document.createElement('section');
  host.className = 'announcer';
  host.setAttribute('aria-label', 'ORION mission announcer');
  host.innerHTML = '<div class="announcer-portrait"><canvas aria-label="A silver robot wearing a dark tailored suit"></canvas><div class="announcer-id">ORION <span>MISSION CONTROL</span></div></div><div class="announcer-copy"><div class="announcer-channel"><i></i> INCOMING TRANSMISSION</div><strong class="announcer-title"></strong><p class="announcer-text" role="status" aria-live="polite"></p><div class="voice-wave" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div><button type="button" class="announcer-close" aria-label="Dismiss announcer">×</button>';
  document.body.append(host);
  host.inert = true;
  const title = host.querySelector('.announcer-title');
  const caption = host.querySelector('.announcer-text');
  const soundButton = controls.querySelector('#pt-sound');
  const voiceButton = controls.querySelector('#pt-voice');
  function refreshButtons() {
    soundButton.textContent = '♫ SFX';
    voiceButton.textContent = '◉ VOICE';
    soundButton.title = prefs.sound ? 'Mute game sound effects' : 'Enable game sound effects';
    voiceButton.title = prefs.voice ? 'Mute announcer voice' : 'Enable announcer voice';
    controls.querySelector('.audio-preview').textContent = '▶';
    soundButton.setAttribute('aria-pressed', String(prefs.sound));
    voiceButton.setAttribute('aria-pressed', String(prefs.voice));
    try { localStorage.setItem('pt-audio', JSON.stringify(prefs)); } catch { /* optional */ }
  }
  function unlock() {
    unlocked = true;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (Audio && !context) {
        context = new Audio(); master = context.createGain();
        master.gain.value = prefs.sound ? 0.24 : 0; master.connect(context.destination);
      }
      if (context && context.state === 'suspended') context.resume().then(() => { controls.dataset.audioState = context.state; }).catch(() => { controls.dataset.audioState = 'unavailable'; });
      else controls.dataset.audioState = context ? context.state : 'unavailable';
    } catch { /* Captions still work if audio is unavailable. */ }
  }
  document.addEventListener('pointerdown', unlock, { once: true });
  document.addEventListener('keydown', unlock, { once: true });
  soundButton.addEventListener('click', () => {
    unlock(); prefs.sound = !prefs.sound;
    if (master && context) master.gain.setTargetAtTime(prefs.sound ? 0.24 : 0, context.currentTime, 0.02);
    refreshButtons();
  });
  voiceButton.addEventListener('click', () => {
    unlock(); prefs.voice = !prefs.voice;
    if (!prefs.voice) stopVoice(); refreshButtons();
  });
  controls.querySelector('.audio-preview').addEventListener('click', () => {
    unlock(); prefs.voice = true; refreshButtons();
    announce('Systems online.', 'I am Orion. Your network is ready. Defenders, take your positions.', 'info', 5, true);
  });
  host.querySelector('.announcer-close').addEventListener('click', hide);
  refreshButtons();

  function note(freq, at, duration, type = 'sine', volume = 0.22, end) {
    if (!context || !master) return;
    const osc = context.createOscillator(), gain = context.createGain();
    const start = context.currentTime + at;
    osc.type = type; osc.frequency.setValueAtTime(freq, start);
    if (end) osc.frequency.exponentialRampToValueAtTime(end, start + duration);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(Math.max(0.001, volume), start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    osc.connect(gain); gain.connect(master); osc.start(start); osc.stop(start + duration + 0.03);
    osc.onended = () => { osc.disconnect(); gain.disconnect(); };
  }
  function effect(kind) {
    if (!unlocked || !prefs.sound || document.hidden) return;
    const now = performance.now();
    if (now - (cooldown.get(kind) || -10000) < (kind === 'patched' ? 700 : 350)) return;
    cooldown.set(kind, now);
    if (kind === 'breach' || kind === 'campaign' || kind === 'defeat') {
      note(135, 0, 0.65, 'sawtooth', 0.16, 48);
      note(270, 0.08, 0.18, 'triangle', 0.3, 180); note(270, 0.34, 0.2, 'triangle', 0.25, 180);
    } else if (kind === 'patched' || kind === 'restored') {
      [440, 660, kind === 'restored' ? 1100 : 880].forEach((f, i) => note(f, i * 0.065, 0.22, 'sine', 0.2));
    } else if (kind === 'monitor') {
      note(900, 0, 0.65, 'sine', 0.2, 450); note(125, 0, 0.3, 'triangle', 0.15);
    } else if (kind === 'attack') {
      note(180, 0, 0.16, 'sawtooth', 0.14, 380); note(70, 0.04, 0.2, 'triangle', 0.12);
    } else if (kind === 'scan') {
      note(700, 0, 0.22, 'sine', 0.12, 1100);
    } else if (kind === 'isolate') {
      note(420, 0, 0.4, 'triangle', 0.3, 70); note(65, 0.2, 0.6, 'sine', 0.3);
    } else if (kind === 'victory') {
      [196, 247, 294, 392, 494, 587].forEach((f, i) => note(f, i * 0.11, 0.9, 'triangle', 0.18));
      note(49, 0, 1.5, 'sine', 0.35);
    } else if (kind === 'patch') {
      note(330, 0, 0.1, 'triangle', 0.12); note(550, 0.1, 0.12, 'triangle', 0.12);
    } else if (kind === 'move') {
      note(200, 0, 0.06, 'sine', 0.08, 130);
    } else {
      [220, 330, 440].forEach((f, i) => note(f, i * 0.09, 0.3, 'triangle', 0.18));
    }
  }
  function stopVoice() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    speaking = false; host.classList.remove('is-speaking'); speech = null;
  }
  function say(text) {
    if (!unlocked || !prefs.voice || !window.speechSynthesis || !window.SpeechSynthesisUtterance || document.hidden) return;
    stopVoice();
    speech = new SpeechSynthesisUtterance(text);
    const utterance = speech;
    const voices = window.speechSynthesis.getVoices();
    const voice = voices.find((v) => /David|Mark|Guy|Daniel|George|James/i.test(v.name) && /^en/i.test(v.lang))
      || voices.find((v) => /^en/i.test(v.lang));
    if (voice) speech.voice = voice;
    speech.lang = 'en-US'; speech.pitch = 0.48; speech.rate = 0.9; speech.volume = 0.85;
    host.dataset.voiceStatus = 'queued';
    speech.onstart = () => { if (speech !== utterance) return; speaking = true; host.dataset.voiceStatus = 'speaking'; host.classList.add('is-speaking'); };
    speech.onend = () => { if (speech !== utterance) return; speaking = false; host.dataset.voiceStatus = 'finished'; host.classList.remove('is-speaking'); };
    speech.onerror = (e) => { if (speech !== utterance) return; speaking = false; host.dataset.voiceStatus = e.error || 'unavailable'; host.classList.remove('is-speaking'); };
    // A short low carrier gives transmissions a mechanical attack, without masking the words.
    if (prefs.sound) { note(62, 0, 0.7, 'triangle', 0.09); note(93, 0, 0.4, 'sine', 0.06); }
    window.speechSynthesis.speak(speech);
    lastSpoken = performance.now();
  }
  function hide() { host.classList.remove('is-visible'); host.inert = true; activePriority = 0; stopVoice(); clearTimeout(hideTimer); }
  function announce(heading, text, kind = 'info', priority = 1, force = false) {
    if (!force && host.classList.contains('is-visible') && priority < activePriority) return;
    host.inert = false; title.textContent = heading; caption.textContent = text;
    host.dataset.kind = kind; host.classList.add('is-visible'); activePriority = priority;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, kind === 'victory' || kind === 'defeat' ? 14000 : 8500);
    if (force || priority >= 3 || performance.now() - lastSpoken > 7000) say(text);
  }

  let hostRenderer, hostScene, hostCamera, avatar, hostEnvironment, portraitObserver;
  try {
    const canvas = host.querySelector('canvas');
    hostRenderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    hostRenderer.setSize(160, 230, false); hostRenderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
    hostRenderer.toneMapping = THREE.ACESFilmicToneMapping; hostRenderer.toneMappingExposure = 1.3;
    hostScene = new THREE.Scene(); hostCamera = new THREE.PerspectiveCamera(33, 160 / 230, 0.1, 20);
    const studio = new RoomEnvironment(), pmrem = new THREE.PMREMGenerator(hostRenderer);
    hostEnvironment = pmrem.fromScene(studio, 0.04).texture;
    hostScene.environment = hostEnvironment; hostScene.environmentIntensity = 0.8;
    studio.dispose(); pmrem.dispose();
    hostCamera.position.set(0.35, 1.5, 3.2); hostCamera.lookAt(0, 1.1, 0);
    hostScene.add(new THREE.HemisphereLight(0xcde4ff, 0x121b2c, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 4); key.position.set(2, 4, 4); hostScene.add(key);
    const rim = new THREE.DirectionalLight(0x40dccc, 4); rim.position.set(-2, 1, -2); hostScene.add(rim);
    avatar = createCharacter('announcer', 0x59e0d3); avatar.root.rotation.y = -0.08; hostScene.add(avatar.root);
    // Agent portraits are rendered once using this same context, then become plain images.
    // No additional renderer or animation loop is needed for the five sidebar portraits.
    const portraits = new Map();
    const roles = [['blue1', 0x3f8cff, 'Triage'], ['blue2', 0x8176e6, 'Patcher'], ['blue3', 0x38b6ff, 'Hunter'], ['red1', 0xff3b4e, 'Opportunist'], ['red2', 0xffa45e, 'Patient']];
    hostScene.remove(avatar.root); hostRenderer.setSize(96, 112, false);
    hostCamera.aspect = 96 / 112; hostCamera.position.set(0.28, 1.65, 1.65); hostCamera.lookAt(0, 1.55, 0); hostCamera.updateProjectionMatrix();
    for (const [id, tint, name] of roles) {
      const model = createCharacter(id, tint); model.root.rotation.y = -0.16;
      hostScene.add(model.root); hostRenderer.render(hostScene, hostCamera);
      portraits.set(id, { image: canvas.toDataURL('image/png'), name });
      hostScene.remove(model.root); model.dispose();
    }
    hostRenderer.setSize(160, 230, false); hostCamera.aspect = 160 / 230;
    hostCamera.position.set(0.35, 1.5, 3.2); hostCamera.lookAt(0, 1.1, 0); hostCamera.updateProjectionMatrix();
    hostScene.add(avatar.root);
    const addPortraits = () => {
      for (const card of document.querySelectorAll('.agent[data-actor]')) {
        const portrait = portraits.get(card.dataset.actor);
        if (!portrait || card.querySelector('.agent-portrait')) continue;
        const image = document.createElement('img'); image.className = 'agent-portrait';
        image.src = portrait.image; image.alt = portrait.name + ' robot portrait';
        card.prepend(image); card.classList.add('has-portrait');
      }
    };
    portraitObserver = new MutationObserver(addPortraits);
    portraitObserver.observe(document.getElementById('agent-cards'), { childList: true });
    addPortraits();
    hostRenderer.setAnimationLoop((ms) => {
      if (!host.classList.contains('is-visible') || document.hidden) return;
      avatar.update(reducedMotion ? 0 : ms / 1000, false, '', 'Idle', 0, speaking);
      hostRenderer.render(hostScene, hostCamera);
    });
  } catch { host.classList.add('portrait-fallback'); }

  function applyState(state) {
    if (!state) return;
    document.body.dataset.phase = state.phase || 'lobby';
    const newMatch = lastMatch !== state.match_id;
    if (newMatch) { cooldown.clear(); lastEvent = ''; hide(); lastMatch = state.match_id; }
    const phaseChanged = !lastState || lastState.phase !== state.phase || newMatch;
    if (phaseChanged && state.phase === 'running') {
      effect('start'); announce('Defenders, stand by.', 'Network online. Patch high priority threats. Restore breached servers. Your mission starts now.', 'info', 2, true);
    }
    if (phaseChanged && state.phase === 'ended') {
      const blue = Number(state.score?.blue) || 0, red = Number(state.score?.red) || 0;
      const kind = blue === red ? 'draw' : blue > red ? 'victory' : 'defeat';
      const heading = blue === red ? 'STALEMATE' : blue > red ? 'DEFENDERS VICTORIOUS' : 'ATTACKERS VICTORIOUS';
      const message = blue === red ? 'The match is a draw. Both teams finish level.' : blue > red
        ? 'Victory. The defenders hold the network. Outstanding work, operators.'
        : 'The attackers win. Review the breaches. Regroup, and reclaim the network.';
      document.body.dataset.verdict = kind;
      effect(kind); announce(heading, message, kind, 10, true);
    }
    const events = Array.isArray(state.events) ? state.events : [];
    const eventKey = state.match_id + '|' + state.tick + '|' + events.map((e) => e.kind + e.text).join('|');
    if (eventKey !== lastEvent && state.phase === 'running') {
      lastEvent = eventKey;
      const kinds = new Set(events.map((e) => e.kind));
      for (const kind of ['breach', 'campaign', 'drop', 'restored', 'patched', 'gate', 'intel']) if (kinds.has(kind)) effect(kind);
      const breach = events.find((e) => e.kind === 'breach');
      if (kinds.has('campaign')) announce('Coordinated attack.', 'Red team has launched a campaign. Defend the newly exposed servers.', 'campaign', 5);
      else if (breach) {
        const match = String(breach.text).match(/breached\s+([A-Z0-9-]+)/i);
        announce('Network breach.', (match ? match[1] : 'A server') + ' is compromised. Restore it to stop the attackers earning points.', 'breach', 3);
      } else if (kinds.has('drop')) announce('New threats incoming.', 'New vulnerability cards have arrived. Check your patch priorities.', 'drop', 2);
      else if (kinds.has('restored')) announce('Server recovered.', 'A compromised server is back under defender control. Keep the remaining weaknesses patched.', 'restored', 1);
      else if (kinds.has('gate')) announce('Risk intelligence updated.', state.world?.score_source === 'model'
        ? 'The new model passed evaluation. Its scores now set patch priorities.' : 'The baseline remains active. The new model did not pass evaluation.', 'gate', 2);
      const rows = Array.isArray(state.ledger) ? state.ledger : [];
      for (const row of rows) {
        if (row.action === 'UNSEEN') continue;
        if (row.action === 'EXPLOIT' && row.outcome === 'failed') effect('attack');
        if (row.action === 'SCAN' && row.outcome === 'scanned') effect('scan');
        if (row.outcome === 'isolated') effect('isolate');
        if (row.outcome === 'monitoring' || (row.action === 'MONITOR' && row.outcome === 'started')) effect('monitor');
        if (row.outcome === 'patching' || (row.action === 'PATCH' && row.outcome === 'started')) effect('patch');
        if (row.outcome === 'restoring' || (row.action === 'RESTORE' && row.outcome === 'started')) effect('patch');
        if (row.outcome === 'moved' && (row.actor_id === 'p1' || row.actor_id === 'p2')) effect('move');
      }
    }
    if (lastState && state.phase === 'running' && state.tick < state.ticks_total) {
      const left = state.ticks_total - state.tick, previousLeft = lastState.ticks_total - lastState.tick;
      if (left <= 15 && previousLeft > 15) { effect('campaign'); announce('Final countdown.', 'Final stretch. Recover breached servers and secure the network.', 'info', 4); }
    }
    lastState = state;
  }
  function dispose() {
    hide(); document.removeEventListener('pointerdown', unlock); document.removeEventListener('keydown', unlock);
    if (hostRenderer) { hostRenderer.setAnimationLoop(null); hostRenderer.dispose(); }
    if (hostEnvironment) hostEnvironment.dispose();
    if (avatar) avatar.dispose();
    if (portraitObserver) portraitObserver.disconnect();
    if (context) context.close().catch(() => {});
    controls.remove(); host.remove();
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopVoice(); });
  return { applyState, dispose };
}
