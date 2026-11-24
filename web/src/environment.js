import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

// A bounded decorative world and reusable event effects. All positions come from the public board.
export function createEnvironment(scene, nodeCenter) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const group = new THREE.Group(); scene.add(group);
  const dark = new THREE.MeshStandardMaterial({ color: 0x070c12, metalness: 0.6, roughness: 0.45 });
  const steel = new THREE.MeshStandardMaterial({ color: 0x101a24, metalness: 0.8, roughness: 0.3 });
  const glow = new THREE.MeshBasicMaterial({ color: 0x19f7c1, toneMapped: false });
  function box(size, pos, mat, r = 0.06) {
    const m = new THREE.Mesh(new RoundedBoxGeometry(...size, 2, r), mat);
    m.position.set(...pos); m.receiveShadow = true; group.add(m); return m;
  }
  box([33.6, 0.65, 32.4], [0, -0.45, 0.6], dark, 0.16);
  box([34, 0.12, 32.8], [0, -0.8, 0.6], steel);
  for (const x of [-17, 17]) {
    box([0.035, 0.035, 31], [x, -0.06, 0.6], glow, 0.01);
    for (let i = 0; i < 12; i++) box([0.28, 0.03, 0.025], [x, -0.035, -13.8 + i * 2.35], glow, 0.008);
  }
  for (const z of [-15.4, 16.6]) box([33, 0.035, 0.035], [0, -0.06, z], glow, 0.01);
  // Dim data-center structures frame the playable platform; they never enter its footprint.
  for (let i = 0; i < 12; i++) {
    const side = i % 2 ? -1 : 1;
    const x = side * (19.8 + (i % 3) * 2.2), z = -17 + Math.floor(i / 2) * 3;
    const h = 0.8 + ((i * 7) % 9) * 0.2;
    box([1.3, h, 1.3], [x, h / 2 - 1, z], dark);
    box([0.04, h * 0.7, 0.04], [x - 0.5, h / 2 - 1, z + 0.68], glow, 0.01);
  }
  const beaconMat = new THREE.MeshBasicMaterial({ color: 0x5d9d8e, transparent: true, opacity: 0.75, toneMapped: false });
  const haloMat = new THREE.MeshBasicMaterial({ color: 0x5d9d8e, transparent: true, opacity: 0.5, toneMapped: false });
  const alarmLights = [];
  for (const x of [-16, 16]) {
    box([0.42, 2.6, 0.42], [x, 1.2, -14.4], steel, 0.06);
    const beacon = new THREE.Mesh(new THREE.SphereGeometry(0.32, 16, 12), beaconMat);
    beacon.position.set(x, 2.65, -14.4);
    group.add(beacon);
    const halo = new THREE.Mesh(new THREE.TorusGeometry(0.42, 0.045, 8, 24), haloMat);
    halo.rotation.x = Math.PI / 2;
    halo.position.set(x, 2.65, -14.4);
    group.add(halo);
    const light = new THREE.PointLight(0x5d9d8e, 0.12, 9, 2);
    light.position.set(x, 2.85, -14.4);
    group.add(light);
    alarmLights.push({ beacon, halo, light });
  }
  const laserGlowMat = new THREE.MeshBasicMaterial({
    color: 0x5d9d8e, transparent: true, opacity: 0.2, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false,
  });
  const laserCoreMat = new THREE.MeshBasicMaterial({
    color: 0x9be8d5, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false,
  });
  const scanLasers = [-1, 1].map((side) => {
    const railGlow = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 29, 10), laserGlowMat);
    const railCore = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 29, 8), laserCoreMat);
    railGlow.rotation.x = Math.PI / 2;
    railCore.rotation.x = Math.PI / 2;
    railGlow.position.set(side * 15.9, 0.12, 0);
    railCore.position.copy(railGlow.position);
    const glowBeam = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 4.2, 10), laserGlowMat);
    const coreBeam = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 4.2, 8), laserCoreMat);
    glowBeam.position.set(side * 15.9, 2.05, 0);
    coreBeam.position.copy(glowBeam.position);
    group.add(railGlow, railCore, glowBeam, coreBeam);
    return { glowBeam, coreBeam, phase: side < 0 ? 0 : 0.5 };
  });
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
  const packets = new THREE.InstancedMesh(new THREE.SphereGeometry(0.045, 6, 4), new THREE.MeshBasicMaterial({ color: 0x3cf2ff }), links.length);
  group.add(packets);
  const dummy = new THREE.Object3D();
  const sparkCount = 180;
  const sparks = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(0.055, 0), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85 }), sparkCount);
  sparks.instanceMatrix.setUsage(THREE.DynamicDrawUsage); sparks.frustumCulled = false;
  group.add(sparks);
  const pool = Array.from({ length: sparkCount }, () => ({ active: false }));
  const rings = [];
  let cursor = 0, previous = new Map(), lastMatch, lastTick, ended = false, alarmMode = 'safe', alarmStrength = 0;
  const color = new THREE.Color();
  function burst(id, tint, time, count = 24) {
    if (reduced) return;
    const c = nodeCenter(id);
    for (let i = 0; i < count; i++) {
      const p = pool[cursor++ % sparkCount];
      const angle = i / count * Math.PI * 2;
      Object.assign(p, { active: true, x: c.x, z: c.z - 0.35, y: c.y + 1,
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
    if (r) { r.active = true; r.time = time; r.mesh.position.set(c.x, c.y + 0.12, c.z + 0.5); r.mesh.material.color.setHex(tint); r.mesh.visible = true; }
  }
  function applyState(state) {
    const signature = state.match_id + '|' + state.tick;
    if (signature === lastTick) return;
    lastTick = signature;
    if (lastMatch !== state.match_id) { previous.clear(); ended = false; lastMatch = state.match_id; }
    const time = performance.now() / 1000;
    const nodes = Array.isArray(state.nodes) ? state.nodes : [];
    const breached = nodes.filter((n) => n.state === 'compromised').length;
    const isolated = nodes.filter((n) => n.state === 'isolated').length;
    if (state.phase === 'ended') alarmMode = (state.score?.blue || 0) >= (state.score?.red || 0) ? 'victory' : 'defeat';
    else if (breached) alarmMode = 'breach';
    else if (isolated) alarmMode = 'isolated';
    else alarmMode = 'safe';
    alarmStrength = Math.min(1, (breached + isolated) / 4);
    for (const n of nodes) {
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
    const pulse = reduced ? 0.75 : 0.55 + 0.45 * Math.abs(Math.sin(t * (alarmMode === 'breach' || alarmMode === 'defeat' ? 7 : 3)));
    const alertColor = alarmMode === 'breach' || alarmMode === 'defeat' ? 0xff526b
      : alarmMode === 'isolated' ? 0xffbf69
        : alarmMode === 'victory' ? 0x5ae5c0 : 0x5d9d8e;
    glow.color.setHex(alertColor);
    beaconMat.color.setHex(alertColor);
    haloMat.color.setHex(alertColor);
    laserGlowMat.color.setHex(alertColor);
    laserCoreMat.color.setHex(alertColor);
    laserGlowMat.opacity = alarmMode === 'safe' ? 0.15 : 0.22 + alarmStrength * 0.18 * pulse;
    laserCoreMat.opacity = alarmMode === 'safe' ? 0.68 : 0.72 + alarmStrength * 0.28 * pulse;
    scanLasers.forEach(({ glowBeam, coreBeam, phase }) => {
      const progress = reduced ? 0.5 : (t * 0.18 + phase) % 1;
      const z = -14.4 + progress * 29;
      glowBeam.position.z = z;
      coreBeam.position.z = z;
    });
    alarmLights.forEach(({ beacon, halo, light }) => {
      const blink = alarmMode === 'safe' ? 0.7 : pulse;
      beaconMat.opacity = alarmMode === 'safe' ? 0.65 : 0.55 + blink * 0.4;
      haloMat.opacity = alarmMode === 'safe' ? 0.4 : 0.42 + blink * 0.45;
      halo.scale.setScalar(alarmMode === 'safe' ? 1 : 1 + blink * 0.12);
      light.color.setHex(alertColor);
      light.intensity = alarmMode === 'safe' ? 0.12 : 0.5 + alarmStrength * 1.5 * blink;
    });
    links.forEach(([a, b], i) => {
      const k = reduced ? 0.5 : (t * 0.19 + i * 0.137) % 1;
      dummy.position.set(THREE.MathUtils.lerp(a.x, b.x, k), THREE.MathUtils.lerp(a.y, b.y, k) + 0.105, THREE.MathUtils.lerp(a.z, b.z, k) + 0.55);
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
