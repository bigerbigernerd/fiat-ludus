import * as THREE from 'three';
import { CFG } from '../config.js';
import { fbm, clamp, smoothstep } from '../rng.js';
import { makeToonMaterial } from '../render/toon.js';

const C = (hex) => new THREE.Color(hex); // sRGB hex -> linear working colour
const PAL = {
  grass: C(0x6fb24c), grassLight: C(0x9ccc5a), grassDeep: C(0x4f9a45),
  sand: C(0xe6d39a), wetSand: C(0xc9b47c), rock: C(0x9c948a), rockDark: C(0x7d756d),
  dirt: C(0xc79b62), seabed: C(0x8fb7a2),
};

function distToSegment(px, pz, ax, az, bx, bz) {
  const dx = bx - ax, dz = bz - az;
  const t = clamp(((px - ax) * dx + (pz - az) * dz) / (dx * dx + dz * dz || 1), 0, 1);
  return Math.hypot(px - (ax + dx * t), pz - (az + dz * t));
}

// Heightfield island. Height queries are bilinear on the same grid the mesh uses,
// so gameplay and visuals agree.
export class Terrain {
  constructor(level) {
    const t = level.terrain;
    this.size = t.size;
    this.res = t.resolution;
    this.seed = level.seed;
    this.def = t;
    this.paths = level.paths || [];
    this.waterLevel = CFG.waterLevel;
    const n = this.res + 1;
    this.heights = new Float32Array(n * n);
    this.cell = this.size / this.res;
    // Pads keep lanterns, the gate and the spawn on flat ground.
    const pads = [
      ...level.lanterns.map((l) => ({ x: l.x, z: l.z, r: 3.5 })),
      { x: level.gate.x, z: level.gate.z, r: 5 },
      { x: level.player.spawn.x, z: level.player.spawn.z, r: 3 },
    ];
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        const x = -this.size / 2 + i * this.cell, z = -this.size / 2 + j * this.cell;
        this.heights[j * n + i] = this.rawHeight(x, z);
      }
    }
    for (const p of pads) {
      const target = this.heightAt(p.x, p.z);
      for (let j = 0; j < n; j++) {
        for (let i = 0; i < n; i++) {
          const x = -this.size / 2 + i * this.cell, z = -this.size / 2 + j * this.cell;
          const w = smoothstep(p.r + 3, p.r, Math.hypot(x - p.x, z - p.z));
          if (w > 0) this.heights[j * n + i] += (target - this.heights[j * n + i]) * w;
        }
      }
    }
    this.pathDist = new Float32Array(n * n);
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        const x = -this.size / 2 + i * this.cell, z = -this.size / 2 + j * this.cell;
        let d = 99;
        for (const path of this.paths) {
          for (let k = 0; k < path.length - 1; k++) {
            d = Math.min(d, distToSegment(x, z, path[k][0], path[k][1], path[k + 1][0], path[k + 1][1]));
          }
        }
        this.pathDist[j * n + i] = d;
      }
    }
  }

  rawHeight(x, z) {
    const t = this.def;
    const s = this.seed;
    const d = Math.hypot(x, z) + (fbm(x * 0.035 + 7, z * 0.035 - 3, s, 3) - 0.5) * 16;
    const island = smoothstep(t.radius, t.radius - 12, d);
    let h = -4 + island * (4 + t.baseHeight + (fbm(x * 0.06, z * 0.06, s + 5, 4) - 0.5) * 2 * t.noiseAmp);
    for (const f of t.features || []) {
      const fd = Math.hypot((x - f.x) / (f.sx || 1), (z - f.z) / (f.sz || 1));
      if (f.type === 'hill') {
        h += f.height * Math.exp(-(fd * fd) / (f.radius * f.radius)) * island;
      } else if (f.type === 'plateau') {
        // Steep-sided rock shelf; edges are too steep to walk.
        const edge = smoothstep(f.radius, f.radius - (f.edge || 1.6), fd + (fbm(x * 0.3, z * 0.3, s + 9, 2) - 0.5) * 1.5);
        h += f.height * edge;
      } else if (f.type === 'pond') {
        h -= f.depth * smoothstep(f.radius, f.radius * 0.35, fd + (fbm(x * 0.2, z * 0.2, s + 3, 2) - 0.5) * 2);
      }
    }
    return h;
  }

  heightAt(x, z) {
    const n = this.res + 1;
    const gx = clamp((x + this.size / 2) / this.cell, 0, this.res - 1e-4);
    const gz = clamp((z + this.size / 2) / this.cell, 0, this.res - 1e-4);
    const i = Math.floor(gx), j = Math.floor(gz);
    const fx = gx - i, fz = gz - j;
    const h = this.heights;
    const a = h[j * n + i], b = h[j * n + i + 1], c = h[(j + 1) * n + i], d = h[(j + 1) * n + i + 1];
    const outside = Math.abs(x) > this.size / 2 || Math.abs(z) > this.size / 2;
    const v = (a + (b - a) * fx) * (1 - fz) + (c + (d - c) * fx) * fz;
    return outside ? Math.min(v, -4) : v;
  }

  slopeAt(x, z) {
    const e = this.cell * 0.75;
    const dx = (this.heightAt(x + e, z) - this.heightAt(x - e, z)) / (2 * e);
    const dz = (this.heightAt(x, z + e) - this.heightAt(x, z - e)) / (2 * e);
    return Math.hypot(dx, dz);
  }

  normalAt(x, z, out = new THREE.Vector3()) {
    const e = this.cell * 0.75;
    const dx = (this.heightAt(x + e, z) - this.heightAt(x - e, z)) / (2 * e);
    const dz = (this.heightAt(x, z + e) - this.heightAt(x, z - e)) / (2 * e);
    return out.set(-dx, 1, -dz).normalize();
  }

  isWater(x, z) { return this.heightAt(x, z) < this.waterLevel + 0.12; }
  isWalkable(x, z) { return !this.isWater(x, z) && this.slopeAt(x, z) <= CFG.player.slopeMax; }

  pathDistanceAt(x, z) {
    const n = this.res + 1;
    const i = clamp(Math.round((x + this.size / 2) / this.cell), 0, this.res);
    const j = clamp(Math.round((z + this.size / 2) / this.cell), 0, this.res);
    return this.pathDist[j * n + i];
  }

  // 0..1 how much grass grows here (also used by scatter rules).
  grassAt(x, z, h, slope, pd) {
    if (h < this.waterLevel + 0.55) return 0;
    let g = smoothstep(this.waterLevel + 0.55, this.waterLevel + 1.2, h);
    g *= 1 - smoothstep(0.55, 0.85, slope);
    g *= smoothstep(1.0, 2.0, pd);
    g *= 0.55 + 0.45 * smoothstep(0.3, 0.6, fbm(x * 0.09, z * 0.09, this.seed + 21, 3));
    return clamp(g, 0, 1);
  }

  buildMesh() {
    const geo = new THREE.PlaneGeometry(this.size, this.size, this.res, this.res);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.getAttribute('position');
    const colors = new Float32Array(pos.count * 3);
    const col = new THREE.Color();
    const tmp = new THREE.Color();
    for (let k = 0; k < pos.count; k++) {
      const x = pos.getX(k), z = pos.getZ(k);
      const h = this.heightAt(x, z);
      pos.setY(k, h);
      const slope = this.slopeAt(x, z);
      const pd = this.pathDistanceAt(x, z);
      const v = fbm(x * 0.07, z * 0.07, this.seed + 11, 3);
      col.copy(PAL.grass).lerp(PAL.grassLight, smoothstep(0.45, 0.75, v)).lerp(PAL.grassDeep, smoothstep(0.4, 0.2, v) * 0.6);
      col.lerp(PAL.dirt, smoothstep(1.7, 0.9, pd) * 0.9);
      const sandT = smoothstep(this.waterLevel + 1.0, this.waterLevel + 0.45, h);
      col.lerp(PAL.sand, sandT);
      col.lerp(PAL.wetSand, smoothstep(this.waterLevel + 0.25, this.waterLevel - 0.1, h));
      col.lerp(PAL.seabed, smoothstep(this.waterLevel - 0.3, this.waterLevel - 1.5, h));
      const rockT = smoothstep(0.62, 0.95, slope);
      tmp.copy(PAL.rock).lerp(PAL.rockDark, smoothstep(0.4, 0.7, fbm(x * 0.25, z * 0.25, this.seed + 4, 2)));
      col.lerp(tmp, rockT);
      colors.set([col.r, col.g, col.b], k * 3);
    }
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geo.computeVertexNormals();
    const mesh = new THREE.Mesh(geo, makeToonMaterial({ vertexColors: true, rim: 0.25, nearFade: false }));
    mesh.receiveShadow = true;
    mesh.name = 'terrain';
    return mesh;
  }

  // RGBA half-float texture: R height, G grass density, B colour variation.
  // Used by the grass and water shaders.
  buildDataTexture() {
    const n = this.res + 1;
    const data = new Uint16Array(n * n * 4);
    const toHalf = THREE.DataUtils.toHalfFloat;
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        const x = -this.size / 2 + i * this.cell, z = -this.size / 2 + j * this.cell;
        const h = this.heights[j * n + i];
        const slope = this.slopeAt(x, z);
        const pd = this.pathDist[j * n + i];
        const k = (j * n + i) * 4;
        data[k] = toHalf(h);
        data[k + 1] = toHalf(this.grassAt(x, z, h, slope, pd));
        data[k + 2] = toHalf(fbm(x * 0.07, z * 0.07, this.seed + 11, 3));
        data[k + 3] = toHalf(1);
      }
    }
    const tex = new THREE.DataTexture(data, n, n, THREE.RGBAFormat, THREE.HalfFloatType);
    tex.minFilter = tex.magFilter = THREE.LinearFilter;
    tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.generateMipmaps = false;
    tex.needsUpdate = true;
    return tex;
  }
}
