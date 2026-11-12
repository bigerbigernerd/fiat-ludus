import * as THREE from 'three';
import { CFG } from './config.js';
import { piece, merge } from './assets/placeholders.js';
import { InstancedModel } from './world/instanced.js';
import { glowTexture } from './render/toon.js';
import { clamp, smoothstep } from './rng.js';

const FLAME = 0xffc35a;

function glowSprite(color, size) {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(), color, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }));
  s.scale.setScalar(size);
  return s;
}

// Wind-lanterns, the north gate, and chests.
export class Objectives {
  constructor(world, emit) {
    this.world = world;
    this.emit = emit;
    const L = world.level;
    this.lanterns = L.lanterns.map((l, i) => ({ ...l, index: i, lit: false, lighting: -1, litTime: 0, glow: 0 }));
    // vec4(x, z, glowRadius, litAmount) per lantern, read by the grass shader.
    this.uniform = { value: this.lanterns.map((l) => new THREE.Vector4(l.x, l.z, 0, 0)) };
    while (this.uniform.value.length < 3) this.uniform.value.push(new THREE.Vector4(9999, 9999, 0, 0));
    this.gate = { ...L.gate, open: false, openT: 0 };
    this.chests = [];
    this.lastLit = null;
    this.won = false;
    this.group = new THREE.Group();
    this.group.name = 'objectives';
  }

  // Register lantern/gate/door placements with the prop system before build().
  addPlacements(props) {
    for (const l of this.lanterns) props.add('lantern', { x: l.x, z: l.z, rot: l.rot || 0 });
    const g = this.gate;
    const c = Math.cos(g.rot || 0), s = Math.sin(g.rot || 0);
    this.gateCos = c; this.gateSin = s;
    props.add('gate', { x: g.x, z: g.z, rot: g.rot || 0 });
    this.doors = [-1.15, 1.15].map((lx) => props.add('gate_door', { x: g.x + lx * c, z: g.z - lx * s, rot: g.rot || 0, y: this.world.terrain.heightAt(g.x, g.z) }));
    for (const p of props.placements.get('chest') || []) this.chests.push({ rec: p, opened: false, seeds: p.seeds ?? 6, pop: 0 });
  }

  build(props) {
    this.props = props;
    const t = this.world.terrain;
    // Flames, glows and spinning pinwheels on top of each lantern.
    this.flames = [];
    const LH = this.world.manifest.props?.lantern?.height ?? 2.2;
    for (const l of this.lanterns) {
      const y = t.heightAt(l.x, l.z);
      const flame = new THREE.Mesh(
        merge([piece(new THREE.ConeGeometry(0.11, 0.3, 6), FLAME, { p: [0, 0.2, 0] }), piece(new THREE.IcosahedronGeometry(0.11, 1), 0xfff0b0, { p: [0, 0.05, 0] })]),
        new THREE.MeshBasicMaterial({ vertexColors: true }),
      );
      flame.position.set(l.x, y + LH * 0.5, l.z);
      flame.scale.setScalar(0.001);
      const glow = glowSprite(0xffc46a, 3.2);
      glow.position.set(l.x, y + LH * 0.56, l.z);
      glow.material.opacity = 0;
      const ground = new THREE.Mesh(new THREE.CircleGeometry(1, 32), new THREE.MeshBasicMaterial({ map: glowTexture(), color: 0xffd27a, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0 }));
      ground.rotation.x = -Math.PI / 2;
      ground.position.set(l.x, y + 0.06, l.z);
      ground.scale.setScalar(5);
      this.group.add(flame, glow, ground);
      this.flames.push({ flame, glow, ground, y });
    }
    const blades = [piece(new THREE.IcosahedronGeometry(0.05, 0), 0x6f4a2e)];
    for (let k = 0; k < 4; k++) {
      const a = (k / 4) * Math.PI * 2;
      blades.push(piece(new THREE.BoxGeometry(0.34, 0.13, 0.025), k % 2 ? 0xd2483c : 0xf3e6c8, { p: [Math.cos(a) * 0.2, Math.sin(a) * 0.2, 0], r: [0.45, 0, a] }));
    }
    this.pinwheel = new InstancedModel('pinwheels', { parts: [{ geometry: merge(blades), kind: 'toon', outline: true, castShadow: false }] }, this.lanterns.length, { dynamic: true });
    this.group.add(this.pinwheel.group);
    this.spin = this.lanterns.map(() => 0);
    this.lanternHeight = this.world.manifest.props?.lantern?.height ?? 2.2;
    // Socket glows on the gate lintel, one per lantern.
    const g = this.gate;
    const gy = t.heightAt(g.x, g.z);
    const gateH = this.world.manifest.props?.gate?.height ?? 5.5;
    this.sockets = [-1.6, 0, 1.6].map((lx) => {
      const sp = glowSprite(0xffd27a, 1.6);
      sp.position.set(g.x + lx * this.gateCos + 0.8 * this.gateSin, gy + gateH * (4.75 / 5.55), g.z - lx * this.gateSin + 0.8 * this.gateCos);
      sp.material.opacity = 0;
      this.group.add(sp);
      return sp;
    });
    this.gateBase = gy;
  }

  nearestLantern(pos) {
    let best = null, bd = Infinity;
    for (const l of this.lanterns) {
      const d = Math.hypot(l.x - pos.x, l.z - pos.z);
      if (d < bd) { bd = d; best = l; }
    }
    return { lantern: best, dist: bd };
  }

  // What the interact button would do right now (drives the prompt and button state).
  interactOption(pl) {
    const { lantern, dist } = this.nearestLantern(pl.pos);
    if (lantern && dist < CFG.lantern.interactRange + 0.6) {
      if (lantern.lit || lantern.lighting >= 0) return null;
      const guards = this.world.enemies.aliveInGroup(lantern.id);
      if (guards > 0) return { kind: 'guarded', lantern, text: `Mosslings guard this lantern (${guards} left)` };
      if (dist < CFG.lantern.interactRange) return { kind: 'lantern', lantern, text: 'Light the lantern' };
    }
    for (const c of this.chests) {
      if (c.opened) continue;
      if (Math.hypot(c.rec.x - pl.pos.x, c.rec.z - pl.pos.z) < 1.8) return { kind: 'chest', chest: c, text: 'Open the chest' };
    }
    return null;
  }

  tryInteract(pl) {
    const opt = this.interactOption(pl);
    if (!opt) return false;
    if (opt.kind === 'lantern') {
      const l = opt.lantern;
      l.lighting = 0;
      pl.locked = CFG.lantern.lightTime;
      pl.vel.set(0, 0, 0);
      pl.yaw = Math.atan2(l.x - pl.pos.x, l.z - pl.pos.z);
      this.emit('lanternStart', { lantern: l });
      return true;
    }
    if (opt.kind === 'chest') {
      const c = opt.chest;
      c.opened = true;
      c.pop = 0.001;
      this.emit('chestOpen', { chest: c, pos: new THREE.Vector3(c.rec.x, c.rec.y + 0.7, c.rec.z), seeds: c.seeds });
      return true;
    }
    return false;
  }

  update(dt, pl) {
    for (const l of this.lanterns) {
      if (l.lighting >= 0 && !l.lit) {
        l.lighting += dt;
        if (l.lighting >= CFG.lantern.lightTime) {
          l.lit = true;
          l.litTime = 0;
          this.lastLit = l;
          this.emit('lanternLit', { lantern: l, count: this.litCount() });
          if (this.litCount() === this.lanterns.length) this.openGate();
        }
      }
      if (l.lit) l.litTime += dt;
      const u = this.uniform.value[l.index];
      u.z = CFG.lantern.glowRadius * smoothstep(0, 2.2, l.litTime);
      u.w = l.lit ? 1 : 0;
    }
    const g = this.gate;
    if (g.open && g.openT < 1) {
      g.openT = Math.min(1, g.openT + dt / CFG.gate.openTime);
      if (g.openT >= 1) this.emit('gateOpened');
    }
    if (g.open && !this.won && g.openT > 0.6) {
      // Into the gate's local frame; walking through to its far side wins.
      const dx = pl.pos.x - g.x, dz = pl.pos.z - g.z;
      const lx = dx * this.gateCos - dz * this.gateSin, lz = dx * this.gateSin + dz * this.gateCos;
      if (Math.abs(lx) < 2.4 && lz < -CFG.gate.winDepth && lz > -6) { this.won = true; this.emit('win'); }
    }
    for (const c of this.chests) if (c.pop > 0) c.pop = Math.min(1, c.pop + dt * 3);
  }

  openGate() {
    this.gate.open = true;
    for (const d of this.doors) for (const c of d?.colliders || []) c.enabled = false;
    this.emit('gateOpening');
  }

  litCount() { return this.lanterns.filter((l) => l.lit).length; }

  updateVisuals(time) {
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
    const P = new THREE.Vector3(), S = new THREE.Vector3(1, 1, 1);
    this.lanterns.forEach((l, i) => {
      const f = this.flames[i];
      const prog = l.lit ? 1 : l.lighting >= 0 ? clamp(l.lighting / CFG.lantern.lightTime, 0, 1) : 0;
      const flick = 1 + Math.sin(time * 13 + i) * 0.06 + Math.sin(time * 23 + i * 2) * 0.04;
      f.flame.scale.set(prog * flick, prog * (1.1 + Math.sin(time * 17 + i) * 0.12), prog * flick);
      const bloom = l.lit ? 1 + Math.max(0, 1 - l.litTime / 0.6) * 2.2 : prog * 0.6;
      f.glow.material.opacity = Math.min(1, bloom) * (0.85 + 0.15 * flick);
      f.glow.scale.setScalar(3.2 * bloom);
      f.ground.material.opacity = (l.lit ? 0.55 : prog * 0.3) * flick;
      this.spin[i] = time * (0.8 + i * 0.1) + (l.lit ? l.litTime * 6 : 0);
      P.set(l.x, f.y + this.lanternHeight + 0.02, l.z + 0.06);
      e.set(-0.15, 0, this.spin[i]);
      q.setFromEuler(e);
      m.compose(P, q, S);
      this.pinwheel.setMatrixAt(i, m);
    });
    this.pinwheel.commit();
    this.sockets.forEach((s, i) => { s.material.opacity = this.lanterns[i]?.lit ? 0.9 + Math.sin(time * 3 + i) * 0.1 : 0; });
    // Doors sink into the ground.
    const ease = this.gate.openT * this.gate.openT * (3 - 2 * this.gate.openT);
    const shake = this.gate.open && this.gate.openT < 1 ? Math.sin(time * 60) * 0.02 : 0;
    for (const rec of this.props.placements.get('gate_door') || []) {
      q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), rec.rot);
      P.set(rec.x + shake, rec.y - ease * 4.5, rec.z);
      m.compose(P, q, S);
      this.props.setRecMatrix(rec, m);
      this.props.commitRec(rec);
    }
    // Chest pop.
    for (const c of this.chests) {
      if (!(c.pop > 0 && c.pop < 1)) continue;
      const k = 1 + Math.sin(c.pop * Math.PI) * 0.25;
      q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), c.rec.rot);
      P.set(c.rec.x, c.rec.y, c.rec.z);
      m.compose(P, q, new THREE.Vector3(c.rec.scale / Math.sqrt(k), c.rec.scale * k, c.rec.scale / Math.sqrt(k)));
      this.props.setRecMatrix(c.rec, m);
      this.props.commitRec(c.rec);
    }
  }

  get summary() {
    return this.lanterns.map((l) => ({ id: l.id, x: l.x, z: l.z, lit: l.lit, guarded: this.world.enemies.aliveInGroup(l.id) > 0 }));
  }
}
