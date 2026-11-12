import * as THREE from 'three';
import { makeToonMaterial, makeOutlineMaterial, addOutlineNormals } from '../render/toon.js';

const shared = {};
function kindMaterial(kind) {
  if (!shared[kind]) {
    shared[kind] = kind === 'glow'
      ? new THREE.MeshBasicMaterial({ vertexColors: true, fog: true })
      : makeToonMaterial({ vertexColors: true });
  }
  return shared[kind];
}

// A prototype (list of parts) drawn as InstancedMeshes: one per part plus one
// outline per outlined part, all sharing the same instance matrices. Swapping
// the prototype (placeholder -> generated model) keeps every instance.
export class InstancedModel {
  constructor(name, proto, capacity, { dynamic = false, castShadow = true } = {}) {
    this.name = name;
    this.capacity = capacity;
    this.dynamic = dynamic;
    this.castShadow = castShadow;
    this.group = new THREE.Group();
    this.group.name = name;
    this.matrices = new Float32Array(capacity * 16);
    this.colors = []; // one Float32Array per tinted part
    const id = new THREE.Matrix4();
    for (let i = 0; i < capacity; i++) id.toArray(this.matrices, i * 16);
    this.count = capacity;
    this.setProto(proto);
  }

  setProto(proto) {
    for (const m of this.meshes || []) { this.group.remove(m); m.dispose(); }
    this.meshes = [];
    this.tinted = [];
    this.proto = proto;
    const matrixAttr = new THREE.InstancedBufferAttribute(this.matrices, 16);
    if (this.dynamic) matrixAttr.setUsage(THREE.DynamicDrawUsage);
    for (const part of proto.parts) {
      const mat = part.material || kindMaterial(part.kind);
      const mesh = new THREE.InstancedMesh(part.geometry, mat, this.capacity);
      mesh.instanceMatrix = matrixAttr;
      mesh.count = this.count;
      mesh.castShadow = this.castShadow && part.castShadow !== false;
      mesh.receiveShadow = part.kind !== 'glow';
      if (part.tint) {
        const k = this.tinted.length;
        if (!this.colors[k]) this.colors[k] = new Float32Array(this.capacity * 3).fill(1);
        mesh.instanceColor = new THREE.InstancedBufferAttribute(this.colors[k], 3);
        if (this.dynamic) mesh.instanceColor.setUsage(THREE.DynamicDrawUsage);
        this.tinted.push(mesh);
      }
      this.meshes.push(mesh);
      this.group.add(mesh);
      if (part.outline) {
        addOutlineNormals(part.geometry);
        const ol = new THREE.InstancedMesh(part.geometry, makeOutlineMaterial(), this.capacity);
        ol.instanceMatrix = matrixAttr;
        ol.count = this.count;
        ol.castShadow = false;
        this.meshes.push(ol);
        this.group.add(ol);
      }
    }
    this.matrixAttr = matrixAttr;
    if (this.dynamic) for (const m of this.meshes) m.frustumCulled = false;
    this.commit();
  }

  setMatrixAt(i, m) { m.toArray(this.matrices, i * 16); }
  // `part` counts tinted parts only (0 = first part with tint: true).
  setColorAt(i, c, part = 0) {
    const a = this.colors[part];
    if (a) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b; }
  }
  setCount(n) { this.count = n; for (const m of this.meshes) m.count = n; }

  commit() {
    this.matrixAttr.needsUpdate = true;
    for (const m of this.tinted) m.instanceColor.needsUpdate = true;
    if (!this.dynamic) for (const m of this.meshes) { m.boundingSphere = null; m.computeBoundingSphere(); }
  }

  triangles() {
    let t = 0;
    for (const p of this.proto.parts) {
      const g = p.geometry;
      const tri = (g.index ? g.index.count : g.getAttribute('position').count) / 3;
      t += tri * (p.outline ? 2 : 1);
    }
    return t;
  }
}
