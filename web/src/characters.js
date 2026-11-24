import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

// Presentation only. Geometry never participates in targeting or game decisions.
const rounded = new Map();
function geometry(w, h, d, r = 0.05) {
  const key = [w, h, d, r].join('/');
  if (!rounded.has(key)) rounded.set(key, new RoundedBoxGeometry(w, h, d, 2, r));
  return rounded.get(key);
}

export function createCharacter(id, tint = 0x55e3db) {
  const root = new THREE.Group();
  const isHost = id === 'announcer';
  const red = id === 'red1' || id === 'red2';
  const human = id === 'p1' || id === 'p2';
  const materials = [];
  const material = (color, metalness, roughness, glow) => {
    const m = new THREE.MeshStandardMaterial({ color, metalness, roughness,
      emissive: glow ? color : 0x000000, emissiveIntensity: glow ? 1.8 : 0 });
    materials.push(m);
    return m;
  };
  const armor = material(isHost ? 0x263e5b : red ? 0xb45c67 : 0x7394aa, 0.4, 0.4);
  const steel = material(0xc3d0d9, 0.65, 0.3);
  const black = material(0x253951, 0.25, 0.45);
  const accent = material(tint, 0.32, 0.4);
  const light = material(tint, 0.25, 0.2, true);
  const shirt = material(isHost ? 0xf0f0e9 : 0xd5e1e9, 0.15, 0.55);
  const gold = material(0xbfa575, 0.85, 0.25);
  function box(parent, size, pos, mat, radius = 0.04) {
    const m = new THREE.Mesh(geometry(...size, radius), mat);
    m.position.set(...pos); m.castShadow = true; m.receiveShadow = true;
    parent.add(m); return m;
  }
  function sphere(parent, radius, pos, mat) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(radius, 14, 10), mat);
    m.position.set(...pos); m.castShadow = true; parent.add(m); return m;
  }
  const body = new THREE.Group(); root.add(body);
  const arms = [];
  const legs = [];
  let head, jaw;
  if (human) {
    // Players are people, not machines: soft cloth, skin, hair and a face. A hoodie in the
    // player's colour keeps them easy to tell apart from each other and from the robots.
    const second = id === 'p2';
    const cloth = (color, rough = 0.85) => material(color, 0, rough);
    const skin = cloth(second ? 0x9a6a48 : 0xf0c29e, 0.7);
    const hair = cloth(second ? 0x1b1410 : 0x5a3620, 0.9);
    const hoodie = cloth(tint);
    const jeans = cloth(0x2f4a7d);
    const white = cloth(0xf4f6f8, 0.6);
    const dark = cloth(0x1d2433, 0.5);
    const ball = (parent, radius, pos, mat, scale) => {
      const m = new THREE.Mesh(new THREE.SphereGeometry(radius, 24, 16), mat);
      m.position.set(...pos); if (scale) m.scale.set(...scale); m.castShadow = true; parent.add(m); return m;
    };
    box(body, [0.56, 0.62, 0.32], [0, 1.14, 0], hoodie, 0.13);          // hoodie
    box(body, [0.5, 0.2, 0.3], [0, 0.8, 0], jeans, 0.07);               // hips
    box(body, [0.3, 0.15, 0.03], [0, 0.98, 0.165], dark, 0.012);        // front pocket
    box(body, [0.02, 0.3, 0.02], [0, 1.25, 0.165], white, 0.006);       // zip
    box(body, [0.42, 0.2, 0.2], [0, 1.43, -0.13], hoodie, 0.09);        // hood, down on the back
    for (const x of [-0.05, 0.05]) box(body, [0.018, 0.16, 0.018], [x, 1.33, 0.17], white, 0.006);   // drawstrings
    ball(body, 0.1, [0, 1.5, 0], skin, [1, 1.2, 1]);                    // neck
    head = new THREE.Group(); head.position.y = 1.8; body.add(head);
    ball(head, 0.25, [0, 0, 0], skin, [0.95, 1.06, 0.98]);
    const cap = new THREE.Mesh(new THREE.SphereGeometry(0.262, 24, 12, 0, Math.PI * 2, 0, Math.PI * 0.52), hair);
    cap.position.set(0, 0.03, -0.022); cap.rotation.x = -0.32; cap.castShadow = true; head.add(cap);
    if (second) { ball(head, 0.12, [0, 0.25, -0.12], hair); box(head, [0.4, 0.3, 0.12], [0, -0.08, -0.2], hair, 0.055); }
    else box(head, [0.3, 0.07, 0.1], [0.03, 0.2, 0.17], hair, 0.03);    // fringe
    for (const x of [-0.088, 0.088]) {
      ball(head, 0.04, [x, 0.02, 0.212], white, [1, 1, 0.5]);
      ball(head, 0.022, [x, 0.02, 0.232], dark, [1, 1, 0.5]);
      box(head, [0.085, 0.018, 0.02], [x, 0.085, 0.225], hair, 0.006);  // eyebrow
      ball(head, 0.05, [x * 2.75, -0.01, 0], skin, [0.6, 1, 0.8]);      // ear
    }
    ball(head, 0.03, [0, -0.04, 0.245], skin);                          // nose
    jaw = box(head, [0.1, 0.02, 0.02], [0, -0.115, 0.218], cloth(0xa8483f), 0.008);   // smile
    // headset: these two are on comms with the agents
    box(head, [0.56, 0.035, 0.05], [0, 0.262, 0], dark, 0.015).scale.set(0.5, 1, 1);
    for (const x of [-0.25, 0.25]) box(head, [0.05, 0.14, 0.12], [x, 0, 0], dark, 0.02);
    box(head, [0.02, 0.02, 0.2], [0.25, -0.08, 0.1], dark, 0.006).rotation.x = 0.35;
    ball(head, 0.028, [0.25, -0.115, 0.2], light);
    for (const side of [-1, 1]) {
      box(head, [0.03, 0.27, 0.05], [side * 0.245, 0.13, 0], dark, 0.012);
      const arm = new THREE.Group(); arm.position.set(side * 0.36, 1.38, 0); body.add(arm);
      box(arm, [0.18, 0.5, 0.2], [0, -0.2, 0], hoodie, 0.08);
      box(arm, [0.185, 0.05, 0.205], [0, -0.43, 0], white, 0.02);      // cuff
      ball(arm, 0.078, [0, -0.52, 0], skin);
      arms.push(arm);
      const leg = new THREE.Group(); leg.position.set(side * 0.145, 0.76, 0); body.add(leg);
      box(leg, [0.21, 0.6, 0.23], [0, -0.3, 0], jeans, 0.08);
      box(leg, [0.22, 0.12, 0.38], [0, -0.69, 0.06], white, 0.05);      // trainers
      box(leg, [0.225, 0.035, 0.385], [0, -0.74, 0.06], hoodie, 0.012); // coloured sole
      legs.push(leg);
    }
  } else {
  const torso = box(body, [0.69, 0.73, 0.37], [0, 1.12, 0], armor, 0.09);
  box(body, [0.5, 0.18, 0.32], [0, 0.71, 0], black);
  box(body, [0.57, 0.07, 0.37], [0, 0.79, 0.015], steel, 0.02);
  box(body, [0.12, 0.09, 0.035], [0, 0.79, 0.21], accent, 0.01);
  const neck = sphere(body, 0.115, [0, 1.53, 0], black);
  head = new THREE.Group(); head.position.y = 1.78; body.add(head);
  box(head, [0.43, 0.48, 0.37], [0, 0, 0], steel, 0.09);
  box(head, [0.45, 0.27, 0.12], [0, 0.07, 0.17], black, 0.055);
  for (const x of [-0.105, 0.105]) box(head, [0.115, 0.035, 0.035], [x, 0.09, 0.244], light, 0.012);
  box(head, [0.06, 0.085, 0.08], [0, -0.055, 0.21], steel, 0.015);
  jaw = box(head, [0.29, 0.08, 0.05], [0, -0.165, 0.185], black, 0.025);
  jaw.userData.articulated = true;
  for (let x = -2; x <= 2; x++) box(head, [0.024, 0.035, 0.025], [x * 0.045, -0.165, 0.218], steel, 0.005);
  for (const x of [-0.245, 0.245]) {
    sphere(head, 0.075, [x, 0.015, 0], black);
    sphere(head, 0.04, [x * 1.1, 0.015, 0], accent);
  }
  for (const side of [-1, 1]) {
    const arm = new THREE.Group(); arm.position.set(side * 0.44, 1.36, 0); body.add(arm);
    sphere(arm, 0.13, [0, 0, 0], black);
    box(arm, [0.23, 0.35, 0.27], [0, -0.15, 0], armor, 0.06);
    box(arm, [0.24, 0.09, 0.28], [0, -0.015, 0], isHost ? armor : accent, 0.035);
    sphere(arm, 0.08, [0, -0.37, 0], steel);
    box(arm, [0.19, 0.28, 0.22], [0, -0.52, 0], armor, 0.055);
    box(arm, [0.18, 0.075, 0.24], [0, -0.65, 0], isHost ? shirt : steel, 0.025);
    box(arm, [0.15, 0.18, 0.16], [0, -0.76, 0], steel, 0.04);
    for (let finger = -1; finger <= 1; finger++) box(arm, [0.03, 0.065, 0.035], [finger * 0.045, -0.86, 0.04], steel, 0.009);
    arms.push(arm);
    const leg = new THREE.Group(); leg.position.set(side * 0.2, 0.72, 0); body.add(leg);
    box(leg, [0.25, 0.31, 0.29], [0, -0.15, 0], armor, 0.055);
    sphere(leg, 0.095, [0, -0.34, 0], black);
    box(leg, [0.24, 0.3, 0.26], [0, -0.5, 0], armor, 0.045);
    box(leg, [0.26, 0.14, 0.42], [0, -0.67, 0.07], black, 0.045);
    if (!isHost) box(leg, [0.18, 0.035, 0.03], [0, -0.43, 0.15], light, 0.01);
    legs.push(leg);
  }

  if (isHost) {
    // Tailored jacket: shirt front, folded lapels, knot, tie and a gold pocket square.
    box(body, [0.29, 0.52, 0.045], [0, 1.2, 0.205], shirt, 0.02);
    for (const side of [-1, 1]) {
      const lapel = box(body, [0.19, 0.55, 0.055], [side * 0.18, 1.22, 0.245], armor, 0.015);
      lapel.rotation.z = side * -0.28;
      box(body, [0.28, 0.24, 0.36], [side * 0.19, 0.78, 0.01], armor, 0.035);
    }
    const tie = box(body, [0.075, 0.34, 0.035], [0, 1.21, 0.275], accent, 0.012);
    box(body, [0.09, 0.08, 0.04], [0, 1.43, 0.275], accent, 0.015).rotation.z = Math.PI / 4;
    box(body, [0.12, 0.045, 0.035], [-0.23, 1.29, 0.245], gold, 0.01);
    for (const y of [0.89, 1.02]) sphere(body, 0.025, [0.055, y, 0.235], gold);
    box(head, [0.46, 0.08, 0.38], [0, 0.23, -0.015], black);
  } else {
    box(body, [0.4, 0.19, 0.07], [0, 1.19, 0.22], black);
    box(body, [0.25, 0.035, 0.025], [0, 1.19, 0.265], light, 0.012);
    box(body, [0.4, 0.45, 0.18], [0, 1.12, -0.26], armor, 0.06);
    if (id === 'blue1') {
      // Triage: white medic/analyst plates and a scanning antenna.
      torso.material = shirt;
      box(body, [0.1, 0.22, 0.025], [0, 1.2, 0.29], light);
      box(body, [0.23, 0.07, 0.025], [0, 1.2, 0.3], light);
      box(head, [0.035, 0.25, 0.035], [0.23, 0.29, 0], steel, 0.01);
      sphere(head, 0.045, [0.23, 0.43, 0], light);
    } else if (id === 'blue2') {
      // Patcher: engineer helmet and a forearm repair tool.
      box(head, [0.53, 0.18, 0.48], [0, 0.21, 0], accent, 0.06);
      box(head, [0.6, 0.045, 0.48], [0, 0.13, 0.04], black, 0.015);
      box(arms[1], [0.28, 0.3, 0.29], [0, -0.49, 0], accent, 0.04);
      box(arms[1], [0.05, 0.16, 0.1], [0, -0.75, 0.2], light);
    } else if (id === 'blue3') {
      // Hunter: armoured sentinel, shoulder radar and a forearm shield.
      for (const arm of arms) box(arm, [0.32, 0.14, 0.36], [0, 0.035, 0], steel, 0.05);
      box(arms[0], [0.08, 0.52, 0.39], [-0.14, -0.48, 0.07], accent, 0.04);
      sphere(body, 0.1, [0.43, 1.55, -0.15], light);
    } else if (id === 'red1') {
      // Opportunist: angular infiltrator, red visor, twin back fins.
      for (const side of [-1, 1]) {
        const fin = box(body, [0.09, 0.6, 0.22], [side * 0.28, 1.21, -0.34], accent);
        fin.rotation.z = side * -0.23;
      }
      box(head, [0.47, 0.06, 0.04], [0, 0.09, 0.26], light, 0.01);
    } else if (id === 'red2') {
      // Patient: strategic reconnaissance unit with a broad hood and monocular sensor.
      box(head, [0.55, 0.42, 0.4], [0, 0.08, -0.085], armor, 0.08);
      sphere(head, 0.075, [-0.11, 0.1, 0.27], light);
      box(body, [0.76, 0.22, 0.48], [0, 1.42, -0.04], black, 0.06);
    }
  }
  }

  // Merge static plates per material within each joint. Detailed models stay inexpensive
  // to draw, while head, jaw, shoulders and hips retain independent animation.
  function pack(part) {
    for (const child of [...part.children]) if (child.isGroup) pack(child);
    const batches = new Map();
    for (const child of part.children) if (child.isMesh && !child.userData.articulated) {
      if (!batches.has(child.material)) batches.set(child.material, []);
      batches.get(child.material).push(child);
    }
    for (const [mat, meshes] of batches) if (meshes.length > 1) {
      const pieces = meshes.map((m) => {
        m.updateMatrix();
        const copy = m.geometry.index ? m.geometry.toNonIndexed() : m.geometry.clone();
        return copy.applyMatrix4(m.matrix);
      });
      const geo = mergeGeometries(pieces, false);
      pieces.forEach((g) => g.dispose());
      if (!geo) continue;
      const merged = new THREE.Mesh(geo, mat); merged.castShadow = true; merged.receiveShadow = true;
      merged.userData.ownedGeometry = true;
      meshes.forEach((m) => part.remove(m)); part.add(merged);
    }
  }
  pack(root);
  return {
    root, materials,
    update(t, moving, action, base, pulse = 0, speaking = false) {
      const step = moving ? Math.sin(t * 10) * 0.48 : 0;
      body.position.y = moving ? Math.abs(Math.sin(t * 10)) * 0.045 : Math.sin(t * 1.5) * 0.012;
      legs[0].rotation.x = step; legs[1].rotation.x = -step;
      arms[0].rotation.x = -step * 0.65; arms[1].rotation.x = step * 0.65;
      head.rotation.y = Math.sin(t * 0.65) * (isHost ? 0.1 : 0.04);
      if (!moving && ['PATCH', 'RESTORE', 'EXPLOIT'].includes(action)) {
        arms[1].rotation.x = -0.85 + Math.sin(t * 7) * 0.14;
        arms[0].rotation.x = -0.4;
      }
      if (action === 'MONITOR' || action === 'SCAN') head.rotation.y = Math.sin(t * 2) * 0.3;
      if (base === 'Dance') { arms[0].rotation.z = -1.1; arms[1].rotation.z = 1.1; body.rotation.z = Math.sin(t * 4) * 0.08; }
      else { arms[0].rotation.z = -0.07; arms[1].rotation.z = 0.07; body.rotation.z = 0; }
      body.rotation.x = base === 'Death' ? 0.55 : 0;
      if (isHost) {
        arms[1].rotation.x = -0.18 + Math.sin(t * 1.4) * 0.08;
        jaw.position.y = -0.165 - (speaking ? Math.abs(Math.sin(t * 21)) * 0.04 : 0);
        light.emissiveIntensity = speaking ? 1.8 + Math.sin(t * 20) * 0.8 : 1.2;
      }
    },
    dispose() {
      root.traverse((o) => { if (o.userData.ownedGeometry && o.geometry) o.geometry.dispose(); });
      for (const m of materials) m.dispose();
    }
  };
}
