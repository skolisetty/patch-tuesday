import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

// A bounded decorative world and reusable event effects. All positions come from the public board.
export function createEnvironment(scene, nodeCenter) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const group = new THREE.Group(); scene.add(group);
  const dark = new THREE.MeshStandardMaterial({ color: 0x7290a3, metalness: 0.22, roughness: 0.6 });
  const steel = new THREE.MeshStandardMaterial({ color: 0x9bb6c6, metalness: 0.45, roughness: 0.4 });
  const glow = new THREE.MeshBasicMaterial({ color: 0x488c9e });
  function box(size, pos, mat, r = 0.06) {
    const m = new THREE.Mesh(new RoundedBoxGeometry(...size, 2, r), mat);
    m.position.set(...pos); m.receiveShadow = true; group.add(m); return m;
  }
  box([30.4, 0.65, 26.4], [0, -0.45, 0.6], dark, 0.16);
  box([30.8, 0.12, 26.8], [0, -0.8, 0.6], steel);
  for (const x of [-15.3, 15.3]) {
    box([0.035, 0.035, 25], [x, -0.06, 0.6], glow, 0.01);
    for (let i = 0; i < 12; i++) box([0.28, 0.03, 0.025], [x, -0.035, -10.8 + i * 2], glow, 0.008);
  }
  for (const z of [-12.7, 13.9]) box([30, 0.035, 0.035], [0, -0.06, z], glow, 0.01);
  // Dim data-center structures frame the playable platform; they never enter its footprint.
  for (let i = 0; i < 12; i++) {
    const side = i % 2 ? -1 : 1;
    const x = side * (18.5 + (i % 3) * 2.2), z = -17 + Math.floor(i / 2) * 3;
    const h = 0.8 + ((i * 7) % 9) * 0.2;
    box([1.3, h, 1.3], [x, h / 2 - 1, z], dark);
    box([0.04, h * 0.7, 0.04], [x - 0.5, h / 2 - 1, z + 0.68], glow, 0.01);
  }
  // Soft light pillars in the distant room, and a quiet field of suspended data motes.
  const dustPositions = new Float32Array(160 * 3);
  for (let i = 0; i < 160; i++) {
    dustPositions[i * 3] = Math.sin(i * 137.5) * 29;
    dustPositions[i * 3 + 1] = 1 + ((i * 71) % 120) / 16;
    dustPositions[i * 3 + 2] = Math.cos(i * 91.7) * 24;
  }
  const dustGeo = new THREE.BufferGeometry(); dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPositions, 3));
  const dust = new THREE.Points(dustGeo, new THREE.PointsMaterial({ color: 0x6295b7, size: 0.035, transparent: true, opacity: 0.45, depthWrite: false }));
  group.add(dust);
  const links = [];
  for (let y = 0; y < 4; y++) for (let x = 0; x < 5; x++) {
    const id = y * 5 + x;
    for (const next of [x < 4 ? id + 1 : -1, y < 3 ? id + 5 : -1]) if (next >= 0) links.push([nodeCenter(id), nodeCenter(next)]);
  }
  const packets = new THREE.InstancedMesh(new THREE.SphereGeometry(0.045, 6, 4), new THREE.MeshBasicMaterial({ color: 0x65d5d2 }), links.length);
  group.add(packets);
  const dummy = new THREE.Object3D();
  const sparkCount = 180;
  const sparks = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(0.055, 0), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85 }), sparkCount);
  sparks.instanceMatrix.setUsage(THREE.DynamicDrawUsage); sparks.frustumCulled = false;
  group.add(sparks);
  const pool = Array.from({ length: sparkCount }, () => ({ active: false }));
  const rings = [];
  let cursor = 0, previous = new Map(), lastMatch, lastTick, ended = false;
  const color = new THREE.Color();
  function burst(id, tint, time, count = 24) {
    if (reduced) return;
    const c = nodeCenter(id);
    for (let i = 0; i < count; i++) {
      const p = pool[cursor++ % sparkCount];
      const angle = i / count * Math.PI * 2;
      Object.assign(p, { active: true, x: c.x, z: c.z - 0.35, y: 1,
        vx: Math.cos(angle) * (0.5 + (i % 4) * 0.25), vz: Math.sin(angle) * (0.5 + (i % 4) * 0.25), vy: 1.3 + (i % 5) * 0.4, time });
      color.setHex(tint); sparks.setColorAt((cursor - 1) % sparkCount, color);
    }
    sparks.instanceColor.needsUpdate = true;
    let r = rings.find((r) => !r.active);
    if (!r && rings.length < 12) {
      const mat = new THREE.MeshBasicMaterial({ color: tint, transparent: true, opacity: 0.8, side: THREE.DoubleSide, depthWrite: false });
      const mesh = new THREE.Mesh(new THREE.RingGeometry(0.85, 0.89, 48), mat);
      mesh.rotation.x = -Math.PI / 2; group.add(mesh); r = { mesh, active: false }; rings.push(r);
    }
    if (r) { r.active = true; r.time = time; r.mesh.position.set(c.x, 0.12, c.z + 0.5); r.mesh.material.color.setHex(tint); r.mesh.visible = true; }
  }
  function applyState(state) {
    const signature = state.match_id + '|' + state.tick;
    if (signature === lastTick) return;
    lastTick = signature;
    if (lastMatch !== state.match_id) { previous.clear(); ended = false; lastMatch = state.match_id; }
    const time = performance.now() / 1000;
    for (const n of state.nodes || []) {
      const before = previous.get(n.id);
      const patched = (n.cards || []).filter((c) => c.patch_state === 'patched').length;
      if (before) {
        if (n.state === 'compromised' && before.state !== 'compromised') burst(n.id, 0xff526b, time, 30);
        else if (n.state === 'isolated' && before.state !== 'isolated') burst(n.id, 0xffbf69, time, 18);
        else if (n.state === 'healthy' && before.state === 'compromised') burst(n.id, 0x5ae5c0, time, 30);
        else if (patched > before.patched) burst(n.id, 0x63bdff, time, 14);
      }
      previous.set(n.id, { state: n.state, patched });
    }
    if (state.phase === 'ended' && !ended) {
      ended = true;
      const tint = (state.score?.blue || 0) >= (state.score?.red || 0) ? 0x5ae5ce : 0xff526b;
      [0, 4, 7, 12, 15, 19].forEach((id) => burst(id, tint, time, 26));
    }
  }
  function update(t) {
    if (!reduced) { dust.rotation.y = t * 0.007; dust.position.y = Math.sin(t * 0.2) * 0.15; }
    links.forEach(([a, b], i) => {
      const k = reduced ? 0.5 : (t * 0.19 + i * 0.137) % 1;
      dummy.position.set(THREE.MathUtils.lerp(a.x, b.x, k), 0.105, THREE.MathUtils.lerp(a.z, b.z, k) + 0.55);
      dummy.scale.setScalar(1); dummy.updateMatrix(); packets.setMatrixAt(i, dummy.matrix);
    });
    packets.instanceMatrix.needsUpdate = true;
    const now = performance.now() / 1000;
    pool.forEach((p, i) => {
      const age = p.active ? now - p.time : 10;
      if (age > 1.5) { p.active = false; dummy.scale.setScalar(0); }
      else {
        dummy.position.set(p.x + p.vx * age, Math.max(0.12, p.y + p.vy * age - age * age * 1.8), p.z + p.vz * age);
        dummy.rotation.set(age * 3, age * 5, age); dummy.scale.setScalar((1 - age / 1.5) * 1.3);
      }
      dummy.updateMatrix(); sparks.setMatrixAt(i, dummy.matrix);
    });
    sparks.instanceMatrix.needsUpdate = true;
    for (const r of rings) if (r.active) {
      const age = now - r.time;
      if (age > 1.4) { r.active = false; r.mesh.visible = false; }
      else { r.mesh.scale.setScalar(0.7 + age * 2); r.mesh.material.opacity = 0.7 * (1 - age / 1.4); }
    }
  }
  return { update, applyState, dispose() {
    scene.remove(group); const geometries = new Set(), materials = new Set();
    group.traverse((o) => { if (o.geometry) geometries.add(o.geometry); if (o.material) materials.add(o.material); });
    geometries.forEach((g) => g.dispose()); materials.forEach((m) => m.dispose());
  } };
}
