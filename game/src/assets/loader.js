import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { makeToonMaterial, makeOutlineMaterial, addOutlineNormals } from '../render/toon.js';
import { buildPlaceholder, genericPlaceholder } from './placeholders.js';

// Used when manifest.json itself is missing or unreadable.
export const DEFAULT_MANIFEST = {
  version: 1,
  hero: { file: 'models/hero.glb', height: 1.5, facing: '+z', clips: { idle: 'idle', walk: 'walk', run: 'run', jump: 'jump', attack: 'attack' } },
  enemies: { mossling: { file: 'models/mossling.glb', height: 0.9, facing: '+z' } },
  props: {},
  sky: { file: 'textures/sky.jpg', type: 'equirect' },
  music: { field: 'audio/field.ogg', combat: 'audio/combat.ogg' },
};

const FACING = { '+z': 0, '-z': Math.PI, '+x': -Math.PI / 2, '-x': Math.PI / 2 };

class MissingAsset extends Error {}

export class AssetStatus {
  constructor() { this.entries = {}; this.listeners = new Set(); }
  set(key, state, reason = '', extra = {}) {
    this.entries[key] = { state, reason, ...extra };
    const line = `[assets] ${key}: ${state}${reason ? ' (' + reason + ')' : ''}`;
    if (state === 'failed') console.warn(line); else console.info(line);
    for (const fn of this.listeners) fn(key, this.entries[key]);
  }
  summary() {
    const counts = { loaded: 0, placeholder: 0, failed: 0, pending: 0 };
    for (const e of Object.values(this.entries)) counts[e.state] = (counts[e.state] || 0) + 1;
    return counts;
  }
  toJSON() { return JSON.parse(JSON.stringify(this.entries)); }
}

export class AssetLoader {
  constructor(renderer, baseUrl) {
    this.base = new URL(baseUrl, window.location.href);
    this.status = new AssetStatus();
    this.maxAniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    // three r187's DRACOLoader resolves its own bundled decoder (emitted by Vite).
    const draco = new DRACOLoader();
    this.gltf = new GLTFLoader();
    this.gltf.setDRACOLoader(draco);
    this.gltf.setMeshoptDecoder(MeshoptDecoder);
    this.manifest = null;
  }

  url(file) { return new URL(file, this.base).href; }

  async fetchBuffer(file) {
    let res;
    try {
      res = await fetch(this.url(file), { cache: 'no-cache' });
    } catch (e) {
      throw new MissingAsset('network error: ' + e.message);
    }
    if (!res.ok) throw new MissingAsset(`file not found (HTTP ${res.status})`);
    const ct = res.headers.get('content-type') || '';
    // Dev servers answer unknown paths with index.html; treat that as missing.
    if (ct.includes('text/html')) throw new MissingAsset('file not found (server returned HTML)');
    return res.arrayBuffer();
  }

  async loadManifest() {
    try {
      const buf = await this.fetchBuffer('manifest.json');
      this.manifest = JSON.parse(new TextDecoder().decode(buf));
      this.status.set('manifest', 'loaded');
    } catch (e) {
      this.manifest = structuredClone(DEFAULT_MANIFEST);
      this.status.set('manifest', 'placeholder', e.message + '; using built-in defaults');
    }
    return this.manifest;
  }

  async parseGLTF(file) {
    const buf = await this.fetchBuffer(file);
    const head = new Uint8Array(buf, 0, Math.min(4, buf.byteLength));
    const isGlb = head[0] === 0x67 && head[1] === 0x6c && head[2] === 0x54 && head[3] === 0x46; // 'glTF'
    const isJson = head[0] === 0x7b; // '{'
    if (!isGlb && !isJson) throw new Error('not a glTF/GLB file');
    const dir = this.url(file).replace(/[^/]*$/, '');
    return this.gltf.parseAsync(isGlb ? buf : new TextDecoder().decode(buf), dir);
  }

  // Static model -> instancing prototype. Falls back to the placeholder.
  async loadStatic(key, entry, placeholderId, seed = 1) {
    const height = entry?.height ?? 1;
    const facing = entry?.facing ?? '+z';
    const fallback = () => normalizeProto(buildPlaceholder(placeholderId, seed) || genericPlaceholder(), height, '+z');
    if (!entry?.file) {
      this.status.set(key, 'placeholder', 'no file in manifest');
      return { proto: fallback(), source: 'placeholder' };
    }
    try {
      const gltf = await this.parseGLTF(entry.file);
      const proto = this.protoFromScene(gltf.scene, height, facing);
      this.status.set(key, 'loaded', '', { file: entry.file, triangles: protoTriangles(proto), ...protoBounds(proto) });
      return { proto, source: 'file' };
    } catch (e) {
      const missing = e instanceof MissingAsset;
      this.status.set(key, missing ? 'placeholder' : 'failed', e.message, { file: entry.file });
      return { proto: fallback(), source: 'placeholder' };
    }
  }

  // Bakes every mesh into metres in the normalised frame and merges meshes
  // that share a base-colour texture, so one model costs few draw calls.
  // `matrixFor(box)` may replace the standard height/facing normalisation
  // (attachments use their own rule).
  protoFromScene(scene, height, facing, matrixFor = null, nearFade = true) {
    scene.updateMatrixWorld(true);
    const items = [];
    scene.traverse((o) => {
      if (!o.isMesh || !o.geometry?.getAttribute('position')) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      const groups = Array.isArray(o.material) && o.geometry.groups.length ? o.geometry.groups : [{ start: 0, count: Infinity, materialIndex: 0 }];
      for (const grp of groups) {
        let g = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry.clone();
        if (Array.isArray(o.material)) {
          const end = Math.min(grp.start + grp.count, g.getAttribute('position').count);
          g = sliceGeometry(g, grp.start, end);
        }
        g.applyMatrix4(o.matrixWorld);
        items.push({ geometry: g, material: mats[grp.materialIndex] || mats[0] });
      }
    });
    if (!items.length) throw new Error('model has no meshes');
    const box = new THREE.Box3();
    for (const it of items) { it.geometry.computeBoundingBox(); box.union(it.geometry.boundingBox); }
    const m = matrixFor ? matrixFor(box) : normalizeMatrix(box, height, facing);
    const byKey = new Map();
    for (const it of items) {
      const g = cleanGeometry(it.geometry);
      g.applyMatrix4(m);
      const src = it.material || {};
      const map = src.map || null;
      const hasColor = !!g.getAttribute('color');
      const key = `${map ? map.uuid : 'none'}|${src.color ? src.color.getHexString() : 'fff'}|${hasColor}|${!!g.getAttribute('uv')}|${src.side ?? 0}`;
      if (!byKey.has(key)) byKey.set(key, { src, map, hasColor, geos: [] });
      byKey.get(key).geos.push(g);
    }
    const parts = [];
    for (const { src, map, hasColor, geos } of byKey.values()) {
      if (map) this.prepTexture(map);
      const geometry = geos.length === 1 ? geos[0] : mergeGeometries(geos, false);
      if (!geometry) continue;
      // Generated meshes are often open sheets (leaves, torn panels); the asset
      // stage marks those doubleSided in the glTF and the toon pass honours it.
      const material = makeToonMaterial({ color: src.color ? src.color.getHex() : 0xffffff, map, vertexColors: hasColor, nearFade, side: src.side ?? THREE.FrontSide });
      parts.push({ geometry, kind: 'toon', material, outline: true, castShadow: true });
    }
    return { parts };
  }

  prepTexture(tex) {
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = this.maxAniso;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    tex.magFilter = THREE.LinearFilter;
    tex.generateMipmaps = true;
    tex.needsUpdate = true;
  }

  // Hero: keeps the scene graph so skinning/clips work. Returns null when the
  // file is missing or broken (caller uses the primitive hero).
  async loadHero(entry) {
    const key = 'hero';
    if (!entry?.file) { this.status.set(key, 'placeholder', 'no file in manifest'); return null; }
    try {
      const gltf = await this.parseGLTF(entry.file);
      const scene = gltf.scene;
      scene.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(scene, true);
      const size = box.getSize(new THREE.Vector3());
      if (!(size.y > 1e-6)) throw new Error('model has zero height');
      const s = entry.height / size.y;
      const center = box.getCenter(new THREE.Vector3());
      const pivot = new THREE.Group();
      pivot.name = 'hero-model';
      const facingGroup = new THREE.Group();
      facingGroup.rotation.y = FACING[entry.facing ?? '+z'] ?? 0;
      const scaler = new THREE.Group();
      scaler.scale.setScalar(s);
      scaler.position.set(-center.x * s, -box.min.y * s, -center.z * s);
      scaler.add(scene);
      facingGroup.add(scaler);
      pivot.add(facingGroup);
      let skinned = false;
      const meshes = [];
      scene.traverse((o) => { if (o.isMesh) meshes.push(o); });
      for (const o of meshes) {
        if (o.isSkinnedMesh) skinned = true;
        o.material = reshade(o.material, o.geometry, this, false);
        o.castShadow = true;
        o.frustumCulled = false;
        addOutlineNormals(o.geometry);
        let outline;
        if (o.isSkinnedMesh) {
          outline = new THREE.SkinnedMesh(o.geometry, makeOutlineMaterial(undefined, false));
          outline.bind(o.skeleton, o.bindMatrix);
          outline.bindMode = o.bindMode;
        } else {
          outline = new THREE.Mesh(o.geometry, makeOutlineMaterial(undefined, false));
        }
        outline.frustumCulled = false;
        outline.name = 'outline';
        o.add(outline);
      }
      const clips = gltf.animations || [];
      const mode = clips.length ? 'clips' : 'static';
      pivot.updateMatrixWorld(true);
      const nb = new THREE.Box3().setFromObject(pivot, true);
      const ns = nb.getSize(new THREE.Vector3());
      const bounds = { size: [ns.x, ns.y, ns.z].map((v) => +v.toFixed(3)), minY: +nb.min.y.toFixed(3) };
      this.status.set(key, 'loaded', mode === 'clips' ? `${clips.length} clips, ${skinned ? 'skinned' : 'node'} animation` : 'no clips: procedural animation', { file: entry.file, mode, clips: clips.map((c) => c.name), ...bounds });
      return { root: pivot, clips, mode, skinned };
    } catch (e) {
      const missing = e instanceof MissingAsset;
      this.status.set(key, missing ? 'placeholder' : 'failed', e.message, { file: entry.file });
      return null;
    }
  }

  // Hand-held props (hero.attachments[]). Returns [{ entry, proto }] for the
  // ones that loaded; each gets a 'hero.attachments.<id>' status line.
  async loadAttachments(list) {
    const out = [];
    for (const [i, a] of (list || []).entries()) {
      const id = a.id || `#${i}`;
      const key = `hero.attachments.${id}`;
      if (!a.file) { this.status.set(key, 'placeholder', 'no file in manifest'); continue; }
      try {
        const gltf = await this.parseGLTF(a.file);
        const proto = this.protoFromScene(gltf.scene, 0, '+z', (box) => attachmentMatrix(box, a), false);
        this.status.set(key, 'pending', 'loaded; waiting for bone', { file: a.file, bone: a.bone, triangles: protoTriangles(proto), ...protoBounds(proto) });
        out.push({ id, key, entry: a, proto });
      } catch (e) {
        this.status.set(key, e instanceof MissingAsset ? 'placeholder' : 'failed', e.message + '; procedural sword arc only', { file: a.file, bone: a.bone });
      }
    }
    return out;
  }

  async loadSky(entry) {
    if (!entry?.file) { this.status.set('sky', 'placeholder', 'no file in manifest'); return null; }
    try {
      const buf = await this.fetchBuffer(entry.file);
      const blob = new Blob([buf]);
      const bmp = await createImageBitmap(blob, { imageOrientation: 'flipY' });
      const tex = new THREE.Texture(bmp);
      tex.flipY = false; // already flipped by createImageBitmap
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.wrapS = THREE.RepeatWrapping;
      tex.minFilter = THREE.LinearFilter;
      tex.generateMipmaps = false;
      tex.needsUpdate = true;
      this.status.set('sky', 'loaded', '', { file: entry.file, size: `${bmp.width}x${bmp.height}` });
      return tex;
    } catch (e) {
      this.status.set('sky', e instanceof MissingAsset ? 'placeholder' : 'failed', e.message, { file: entry.file });
      return null;
    }
  }

  // Music entries are a path or a list of candidate paths (e.g. ogg then m4a).
  // Decoded with an OfflineAudioContext so it works before the first gesture.
  async loadMusic(music) {
    const out = {};
    for (const [name, value] of Object.entries(music || {})) {
      const key = 'music.' + name;
      const files = Array.isArray(value) ? value : [value];
      let lastErr = null, missingOnly = true;
      for (const file of files) {
        try {
          const buf = await this.fetchBuffer(file);
          const ctx = new OfflineAudioContext(2, 1, 44100);
          out[name] = await ctx.decodeAudioData(buf);
          this.status.set(key, 'loaded', '', { file, seconds: +out[name].duration.toFixed(1) });
          break;
        } catch (e) {
          lastErr = e;
          if (!(e instanceof MissingAsset)) missingOnly = false;
        }
      }
      if (!out[name]) this.status.set(key, missingOnly ? 'placeholder' : 'failed', (lastErr?.message || 'no file') + (missingOnly ? '; silence' : ''), { file: files.join(', ') });
    }
    return out;
  }
}

function sliceGeometry(g, start, end) {
  const out = new THREE.BufferGeometry();
  for (const [name, attr] of Object.entries(g.attributes)) {
    const size = attr.itemSize;
    out.setAttribute(name, new THREE.BufferAttribute(attr.array.slice(start * size, end * size), size, attr.normalized));
  }
  return out;
}

// Keep only what the toon material uses.
function cleanGeometry(g) {
  const keep = new Set(['position', 'normal', 'uv', 'color']);
  for (const name of Object.keys(g.attributes)) if (!keep.has(name)) g.deleteAttribute(name);
  g.morphAttributes = {};
  if (!g.getAttribute('normal')) g.computeVertexNormals();
  const col = g.getAttribute('color');
  if (col && col.itemSize === 4) {
    const n = col.count, rgb = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) { rgb[i * 3] = col.getX(i); rgb[i * 3 + 1] = col.getY(i); rgb[i * 3 + 2] = col.getZ(i); }
    g.setAttribute('color', new THREE.BufferAttribute(rgb, 3));
  }
  return g;
}

export function normalizeMatrix(box, height, facing) {
  const size = box.getSize(new THREE.Vector3());
  if (!(size.y > 1e-6)) throw new Error('model has zero height');
  const s = height / size.y;
  const c = box.getCenter(new THREE.Vector3());
  const t = new THREE.Matrix4().makeTranslation(-c.x, -box.min.y, -c.z);
  const sc = new THREE.Matrix4().makeScale(s, s, s);
  const r = new THREE.Matrix4().makeRotationY(FACING[facing] ?? 0);
  return r.multiply(sc).multiply(t);
}

// Attachment rule: the longest bounding-box axis is the item's length axis,
// with the handle at its minimum end. Scale so that axis is `length` metres,
// put the grip point (`grip` fraction from the handle end, centred on the
// other two axes) at the origin, and turn the length axis to +Y.
export function attachmentMatrix(box, a) {
  const size = box.getSize(new THREE.Vector3());
  const axis = size.x >= size.y && size.x >= size.z ? 'x' : size.z > size.y ? 'z' : 'y';
  const len = size[axis];
  if (!(len > 1e-6)) throw new Error('attachment has zero size');
  const s = (a.length ?? 0.75) / len;
  const c = box.getCenter(new THREE.Vector3());
  const atMax = a.handle === 'max';
  c[axis] = atMax ? box.max[axis] - (a.grip ?? 0.15) * len : box.min[axis] + (a.grip ?? 0.15) * len;
  const t = new THREE.Matrix4().makeTranslation(-c.x, -c.y, -c.z);
  const sc = new THREE.Matrix4().makeScale(s, s, s);
  // +X -> +Y is a +90 deg turn about Z; +Z -> +Y is -90 deg about X.
  const r = axis === 'x' ? new THREE.Matrix4().makeRotationZ(Math.PI / 2) : axis === 'z' ? new THREE.Matrix4().makeRotationX(-Math.PI / 2) : new THREE.Matrix4();
  // handle: "max" means the handle sits at the maximum end: turn it half way first.
  const flip = !atMax ? new THREE.Matrix4() : axis === 'x' ? new THREE.Matrix4().makeRotationZ(Math.PI) : new THREE.Matrix4().makeRotationX(Math.PI);
  return r.multiply(flip).multiply(sc).multiply(t);
}

// Same rule for placeholders: base centre at origin, exact manifest height.
export function normalizeProto(proto, height, facing = '+z') {
  const box = new THREE.Box3();
  for (const p of proto.parts) { p.geometry.computeBoundingBox(); box.union(p.geometry.boundingBox); }
  const m = normalizeMatrix(box, height, facing);
  for (const p of proto.parts) { p.geometry.applyMatrix4(m); p.geometry.computeBoundingSphere(); }
  return proto;
}

// Size after normalisation, reported in the status so agents can verify it.
export function protoBounds(proto) {
  const box = new THREE.Box3();
  for (const p of proto.parts) { p.geometry.computeBoundingBox(); box.union(p.geometry.boundingBox); }
  const s = box.getSize(new THREE.Vector3());
  return { size: [s.x, s.y, s.z].map((v) => +v.toFixed(3)), minY: +box.min.y.toFixed(3) };
}

export function protoTriangles(proto) {
  let t = 0;
  for (const p of proto.parts) {
    const g = p.geometry;
    t += (g.index ? g.index.count : g.getAttribute('position').count) / 3;
  }
  return Math.round(t);
}

function reshade(material, geometry, loader, nearFade = true) {
  const one = (src) => {
    const map = src?.map || null;
    if (map) loader.prepTexture(map);
    return makeToonMaterial({ color: src?.color ? src.color.getHex() : 0xffffff, map, vertexColors: !!geometry.getAttribute('color'), nearFade, side: src?.side ?? THREE.FrontSide });
  };
  return Array.isArray(material) ? material.map(one) : one(material);
}
