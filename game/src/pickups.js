import * as THREE from 'three';
import { InstancedModel } from './world/instanced.js';
import { piece, merge } from './assets/placeholders.js';

// Sunseeds: small glowing seeds that pop out of defeated mosslings and chests
// and drift to the hero when close.
export class Pickups {
  constructor(world, emit, capacity = 160) {
    this.world = world;
    this.emit = emit;
    this.rng = world.rngSim;
    this.seeds = [];
    const geo = merge([
      piece(new THREE.OctahedronGeometry(0.13, 0), 0xffd84a, { s: [0.8, 1.35, 0.8] }),
      piece(new THREE.OctahedronGeometry(0.07, 0), 0xfff6c0, { p: [0, 0.02, 0.05] }),
      piece(new THREE.ConeGeometry(0.04, 0.12, 4), 0x8fd16a, { p: [0, 0.21, 0] }),
    ]);
    this.model = new InstancedModel('sunseeds', { parts: [{ geometry: geo, kind: 'glow', outline: true, castShadow: false }] }, capacity, { dynamic: true, castShadow: false });
    this.capacity = capacity;
    this.group = this.model.group;
    for (const s of world.level.seeds || []) this.spawn(new THREE.Vector3(s.x, world.terrain.heightAt(s.x, s.z) + 0.5, s.z), s.count || 1, false);
    this.updateVisuals(0);
  }

  spawn(pos, n, burst = true) {
    for (let i = 0; i < n; i++) {
      if (this.seeds.length >= this.capacity) return;
      const a = this.rng() * Math.PI * 2;
      const sp = burst ? 1.6 + this.rng() * 1.8 : 0;
      this.seeds.push({
        pos: pos.clone().add(new THREE.Vector3(burst ? 0 : Math.cos(a) * 0.6 * (i > 0), 0, burst ? 0 : Math.sin(a) * 0.6 * (i > 0))),
        vel: new THREE.Vector3(Math.cos(a) * sp, burst ? 5 + this.rng() * 2 : 0, Math.sin(a) * sp),
        t: 0, rest: !burst, phase: this.rng() * 6, collected: false,
      });
    }
  }

  update(dt) {
    const pl = this.world.player;
    const t = this.world.terrain;
    for (const s of this.seeds) {
      s.t += dt;
      const dx = pl.pos.x - s.pos.x, dy = pl.pos.y + 0.7 - s.pos.y, dz = pl.pos.z - s.pos.z;
      const d = Math.hypot(dx, dy, dz);
      if (s.t > 0.45 && pl.dead <= 0 && d < 2.6) {
        // Magnet: accelerate toward the hero.
        const k = 18 * dt;
        s.vel.x += (dx / d) * k * 3; s.vel.y += (dy / d) * k * 3; s.vel.z += (dz / d) * k * 3;
        s.vel.multiplyScalar(Math.exp(-3 * dt));
        s.pos.addScaledVector(s.vel, dt);
        s.rest = false;
        if (d < 0.55) { s.collected = true; pl.addSeeds(1); this.emit('seed', { total: pl.sunseeds }); }
        continue;
      }
      if (!s.rest) {
        s.vel.y -= 20 * dt;
        s.pos.addScaledVector(s.vel, dt);
        const g = t.heightAt(s.pos.x, s.pos.z) + 0.35;
        if (t.isWater(s.pos.x, s.pos.z)) { s.vel.x *= -1; s.vel.z *= -1; }
        if (s.pos.y < g) {
          s.pos.y = g;
          if (Math.abs(s.vel.y) < 2) { s.rest = true; s.vel.set(0, 0, 0); }
          else { s.vel.y *= -0.4; s.vel.x *= 0.5; s.vel.z *= 0.5; }
        }
      } else {
        s.pos.y = t.heightAt(s.pos.x, s.pos.z) + 0.35;
      }
    }
    this.seeds = this.seeds.filter((s) => !s.collected);
  }

  updateVisuals(time) {
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), p = new THREE.Vector3(), sc = new THREE.Vector3();
    const up = new THREE.Vector3(0, 1, 0);
    this.seeds.forEach((s, i) => {
      p.copy(s.pos);
      if (s.rest) p.y += Math.sin(time * 2.5 + s.phase) * 0.08;
      q.setFromAxisAngle(up, time * 2 + s.phase);
      const pulse = 1 + Math.sin(time * 5 + s.phase) * 0.08;
      sc.setScalar(Math.min(1, s.t * 6) * pulse * 1.15);
      m.compose(p, q, sc);
      this.model.setMatrixAt(i, m);
    });
    this.model.setCount(this.seeds.length);
    this.model.commit();
  }
}
