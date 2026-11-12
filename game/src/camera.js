import * as THREE from 'three';
import { CFG } from './config.js';
import { clamp, damp, dampAngle, angleDiff } from './rng.js';

// Third-person orbit rig: yaw/pitch around the hero, auto-follow behind the
// direction of travel when the player is not steering the camera, terrain
// collision pull-in, and an additive trauma shake.
export class CameraRig {
  constructor(camera, terrain, colliders = null) {
    this.colliders = colliders;
    this.camera = camera;
    this.terrain = terrain;
    this.yaw = 0;
    this.pitch = CFG.camera.pitch;
    this.distance = CFG.camera.distance;
    this.wantDistance = CFG.camera.distance;
    this.curDist = CFG.camera.distance;
    this.target = new THREE.Vector3();
    this.idleCam = 99;
    this.shakeT = 0;
    this.lockYaw = null;
  }

  snap(pos, yaw) {
    this.target.set(pos.x, pos.y + CFG.camera.targetHeight, pos.z);
    if (yaw != null) this.yaw = yaw;
    this.curDist = this.distance;
  }

  update(dt, player, camInput, trauma, time) {
    const C = CFG.camera;
    if (camInput.dx || camInput.dy) this.idleCam = 0; else this.idleCam += dt;
    this.yaw -= camInput.dx;
    this.pitch = clamp(this.pitch + camInput.dy, C.pitchMin, C.pitchMax);
    this.wantDistance = clamp(this.wantDistance + camInput.zoom, 3, C.maxDistance);
    if (this.lockYaw != null) {
      this.yaw = this.lockYaw;
    } else {
      // Auto-follow: ease behind the hero's travel direction, only while moving.
      const hs = Math.hypot(player.vel.x, player.vel.z);
      if (this.idleCam > C.autoYawDelay && hs > 1.5 && player.attackT < 0) {
        const want = Math.atan2(-player.vel.x, -player.vel.z); // camera yaw that looks along velocity
        // Running back toward the camera should not spin it around.
        if (Math.abs(angleDiff(this.yaw, want)) < 2.3) {
          const k = clamp((hs - 1.5) / 4, 0, 1) * C.autoYawRate;
          this.yaw = dampAngle(this.yaw, want, k, dt);
        }
      }
    }
    // Follow target (y eased more slowly so jumps do not jerk the view).
    const tx = player.pos.x, tz = player.pos.z, ty = player.pos.y + C.targetHeight;
    this.target.x = damp(this.target.x, tx, C.followRate, dt);
    this.target.z = damp(this.target.z, tz, C.followRate, dt);
    this.target.y = damp(this.target.y, ty, player.grounded ? 8 : 3, dt);

    // Collision: march toward the desired camera spot; stop before the ground.
    const dir = new THREE.Vector3(Math.sin(this.yaw) * Math.cos(this.pitch), Math.sin(this.pitch), Math.cos(this.yaw) * Math.cos(this.pitch));
    let allowed = this.wantDistance;
    const steps = 14;
    for (let i = 1; i <= steps; i++) {
      const d = (i / steps) * this.wantDistance;
      const px = this.target.x + dir.x * d, py = this.target.y + dir.y * d, pz = this.target.z + dir.z * d;
      const g = Math.max(this.terrain.heightAt(px, pz), CFG.waterLevel) + C.clearance;
      if (py < g) { allowed = Math.max(C.minDistance, ((i - 1) / steps) * this.wantDistance); break; }
    }
    if (this.colliders) {
      // Line of sight to the hero's head, shoulders and knees: any prop
      // collider in the way pulls the camera in front of it.
      const right = new THREE.Vector3(Math.cos(this.yaw), 0, -Math.sin(this.yaw));
      const o = new THREE.Vector3();
      for (const [sx, sy] of [[0, 0], [0.32, -0.1], [-0.32, -0.1], [0, -0.85], [0, 0.35]]) {
        o.copy(this.target).addScaledVector(right, sx);
        o.y += sy;
        const hit = this.colliders.rayBlock(o, dir, allowed);
        if (hit < allowed) allowed = Math.max(C.minDistance, hit - 0.35);
      }
    }
    // Pull in fast, ease back out slowly.
    this.curDist = allowed < this.curDist ? damp(this.curDist, allowed, 25, dt) : damp(this.curDist, allowed, 3, dt);
    const cam = this.camera;
    cam.position.copy(this.target).addScaledVector(dir, this.curDist);
    const floor = Math.max(this.terrain.heightAt(cam.position.x, cam.position.z), CFG.waterLevel) + 0.3;
    if (cam.position.y < floor) cam.position.y = floor;
    cam.lookAt(this.target);
    if (trauma > 0) {
      const s = trauma * trauma;
      cam.position.x += Math.sin(time * 41) * 0.18 * s;
      cam.position.y += Math.sin(time * 37 + 1) * 0.14 * s;
      cam.rotateZ(Math.sin(time * 29) * 0.03 * s);
    }
  }

  // Slow orbit for the title screen.
  titleOrbit(time, center) {
    const a = time * 0.05 + 0.6;
    this.camera.position.set(center.x + Math.sin(a) * 38, 17, center.z + Math.cos(a) * 38);
    this.camera.lookAt(center.x, 3, center.z);
  }
}
