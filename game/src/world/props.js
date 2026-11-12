import * as THREE from 'three';
import { CFG } from '../config.js';
import { InstancedModel } from './instanced.js';
import { buildPlaceholder, genericPlaceholder } from '../assets/placeholders.js';
import { normalizeProto, protoTriangles } from '../assets/loader.js';
import { mulberry32, hashString } from '../rng.js';

// Static colliders on the XZ plane: circles and rotated boxes. Shapes come
// from the manifest, never from mesh geometry.
export class Colliders {
  constructor() { this.list = []; }

  addFromDef(def, x, z, rot = 0, scale = 1, tag = null, height = 2, baseY = 0) {
    if (!def) return [];
    const defs = Array.isArray(def) ? def : [def];
    const out = [];
    const c = Math.cos(rot), s = Math.sin(rot);
    for (const d of defs) {
      if (!d || d.type === 'none') continue;
      // Offsets are in the model's local frame (front = +Z) and rotate with it.
      const ox = (d.x || 0) * scale, oz = (d.z || 0) * scale;
      const cx = x + ox * c + oz * s, cz = z - ox * s + oz * c;
      let col;
      const top = baseY + height * scale;
      if (d.type === 'box') col = { type: 'box', x: cx, z: cz, hw: (d.width * scale) / 2, hd: (d.depth * scale) / 2, rot, enabled: true, tag, top };
      else col = { type: 'circle', x: cx, z: cz, r: (d.radius ?? 0.5) * scale, enabled: true, tag, top };
      this.list.push(col);
      out.push(col);
    }
    return out;
  }

  // Push a circle (x,z,r) out of every collider. Returns true if it touched any.
  resolve(pos, r) {
    let hit = false;
    for (const c of this.list) {
      if (!c.enabled) continue;
      if (c.type === 'circle') {
        const dx = pos.x - c.x, dz = pos.z - c.z;
        const min = r + c.r;
        const d2 = dx * dx + dz * dz;
        if (d2 < min * min) {
          const d = Math.sqrt(d2) || 1e-4;
          pos.x = c.x + (dx / d) * min;
          pos.z = c.z + (dz / d) * min;
          hit = true;
        }
      } else {
        const cs = Math.cos(c.rot), sn = Math.sin(c.rot);
        const dx = pos.x - c.x, dz = pos.z - c.z;
        // Into box space (inverse of a Y rotation by rot).
        const lx = dx * cs - dz * sn, lz = dx * sn + dz * cs;
        const qx = Math.max(-c.hw, Math.min(c.hw, lx)), qz = Math.max(-c.hd, Math.min(c.hd, lz));
        let ex = lx - qx, ez = lz - qz;
        let d = Math.hypot(ex, ez);
        if (d < r) {
          let nx, nz;
          if (d > 1e-5) { nx = ex / d; nz = ez / d; }
          else {
            // Centre inside the box: leave by the nearest face.
            const px = c.hw - Math.abs(lx), pz = c.hd - Math.abs(lz);
            if (px < pz) { nx = Math.sign(lx) || 1; nz = 0; d = -px; } else { nx = 0; nz = Math.sign(lz) || 1; d = -pz; }
          }
          const push = r - d;
          const wx = nx * cs + nz * sn, wz = -nx * sn + nz * cs;
          pos.x += wx * push; pos.z += wz * push;
          hit = true;
        }
      }
    }
    return hit;
  }
}

  // First distance along a 3D ray (origin o, unit dir d) blocked by a collider
  // tall enough to be in the way. Boxes are treated as their bounding circle.
Colliders.prototype.rayBlock = function (o, d, maxDist) {
  let best = maxDist;
  const hx = d.x, hz = d.z;
  const hl2 = hx * hx + hz * hz;
  if (hl2 < 1e-8) return best;
  for (const c of this.list) {
    if (!c.enabled) continue;
    // Slightly smaller than the walk collider: models are rounded at the edges.
    const r = (c.type === 'circle' ? c.r : Math.hypot(c.hw, c.hd)) * 0.8;
    if (r < 0.15) continue;
    const ox = o.x - c.x, oz = o.z - c.z;
    const b = ox * hx + oz * hz, cc = ox * ox + oz * oz - r * r;
    const disc = b * b - hl2 * cc;
    if (disc < 0) continue;
    const s = (-b - Math.sqrt(disc)) / hl2;
    if (s <= 0.2 || s >= best) continue;
    if (o.y + d.y * s > c.top - 0.3) continue;
    best = s;
  }
  return best;
};

const FLOWER_TINTS = [0xffffff, 0xffd6e0, 0xf7a8c4, 0xffe28a, 0xc9b8ff, 0xffb27a].map((h) => new THREE.Color(h));

// Places hand-authored and scattered props from level.json and draws each prop
// id as one InstancedModel.
export class Props {
  constructor(level, manifest, terrain, colliders) {
    this.level = level;
    this.manifest = manifest;
    this.terrain = terrain;
    this.colliders = colliders;
    this.placements = new Map(); // id -> [{x,z,rot,scale,y,scatter}]
    this.sets = new Map();
    this.group = new THREE.Group();
    this.group.name = 'props';
    this.warnings = [];
    this.placeAll();
  }

  entry(id) { return this.manifest.props?.[id]; }

  add(id, p) {
    if (!this.entry(id)) {
      const msg = `level references unknown prop id "${id}" (not in manifest.props); skipped`;
      if (!this.warnings.includes(msg)) { this.warnings.push(msg); console.warn('[level] ' + msg); }
      return null;
    }
    if (!this.placements.has(id)) this.placements.set(id, []);
    const rec = { rot: 0, scale: 1, ...p };
    rec.y = p.y ?? this.terrain.heightAt(rec.x, rec.z) + (p.lift || 0);
    this.placements.get(id).push(rec);
    return rec;
  }

  placeAll() {
    const L = this.level;
    for (const p of L.props || []) this.add(p.id, p);
    // Clear zones keep scatter off the play spaces.
    const clear = [
      ...L.lanterns.map((l) => ({ x: l.x, z: l.z, r: 6 })),
      { x: L.gate.x, z: L.gate.z, r: 8 },
      { x: L.player.spawn.x, z: L.player.spawn.z, r: 5 },
      ...(L.clearings || []),
      ...(L.props || []).map((p) => ({ x: p.x, z: p.z, r: 1.8 })),
    ];
    const placedSolid = [];
    for (const rule of L.scatter || []) {
      if (!this.entry(rule.id)) { this.add(rule.id, { x: 0, z: 0 }); continue; }
      const rnd = mulberry32(L.seed ^ hashString(rule.id));
      const half = this.terrain.size / 2 - 2;
      const want = rule.count ?? 0;
      const spacing = rule.spacing ?? 2;
      const w = rule.where || {};
      let placed = 0;
      for (let tries = 0; tries < want * 60 && placed < want; tries++) {
        const x = (rnd() * 2 - 1) * half, z = (rnd() * 2 - 1) * half;
        const h = this.terrain.heightAt(x, z);
        if (h < (w.heightMin ?? 0.6) || h > (w.heightMax ?? 99)) continue;
        const slope = this.terrain.slopeAt(x, z);
        if (slope > (w.slopeMax ?? 0.7) || slope < (w.slopeMin ?? 0)) continue;
        if (this.terrain.pathDistanceAt(x, z) < (w.pathMin ?? 2.5)) continue;
        if (clear.some((c) => (x - c.x) ** 2 + (z - c.z) ** 2 < c.r * c.r)) continue;
        if (rule.solid !== false && placedSolid.some((o) => (x - o.x) ** 2 + (z - o.z) ** 2 < Math.max(spacing, o.spacing) ** 2)) continue;
        if (rule.cluster) {
          // Rejection by noise makes woods and clearings instead of an even sprinkle.
          const n = Math.sin(x * 0.11 + 1.7) * Math.cos(z * 0.09 - 0.4) * 0.5 + 0.5;
          if (rnd() > n * rule.cluster + (1 - rule.cluster)) continue;
        }
        const scale = rule.scale ? rule.scale[0] + rnd() * (rule.scale[1] - rule.scale[0]) : 1;
        this.add(rule.id, { x, z, rot: rnd() * Math.PI * 2, scale, scatter: true });
        if (rule.solid !== false) placedSolid.push({ x, z, spacing });
        placed++;
      }
    }
  }

  // Builds instanced sets with placeholders right away (synchronously playable).
  build() {
    for (const [id, list] of this.placements) {
      const entry = this.entry(id);
      const proto = normalizeProto(buildPlaceholder(id, hashString(id)) || genericPlaceholder(), entry.height ?? 1);
      for (const rec of list) rec.colliders = this.colliders.addFromDef(entry.collider, rec.x, rec.z, rec.rot, rec.scale, id, entry.height ?? 1, rec.y);
      this.sets.set(id, { proto, chunks: [] });
      this.applyTransforms(id);
    }
  }

  // Large scattered sets are split into 2x2 spatial chunks (one InstancedModel
  // each) so the camera and shadow frustums can cull whole quadrants.
  applyTransforms(id) {
    const set = this.sets.get(id);
    const list = this.placements.get(id);
    for (const c of set.chunks) this.group.remove(c.group);
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
    const up = new THREE.Vector3(0, 1, 0);
    const tintRnd = mulberry32(hashString(id) + 99);
    // Heavy generated meshes: drop scatter instances to stay inside the budget.
    const probe = new InstancedModel('probe', set.proto, 1);
    const tri = probe.triangles();
    for (const mesh of probe.meshes) mesh.dispose();
    let limit = list.length;
    const hand = list.filter((r) => !r.scatter).length;
    if (tri * list.length > CFG.render.maxTrianglesPerPropSet) {
      limit = Math.max(hand, Math.floor(CFG.render.maxTrianglesPerPropSet / tri));
      console.warn(`[props] ${id}: ${tri} tris x ${list.length} instances is over budget; drawing ${limit}`);
    }
    // Hand-placed first so they survive any trimming.
    const ordered = [...list.filter((r) => !r.scatter), ...list.filter((r) => r.scatter)].slice(0, limit);
    for (const rec of list) { rec.chunk = null; rec.index = null; }
    const chunked = ordered.length >= 24;
    const buckets = [[], [], [], []];
    for (const rec of ordered) buckets[chunked ? (rec.x < 0 ? 0 : 1) + (rec.z < 0 ? 0 : 2) : 0].push(rec);
    set.chunks = [];
    for (const recs of buckets) {
      if (!recs.length) continue;
      const model = new InstancedModel(`prop:${id}:${set.chunks.length}`, set.proto, recs.length, { dynamic: id === 'gate_door' });
      recs.forEach((rec, i) => {
        const slope = this.terrain.slopeAt(rec.x, rec.z);
        const sink = Math.min(0.4, slope * 0.5 * (rec.scale || 1));
        p.set(rec.x, rec.y - sink, rec.z);
        q.setFromAxisAngle(up, rec.rot);
        s.setScalar(rec.scale);
        m.compose(p, q, s);
        model.setMatrixAt(i, m);
        rec.chunk = model;
        rec.index = i;
        if (id === 'flowers') model.setColorAt(i, FLOWER_TINTS[Math.floor(tintRnd() * FLOWER_TINTS.length)]);
      });
      model.commit();
      set.chunks.push(model);
      this.group.add(model.group);
    }
  }

  // Moves one placed instance (gate doors, chest pop). Call commitRec after.
  setRecMatrix(rec, m) { if (rec.chunk) rec.chunk.setMatrixAt(rec.index, m); }
  commitRec(rec) { rec.chunk?.commit(); }

  swapProto(id, proto) {
    const set = this.sets.get(id);
    if (!set) return;
    set.proto = proto;
    this.applyTransforms(id);
  }

  // On first load only real files replace the placeholders; on reload every set
  // is rebuilt so a deleted file falls back to its placeholder again.
  async loadAll(loader, reload = false) {
    const jobs = [];
    for (const id of this.sets.keys()) {
      const entry = { ...this.entry(id), ...(loader.manifest.props?.[id] || {}) };
      jobs.push(loader.loadStatic('props.' + id, entry, id, hashString(id)).then(({ proto, source }) => {
        if (source === 'file' || reload) this.swapProto(id, proto);
      }));
    }
    // Manifest props with no placements still get a status line.
    for (const id of Object.keys(this.manifest.props || {})) {
      if (!this.sets.has(id)) loader.status.set('props.' + id, 'placeholder', 'not placed in level');
    }
    await Promise.all(jobs);
  }

  triangles() {
    let t = 0;
    for (const s of this.sets.values()) for (const c of s.chunks) t += c.triangles() * c.count;
    return t;
  }
}

export { protoTriangles };
