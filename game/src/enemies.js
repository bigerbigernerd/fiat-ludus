import * as THREE from 'three';
import { CFG } from './config.js';
import { InstancedModel } from './world/instanced.js';
import { buildPlaceholder } from './assets/placeholders.js';
import { normalizeProto } from './assets/loader.js';
import { angleDiff, dampAngle, clamp } from './rng.js';

const WHITE = new THREE.Color(1, 1, 1);
const FLASH = new THREE.Color(3.2, 3.2, 3.0);
const EYE = new THREE.Color(0xffc24d);
const EYE_ANGRY = new THREE.Color(0xff4a2e);
const tmpC = new THREE.Color();

// Mosslings: round mossy rocks that hop. No skeleton; every pose is a
// procedural transform written into an instanced mesh.
export class Enemies {
  constructor(world, emit) {
    this.world = world;
    this.emit = emit;
    this.list = [];
    this.rng = world.rngSim;
    const L = world.level;
    let id = 0;
    for (const g of L.enemyGroups || []) {
      const type = g.type || 'mossling';
      const anchor = g.lantern ? L.lanterns.find((l) => l.id === g.lantern) : g;
      if (!anchor) { console.warn(`[level] enemy group references unknown lantern "${g.lantern}"`); continue; }
      for (let i = 0; i < g.count; i++) {
        const a = (i / g.count) * Math.PI * 2 + 0.5;
        const r = g.radius ?? 3.5;
        const hx = anchor.x + (g.dx || 0) + Math.cos(a) * r, hz = anchor.z + (g.dz || 0) + Math.sin(a) * r;
        this.list.push(this.make(id++, type, g.lantern || null, hx, hz));
      }
    }
    const entry = world.manifest.enemies?.mossling;
    this.height = entry?.height ?? 0.9;
    const proto = normalizeProto(buildPlaceholder('mossling'), this.height);
    proto.eyePart = 1;
    this.model = new InstancedModel('mosslings', proto, Math.max(1, this.list.length), { dynamic: true });
    this.group = this.model.group;
    this.m = new THREE.Matrix4();
    this.q = new THREE.Quaternion();
    this.updateVisuals(0);
  }

  make(id, type, group, x, z) {
    const y = this.world.terrain.heightAt(x, z);
    return {
      id, type, group, home: { x, z },
      pos: new THREE.Vector3(x, y, z), vel: new THREE.Vector3(),
      yaw: this.rng() * Math.PI * 2, hp: CFG.enemies.mossling.hp, alive: true,
      state: 'idle', t: this.rng() * 1.5, hop: null, squash: 0, squashVel: 0,
      flash: 0, deathT: 0, struck: false, lungeTarget: null, lungeHit: false,
    };
  }

  setProto(proto) {
    for (const p of proto.parts) p.tint = true;
    proto.eyePart = -1;
    this.model.setProto(proto);
  }

  alive() { return this.list.filter((e) => e.alive); }
  aliveInGroup(g) { let n = 0; for (const e of this.list) if (e.alive && e.group === g) n++; return n; }

  nearestAlive(pos, range) {
    let best = null, bd = range;
    for (const e of this.list) {
      if (!e.alive) continue;
      const d = Math.hypot(e.pos.x - pos.x, e.pos.z - pos.z);
      if (d < bd) { bd = d; best = e; }
    }
    return best;
  }

  startHop(e, dirX, dirZ, speed, dur, height) {
    e.hop = { t: 0, dur, vx: dirX * speed, vz: dirZ * speed, h: height };
    e.squashVel = 5; // stretch on take-off
  }

  moveEnemy(e, dx, dz) {
    const t = this.world.terrain;
    const r = CFG.enemies.mossling.radius;
    const nx = e.pos.x + dx, nz = e.pos.z + dz;
    if (t.isWalkable(nx, nz)) { e.pos.x = nx; e.pos.z = nz; }
    else if (t.isWalkable(nx, e.pos.z)) e.pos.x = nx;
    else if (t.isWalkable(e.pos.x, nz)) e.pos.z = nz;
    const px = e.pos.x, pz = e.pos.z;
    this.world.colliders.resolve(e.pos, r);
    if (!t.isWalkable(e.pos.x, e.pos.z)) { e.pos.x = px; e.pos.z = pz; }
  }

  update(dt) {
    const M = CFG.enemies.mossling;
    const pl = this.world.player;
    const plAlive = pl.dead <= 0;
    for (const e of this.list) {
      if (!e.alive) { e.deathT += dt; continue; }
      e.flash = Math.max(0, e.flash - dt);
      e.t -= dt;
      const dxp = pl.pos.x - e.pos.x, dzp = pl.pos.z - e.pos.z;
      const distP = Math.hypot(dxp, dzp);
      const playerFromHome = Math.hypot(pl.pos.x - e.home.x, pl.pos.z - e.home.z);
      const wantYawP = Math.atan2(dxp, dzp);
      const onGround = !e.hop;

      switch (e.state) {
        case 'idle': {
          if (plAlive && distP < M.aggroRange && playerFromHome < M.leash) { e.state = 'chase'; e.t = 0; this.emit('enemyAlert', { e }); break; }
          if (onGround && e.t <= 0) {
            const a = this.rng() * Math.PI * 2, r = this.rng() * M.wanderRadius;
            const tx = e.home.x + Math.cos(a) * r - e.pos.x, tz = e.home.z + Math.sin(a) * r - e.pos.z;
            const d = Math.hypot(tx, tz) || 1;
            e.yaw = Math.atan2(tx, tz);
            this.startHop(e, tx / d, tz / d, Math.min(M.wanderSpeed, d / M.hopTime), M.hopTime, M.hopHeight);
            e.t = M.hopTime + M.hopPause[0] + this.rng() * (M.hopPause[1] - M.hopPause[0]) + 0.6;
          }
          break;
        }
        case 'chase': {
          e.yaw = dampAngle(e.yaw, wantYawP, 8, dt);
          if (!plAlive || playerFromHome > M.leash + 2) { e.state = 'return'; break; }
          if (onGround && distP < M.attackRange && Math.abs(angleDiff(e.yaw, wantYawP)) < 0.6) {
            e.state = 'telegraph'; e.t = M.telegraph; this.emit('enemyTelegraph', { e }); break;
          }
          if (onGround && e.t <= 0) {
            const d = distP || 1;
            this.startHop(e, dxp / d, dzp / d, Math.min(M.chaseSpeed, (distP - 1.2) / M.hopTime + 0.5), M.hopTime, M.hopHeight * 1.2);
            e.t = M.hopTime + M.hopPause[0];
          }
          break;
        }
        case 'telegraph': {
          e.yaw = dampAngle(e.yaw, wantYawP, 5, dt);
          if (e.t <= 0) {
            const d = distP || 1;
            e.state = 'lunge';
            e.lungeHit = false;
            const reach = Math.min(M.lungeSpeed, (distP + 0.6) / M.lungeTime);
            this.startHop(e, dxp / d, dzp / d, reach, M.lungeTime, 0.55);
            this.emit('enemyLunge', { e });
          }
          break;
        }
        case 'lunge': {
          if (!e.lungeHit && plAlive && distP < M.radius + CFG.player.radius + 0.25 && Math.abs(pl.pos.y - e.pos.y) < 1.2) {
            e.lungeHit = true;
            if (pl.hurt(M.damage, e.pos)) this.emit('playerHit', { e });
          }
          if (onGround) { e.state = 'recover'; e.t = M.recover; }
          break;
        }
        case 'recover':
          if (e.t <= 0) { e.state = 'chase'; e.t = 0.1; }
          break;
        case 'hurt':
          e.vel.multiplyScalar(Math.exp(-7 * dt));
          this.moveEnemy(e, e.vel.x * dt, e.vel.z * dt);
          if (e.t <= 0) { e.state = 'chase'; e.t = 0.2; }
          break;
        case 'return': {
          const hx = e.home.x - e.pos.x, hz = e.home.z - e.pos.z;
          const d = Math.hypot(hx, hz);
          if (d < 1) { e.state = 'idle'; e.t = 0.5; break; }
          if (plAlive && distP < M.aggroRange * 0.7 && playerFromHome < M.leash) { e.state = 'chase'; break; }
          if (onGround && e.t <= 0) {
            e.yaw = Math.atan2(hx, hz);
            this.startHop(e, hx / d, hz / d, Math.min(M.chaseSpeed, d / M.hopTime), M.hopTime, M.hopHeight);
            e.t = M.hopTime + 0.2;
          }
          break;
        }
      }

      // Hop integration.
      const ground = this.world.terrain.heightAt(e.pos.x, e.pos.z);
      if (e.hop) {
        const h = e.hop;
        h.t += dt;
        this.moveEnemy(e, h.vx * dt, h.vz * dt);
        const p = clamp(h.t / h.dur, 0, 1);
        e.pos.y = this.world.terrain.heightAt(e.pos.x, e.pos.z) + 4 * h.h * p * (1 - p);
        if (p >= 1) { e.hop = null; e.squashVel = -6; this.emit('enemyLand', { e }); }
      } else {
        e.pos.y = ground;
      }
      e.squashVel += (-e.squash * 160 - e.squashVel * 12) * dt;
      e.squash += e.squashVel * dt;
    }
    this.separate();
  }

  separate() {
    const r = CFG.enemies.mossling.radius;
    const L = this.list;
    for (let i = 0; i < L.length; i++) {
      const a = L[i];
      if (!a.alive) continue;
      for (let j = i + 1; j < L.length; j++) {
        const b = L[j];
        if (!b.alive) continue;
        const dx = b.pos.x - a.pos.x, dz = b.pos.z - a.pos.z;
        const d = Math.hypot(dx, dz);
        if (d < r * 2 && d > 1e-4) {
          const push = (r * 2 - d) / 2;
          a.pos.x -= (dx / d) * push; a.pos.z -= (dz / d) * push;
          b.pos.x += (dx / d) * push; b.pos.z += (dz / d) * push;
        }
      }
    }
  }

  // Called each sim step while the hero's swing is active.
  playerSwing(pl, hitSet) {
    const A = CFG.player.attack;
    const M = CFG.enemies.mossling;
    for (const e of this.list) {
      if (!e.alive || hitSet.has(e.id)) continue;
      const dx = e.pos.x - pl.pos.x, dz = e.pos.z - pl.pos.z;
      const d = Math.hypot(dx, dz);
      if (d > A.range + M.radius || Math.abs(e.pos.y - pl.pos.y) > 1.4) continue;
      if (d > 0.6 && Math.abs(angleDiff(pl.yaw, Math.atan2(dx, dz))) > A.arc / 2) continue;
      hitSet.add(e.id);
      e.hp -= A.damage;
      e.flash = 0.14;
      e.squashVel = -7;
      const nd = d || 1;
      e.vel.set((dx / nd) * M.knockback, 0, (dz / nd) * M.knockback);
      e.hop = null;
      e.state = 'hurt';
      e.t = M.hurtStun;
      this.emit('enemyHit', { e, killed: e.hp <= 0 });
      if (e.hp <= 0) {
        e.alive = false;
        e.deathT = 0;
        this.emit('enemyDefeated', { e });
      }
    }
  }

  // After the hero respawns: guards of dark lanterns heal and go home.
  regroup(litGroups) {
    for (const e of this.list) {
      if (!e.alive || litGroups.has(e.group)) continue;
      e.hp = CFG.enemies.mossling.hp;
      e.state = 'return';
      e.t = 0;
    }
  }

  updateVisuals(time) {
    const M = CFG.enemies.mossling;
    const m = this.m, q = this.q;
    const pos = new THREE.Vector3(), scl = new THREE.Vector3();
    const up = new THREE.Vector3(0, 1, 0);
    const eyePart = this.model.proto.eyePart ?? -1;
    this.list.forEach((e, i) => {
      let s = 1;
      if (!e.alive) s = Math.max(0, 1 - e.deathT / 0.25);
      let sy = 1 + clamp(e.squash * 0.07, -0.3, 0.3);
      let shakeX = 0;
      if (e.state === 'telegraph') {
        const k = 1 - e.t / M.telegraph;
        sy *= 1 - 0.22 * k;
        shakeX = Math.sin(time * 70) * 0.04 * k;
      }
      const sxz = 1 / Math.sqrt(sy);
      pos.copy(e.pos);
      pos.x += shakeX;
      q.setFromAxisAngle(up, e.yaw);
      scl.set(sxz * s, sy * s, sxz * s);
      m.compose(pos, q, scl);
      this.model.setMatrixAt(i, m);
      const fl = e.flash > 0;
      this.model.setColorAt(i, fl ? FLASH : WHITE, 0);
      if (eyePart === 1) {
        tmpC.copy(e.state === 'telegraph' || e.state === 'lunge' ? EYE_ANGRY : EYE);
        this.model.setColorAt(i, fl ? FLASH : tmpC, 1);
      } else {
        for (let k = 1; k < this.model.tinted.length; k++) this.model.setColorAt(i, fl ? FLASH : WHITE, k);
      }
    });
    this.model.commit();
  }

  get stateSummary() {
    return this.list.map((e) => ({ id: e.id, group: e.group, alive: e.alive, hp: e.hp, state: e.state, x: +e.pos.x.toFixed(2), z: +e.pos.z.toFixed(2) }));
  }
}
