import * as THREE from 'three';
import { mulberry32 } from './rng.js';
import { glowTexture } from './render/toon.js';

// Visual-only effects: pooled soft particles, the sword slash arc, and the
// camera trauma value. Nothing here feeds back into the simulation.
export class FX {
  constructor(capacity = 420) {
    this.rng = mulberry32(4242);
    this.cap = capacity;
    this.parts = [];
    const geo = new THREE.BufferGeometry();
    this.aPos = new Float32Array(capacity * 3);
    this.aCol = new Float32Array(capacity * 3);
    this.aSize = new Float32Array(capacity);
    this.aAlpha = new Float32Array(capacity);
    geo.setAttribute('position', new THREE.BufferAttribute(this.aPos, 3).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('color', new THREE.BufferAttribute(this.aCol, 3).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('size', new THREE.BufferAttribute(this.aSize, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('alpha', new THREE.BufferAttribute(this.aAlpha, 1).setUsage(THREE.DynamicDrawUsage));
    this.uniforms = { uScale: { value: 600 }, uMaxSize: { value: 48 }, uTex: { value: glowTexture() } };
    const mat = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      transparent: true,
      depthWrite: false,
      vertexShader: /* glsl */ `
        attribute float size; attribute float alpha; attribute vec3 color;
        uniform float uScale;
        uniform float uMaxSize;
        varying vec3 vColor; varying float vAlpha;
        void main() {
          vColor = color; vAlpha = alpha;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = min(size * uScale / max(-mv.z, 0.1), uMaxSize);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */ `
        varying vec3 vColor; varying float vAlpha;
        void main() {
          vec2 c = gl_PointCoord - 0.5;
          float d = length(c);
          float a = smoothstep(0.5, 0.36, d);
          if (a * vAlpha < 0.01) discard;
          gl_FragColor = vec4(vColor, a * vAlpha);
          #include <colorspace_fragment>
        }`,
    });
    this.points = new THREE.Points(geo, mat);
    this.points.frustumCulled = false;
    this.points.renderOrder = 5;

    // Slash arc in front of the hero.
    const ring = new THREE.RingGeometry(0.55, 1.75, 24, 1, -Math.PI / 2 - 1.15, 2.3);
    ring.rotateX(-Math.PI / 2);
    const cols = new Float32Array(ring.getAttribute('position').count * 4);
    const pos = ring.getAttribute('position');
    for (let i = 0; i < pos.count; i++) {
      const a = Math.atan2(pos.getX(i), pos.getZ(i)); // -1.15..1.15 across the arc
      const r = Math.hypot(pos.getX(i), pos.getZ(i));
      const w = (1 - Math.abs(a) / 1.2) * (r > 1.2 ? 1 : 0.35);
      cols.set([1, 0.97, 0.88, Math.max(0, w)], i * 4);
    }
    ring.setAttribute('color', new THREE.BufferAttribute(cols, 4));
    this.slash = new THREE.Mesh(ring, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, opacity: 0 }));
    this.slash.visible = false;
    this.slashT = 1;

    this.group = new THREE.Group();
    this.group.add(this.points, this.slash);
    this.trauma = 0;
    this.color = new THREE.Color();
  }

  emit(pos, n, { color = 0xffffff, speed = 2, up = 2, size = 0.25, life = 0.6, gravity = -4, spread = 0.2, drag = 2 } = {}) {
    this.color.setHex(color);
    for (let i = 0; i < n; i++) {
      if (this.parts.length >= this.cap) this.parts.shift();
      const a = this.rng() * Math.PI * 2;
      const s = speed * (0.4 + this.rng() * 0.6);
      this.parts.push({
        x: pos.x + (this.rng() - 0.5) * spread, y: pos.y + (this.rng() - 0.5) * spread, z: pos.z + (this.rng() - 0.5) * spread,
        vx: Math.cos(a) * s, vy: up * (0.5 + this.rng() * 0.8), vz: Math.sin(a) * s,
        life, t: 0, size: size * (0.7 + this.rng() * 0.6), r: this.color.r, g: this.color.g, b: this.color.b, gravity, drag,
      });
    }
  }

  dust(pos, n = 4) { this.emit(pos, n, { color: 0xeadfc4, speed: 1.2, up: 0.8, size: 0.24, life: 0.5, gravity: 0.5, spread: 0.3, drag: 4 }); }
  sparks(pos, color = 0xfff1b8, n = 10) { this.emit(pos, n, { color, speed: 5, up: 3, size: 0.16, life: 0.35, gravity: -8, spread: 0.15, drag: 3 }); }
  moss(pos, n = 14) { this.emit(pos, n, { color: 0x86b24f, speed: 3.5, up: 4, size: 0.22, life: 0.7, gravity: -14, spread: 0.4, drag: 1 }); }
  embers(pos, n = 30) { this.emit(pos, n, { color: 0xffcf6a, speed: 2.2, up: 4, size: 0.22, life: 1.4, gravity: 1.2, spread: 0.6, drag: 1.5 }); }

  swing(playerPos, yaw) {
    this.slash.position.set(playerPos.x, playerPos.y + 0.75, playerPos.z);
    this.slash.rotation.set(0, yaw, 0.0);
    this.slashT = 0;
    this.slash.visible = true;
  }

  addTrauma(a) { this.trauma = Math.min(1, this.trauma + a); }

  update(dt, decay) {
    this.trauma = Math.max(0, this.trauma - decay * dt);
    let n = 0;
    for (const p of this.parts) {
      p.t += dt;
      if (p.t >= p.life) continue;
      const k = Math.exp(-p.drag * dt);
      p.vx *= k; p.vz *= k; p.vy = p.vy * k + p.gravity * dt;
      p.x += p.vx * dt; p.y += p.vy * dt; p.z += p.vz * dt;
      const life = p.t / p.life;
      this.aPos[n * 3] = p.x; this.aPos[n * 3 + 1] = p.y; this.aPos[n * 3 + 2] = p.z;
      this.aCol[n * 3] = p.r; this.aCol[n * 3 + 1] = p.g; this.aCol[n * 3 + 2] = p.b;
      this.aSize[n] = p.size * (life < 0.2 ? life / 0.2 : 1 - (life - 0.2) * 0.5);
      this.aAlpha[n] = 1 - life * life;
      n++;
    }
    this.parts = this.parts.filter((p) => p.t < p.life);
    const g = this.points.geometry;
    g.setDrawRange(0, n);
    for (const k of ['position', 'color', 'size', 'alpha']) g.getAttribute(k).needsUpdate = true;
    this.points.visible = n > 0;
    if (this.slash.visible) {
      this.slashT += dt;
      const t = this.slashT / 0.2;
      this.slash.material.opacity = Math.max(0, 1 - t) * 0.9;
      this.slash.scale.setScalar(0.85 + t * 0.3);
      this.slash.rotation.y += dt * -4; // keeps sweeping in the swing direction
      if (t >= 1) this.slash.visible = false;
    }
  }

  setViewport(h, fovDeg, pixelRatio) {
    this.uniforms.uScale.value = (h * pixelRatio) / (2 * Math.tan((fovDeg * Math.PI) / 360));
    this.uniforms.uMaxSize.value = 40 * pixelRatio;
  }
}
