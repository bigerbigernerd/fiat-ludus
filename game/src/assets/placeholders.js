import * as THREE from 'three';
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { mulberry32 } from '../rng.js';

// Built-in stand-ins made of primitives. Each builder returns a prototype
// { parts: [{ geometry, kind, outline, castShadow, tint }] } in metres with the
// front facing +Z; the loader then normalises it exactly like a GLB, so the
// placeholder always has the manifest's height and the same collider.
//   kind: 'toon' (vertex-coloured toon shading) | 'glow' (unlit, bright)
//   tint: per-instance colour is applied to this part (flowers, enemy eyes)

const tmpColor = new THREE.Color();

function prep(geo) {
  const g = geo.index ? geo.toNonIndexed() : geo;
  g.deleteAttribute('uv');
  if (!g.getAttribute('normal')) g.computeVertexNormals();
  return g;
}

// Clone, transform and vertex-paint one primitive.
function piece(geo, hex, { p = [0, 0, 0], r = [0, 0, 0], s = [1, 1, 1] } = {}) {
  const g = prep(geo.clone());
  const m = new THREE.Matrix4().compose(
    new THREE.Vector3(...p),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...r)),
    new THREE.Vector3(...s),
  );
  g.applyMatrix4(m);
  const n = g.getAttribute('position').count;
  const col = new Float32Array(n * 3);
  tmpColor.setHex(hex);
  for (let i = 0; i < n; i++) col.set([tmpColor.r, tmpColor.g, tmpColor.b], i * 3);
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  return g;
}

// Displace a closed primitive along its normals for an organic, faceted blob.
function lumpy(geo, amount, seed) {
  let g = geo.clone();
  g.deleteAttribute('normal');
  g.deleteAttribute('uv');
  g = mergeVertices(g);
  const rnd = mulberry32(seed);
  const pos = g.getAttribute('position');
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    const len = v.length() || 1;
    v.multiplyScalar(1 + (rnd() - 0.5) * 2 * amount / len);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  g = g.toNonIndexed();
  g.computeVertexNormals();
  return g;
}

const merge = (list) => mergeGeometries(list, false);
const toon = (geometry, extra = {}) => ({ geometry, kind: 'toon', outline: true, castShadow: true, ...extra });

const BOX = new THREE.BoxGeometry(1, 1, 1);
const CYL = (top = 0.5, bot = 0.5, seg = 8) => new THREE.CylinderGeometry(top, bot, 1, seg);
const SPH = (detail = 1) => new THREE.IcosahedronGeometry(0.5, detail);

const COL = {
  stone: 0xc2b9a8, stoneDark: 0x9b9283, moss: 0x86b24f, mossDark: 0x5f8f3e,
  wood: 0xa7744a, woodDark: 0x76502f, bark: 0x7b5638, leaf: 0x67ad47, leafLight: 0x8cc85a,
  leafDark: 0x4e9142, pine: 0x3f8460, pineDark: 0x2f6c50, gold: 0xe8b84a, rock: 0xaaa298,
  cream: 0xf3e6c8, red: 0xd2483c, skin: 0xf2c49b, hair: 0x5a3a28,
};

const builders = {
  tree_broadleaf(seed) {
    const r = mulberry32(seed);
    const trunk = merge([
      piece(CYL(0.2, 0.32, 7), COL.bark, { p: [0, 1.4, 0], s: [1, 2.8, 1] }),
      piece(CYL(0.08, 0.13, 5), COL.bark, { p: [0.45, 2.6, 0.1], r: [0, 0, -0.8], s: [1, 1.2, 1] }),
    ]);
    const blobs = [];
    const spots = [[0, 4.2, 0, 2.4], [1.0, 3.5, 0.5, 1.7], [-0.9, 3.6, -0.3, 1.8], [0.1, 4.9, -0.2, 1.6]];
    spots.forEach(([x, y, z, s], i) => {
      const hex = i % 2 ? COL.leafLight : COL.leaf;
      blobs.push(piece(lumpy(SPH(1), 0.08, seed + i), hex, { p: [x, y, z], s: [s * (0.9 + r() * 0.2), s * 0.85, s] }));
    });
    return { parts: [toon(merge([trunk, ...blobs]))] };
  },
  tree_pine(seed) {
    const trunk = piece(CYL(0.16, 0.26, 6), COL.bark, { p: [0, 1.0, 0], s: [1, 2.0, 1] });
    const tiers = [];
    [[1.6, 2.6, 2.4], [2.9, 2.1, 2.1], [4.0, 1.6, 1.9], [5.0, 1.0, 1.5]].forEach(([y, rad, h], i) => {
      tiers.push(piece(new THREE.ConeGeometry(rad, h, 7), i % 2 ? COL.pine : COL.pineDark, { p: [0, y + h / 2 - 0.4, 0], r: [0, i * 0.4, 0] }));
    });
    return { parts: [toon(merge([trunk, ...tiers]))] };
  },
  rock_large(seed) {
    return { parts: [toon(merge([
      piece(lumpy(new THREE.DodecahedronGeometry(1, 1), 0.18, seed), COL.rock, { p: [0, 0.75, 0], s: [1.5, 1.0, 1.25] }),
      piece(lumpy(new THREE.DodecahedronGeometry(0.6, 0), 0.1, seed + 1), COL.moss, { p: [0.15, 1.55, 0.1], s: [1.4, 0.35, 1.2] }),
    ]))] };
  },
  rock_small(seed) {
    return { parts: [toon(piece(lumpy(new THREE.DodecahedronGeometry(1, 0), 0.15, seed), COL.rock, { p: [0, 0.5, 0], s: [1.2, 0.75, 1] }), { castShadow: false })] };
  },
  bush(seed) {
    const r = mulberry32(seed);
    const blobs = [];
    [[0, 0.55, 0, 1.3], [0.6, 0.45, 0.15, 0.95], [-0.5, 0.45, -0.2, 1.0]].forEach(([x, y, z, s], i) => {
      blobs.push(piece(lumpy(SPH(1), 0.06, seed + i), i % 2 ? COL.leafDark : COL.leaf, { p: [x, y, z], s: [s, s * (0.8 + r() * 0.15), s] }));
    });
    return { parts: [toon(merge(blobs), { castShadow: false })] };
  },
  flowers() {
    const stems = [], blooms = [];
    [[0, 0], [0.2, 0.12], [-0.14, 0.14]].forEach(([x, z], i) => {
      const h = 0.32 + i * 0.05;
      stems.push(piece(CYL(0.012, 0.015, 3), COL.leafDark, { p: [x, h / 2, z], s: [1, h, 1] }));
      stems.push(piece(new THREE.OctahedronGeometry(0.5, 0), COL.leaf, { p: [x + 0.04, 0.08, z], s: [0.14, 0.03, 0.06] }));
      // Five-point flat bloom: a low 5-sided cone reads as petals once outlined by the ramp.
      blooms.push(piece(new THREE.ConeGeometry(0.1, 0.04, 5), 0xffffff, { p: [x, h, z], r: [Math.PI, 0, 0] }));
      blooms.push(piece(new THREE.OctahedronGeometry(0.5, 0), 0xffe9a0, { p: [x, h + 0.015, z], s: [0.055, 0.035, 0.055] }));
    });
    return { parts: [
      { geometry: merge(stems), kind: 'toon', outline: false, castShadow: false },
      { geometry: merge(blooms), kind: 'toon', outline: false, castShadow: false, tint: true },
    ] };
  },
  ruin_pillar(seed) {
    const r = mulberry32(seed);
    const parts = [piece(BOX, COL.stoneDark, { p: [0, 0.15, 0], s: [1.1, 0.3, 1.1] })];
    let y = 0.3;
    for (let i = 0; i < 3; i++) {
      const h = 0.85 + r() * 0.2;
      parts.push(piece(CYL(0.36, 0.38, 8), i % 2 ? COL.stone : COL.stoneDark, { p: [(r() - 0.5) * 0.06, y + h / 2, (r() - 0.5) * 0.06], r: [0, r(), (r() - 0.5) * 0.05], s: [1, h, 1] }));
      y += h;
    }
    parts.push(piece(lumpy(SPH(1), 0.04, seed), COL.moss, { p: [0, y - 0.05, 0], s: [0.85, 0.3, 0.85] }));
    return { parts: [toon(merge(parts))] };
  },
  ruin_arch() {
    const p = [];
    for (const x of [-1.5, 1.5]) {
      p.push(piece(BOX, COL.stoneDark, { p: [x, 0.15, 0], s: [1.0, 0.3, 1.0] }));
      p.push(piece(BOX, COL.stone, { p: [x, 1.8, 0], s: [0.7, 3.0, 0.7] }));
    }
    p.push(piece(BOX, COL.stone, { p: [-0.4, 3.55, 0], s: [3.4, 0.55, 0.85], r: [0, 0, -0.05] }));
    p.push(piece(BOX, COL.stoneDark, { p: [1.55, 3.45, 0.05], s: [0.8, 0.4, 0.8], r: [0, 0.2, 0.3] }));
    p.push(piece(lumpy(SPH(1), 0.04, 5), COL.moss, { p: [-1.0, 3.85, 0], s: [1.6, 0.25, 0.8] }));
    return { parts: [toon(merge(p))] };
  },
  fence() {
    const p = [];
    for (const x of [-1.15, 1.15]) p.push(piece(BOX, COL.woodDark, { p: [x, 0.5, 0], s: [0.14, 1.0, 0.14] }));
    p.push(piece(BOX, COL.wood, { p: [0, 0.75, 0], s: [2.5, 0.1, 0.06] }));
    p.push(piece(BOX, COL.wood, { p: [0, 0.4, 0], s: [2.5, 0.1, 0.06], r: [0, 0, 0.04] }));
    return { parts: [toon(merge(p), { castShadow: false })] };
  },
  signpost() {
    const p = [piece(BOX, COL.woodDark, { p: [0, 0.9, 0], s: [0.13, 1.8, 0.13] })];
    p.push(piece(BOX, COL.wood, { p: [0.35, 1.55, 0.05], s: [0.75, 0.22, 0.05] }));
    p.push(piece(new THREE.ConeGeometry(0.16, 0.2, 3), COL.wood, { p: [0.82, 1.55, 0.05], r: [0, 0, -Math.PI / 2], s: [1, 1, 0.3] }));
    p.push(piece(BOX, COL.wood, { p: [-0.3, 1.2, 0.05], s: [0.65, 0.2, 0.05], r: [0, 0, 0.05] }));
    p.push(piece(new THREE.ConeGeometry(0.15, 0.2, 3), COL.wood, { p: [-0.72, 1.18, 0.05], r: [0, 0, Math.PI / 2], s: [1, 1, 0.3] }));
    return { parts: [toon(merge(p))] };
  },
  crate() {
    const p = [piece(BOX, COL.wood, { p: [0, 0.4, 0], s: [0.8, 0.8, 0.8] })];
    for (const [sx, sz] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      p.push(piece(BOX, COL.woodDark, { p: [sx * 0.37, 0.4, sz * 0.37], s: [0.1, 0.82, 0.1] }));
    }
    p.push(piece(BOX, COL.woodDark, { p: [0, 0.4, 0.405], s: [0.85, 0.1, 0.02], r: [0, 0, 0.78] }));
    return { parts: [toon(merge(p))] };
  },
  chest() {
    const p = [piece(BOX, COL.wood, { p: [0, 0.25, 0], s: [0.95, 0.5, 0.62] })];
    p.push(piece(new THREE.CylinderGeometry(0.31, 0.31, 0.95, 10, 1, false, 0, Math.PI), COL.woodDark, { p: [0, 0.5, 0], r: [0, 0, Math.PI / 2] }));
    for (const x of [-0.3, 0.3]) p.push(piece(BOX, COL.gold, { p: [x, 0.42, 0], s: [0.08, 0.86, 0.66] }));
    p.push(piece(BOX, COL.gold, { p: [0, 0.48, 0.32], s: [0.14, 0.18, 0.05] }));
    return { parts: [toon(merge(p))] };
  },
  lantern() {
    const p = [];
    p.push(piece(BOX, COL.stoneDark, { p: [0, 0.14, 0], s: [1.05, 0.28, 1.05] }));
    p.push(piece(BOX, COL.stone, { p: [0, 0.36, 0], s: [0.8, 0.16, 0.8] }));
    p.push(piece(CYL(0.2, 0.26, 8), COL.stone, { p: [0, 0.85, 0], s: [1, 0.85, 1] }));
    p.push(piece(BOX, COL.stoneDark, { p: [0, 1.32, 0], s: [0.85, 0.12, 0.85] }));
    for (const [sx, sz] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      p.push(piece(BOX, COL.stone, { p: [sx * 0.33, 1.62, sz * 0.33], s: [0.14, 0.5, 0.14] }));
    }
    p.push(piece(BOX, COL.stoneDark, { p: [0, 1.92, 0], s: [0.85, 0.1, 0.85] }));
    p.push(piece(new THREE.ConeGeometry(0.9, 0.42, 4), COL.stone, { p: [0, 2.15, 0], r: [0, Math.PI / 4, 0] }));
    p.push(piece(lumpy(SPH(1), 0.03, 3), COL.moss, { p: [0.15, 2.05, 0.2], s: [0.7, 0.18, 0.55] }));
    p.push(piece(SPH(1), COL.stoneDark, { p: [0, 2.42, 0], s: [0.16, 0.16, 0.16] }));
    p.push(piece(CYL(0.02, 0.025, 5), COL.woodDark, { p: [0, 2.53, 0.04], s: [1, 0.36, 1] }));
    return { parts: [toon(merge(p))] };
  },
  gate() {
    const p = [];
    for (const x of [-2.9, 2.9]) {
      p.push(piece(BOX, COL.stoneDark, { p: [x, 0.25, 0], s: [1.7, 0.5, 1.7] }));
      p.push(piece(BOX, COL.stone, { p: [x, 2.4, 0], s: [1.25, 3.9, 1.25] }));
      p.push(piece(BOX, COL.stoneDark, { p: [x, 1.6, 0], s: [1.32, 0.18, 1.32] }));
      p.push(piece(BOX, COL.stoneDark, { p: [x, 3.3, 0], s: [1.32, 0.18, 1.32] }));
    }
    p.push(piece(BOX, COL.stone, { p: [0, 4.75, 0], s: [7.6, 0.9, 1.5] }));
    p.push(piece(BOX, COL.stoneDark, { p: [0, 5.3, 0], s: [8.2, 0.25, 1.7] }));
    for (const x of [-1.6, 0, 1.6]) p.push(piece(CYL(0.32, 0.32, 10), 0x6a6258, { p: [x, 4.75, 0.72], r: [Math.PI / 2, 0, 0], s: [1, 0.1, 1] }));
    p.push(piece(lumpy(SPH(1), 0.05, 9), COL.moss, { p: [-2.4, 5.45, 0.1], s: [2.6, 0.3, 1.4] }));
    p.push(piece(lumpy(SPH(1), 0.05, 10), COL.moss, { p: [2.9, 0.6, 0.4], s: [1.4, 0.5, 1.2] }));
    return { parts: [toon(merge(p))] };
  },
  gate_door() {
    const p = [piece(BOX, 0x8d8477, { p: [0, 2.15, 0], s: [2.3, 4.3, 0.45] })];
    for (const y of [1.2, 2.5, 3.6]) p.push(piece(BOX, 0x6f675c, { p: [0, y, 0.24], s: [1.9, 0.12, 0.05] }));
    p.push(piece(BOX, 0x6f675c, { p: [0, 2.15, 0.24], s: [0.12, 3.6, 0.05] }));
    return { parts: [toon(merge(p))] };
  },
  mossling(seed = 7) {
    const body = merge([
      piece(lumpy(new THREE.IcosahedronGeometry(0.5, 1), 0.06, seed), 0x9d9a8c, { p: [0, 0.42, 0], s: [1.25, 0.9, 1.15] }),
      piece(lumpy(new THREE.IcosahedronGeometry(0.5, 1), 0.05, seed + 1), COL.moss, { p: [0, 0.7, -0.04], s: [1.2, 0.5, 1.1] }),
      piece(lumpy(SPH(0), 0.02, seed + 2), COL.mossDark, { p: [0.2, 0.92, -0.1], s: [0.35, 0.2, 0.3] }),
      piece(SPH(0), 0x7e7a6e, { p: [-0.28, 0.07, 0.12], s: [0.3, 0.16, 0.36] }),
      piece(SPH(0), 0x7e7a6e, { p: [0.28, 0.07, 0.12], s: [0.3, 0.16, 0.36] }),
      piece(SPH(0), 0x3b3630, { p: [-0.2, 0.5, 0.52], s: [0.2, 0.24, 0.1] }),
      piece(SPH(0), 0x3b3630, { p: [0.2, 0.5, 0.52], s: [0.2, 0.24, 0.1] }),
    ]);
    const eyes = merge([
      piece(SPH(0), 0xffffff, { p: [-0.2, 0.52, 0.565], s: [0.12, 0.15, 0.06] }),
      piece(SPH(0), 0xffffff, { p: [0.2, 0.52, 0.565], s: [0.12, 0.15, 0.06] }),
    ]);
    return { parts: [toon(body, { tint: true }), { geometry: eyes, kind: 'glow', outline: false, castShadow: false, tint: true }] };
  },
};

export function hasPlaceholder(id) { return id in builders; }

export function buildPlaceholder(id, seed = 1) {
  const b = builders[id];
  if (!b) return null;
  return b(seed);
}

// A generic box stand-in for prop ids that have no builder.
export function genericPlaceholder() {
  return { parts: [toon(piece(BOX, 0xb7a58c, { p: [0, 0.5, 0] }))] };
}

export { piece, lumpy, merge, COL, SPH, CYL, BOX };
