// 3D view: a 5x4 grid of server racks and seven characters, or eight once the Coach's
// specialist ("blue4", reinforcement: true) has joined the Blue team.
// Everything here is driven by the "state" message in docs/CONTRACTS.md.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { createCharacter } from './characters.js';
import { createEnvironment } from './environment.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

const COLS = 5;
const ROWS = 4;
const SPX = 6.8; // spacing between columns
const SPZ = 8.0; // spacing between rows
const PAD_Y = 0.06;
const ROBOT_HEIGHT = 2.1;
const MODEL_URL = 'models/RobotExpressive.glb';
const MODEL_TIMEOUT_MS = 10000;

const BG = 0x152129;
const STATE_COLOR = {
  healthy: 0x1fd18a,
  compromised: 0xff3048,
  isolated: 0xffb020,
};
const PIP_COLOR = {
  patching: 0xffc933,
  patched: 0x2bd98a,
};
// Unpatched cards are coloured and sized by danger (the card's predicted chance of being
// exploited). The HTML board legend uses the same three colours.
const DANGER = {
  low: { color: 0x8a97a8, size: 0.72 }, // under 5%
  medium: { color: 0xff9f43, size: 1.0 }, // 5% to 50%
  high: { color: 0xff3048, size: 1.3 }, // over 50%, or known exploited (KEV)
};
const FLOAT_COLOR = { blue: '#62b0ff', red: '#ff5468' };
const FLOAT_CAUSE = { patch: 'patch', restore: 'restore', isolate_cost: 'isolated', first_breach: 'breach' };
const FLOAT_MS = 1600;
const FLOAT_MAX = 14; // live score-change sprites; the oldest is reused beyond this
const LABEL_Y = 3.9;
const LABEL_Z = -0.75;
const LABEL_W = 4.3; // world width of the machine name label
const LABEL_H = (LABEL_W * 112) / 440;
const BREACH_W = 3.6;
const BREACH_H = (BREACH_W * 84) / 384;
const ACTOR_TINT = {
  red1: 0xff3b4e,
  red2: 0xff7a2f,
  blue1: 0x3f8cff,
  blue2: 0x6c7bff,
  blue3: 0x38b6ff,
  p1: 0x2ff3e0,
  p2: 0xffd23f,
};
const TEAM_TINT = { red: 0xff3b4e, blue: 0x3f8cff };
// The specialist called in as backup: white-green, unlike any other character.
const BACKUP_TINT = 0xc9ffd6;
const BACKUP_CSS = '#c9ffd6';
const ARRIVE_MS = 1900; // length of its arrival effect

const isBackupActor = (a) => !!a && (a.reinforcement === true || a.id === 'blue4');

// Where each actor stands on a node pad, so all eight can share one node.
const SLOT = {
  blue4: [0.0, 2.3], // front centre, between the two attackers' slots
  p1: [-0.8, 1.55],
  p2: [0.8, 1.55],
  blue1: [-1.5, 0.8],
  blue2: [0.0, 0.75],
  blue3: [1.5, 0.8],
  red1: [-1.55, 2.3],
  red2: [1.55, 2.3],
};

const HOP_HEIGHT = 0.7; // how high a character hops on each step between machines
const JUMP_HEIGHT = 1.1;
const JUMP_MS = 480;
const LOOPING = new Set(['Idle', 'Walking', 'Running', 'Dance']);

function nodeCenter(id) {
  const gx = ((id % COLS) + COLS) % COLS;
  const gy = Math.floor(id / COLS);
  const y = 0;
  return { x: (gx - (COLS - 1) / 2) * SPX, y, z: (gy - (ROWS - 1) / 2) * SPZ };
}

function combineBoxes(boxes) {
  const parts = boxes.map(([w, h, d, x, y, z]) => new THREE.BoxGeometry(w, h, d).translate(x, y, z));
  const merged = mergeGeometries(parts, false);
  for (const part of parts) part.dispose();
  return merged;
}

function slotPosition(nodeId, actor, out) {
  const c = nodeCenter(Number.isFinite(nodeId) ? nodeId : 0);
  const s = SLOT[actor.id] || (isBackupActor(actor) ? SLOT.blue4 : [0, 1.5]);
  return out.set(c.x + s[0], c.y + PAD_Y, c.z + s[1]);
}

function hex(n) {
  return '#' + n.toString(16).padStart(6, '0');
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// A sprite whose text is drawn on a 2D canvas (so no HTML is involved).
function makeTextSprite(w, h, worldW, depthTest) {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest, depthWrite: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(worldW, (worldW * h) / w, 1);
  return { sprite, canvas, ctx: canvas.getContext('2d'), tex, mat };
}

function fitText(ctx, text, maxW, size, weight, family) {
  let s = size;
  do {
    ctx.font = `${weight} ${s}px ${family}`;
    if (ctx.measureText(text).width <= maxW) break;
    s -= 2;
  } while (s > 12);
}

const FONT = '"Cascadia Mono", "Consolas", "SFMono-Regular", "Menlo", monospace';

function dangerLevel(card) {
  const s = Number(card.score);
  if (card.in_kev || s > 0.5) return 'high';
  if (s >= 0.05) return 'medium';
  return 'low'; // also when the score is missing
}

// "+1.4", "+3", "-1": one decimal at most, sign always shown.
function formatAmount(amount) {
  const r = Math.round(amount * 10) / 10;
  const body = Number.isInteger(r) ? String(Math.abs(r)) : Math.abs(r).toFixed(1);
  return (r < 0 ? '-' : '+') + body;
}

// One "Red +N / tick" texture per machine value, shared by every breached machine.
function breachMaterial(shared, value) {
  let mat = shared.breachMat.get(value);
  if (mat) return mat;
  const canvas = document.createElement('canvas');
  canvas.width = 384;
  canvas.height = 84;
  const ctx = canvas.getContext('2d');
  roundRect(ctx, 5, 5, canvas.width - 10, canvas.height - 10, 18);
  ctx.fillStyle = 'rgba(92, 8, 20, 0.94)';
  ctx.fill();
  ctx.lineWidth = 5;
  ctx.strokeStyle = hex(STATE_COLOR.compromised);
  ctx.stroke();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = '#ffe3e6';
  const text = `Red +${value} / tick`;
  fitText(ctx, text, canvas.width - 40, 46, 700, FONT);
  ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 2);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: true, depthWrite: false });
  shared.breachMat.set(value, mat);
  return mat;
}

// ---------------------------------------------------------------- nodes

class NodeView {
  constructor(id, shared) {
    this.id = id;
    this.shared = shared;
    this.state = null;
    this.monitored = false;
    this.labelKey = '';
    this.pipKey = '';
    this.pulseT0 = -1e9;
    this.newMarkers = [];
    this.pulsePips = [];
    this.breach = null; // "Red +N / tick" sprite, made the first time this machine is breached

    const c = nodeCenter(id);
    const g = (this.group = new THREE.Group());
    g.position.set(c.x, c.y, c.z);

    const pad = new THREE.Mesh(shared.padGeo, shared.padMat);
    pad.position.set(0, PAD_Y / 2, 0.55);
    pad.receiveShadow = true;
    pad.userData.nodeId = id; // click target: see pickNode()
    this.pad = pad;
    g.add(pad);

    this.rackMat = new THREE.MeshStandardMaterial({
      color: 0x70aebb,
      metalness: 0.25,
      roughness: 0.55,
      emissive: STATE_COLOR.healthy,
      emissiveIntensity: 0.2,
    });
    const rack = (this.rack = new THREE.Mesh(shared.rackGeo, this.rackMat));
    rack.position.set(0, PAD_Y + 1.3, -0.6);
    rack.castShadow = true;
    rack.receiveShadow = true;
    rack.userData.nodeId = id;
    g.add(rack);

    // Five recessed drive bays, metallic door rails and pull handles share just two meshes.
    rack.add(new THREE.Mesh(shared.faceGeo, shared.faceMat));
    rack.add(new THREE.Mesh(shared.trimGeo, shared.trimMat));

    // Glowing vent strips on the rack front and a rim on the pad.
    this.glowMat = new THREE.MeshBasicMaterial({ color: STATE_COLOR.healthy });
    const strip = new THREE.Mesh(shared.stripClusterGeo, this.glowMat);
    strip.position.copy(rack.position);
    g.add(strip);
    const rim = new THREE.LineSegments(shared.rimGeo, new THREE.LineBasicMaterial({ color: STATE_COLOR.healthy }));
    rim.position.copy(pad.position);
    this.rimMat = rim.material;
    g.add(rim);

    // Monitored ring.
    this.ring = new THREE.Mesh(shared.ringGeo, shared.ringMat);
    this.ring.rotation.x = Math.PI / 2;
    this.ring.position.set(0, 0.22, 0.45);
    this.ring.visible = false;
    g.add(this.ring);
    this.ring2 = new THREE.Mesh(shared.ringGeo, shared.ringMat);
    this.ring2.rotation.x = Math.PI / 2;
    this.ring2.position.set(0, 2.95, -0.6);
    this.ring2.scale.setScalar(0.5);
    this.ring2.visible = false;
    g.add(this.ring2);

    // Isolation cage.
    this.cage = new THREE.LineSegments(shared.cageGeo, shared.cageMat);
    this.cage.position.set(0, PAD_Y + 1.45, -0.6);
    this.cage.visible = false;
    g.add(this.cage);
    this.shield = new THREE.Mesh(shared.shieldGeo, shared.shieldMat);
    this.shield.position.copy(this.cage.position);
    this.shield.visible = false;
    g.add(this.shield);
    this.scanSheet = new THREE.Mesh(shared.scanGeo, shared.scanMat);
    this.scanSheet.rotation.x = -Math.PI / 2;
    this.scanSheet.position.set(0, 0.4, -0.6);
    this.scanSheet.visible = false;
    g.add(this.scanSheet);

    // Breach shock ring.
    this.shockMat = new THREE.MeshBasicMaterial({
      color: STATE_COLOR.compromised,
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    this.shock = new THREE.Mesh(shared.shockGeo, this.shockMat);
    this.shock.rotation.x = -Math.PI / 2;
    this.shock.position.set(0, 0.12, 0.3);
    this.shock.visible = false;
    g.add(this.shock);

    // Dark tray on the rack top, so the pips read against every rack colour (a red pip on a
    // breached, red rack would vanish otherwise).
    this.tray = new THREE.Mesh(shared.trayGeo, shared.trayMat);
    this.tray.position.set(0, PAD_Y + 2.6 + 0.06, -0.42);
    this.tray.visible = false;
    g.add(this.tray);

    // Card pips sit on top of the rack, toward the camera.
    this.pips = new THREE.Group();
    this.pips.position.set(0, PAD_Y + 2.6 + 0.24, -0.25);
    g.add(this.pips);

    this.label = makeTextSprite(440, 112, LABEL_W, true);
    this.label.sprite.position.set(0, LABEL_Y, LABEL_Z);
    g.add(this.label.sprite);
  }

  // Small red label sitting on top of the name label while the machine is compromised.
  setBreach(value) {
    if (!(value > 0)) {
      if (this.breach) this.breach.visible = false;
      return;
    }
    const mat = breachMaterial(this.shared, value);
    if (!this.breach) {
      const sp = (this.breach = new THREE.Sprite(mat));
      sp.scale.set(BREACH_W, BREACH_H, 1);
      // Same anchor as the name label, shifted up in screen space (sprite.center), so the two
      // never overlap whatever the camera pitch.
      sp.center.set(0.5, -(LABEL_H / 2 + 0.03) / BREACH_H);
      sp.position.set(0, LABEL_Y, LABEL_Z);
      this.group.add(sp);
    }
    if (this.breach.material !== mat) this.breach.material = mat;
    this.breach.visible = true;
  }

  drawLabel(name, value, state) {
    const key = `${name}|${value}|${state}`;
    if (key === this.labelKey) return;
    this.labelKey = key;
    const { ctx, canvas, tex } = this.label;
    const col = hex(STATE_COLOR[state] || STATE_COLOR.healthy);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    roundRect(ctx, 6, 6, canvas.width - 12, canvas.height - 12, 20);
    ctx.fillStyle = '#17374b';
    ctx.fill();
    ctx.lineWidth = 6;
    ctx.strokeStyle = col;
    ctx.stroke();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#eaf2ff';
    // Name, then the machine value as a "×3" chip in the state colour.
    const v = Math.round(Number(value) || 0);
    const cy = canvas.height / 2 + 2;
    const chipText = v > 0 ? `\u00d7${v}` : '';
    ctx.font = `700 54px ${FONT}`;
    const chipW = chipText ? Math.ceil(ctx.measureText(chipText).width) + 30 : 0;
    const gap = chipText ? 20 : 0;
    fitText(ctx, name, canvas.width - 44 - chipW - gap, 60, 700, FONT);
    const nameW = ctx.measureText(name).width;
    const x0 = (canvas.width - (nameW + gap + chipW)) / 2;
    ctx.fillText(name, x0 + nameW / 2, cy);
    if (chipText) {
      const cx = x0 + nameW + gap;
      roundRect(ctx, cx, cy - 36, chipW, 70, 12);
      ctx.fillStyle = col;
      ctx.fill();
      ctx.fillStyle = '#06101c';
      ctx.font = `700 54px ${FONT}`;
      ctx.fillText(chipText, cx + chipW / 2, cy);
    }
    tex.needsUpdate = true;
  }

  rebuildPips(cards) {
    const key = cards
      .map((c) => `${c.card_id}:${c.patch_state}:${c.is_new ? 1 : 0}:${c.in_kev ? 1 : 0}:${dangerLevel(c)}`)
      .join(',');
    if (key === this.pipKey) return;
    this.pipKey = key;
    this.pips.clear(); // geometries and materials are shared, nothing to dispose
    this.newMarkers = [];
    this.pulsePips = [];
    const n = cards.length;
    this.tray.visible = n > 0;
    if (!n) return;
    const step = Math.min(0.5, 2.2 / n);
    const size = Math.min(1, step / 0.5);
    cards.forEach((c, i) => {
      // Patching (blinking yellow) and patched (green) keep one size; an unpatched card
      // is coloured and sized by its danger.
      const level = c.patch_state === 'patching' || c.patch_state === 'patched' ? null : dangerLevel(c);
      const mat = level ? this.shared.dangerMat[level] : this.shared.pipMat[c.patch_state];
      const pip = new THREE.Mesh(c.in_kev ? this.shared.pipKevGeo : this.shared.pipGeo, mat);
      const base = size * (level ? DANGER[level].size : 0.9);
      pip.position.set((i - (n - 1) / 2) * step, 0, 0);
      pip.scale.setScalar(base);
      this.pips.add(pip);
      if (level === 'high') {
        pip.userData.base = base;
        pip.userData.phase = i * 1.3 + this.id;
        this.pulsePips.push(pip);
      }
      if (c.is_new) {
        const mk = new THREE.Mesh(this.shared.newGeo, this.shared.newMat);
        mk.position.set(pip.position.x, 0.46, 0);
        mk.rotation.x = Math.PI; // point down at the pip
        mk.scale.setScalar(size);
        mk.userData.baseY = 0.46;
        mk.userData.phase = i * 0.9;
        this.pips.add(mk);
        this.newMarkers.push(mk);
      }
    });
  }

  apply(n, now) {
    const state = STATE_COLOR[n.state] ? n.state : 'healthy';
    if (state !== this.state) {
      if (state === 'compromised' && this.state !== null) this.pulseT0 = now;
      this.state = state;
      const col = STATE_COLOR[state];
      this.rackMat.emissive.setHex(col);
      this.glowMat.color.setHex(col);
      this.rimMat.color.setHex(col);
      this.cage.visible = state === 'isolated';
      this.shield.visible = state === 'isolated';
    }
    this.monitored = !!n.monitored;
    this.ring.visible = this.monitored;
    this.ring2.visible = this.monitored;
    this.scanSheet.visible = this.monitored;
    this.drawLabel(String(n.name ?? `MACHINE ${String(this.id + 1).padStart(2, '0')}`), n.value, state);
    this.setBreach(state === 'compromised' ? Math.round(Number(n.value) || 0) : 0);
    this.rebuildPips(Array.isArray(n.cards) ? n.cards : []);
  }

  update(now, t) {
    if (this.state === 'compromised') {
      this.rackMat.emissiveIntensity = 0.25 + 0.13 * Math.sin(t * 4 + this.id);
    } else if (this.state === 'isolated') {
      this.rackMat.emissiveIntensity = 0.16;
    } else {
      this.rackMat.emissiveIntensity = 0.045;
    }
    if (this.monitored) {
      const s = 1 + 0.04 * Math.sin(t * 3 + this.id);
      this.ring.scale.setScalar(s);
      this.ring2.rotation.z = t * 1.5;
      this.scanSheet.position.y = 0.25 + ((t * 0.7 + this.id * 0.13) % 1) * 2.5;
    }
    for (const pip of this.pulsePips) {
      pip.scale.setScalar(pip.userData.base * (1 + 0.13 * Math.sin(t * 3.2 + pip.userData.phase)));
    }
    for (const mk of this.newMarkers) {
      mk.position.y = mk.userData.baseY + 0.08 * Math.sin(t * 4 + mk.userData.phase);
      mk.rotation.y = t * 2;
    }
    const k = (now - this.pulseT0) / 1300;
    if (k >= 0 && k < 1) {
      this.shock.visible = true;
      this.shock.scale.setScalar(0.4 + k * 3.2);
      this.shockMat.opacity = 0.85 * (1 - k);
      this.rack.scale.setScalar(1 + 0.1 * Math.sin(k * Math.PI));
    } else if (this.shock.visible) {
      this.shock.visible = false;
      this.rack.scale.setScalar(1);
    }
  }
}

// --------------------------------------------------------------- actors

class ActorView {
  constructor(actor, template) {
    this.id = actor.id;
    this.team = actor.team === 'red' ? 'red' : 'blue';
    this.kind = actor.kind;
    this.backup = isBackupActor(actor);
    this.tint = this.backup ? BACKUP_TINT : ACTOR_TINT[actor.id] ?? TEAM_TINT[this.team];
    this.fx = null; // arrival effect, while it runs
    this.grow = 1; // body scale during the arrival effect
    this.bodyScale = 1;
    this.group = new THREE.Group();
    this.materials = [];
    this.meshes = [];
    this.mixer = null;
    this.actions = {};
    this.active = null;
    this.activeName = null;
    this.base = 'Idle';
    this.emote = null;
    this.faint = null;
    this.inited = false;
    this.from = new THREE.Vector3();
    this.to = new THREE.Vector3();
    this.moveT0 = 0;
    this.moveDur = 1;
    this.moving = false;
    this.yaw = 0;
    this.targetYaw = 0;
    this.restYaw = 0;
    this.prev = null;
    this.tagKey = '';
    this.capsule = null;
    this.pulse = -1e9;

    if (template) this.buildRobot(template);
    else this.buildCapsule();

    if (this.kind === 'human' || this.backup) {
      // Ground marker so each player can find their own character (a thinner, wider ring
      // marks the specialist).
      this.markerMat = new THREE.MeshBasicMaterial({
        color: this.tint,
        transparent: true,
        opacity: this.backup ? 0.75 : 0.9,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      this.marker = new THREE.Mesh(this.backup ? new THREE.RingGeometry(0.6, 0.67, 40) : new THREE.RingGeometry(0.5, 0.66, 40), this.markerMat);
      this.marker.rotation.x = -Math.PI / 2;
      this.marker.position.y = 0.03;
      this.group.add(this.marker);
    }

    this.tag = makeTextSprite(384, 128, 3.3, false);
    this.tag.sprite.position.set(0, ROBOT_HEIGHT + 1.2, 0);
    this.tag.sprite.renderOrder = 20;
    this.group.add(this.tag.sprite);
  }

  buildRobot(template) {
    if (template.procedural) {
      this.character = createCharacter(this.id, this.tint);
      this.character.root.scale.setScalar(1.3);
      this.group.add(this.character.root);
      this.materials.push(...this.character.materials);
      this.character.root.traverse((o) => { if (o.isMesh) this.meshes.push(o); });
      return;
    }
    const obj = SkeletonUtils.clone(template.scene);
    this.bodyScale = template.scale;
    this.robot = obj;
    obj.scale.setScalar(template.scale);
    obj.position.y = template.offsetY;
    const tint = new THREE.Color(this.tint);
    const cloned = new Map(); // one private copy per source material
    const own = (m) => {
      if (!m) return m;
      if (!cloned.has(m)) {
        const c = m.clone();
        if (c.color) {
          if (m.name === 'Main' || !template.hasMain) c.color.copy(tint);
          else c.color.lerp(tint, 0.22);
        }
        if (c.emissive && (m.name === 'Main' || !template.hasMain)) {
          c.emissive.copy(tint);
          c.emissiveIntensity = 0.18;
        }
        cloned.set(m, c);
        this.materials.push(c);
      }
      return cloned.get(m);
    };
    obj.traverse((o) => {
      if (!o.isMesh) return;
      o.castShadow = true;
      o.frustumCulled = false; // skinned bounds are in bind pose
      o.material = Array.isArray(o.material) ? o.material.map(own) : own(o.material);
      this.meshes.push(o);
    });
    this.group.add(obj);

    this.mixer = new THREE.AnimationMixer(obj);
    for (const clip of template.animations) {
      const action = this.mixer.clipAction(clip);
      if (!LOOPING.has(clip.name)) {
        action.setLoop(THREE.LoopOnce, 1);
        action.clampWhenFinished = true;
      }
      this.actions[clip.name] = action;
    }
    this.mixer.addEventListener('finished', (e) => {
      if (this.emote && e.action === this.actions[this.emote]) {
        this.emote = null;
        if (this.base !== 'Death') this.fadeTo(this.base, 0.25);
      }
    });
    this.fadeTo('Idle', 0);
  }

  buildCapsule() {
    const mat = new THREE.MeshStandardMaterial({
      color: this.tint,
      roughness: 0.5,
      metalness: 0.1,
      emissive: this.tint,
      emissiveIntensity: 0.2,
    });
    const dark = new THREE.MeshStandardMaterial({ color: 0x0b1220, roughness: 0.4 });
    this.materials.push(mat, dark);
    const body = new THREE.Group();
    const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.34, 0.6, 6, 14), mat);
    torso.position.y = 0.64;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.27, 18, 14), mat);
    head.position.y = 1.3;
    const visor = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.1, 0.12), dark);
    visor.position.set(0, 1.32, 0.22);
    for (const m of [torso, head, visor]) {
      m.castShadow = true;
      body.add(m);
      this.meshes.push(m);
    }
    this.capsule = body;
    this.group.add(body);
  }

  fadeTo(name, duration) {
    const next = this.actions[name];
    if (!next) return false;
    const prev = this.active;
    if (prev === next) {
      if (LOOPING.has(name)) return true;
      next.reset().play(); // replay a one-shot clip
      return true;
    }
    this.active = next;
    this.activeName = name;
    if (!(duration > 0)) {
      if (prev) prev.stop();
      next.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).play();
      return true;
    }
    if (prev) prev.fadeOut(duration);
    next.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).fadeIn(duration).play();
    return true;
  }

  setBase(name) {
    if (this.base === name) return;
    this.base = name;
    if (!this.emote) this.fadeTo(name, 0.25);
  }

  playEmote(name, now) {
    this.pulse = now;
    if (!this.mixer) return;
    if (this.fadeTo(name, 0.15)) this.emote = name;
  }

  setFaint(faint) {
    if (faint === this.faint) return;
    this.faint = faint;
    for (const m of this.materials) {
      m.transparent = faint;
      m.opacity = faint ? 0.2 : 1;
      m.depthWrite = !faint;
      m.needsUpdate = true;
    }
    for (const o of this.meshes) o.castShadow = !faint;
    this.tag.mat.opacity = faint ? 0.38 : 1;
  }

  drawTag(name, action) {
    const act = action && action !== 'WAIT' ? action : '';
    const key = `${name}|${act}`;
    if (key === this.tagKey) return;
    this.tagKey = key;
    const { ctx, canvas, tex } = this.tag;
    const col = hex(this.tint);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    roundRect(ctx, 4, 4, canvas.width - 8, 72, 16);
    ctx.fillStyle = 'rgba(5, 8, 14, 0.82)';
    ctx.fill();
    ctx.lineWidth = 5;
    ctx.strokeStyle = col;
    ctx.stroke();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = col;
    fitText(ctx, name, canvas.width - 36, 50, 700, FONT);
    ctx.fillText(name, canvas.width / 2, 42);
    if (act) {
      ctx.fillStyle = '#ffffff';
      fitText(ctx, act, canvas.width - 36, 34, 700, FONT);
      ctx.fillText(act, canvas.width / 2, 104);
    }
    tex.needsUpdate = true;
  }

  // The specialist steps onto the board: a beam of light, a ring on the pad, and it grows in.
  arrive(now) {
    this.endArrival();
    const mat = (opacity) => new THREE.MeshBasicMaterial({
      color: BACKUP_TINT,
      transparent: true,
      opacity,
      side: THREE.DoubleSide,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.42, 5.4, 24, 1, true), mat(0.3));
    beam.position.y = 2.7;
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.85, 1.0, 48), mat(0.9));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.09;
    beam.renderOrder = 15;
    ring.renderOrder = 15;
    this.group.add(beam, ring);
    this.fx = { beam, ring, t0: now };
    this.grow = 0.02;
  }

  endArrival() {
    if (!this.fx) return;
    for (const m of [this.fx.beam, this.fx.ring]) {
      this.group.remove(m);
      m.geometry.dispose();
      m.material.dispose();
    }
    this.fx = null;
    this.grow = 1;
    if (this.robot) this.robot.scale.setScalar(this.bodyScale);
  }

  // Frees what only this view owns, when its actor leaves the match.
  dispose() {
    this.endArrival();
    if (this.marker) {
      this.marker.geometry.dispose();
      this.markerMat.dispose();
    }
    this.tag.tex.dispose();
    this.tag.mat.dispose();
  }

  // Called once per state message.
  apply(a, ctx) {
    const { now, tickMs, tmp } = ctx;
    slotPosition(a.node_id, a, tmp);
    if (!this.inited) {
      this.inited = true;
      this.group.position.copy(tmp);
      this.to.copy(tmp);
      this.from.copy(tmp);
    } else if (tmp.distanceToSquared(this.to) > 1e-4) {
      this.from.copy(this.group.position);
      this.to.copy(tmp);
      this.moveT0 = now;
      this.moveDur = Math.max(250, tickMs * 0.92);
      this.moving = true;
      this.targetYaw = Math.atan2(this.to.x - this.from.x, this.to.z - this.from.z);
      if (ctx.phase !== 'ended') {
        this.emote = null;
        this.base = 'Walking';
        this.fadeTo('Walking', 0.2);
      }
    }
    // An undetected red arrives fogged from the server (last-seen node, no action): draw it
    // faint with a "last seen" tag, so the humans see no more than the blue agents do.
    const unseen = this.team === 'red' && a.revealed === false;
    this.currentAction = unseen ? '' : a.action;
    this.setFaint(unseen);
    this.drawTag(String(a.name ?? a.id), unseen ? 'LAST SEEN' : a.action);
  }

  update(now, dt, t) {
    if (this.moving) {
      let k = (now - this.moveT0) / this.moveDur;
      if (k >= 1) {
        k = 1;
        this.moving = false;
        this.targetYaw = this.restYaw;
        if (this.base === 'Walking') this.setBase('Idle');
      }
      const e = k * k * (3 - 2 * k);
      this.group.position.lerpVectors(this.from, this.to, e);
      this.group.position.y += Math.sin(Math.PI * e) * (0.65 + Math.abs(this.to.y - this.from.y) * 0.45);
    } else {
      this.targetYaw = this.restYaw;
      if (this.inited) {
        const j = (now - (this.jumpT0 ?? -1e9)) / JUMP_MS;                     // Space: jump on the spot
        this.group.position.y = this.to.y + (j >= 0 && j < 1 ? Math.sin(j * Math.PI) * JUMP_HEIGHT : 0);
      }
    }
    // Turn the short way round.
    let d = this.targetYaw - this.yaw;
    d = Math.atan2(Math.sin(d), Math.cos(d));
    this.yaw += d * Math.min(1, dt * 10);
    const body = this.group.children[0];
    body.rotation.y = this.yaw;

    if (this.fx) {
      const k = (now - this.fx.t0) / ARRIVE_MS;
      if (k >= 1) this.endArrival();
      else {
        const g = Math.min(1, k / 0.35);
        this.grow = 0.02 + 0.98 * (1 - (1 - g) * (1 - g));
        const { beam, ring } = this.fx;
        beam.material.opacity = 0.3 * (1 - k) * (1 - k);
        beam.scale.set(1 - 0.55 * k, 1, 1 - 0.55 * k);
        ring.scale.setScalar(0.5 + 2.6 * k);
        ring.material.opacity = 0.9 * (1 - k);
        if (this.robot) this.robot.scale.setScalar(this.bodyScale * this.grow);
      }
    }

    if (this.mixer) this.mixer.update(dt);
    if (this.character) this.character.update(t, this.moving, this.currentAction, this.base, this.pulse);
    if (this.capsule) {
      const bob = this.moving ? Math.abs(Math.sin(t * 11)) * 0.12 : Math.sin(t * 2 + this.yaw) * 0.015;
      const p = (now - this.pulse) / 450;
      const s = p >= 0 && p < 1 ? 1 + 0.28 * Math.sin(p * Math.PI) : 1;
      this.capsule.position.y = bob;
      this.capsule.scale.setScalar(s * this.grow * (ROBOT_HEIGHT / 1.57));
    }
    if (this.marker) {
      const s = 1 + 0.12 * Math.sin(t * 4);
      this.marker.scale.set(s, s, 1);
    }
  }
}

// ---------------------------------------------------------------- scene

function loadTemplate() {
  // ?nomodel=1 skips the GLB so the capsule fallback can be checked.
  if (new URLSearchParams(location.search).get('nomodel') === '1') return Promise.resolve(null);
  if (new URLSearchParams(location.search).get('classicmodel') !== '1') return Promise.resolve({ procedural: true });
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      resolve(v);
    };
    const timer = setTimeout(() => finish(null), MODEL_TIMEOUT_MS);
    try {
      new GLTFLoader().load(
        MODEL_URL,
        (gltf) => {
          clearTimeout(timer);
          try {
            if (!gltf || !gltf.scene || !Array.isArray(gltf.animations) || !gltf.animations.length) {
              finish(null);
              return;
            }
            gltf.scene.updateMatrixWorld(true);
            const box = new THREE.Box3().setFromObject(gltf.scene);
            let h = box.max.y - box.min.y;
            let minY = box.min.y;
            if (!Number.isFinite(h) || h < 0.2 || h > 60) {
              h = 4.6;
              minY = 0;
            }
            const scale = ROBOT_HEIGHT / h;
            let hasMain = false;
            gltf.scene.traverse((o) => {
              const ms = o.isMesh ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
              for (const m of ms) if (m && m.name === 'Main') hasMain = true;
            });
            finish({ scene: gltf.scene, animations: gltf.animations, scale, offsetY: -minY * scale, hasMain });
          } catch (err) {
            finish(null);
          }
        },
        undefined,
        () => {
          clearTimeout(timer);
          finish(null);
        },
      );
    } catch (err) {
      clearTimeout(timer);
      finish(null);
    }
  });
}

/**
 * Returns { applyState(state), setTickMs(ms), ok, usingFallback() }.
 * If WebGL is not available it returns a stub with ok = false, so the HUD
 * keeps working.
 */
export function createScene(canvas, container, { onNote, onPick } = {}) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  } catch (err) {
    if (onNote) onNote('3D view unavailable (WebGL could not start). The HUD still works.');
    return { ok: false, applyState() {}, setTickMs() {}, setDestination() {}, usingFallback: () => true };
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.92;
  renderer.setClearColor(BG, 1);

  const scene = new THREE.Scene();
  const studio = new RoomEnvironment();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const environment = pmrem.fromScene(studio, 0.04).texture;
  scene.environment = environment;
  scene.environmentIntensity = 0.7;
  studio.dispose(); pmrem.dispose();
  scene.background = new THREE.Color(BG);
  // A clean studio backdrop keeps every row and its status labels readable.

  // Fixed elevated three-quarter camera. Yaw 0 keeps "N" pointing up the
  // screen so the movement keys match what the players see. ?yaw=35 gives
  // a classic isometric angle for spectating.
  const params = new URLSearchParams(location.search);
  const yaw = THREE.MathUtils.degToRad(Number(params.get('yaw')) || 0);
  const pitch = THREE.MathUtils.degToRad(Math.min(80, Math.max(25, Number(params.get('pitch')) || 47)));
  const camera = new THREE.PerspectiveCamera(30, 16 / 9, 0.5, 400);
  const camDir = new THREE.Vector3(
    Math.sin(yaw) * Math.cos(pitch),
    Math.sin(pitch),
    Math.cos(yaw) * Math.cos(pitch),
  );
  const camTarget = new THREE.Vector3(0, 1.25, 0.9);
  const composer = new EffectComposer(renderer);
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.06, 0.25, 1.8);
  const output = new OutputPass();
  composer.addPass(new RenderPass(scene, camera)); composer.addPass(bloom); composer.addPass(output);

  // Points the view must contain: the grid footprint plus label height.
  const halfX = ((COLS - 1) / 2) * SPX + 3.0;
  const zMin = -((ROWS - 1) / 2) * SPZ - 2.8;
  const zMax = ((ROWS - 1) / 2) * SPZ + 3.1;
  const fitPoints = [];
  for (const x of [-halfX, halfX]) {
    for (const z of [zMin, zMax]) {
      fitPoints.push(new THREE.Vector3(x, 0, z), new THREE.Vector3(x, 4.45, z));
    }
  }
  const proj = new THREE.Vector3();

  function fits(d) {
    camera.position.copy(camTarget).addScaledVector(camDir, d);
    camera.lookAt(camTarget);
    camera.updateMatrixWorld(true);
    for (const p of fitPoints) {
      proj.copy(p).project(camera);
      if (Math.abs(proj.x) > 0.97 || Math.abs(proj.y) > 0.95) return false;
    }
    return true;
  }

  function fitCamera() {
    let lo = 6;
    let hi = 260;
    for (let i = 0; i < 26; i++) {
      const mid = (lo + hi) / 2;
      if (fits(mid)) hi = mid;
      else lo = mid;
    }
    fits(hi);
  }

  function resize() {
    const w = Math.max(1, container.clientWidth);
    const h = Math.max(1, container.clientHeight);
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    fitCamera();
  }

  // Lights.
  scene.add(new THREE.HemisphereLight(0xf1f8ff, 0x91a6b1, 1.25));
  const sun = new THREE.DirectionalLight(0xfff4e6, 2.0);
  sun.position.set(-11, 26, 15);
  sun.target.position.set(0, 0, 0.5);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.camera.left = -19;
  sun.shadow.camera.right = 19;
  sun.shadow.camera.top = 17;
  sun.shadow.camera.bottom = -17;
  sun.shadow.camera.near = 2;
  sun.shadow.camera.far = 80;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.03;
  scene.add(sun, sun.target);
  const fill = new THREE.DirectionalLight(0xc4e4ff, 1.1);
  fill.position.set(8, 10, 22);
  scene.add(fill);
  const edge = new THREE.DirectionalLight(0xa3eee1, 0.75);
  edge.position.set(-15, 6, -16);
  scene.add(edge);

  // Floor.
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(400, 400),
    new THREE.MeshStandardMaterial({ color: 0x111a20, roughness: 0.9, metalness: 0.05 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);
  const gridHelper = new THREE.GridHelper(160, 80, 0x35565b, 0x263e45);
  gridHelper.position.y = 0.01;
  gridHelper.material.transparent = true;
  gridHelper.material.opacity = 0.2;
  gridHelper.material.depthWrite = false;
  scene.add(gridHelper);
  const ambience = createEnvironment(scene, nodeCenter);

  // Network links between neighbouring nodes.
  {
    const pts = [];
    for (let y = 0; y < ROWS; y++) {
      for (let x = 0; x < COLS; x++) {
        const a = nodeCenter(y * COLS + x);
        if (x + 1 < COLS) {
          const b = nodeCenter(y * COLS + x + 1);
          pts.push(a.x, a.y + 0.02, a.z + 0.55, b.x, b.y + 0.02, b.z + 0.55);
        }
        if (y + 1 < ROWS) {
          const b = nodeCenter((y + 1) * COLS + x);
          pts.push(a.x, a.y + 0.02, a.z + 0.55, b.x, b.y + 0.02, b.z + 0.55);
        }
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    scene.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x1f5f8f })));
  }

  // Geometry and materials shared by all nodes.
  const padGeo = new RoundedBoxGeometry(4.3, PAD_Y, 4.3, 2, 0.025);
  const shared = {
    padGeo,
    padMat: new THREE.MeshStandardMaterial({ color: 0x24343a, roughness: 0.75, metalness: 0.15 }),
    rimGeo: new THREE.EdgesGeometry(padGeo),
    rackGeo: new RoundedBoxGeometry(3.0, 2.6, 1.7, 2, 0.12),
    faceGeo: combineBoxes(Array.from({ length: 5 }, (_, i) => [2.5, 0.35, 0.05, 0, -0.9 + i * 0.43, 0.865])),
    faceMat: new THREE.MeshStandardMaterial({ color: 0x173b4a, roughness: 0.65, metalness: 0.15 }),
    trimGeo: combineBoxes([
      [0.09, 2.33, 0.07, -1.33, 0, 0.875], [0.09, 2.33, 0.07, 1.33, 0, 0.875],
      ...Array.from({ length: 5 }, (_, i) => [0.7, 0.035, 0.075, 0.12, -0.87 + i * 0.43, 0.925]),
      ...Array.from({ length: 8 }, (_, i) => [0.045, 0.055, 0.6, -0.8 + i * 0.22, 1.32, -0.04]),
    ]),
    trimMat: new THREE.MeshStandardMaterial({ color: 0xc0d0ce, metalness: 0.65, roughness: 0.35 }),
    stripClusterGeo: combineBoxes(Array.from({ length: 5 }, (_, i) => [0.14, 0.065, 0.035, -1.0, -0.89 + i * 0.43, 0.915])),
    shieldGeo: new THREE.BoxGeometry(3.45, 3.05, 2.15),
    shieldMat: new THREE.MeshBasicMaterial({ color: STATE_COLOR.isolated, transparent: true, opacity: 0.075, depthWrite: false }),
    scanGeo: new THREE.PlaneGeometry(2.25, 1.5),
    scanMat: new THREE.MeshBasicMaterial({ color: 0x58dced, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false }),
    stripGeo: new THREE.BoxGeometry(1.9, 0.07, 0.03),
    ringGeo: new THREE.TorusGeometry(2.25, 0.07, 8, 56),
    ringMat: new THREE.MeshBasicMaterial({ color: 0x37e0ff }),
    cageGeo: new THREE.EdgesGeometry(new THREE.BoxGeometry(3.45, 3.05, 2.15)),
    cageMat: new THREE.LineBasicMaterial({ color: STATE_COLOR.isolated }),
    shockGeo: new THREE.RingGeometry(0.85, 1.0, 48),
    pipGeo: new THREE.SphereGeometry(0.16, 14, 10),
    pipKevGeo: new THREE.OctahedronGeometry(0.22),
    trayGeo: new THREE.BoxGeometry(2.95, 0.2, 1.2),
    trayMat: new THREE.MeshBasicMaterial({ color: 0x0a111c }),
    newGeo: new THREE.ConeGeometry(0.11, 0.24, 4),
    newMat: new THREE.MeshBasicMaterial({ color: 0xffffff }),
    pipMat: {
      patching: new THREE.MeshBasicMaterial({ color: PIP_COLOR.patching }),
      patched: new THREE.MeshBasicMaterial({ color: PIP_COLOR.patched }),
    },
    dangerMat: {
      low: new THREE.MeshBasicMaterial({ color: DANGER.low.color }),
      medium: new THREE.MeshBasicMaterial({ color: DANGER.medium.color }),
      high: new THREE.MeshBasicMaterial({ color: DANGER.high.color }),
    },
    breachMat: new Map(), // machine value -> SpriteMaterial, filled on first use
  };

  const nodes = [];
  for (let id = 0; id < COLS * ROWS; id++) {
    const nv = new NodeView(id, shared);
    nodes.push(nv);
    scene.add(nv.group);
    nv.drawLabel(`MACHINE ${String(id + 1).padStart(2, '0')}`, 0, 'healthy');
  }

  // Characters.
  const actors = new Map();
  let template = null;
  let assetsSettled = false;
  let fallback = false;
  let pendingState = null;
  let tickMs = 2000;
  let lastPhase = null;
  let actorsShown = false; // true once a state's actors have been drawn: later arrivals are news
  let prevCards = new Map(); // card_id -> patch_state
  const tmp = new THREE.Vector3();

  loadTemplate().then((tpl) => {
    template = tpl;
    fallback = !tpl;
    assetsSettled = true;
    if (fallback && onNote) onNote('Character model did not load. Using simple capsule figures.');
    if (pendingState) {
      const s = pendingState;
      pendingState = null;
      applyActors(s);
    }
  });

  function makeActor(a) {
    let view;
    try {
      view = new ActorView(a, template);
    } catch (err) {
      view = new ActorView(a, null); // capsule if cloning the model fails
    }
    actors.set(a.id, view);
    scene.add(view.group);
    return view;
  }

  function applyNodes(state, now) {
    const list = Array.isArray(state.nodes) ? state.nodes : [];
    for (const n of list) {
      const nv = nodes[n.id];
      if (nv) nv.apply(n, now);
    }
  }

  function applyActors(state) {
    const now = performance.now();
    const list = Array.isArray(state.actors) ? state.actors : [];
    const nodeList = Array.isArray(state.nodes) ? state.nodes : [];
    const phase = state.phase;

    // Which nodes had a card finish patching since the last message.
    const patchedOn = new Set();
    const cardsNow = new Map();
    for (const n of nodeList) {
      for (const c of n.cards || []) {
        cardsNow.set(c.card_id, c.patch_state);
        const before = prevCards.get(c.card_id);
        if (c.patch_state === 'patched' && before && before !== 'patched') patchedOn.add(n.id);
      }
    }
    prevCards = cardsNow;
    const nodeById = new Map(nodeList.map((n) => [n.id, n]));
    const rowByActor = new Map((state.ledger || []).map((r) => [r.actor_id, r]));

    const seen = new Set();
    const ctx = { now, tickMs, tmp, phase };
    for (const a of list) {
      if (!a || a.id == null) continue;
      seen.add(a.id);
      const arriving = !actors.has(a.id) && actorsShown && phase === 'running' && isBackupActor(a);
      const view = actors.get(a.id) || makeActor(a);
      const prev = view.prev;
      const movedNode = prev && prev.node_id !== a.node_id;
      view.apply(a, ctx);
      if (arriving) {
        // The specialist joins mid-match: show where, and who.
        view.arrive(now);
        view.playEmote('Wave', now);
        if (Number.isInteger(a.node_id) && nodes[a.node_id]) {
          spawnFloat(a.node_id, `${String(a.name ?? 'Backup').slice(0, 14)} joined`, BACKUP_CSS, now, 0);
        }
      }

      // Face the rack while working on it, the camera otherwise.
      const working = ['PATCH', 'MONITOR', 'ISOLATE', 'RESTORE', 'EXPLOIT', 'SCAN'].includes(a.action);
      view.restYaw = working ? Math.PI : 0;

      if (phase === 'ended') {
        if (lastPhase !== 'ended') {
          const sc = state.score || {};
          const blueWon = (sc.blue ?? 0) >= (sc.red ?? 0);
          const won = view.team === 'blue' ? blueWon : !blueWon;
          view.emote = null;
          view.base = won ? 'Dance' : 'Death';
          view.fadeTo(view.base, 0.3);
        }
      } else {
        if (lastPhase === 'ended' || view.base === 'Dance' || view.base === 'Death') {
          view.emote = null;
          view.base = view.moving ? 'Walking' : 'Idle';
          view.fadeTo(view.base, 0.3);
        }
        if (!movedNode && prev && phase === 'running') {
          const fresh = a.action !== prev.action || !(prev.busy_ticks > 0);
          const row = rowByActor.get(a.id);
          const outcome = row && typeof row.outcome === 'string' ? row.outcome.toLowerCase() : '';
          const patchDone =
            view.team === 'blue' &&
            (a.action === 'PATCH' || prev.action === 'PATCH') &&
            (patchedOn.has(a.node_id) || (a.action === 'PATCH' && /patched|complete|done/.test(outcome)));
          let emote = null;
          if (patchDone) emote = 'ThumbsUp';
          else if (a.action === 'EXPLOIT') emote = 'Punch';
          else if (a.action === 'SCAN') emote = 'Yes';
          else if (a.action === 'MONITOR' && fresh) emote = 'Wave';
          else if (a.action === 'ISOLATE' && fresh) emote = 'No';
          else if (a.action === 'RESTORE' && fresh) emote = 'Jump';
          else if (a.action === 'INTEL') emote = 'Yes'; // Hunter's Telem search (TELEM_ENABLED=1 only)
          else if (a.action === 'RESTORE' && prev.action === 'RESTORE') {
            const n = nodeById.get(a.node_id);
            if (n && n.state === 'healthy' && prev.busy_ticks > 0 && !(a.busy_ticks > 0)) emote = 'ThumbsUp';
          }
          if (emote) view.playEmote(emote, now);
        }
      }
      view.prev = { node_id: a.node_id, action: a.action, busy_ticks: a.busy_ticks };
    }
    for (const [id, view] of actors) {
      if (!seen.has(id)) {
        scene.remove(view.group);
        view.dispose();
        actors.delete(id);
      }
    }
    actorsShown = true;
    lastPhase = phase;
  }

  // Floating score changes ("+1.4 patch") from state.score_deltas. A small pool of sprites is
  // reused, so a long match never allocates more than FLOAT_MAX canvases and textures.
  const floats = [];
  let lastDeltaTick = null;

  function spawnFloat(nodeId, text, color, now, stack) {
    let f = floats.find((x) => !x.active);
    if (!f && floats.length < FLOAT_MAX) {
      f = makeTextSprite(320, 76, 3.4, false);
      f.sprite.renderOrder = 30;
      f.sprite.visible = false;
      f.active = false;
      scene.add(f.sprite);
      floats.push(f);
    }
    if (!f) f = floats.reduce((a, b) => (a.t0 <= b.t0 ? a : b)); // all busy: take the oldest
    const { ctx, canvas, tex } = f;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    fitText(ctx, text, canvas.width - 44, 46, 700, FONT);
    const w = Math.min(canvas.width - 8, ctx.measureText(text).width + 44);
    roundRect(ctx, (canvas.width - w) / 2, 5, w, canvas.height - 10, 20);
    ctx.fillStyle = color; // solid team colour, so it cannot be mistaken for a name tag
    ctx.fill();
    ctx.lineWidth = 4;
    ctx.strokeStyle = 'rgba(4, 7, 13, 0.9)';
    ctx.stroke();
    ctx.fillStyle = '#04101c';
    ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 2);
    tex.needsUpdate = true;
    const c = nodeCenter(nodeId);
    f.x = c.x;
    f.z = c.z + LABEL_Z;
    f.y0 = c.y + LABEL_Y + 0.95 + stack * 0.95; // just above the name label, stacked when several land at once
    f.t0 = now + stack * 110;
    f.active = true;
    f.mat.opacity = 0;
    f.sprite.position.set(f.x, f.y0, f.z);
    f.sprite.visible = true;
  }

  function applyDeltas(state, now) {
    const list = Array.isArray(state.score_deltas) ? state.score_deltas : null; // absent on older servers
    if (!list || !list.length) return;
    if (state.tick != null && state.tick === lastDeltaTick) return; // same tick sent twice
    lastDeltaTick = state.tick ?? null;
    const perNode = new Map();
    for (const d of list) {
      if (!d || d.cause === 'held') continue; // the "Red +N / tick" label covers held income
      const amount = Number(d.amount);
      const nodeId = Number(d.node_id);
      if (!Number.isFinite(amount) || amount === 0) continue;
      if (d.node_id == null || !Number.isInteger(nodeId) || !nodes[nodeId]) continue;
      const cause = FLOAT_CAUSE[d.cause] ?? String(d.cause ?? '').replace(/_/g, ' ').slice(0, 14);
      const stack = perNode.get(nodeId) || 0;
      if (stack >= 3) continue;
      perNode.set(nodeId, stack + 1);
      const color = d.team === 'red' ? FLOAT_COLOR.red : FLOAT_COLOR.blue;
      spawnFloat(nodeId, `${formatAmount(amount)} ${cause}`.trim(), color, now, stack);
    }
  }

  function updateFloats(now) {
    for (const f of floats) {
      if (!f.active) continue;
      const k = (now - f.t0) / FLOAT_MS;
      if (k >= 1) {
        f.active = false;
        f.sprite.visible = false;
        continue;
      }
      if (k < 0) continue; // staggered start
      f.mat.opacity = k < 0.12 ? k / 0.12 : k > 0.6 ? (1 - k) / 0.4 : 1;
      f.sprite.position.y = f.y0 + 1.0 * (1 - (1 - k) * (1 - k));
    }
  }

  function applyState(state) {
    if (!state) return;
    try {
      applyNodes(state, performance.now());
      ambience.applyState(state);
      applyDeltas(state, performance.now());
      if (!assetsSettled) pendingState = state;
      else applyActors(state);
    } catch (err) {
      console.error('scene.applyState failed', err);
    }
  }

  // Render loop.
  let last = performance.now();
  const t0 = last;
  renderer.setAnimationLoop(() => {
    const now = performance.now();
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const t = (now - t0) / 1000;
    ambience.update(t);
    shared.pipMat.patching.color.setHex(Math.sin(t * 9) > 0 ? PIP_COLOR.patching : 0x6b5200);
    for (const nv of nodes) nv.update(now, t);
    updateFloats(now);
    for (const av of actors.values()) av.update(now, dt, t);
    composer.render(dt);
  });

  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();

  // ---- click a machine to walk there
  // A click (not a drag) on a machine's pad or rack reports its id through onPick(nodeId, event).
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const pickTargets = nodes.flatMap((nv) => [nv.pad, nv.rack]);
  let downAt = null;

  function pickNode(e) {
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(pickTargets, false)[0];
    return hit ? hit.object.userData.nodeId : null;
  }
  function onPointerDown(e) {
    downAt = e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
  }
  function onPointerUp(e) {
    const d = downAt;
    downAt = null;
    if (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 6) return;
    const id = pickNode(e);
    if (id !== null && id !== undefined && onPick) onPick(id, e);
  }
  let hoverAt = 0;
  function onPointerMove(e) {
    const now = performance.now();
    if (now - hoverAt < 60) return;
    hoverAt = now;
    canvas.style.cursor = pickNode(e) === null ? '' : 'pointer';
  }
  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointermove', onPointerMove);

  // A ring in the player's colour on the machine that player is walking to.
  const destRings = new Map(); // 'p1' | 'p2' -> mesh
  function setDestination(playerId, nodeId) {
    let ring = destRings.get(playerId);
    if (nodeId === null || nodeId === undefined || !nodes[nodeId]) {
      if (ring) ring.visible = false;
      return;
    }
    if (!ring) {
      const mat = new THREE.MeshBasicMaterial({
        color: ACTOR_TINT[playerId] ?? 0xffffff,
        transparent: true,
        opacity: 0.9,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      ring = new THREE.Mesh(new THREE.RingGeometry(1.75, 2.0, 4), mat);
      ring.rotation.x = -Math.PI / 2;
      ring.rotation.z = Math.PI / 4; // a square outline around the pad, unlike the round monitor ring
      scene.add(ring);
      destRings.set(playerId, ring);
    }
    const c = nodeCenter(nodeId);
    ring.position.set(c.x, c.y + 0.1 + (playerId === 'p2' ? 0.02 : 0), c.z + 0.55);
    ring.scale.setScalar(playerId === 'p2' ? 1.12 : 1);
    ring.visible = true;
  }

  return {
    ok: true,
    applyState,
    setDestination,
    // Space bar: that character jumps on the spot (ignored while it is walking).
    jump(actorId) {
      const view = actors.get(actorId);
      if (view && !view.moving) view.jumpT0 = performance.now();
    },
    setTickMs(ms) {
      if (ms > 100) tickMs = ms;
    },
    usingFallback: () => fallback,
    // Frees what this module allocates on demand (score-change sprites, breach labels).
    dispose() {
      renderer.setAnimationLoop(null);
      ambience.dispose();
      environment.dispose();
      bloom.dispose(); output.dispose(); composer.dispose();
      ro.disconnect();
      canvas.removeEventListener('pointerdown', onPointerDown);
      canvas.removeEventListener('pointerup', onPointerUp);
      canvas.removeEventListener('pointermove', onPointerMove);
      for (const ring of destRings.values()) {
        scene.remove(ring);
        ring.geometry.dispose();
        ring.material.dispose();
      }
      destRings.clear();
      for (const f of floats) {
        scene.remove(f.sprite);
        f.tex.dispose();
        f.mat.dispose();
      }
      floats.length = 0;
      for (const mat of shared.breachMat.values()) {
        if (mat.map) mat.map.dispose();
        mat.dispose();
      }
      shared.breachMat.clear();
    },
    debug: { scene, camera, renderer, actors, nodes, floats },
  };
}
