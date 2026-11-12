import * as THREE from 'three';
import { CFG } from './config.js';
import { dampAngle, angleDiff } from './rng.js';

// Hero simulation (no rendering). Emits events through `emit(type, data)`.
export class Player {
  constructor(world, emit) {
    this.world = world;
    this.emit = emit;
    this.pos = new THREE.Vector3();
    this.vel = new THREE.Vector3();
    this.yaw = 0;
    this.health = CFG.player.maxHealth;
    this.sunseeds = 0;
    this.seedsTowardPetal = 0;
    this.hitsTaken = 0;
    this.deaths = 0;
    this.time = 0;
    this.reset(world.level.player.spawn);
  }

  reset(spawn) {
    this.pos.set(spawn.x, this.world.terrain.heightAt(spawn.x, spawn.z), spawn.z);
    this.vel.set(0, 0, 0);
    this.yaw = (spawn.yaw ?? 0) + Math.PI; // spawn.yaw is the camera yaw; face away from it
    this.grounded = true;
    this.coyote = 0;
    this.jumpBuf = 0;
    this.jumpHeld = false;
    this.attackT = -1;
    this.attackCd = 0;
    this.hitSet = new Set();
    this.invuln = 0;
    this.stepAcc = 0;
    this.locked = 0; // seconds of no control (lighting a lantern, respawn)
    this.dead = 0;
  }

  teleport(x, z) {
    this.pos.set(x, this.world.terrain.heightAt(x, z), z);
    this.vel.set(0, 0, 0);
    this.grounded = true;
  }

  // Tries a horizontal move, sliding along blocked axes. Returns true if blocked.
  moveHorizontal(dx, dz) {
    const t = this.world.terrain;
    const r = CFG.player.radius;
    const ok = (x, z) => t.isWalkable(x, z) && t.isWalkable(x + Math.sign(dx) * r * 0.5, z + Math.sign(dz) * r * 0.5);
    let blocked = false;
    const nx = this.pos.x + dx, nz = this.pos.z + dz;
    if (ok(nx, nz)) { this.pos.x = nx; this.pos.z = nz; }
    else if (ok(nx, this.pos.z)) { this.pos.x = nx; blocked = true; }
    else if (ok(this.pos.x, nz)) { this.pos.z = nz; blocked = true; }
    else blocked = true;
    const px = this.pos.x, pz = this.pos.z;
    this.world.colliders.resolve(this.pos, r);
    // A collider push must not shove us into water or up a cliff.
    if (!t.isWalkable(this.pos.x, this.pos.z)) { this.pos.x = px; this.pos.z = pz; }
    return blocked;
  }

  update(dt, input, camYaw) {
    const P = CFG.player;
    this.time += dt;
    this.invuln = Math.max(0, this.invuln - dt);
    this.attackCd = Math.max(0, this.attackCd - dt);
    if (this.dead > 0) {
      this.dead -= dt;
      this.vel.set(0, 0, 0);
      if (this.dead <= 0) this.emit('respawn');
      return;
    }
    const control = this.locked <= 0;
    this.locked = Math.max(0, this.locked - dt);

    // Desired velocity, camera-relative.
    let mx = control ? input.moveX : 0, my = control ? input.moveY : 0;
    let mag = Math.hypot(mx, my);
    if (mag > 1) { mx /= mag; my /= mag; mag = 1; }
    const fx = -Math.sin(camYaw), fz = -Math.cos(camYaw);
    const rx = Math.cos(camYaw), rz = -Math.sin(camYaw);
    let dirX = rx * mx + fx * my, dirZ = rz * mx + fz * my;
    const speed = (input.walk ? P.walkSpeed : P.runSpeed) * mag;
    const attacking = this.attackT >= 0;
    const tvx = dirX * speed * (attacking ? 0.15 : 1), tvz = dirZ * speed * (attacking ? 0.15 : 1);
    const rate = (mag > 0.05 ? P.accel : P.decel) * (this.grounded ? 1 : P.airControl);
    const ax = tvx - this.vel.x, az = tvz - this.vel.z;
    const al = Math.hypot(ax, az), maxd = rate * dt;
    if (al > maxd) { this.vel.x += (ax / al) * maxd; this.vel.z += (az / al) * maxd; }
    else { this.vel.x = tvx; this.vel.z = tvz; }
    if (mag > 0.1 && !attacking) this.yaw = dampAngle(this.yaw, Math.atan2(dirX, dirZ), P.turnRate, dt);

    // Jump with coyote time, buffering and variable height.
    if (control && input.consume('jump')) this.jumpBuf = P.jumpBuffer;
    else this.jumpBuf = Math.max(0, this.jumpBuf - dt);
    this.coyote = this.grounded ? P.coyoteTime : Math.max(0, this.coyote - dt);
    if (this.jumpBuf > 0 && this.coyote > 0) {
      this.vel.y = P.jumpVelocity;
      this.grounded = false;
      this.coyote = 0;
      this.jumpBuf = 0;
      this.jumpHeld = true;
      this.emit('jump');
    }
    if (this.jumpHeld && !input.held.jump) {
      if (this.vel.y > 0) this.vel.y *= P.jumpCut;
      this.jumpHeld = false;
    }

    // Attack: short forward lunge, soft auto-aim toward the nearest enemy.
    if (control && input.consume('attack') && this.attackT < 0 && this.attackCd <= 0) {
      const A = P.attack;
      const target = this.world.enemies.nearestAlive(this.pos, A.autoAimRange);
      if (target) {
        const want = Math.atan2(target.pos.x - this.pos.x, target.pos.z - this.pos.z);
        if (Math.abs(angleDiff(this.yaw, want)) < A.autoAimArc || mag < 0.1) this.yaw = want;
      }
      this.attackT = 0;
      this.hitSet.clear();
      this.vel.x += Math.sin(this.yaw) * A.lunge;
      this.vel.z += Math.cos(this.yaw) * A.lunge;
      this.emit('attack');
    }
    if (this.attackT >= 0) {
      const A = P.attack;
      this.attackT += dt;
      if (this.attackT >= A.activeFrom && this.attackT <= A.activeTo) this.world.enemies.playerSwing(this, this.hitSet);
      if (this.attackT >= A.duration) { this.attackT = -1; this.attackCd = A.cooldown; }
    }

    // Integrate.
    const blocked = this.moveHorizontal(this.vel.x * dt, this.vel.z * dt);
    if (blocked) {
      // Kill velocity into the obstacle so we do not keep pushing.
      const t = this.world.terrain;
      if (!t.isWalkable(this.pos.x + Math.sign(this.vel.x) * 0.5, this.pos.z)) this.vel.x *= 0.2;
      if (!t.isWalkable(this.pos.x, this.pos.z + Math.sign(this.vel.z) * 0.5)) this.vel.z *= 0.2;
    }
    this.vel.y += P.gravity * dt;
    this.pos.y += this.vel.y * dt;
    const ground = this.world.terrain.heightAt(this.pos.x, this.pos.z);
    const wasGrounded = this.grounded;
    if (this.pos.y <= ground) {
      if (!wasGrounded && this.vel.y < -2) this.emit('land', { speed: -this.vel.y });
      this.pos.y = ground;
      this.vel.y = 0;
      this.grounded = true;
    } else if (wasGrounded && this.vel.y <= 0 && this.pos.y - ground < 0.35) {
      this.pos.y = ground; // stick to the ground walking downhill
      this.vel.y = 0;
    } else {
      this.grounded = false;
    }

    // Footsteps.
    const hs = Math.hypot(this.vel.x, this.vel.z);
    if (this.grounded && hs > 1) {
      this.stepAcc += hs * dt;
      if (this.stepAcc > P.stepDistance) { this.stepAcc = 0; this.emit('step', { speed: hs }); }
    }

    if (this.pos.y < -3) this.hurt(99, this.pos);
  }

  hurt(amount, from) {
    if (this.invuln > 0 || this.dead > 0) return false;
    this.health = Math.max(0, this.health - amount);
    this.hitsTaken++;
    this.invuln = CFG.player.invulnTime;
    const dx = this.pos.x - from.x, dz = this.pos.z - from.z;
    const d = Math.hypot(dx, dz) || 1;
    this.vel.x = (dx / d) * CFG.player.hurtKnockback;
    this.vel.z = (dz / d) * CFG.player.hurtKnockback;
    this.vel.y = 4;
    this.grounded = false;
    this.attackT = -1;
    this.emit('hurt', { health: this.health });
    if (this.health <= 0) {
      this.dead = 1.4;
      this.deaths++;
      this.emit('death');
    }
    return true;
  }

  addSeeds(n) {
    this.sunseeds += n;
    this.seedsTowardPetal += n;
    // Seeds bank up at full health and heal on the next pickup after a hit.
    while (this.seedsTowardPetal >= CFG.player.seedsPerPetal && this.health < CFG.player.maxHealth && this.health > 0) {
      this.seedsTowardPetal -= CFG.player.seedsPerPetal;
      this.health++;
      this.emit('heal', { health: this.health });
    }
  }
}
