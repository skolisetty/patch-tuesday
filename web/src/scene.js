// 3D view: a 5x4 grid of server racks and seven characters.
// Everything here is driven by the "state" message in docs/CONTRACTS.md.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';

const COLS = 5;
const ROWS = 4;
const SPX = 5.4; // spacing between columns
const SPZ = 5.8; // spacing between rows
const PAD_Y = 0.06;
const ROBOT_HEIGHT = 2.1;
const MODEL_URL = 'models/RobotExpressive.glb';
const MODEL_TIMEOUT_MS = 10000;

const BG = 0x070b12;
const STATE_COLOR = {
  healthy: 0x1fd18a,
  compromised: 0xff3048,
  isolated: 0xffb020,
};
const PIP_COLOR = {
  unpatched: 0xff5a3c,
  patching: 0xffc933,
  patched: 0x2bd98a,
};
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

// Where each actor stands on a node pad, so seven can share one node.
const SLOT = {
  p1: [-0.8, 1.55],
  p2: [0.8, 1.55],
  blue1: [-1.5, 0.8],
  blue2: [0.0, 0.75],
  blue3: [1.5, 0.8],
  red1: [-1.55, 2.3],
  red2: [1.55, 2.3],
};

const LOOPING = new Set(['Idle', 'Walking', 'Running', 'Dance']);

function nodeCenter(id) {
  const gx = ((id % COLS) + COLS) % COLS;
  const gy = Math.floor(id / COLS);
  return { x: (gx - (COLS - 1) / 2) * SPX, z: (gy - (ROWS - 1) / 2) * SPZ };
}

function slotPosition(nodeId, actorId, out) {
  const c = nodeCenter(Number.isFinite(nodeId) ? nodeId : 0);
  const s = SLOT[actorId] || [0, 1.5];
  return out.set(c.x + s[0], PAD_Y, c.z + s[1]);
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
    this.patchingPips = [];

    const c = nodeCenter(id);
    const g = (this.group = new THREE.Group());
    g.position.set(c.x, 0, c.z);

    const pad = new THREE.Mesh(shared.padGeo, shared.padMat);
    pad.position.set(0, PAD_Y / 2, 0.55);
    pad.receiveShadow = true;
    g.add(pad);

    this.rackMat = new THREE.MeshStandardMaterial({
      color: 0x1b2636,
      metalness: 0.55,
      roughness: 0.42,
      emissive: STATE_COLOR.healthy,
      emissiveIntensity: 0.2,
    });
    const rack = (this.rack = new THREE.Mesh(shared.rackGeo, this.rackMat));
    rack.position.set(0, PAD_Y + 0.9, -0.6);
    rack.castShadow = true;
    rack.receiveShadow = true;
    g.add(rack);

    // Glowing vent strips on the rack front and a rim on the pad.
    this.glowMat = new THREE.MeshBasicMaterial({ color: STATE_COLOR.healthy });
    for (let i = 0; i < 5; i++) {
      const strip = new THREE.Mesh(shared.stripGeo, this.glowMat);
      strip.position.set(0, PAD_Y + 0.32 + i * 0.3, 0.06);
      g.add(strip);
    }
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
    this.ring2.position.set(0, 2.25, -0.6);
    this.ring2.scale.setScalar(0.5);
    this.ring2.visible = false;
    g.add(this.ring2);

    // Isolation cage.
    this.cage = new THREE.LineSegments(shared.cageGeo, shared.cageMat);
    this.cage.position.set(0, PAD_Y + 1.05, -0.6);
    this.cage.visible = false;
    g.add(this.cage);

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

    // Card pips sit on top of the rack, toward the camera.
    this.pips = new THREE.Group();
    this.pips.position.set(0, PAD_Y + 1.8 + 0.24, -0.25);
    g.add(this.pips);

    this.label = makeTextSprite(512, 144, 3.7, true);
    this.label.sprite.position.set(0, 3.05, -0.75);
    g.add(this.label.sprite);
  }

  drawLabel(name, value, state) {
    const key = `${name}|${value}|${state}`;
    if (key === this.labelKey) return;
    this.labelKey = key;
    const { ctx, canvas, tex } = this.label;
    const col = hex(STATE_COLOR[state] || STATE_COLOR.healthy);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    roundRect(ctx, 6, 6, canvas.width - 12, canvas.height - 12, 20);
    ctx.fillStyle = 'rgba(6, 10, 18, 0.86)';
    ctx.fill();
    ctx.lineWidth = 6;
    ctx.strokeStyle = col;
    ctx.stroke();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#eaf2ff';
    fitText(ctx, name, canvas.width - 60, 62, 700, FONT);
    ctx.fillText(name, canvas.width / 2, 58);
    // Node value shown as filled blocks.
    const v = Math.max(0, Math.min(5, Math.round(Number(value) || 0)));
    const bw = 34;
    const gap = 12;
    const total = v * bw + (v - 1) * gap;
    ctx.fillStyle = col;
    for (let i = 0; i < v; i++) {
      ctx.fillRect(canvas.width / 2 - total / 2 + i * (bw + gap), 100, bw, 16);
    }
    tex.needsUpdate = true;
  }

  rebuildPips(cards) {
    const key = cards
      .map((c) => `${c.card_id}:${c.patch_state}:${c.is_new ? 1 : 0}:${c.in_kev ? 1 : 0}`)
      .join(',');
    if (key === this.pipKey) return;
    this.pipKey = key;
    this.pips.clear(); // geometries and materials are shared, nothing to dispose
    this.newMarkers = [];
    const n = cards.length;
    if (!n) return;
    const step = Math.min(0.42, 2.0 / n);
    const size = Math.min(1, step / 0.42);
    cards.forEach((c, i) => {
      const mat = this.shared.pipMat[c.patch_state] || this.shared.pipMat.unpatched;
      const pip = new THREE.Mesh(c.in_kev ? this.shared.pipKevGeo : this.shared.pipGeo, mat);
      pip.position.set((i - (n - 1) / 2) * step, 0, 0);
      pip.scale.setScalar(size);
      this.pips.add(pip);
      if (c.is_new) {
        const mk = new THREE.Mesh(this.shared.newGeo, this.shared.newMat);
        mk.position.set(pip.position.x, 0.42, 0);
        mk.rotation.x = Math.PI; // point down at the pip
        mk.scale.setScalar(size);
        mk.userData.baseY = 0.42;
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
    }
    this.monitored = !!n.monitored;
    this.ring.visible = this.monitored;
    this.ring2.visible = this.monitored;
    this.drawLabel(String(n.name ?? `NODE-${this.id}`), n.value, state);
    this.rebuildPips(Array.isArray(n.cards) ? n.cards : []);
  }

  update(now, t) {
    if (this.state === 'compromised') {
      this.rackMat.emissiveIntensity = 0.7 + 0.35 * Math.sin(t * 6 + this.id);
    } else if (this.state === 'isolated') {
      this.rackMat.emissiveIntensity = 0.16;
    } else {
      this.rackMat.emissiveIntensity = 0.2;
    }
    if (this.monitored) {
      const s = 1 + 0.04 * Math.sin(t * 3 + this.id);
      this.ring.scale.setScalar(s);
      this.ring2.rotation.z = t * 1.5;
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
    this.tint = ACTOR_TINT[actor.id] ?? TEAM_TINT[this.team];
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

    if (this.kind === 'human') {
      // Ground marker so each player can find their own character.
      this.markerMat = new THREE.MeshBasicMaterial({
        color: this.tint,
        transparent: true,
        opacity: 0.9,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      this.marker = new THREE.Mesh(new THREE.RingGeometry(0.5, 0.66, 40), this.markerMat);
      this.marker.rotation.x = -Math.PI / 2;
      this.marker.position.y = 0.03;
      this.group.add(this.marker);
    }

    this.tag = makeTextSprite(384, 128, 3.3, false);
    this.tag.sprite.position.set(0, ROBOT_HEIGHT + 0.85, 0);
    this.tag.sprite.renderOrder = 20;
    this.group.add(this.tag.sprite);
  }

  buildRobot(template) {
    const obj = SkeletonUtils.clone(template.scene);
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

  // Called once per state message.
  apply(a, ctx) {
    const { now, tickMs, tmp } = ctx;
    slotPosition(a.node_id, a.id, tmp);
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
    } else {
      this.targetYaw = this.restYaw;
    }
    // Turn the short way round.
    let d = this.targetYaw - this.yaw;
    d = Math.atan2(Math.sin(d), Math.cos(d));
    this.yaw += d * Math.min(1, dt * 10);
    const body = this.group.children[0];
    body.rotation.y = this.yaw;

    if (this.mixer) this.mixer.update(dt);
    if (this.capsule) {
      const bob = this.moving ? Math.abs(Math.sin(t * 11)) * 0.12 : Math.sin(t * 2 + this.yaw) * 0.015;
      const p = (now - this.pulse) / 450;
      const s = p >= 0 && p < 1 ? 1 + 0.28 * Math.sin(p * Math.PI) : 1;
      this.capsule.position.y = bob;
      this.capsule.scale.setScalar(s * (ROBOT_HEIGHT / 1.57));
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
export function createScene(canvas, container, { onNote } = {}) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  } catch (err) {
    if (onNote) onNote('3D view unavailable (WebGL could not start). The HUD still works.');
    return { ok: false, applyState() {}, setTickMs() {}, usingFallback: () => true };
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.setClearColor(BG, 1);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG);
  scene.fog = new THREE.Fog(BG, 60, 150);

  // Fixed elevated three-quarter camera. Yaw 0 keeps "N" pointing up the
  // screen so the movement keys match what the players see. ?yaw=35 gives
  // a classic isometric angle for spectating.
  const params = new URLSearchParams(location.search);
  const yaw = THREE.MathUtils.degToRad(Number(params.get('yaw')) || 0);
  const pitch = THREE.MathUtils.degToRad(Math.min(80, Math.max(25, Number(params.get('pitch')) || 46)));
  const camera = new THREE.PerspectiveCamera(30, 16 / 9, 0.5, 400);
  const camDir = new THREE.Vector3(
    Math.sin(yaw) * Math.cos(pitch),
    Math.sin(pitch),
    Math.cos(yaw) * Math.cos(pitch),
  );
  const camTarget = new THREE.Vector3(0, 0.9, 0.9);

  // Points the view must contain: the grid footprint plus label height.
  const halfX = ((COLS - 1) / 2) * SPX + 2.3;
  const zMin = -((ROWS - 1) / 2) * SPZ - 1.6;
  const zMax = ((ROWS - 1) / 2) * SPZ + 3.1;
  const fitPoints = [];
  for (const x of [-halfX, halfX]) {
    for (const z of [zMin, zMax]) {
      fitPoints.push(new THREE.Vector3(x, 0, z), new THREE.Vector3(x, 3.5, z));
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
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    fitCamera();
  }

  // Lights.
  scene.add(new THREE.HemisphereLight(0xbfd6ff, 0x0a0f18, 1.15));
  const sun = new THREE.DirectionalLight(0xffffff, 2.3);
  sun.position.set(-11, 26, 15);
  sun.target.position.set(0, 0, 0.5);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.left = -19;
  sun.shadow.camera.right = 19;
  sun.shadow.camera.top = 17;
  sun.shadow.camera.bottom = -17;
  sun.shadow.camera.near = 2;
  sun.shadow.camera.far = 80;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.03;
  scene.add(sun, sun.target);
  const fill = new THREE.DirectionalLight(0x4f8cff, 0.55);
  fill.position.set(14, 10, -12);
  scene.add(fill);

  // Floor.
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(400, 400),
    new THREE.MeshStandardMaterial({ color: 0x0c1320, roughness: 0.95, metalness: 0 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);
  const gridHelper = new THREE.GridHelper(400, 200, 0x16263c, 0x101b2c);
  gridHelper.position.y = 0.01;
  scene.add(gridHelper);

  // Network links between neighbouring nodes.
  {
    const pts = [];
    for (let y = 0; y < ROWS; y++) {
      for (let x = 0; x < COLS; x++) {
        const a = nodeCenter(y * COLS + x);
        if (x + 1 < COLS) {
          const b = nodeCenter(y * COLS + x + 1);
          pts.push(a.x, 0.02, a.z + 0.55, b.x, 0.02, b.z + 0.55);
        }
        if (y + 1 < ROWS) {
          const b = nodeCenter((y + 1) * COLS + x);
          pts.push(a.x, 0.02, a.z + 0.55, b.x, 0.02, b.z + 0.55);
        }
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    scene.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x1f5f8f })));
  }

  // Geometry and materials shared by all nodes.
  const padGeo = new THREE.BoxGeometry(4.3, PAD_Y, 4.3);
  const shared = {
    padGeo,
    padMat: new THREE.MeshStandardMaterial({ color: 0x121c2c, roughness: 0.8, metalness: 0.2 }),
    rimGeo: new THREE.EdgesGeometry(padGeo),
    rackGeo: new THREE.BoxGeometry(2.3, 1.8, 1.3),
    stripGeo: new THREE.BoxGeometry(1.9, 0.07, 0.03),
    ringGeo: new THREE.TorusGeometry(2.25, 0.07, 8, 56),
    ringMat: new THREE.MeshBasicMaterial({ color: 0x37e0ff }),
    cageGeo: new THREE.EdgesGeometry(new THREE.BoxGeometry(2.8, 2.25, 1.8)),
    cageMat: new THREE.LineBasicMaterial({ color: STATE_COLOR.isolated }),
    shockGeo: new THREE.RingGeometry(0.85, 1.0, 48),
    pipGeo: new THREE.SphereGeometry(0.16, 14, 10),
    pipKevGeo: new THREE.OctahedronGeometry(0.22),
    newGeo: new THREE.ConeGeometry(0.11, 0.24, 4),
    newMat: new THREE.MeshBasicMaterial({ color: 0xffffff }),
    pipMat: {
      unpatched: new THREE.MeshBasicMaterial({ color: PIP_COLOR.unpatched }),
      patching: new THREE.MeshBasicMaterial({ color: PIP_COLOR.patching }),
      patched: new THREE.MeshBasicMaterial({ color: PIP_COLOR.patched }),
    },
  };

  const nodes = [];
  for (let id = 0; id < COLS * ROWS; id++) {
    const nv = new NodeView(id, shared);
    nodes.push(nv);
    scene.add(nv.group);
    nv.drawLabel(`NODE-${String(id).padStart(2, '0')}`, 0, 'healthy');
  }

  // Characters.
  const actors = new Map();
  let template = null;
  let assetsSettled = false;
  let fallback = false;
  let pendingState = null;
  let tickMs = 2000;
  let lastPhase = null;
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
      const view = actors.get(a.id) || makeActor(a);
      const prev = view.prev;
      const movedNode = prev && prev.node_id !== a.node_id;
      view.apply(a, ctx);

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
        actors.delete(id);
      }
    }
    lastPhase = phase;
  }

  function applyState(state) {
    if (!state) return;
    try {
      applyNodes(state, performance.now());
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
    shared.pipMat.patching.color.setHex(Math.sin(t * 9) > 0 ? PIP_COLOR.patching : 0x6b5200);
    for (const nv of nodes) nv.update(now, t);
    for (const av of actors.values()) av.update(now, dt, t);
    renderer.render(scene, camera);
  });

  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();

  return {
    ok: true,
    applyState,
    setTickMs(ms) {
      if (ms > 100) tickMs = ms;
    },
    usingFallback: () => fallback,
    debug: { scene, camera, renderer, actors, nodes },
  };
}
