import * as THREE from 'three';
import { CFG } from '../config.js';
import { mulberry32 } from '../rng.js';

// Shared GLSL: sample the terrain data texture at a world XZ position.
export const terrainSampleGLSL = /* glsl */ `
  uniform sampler2D uTerrain;
  uniform float uTerrainSize;
  uniform float uTerrainTexels;
  vec4 terrainSample(vec2 xz) {
    vec2 uv = (xz / uTerrainSize + 0.5) * (uTerrainTexels - 1.0) / uTerrainTexels + 0.5 / uTerrainTexels;
    vec4 s = texture2D(uTerrain, clamp(uv, 0.0, 1.0));
    if (abs(xz.x) > uTerrainSize * 0.5 || abs(xz.y) > uTerrainSize * 0.5) s = vec4(-5.0, 0.0, 0.5, 1.0);
    return s;
  }
`;

// One draw call of wind-blown blade clumps. A fixed tile of instances wraps
// around the player in the vertex shader, so the cost is constant and the
// blades stay put in world space while you walk.
export class Grass {
  constructor(terrain, terrainTex, lanternUniform, { mobile }) {
    const clumps = mobile ? CFG.render.grassClumpsMobile : CFG.render.grassClumps;
    const tile = CFG.render.grassTile;
    // One clump = three tapered blades, each 3 triangles (5 verts).
    const blade = [];
    const idx = [];
    for (let b = 0; b < 3; b++) {
      const base = blade.length / 4;
      const a = (b / 3) * Math.PI + 0.3;
      const ox = Math.cos(a + 1.2) * 0.1, oz = Math.sin(a + 1.2) * 0.1;
      const ca = Math.cos(a), sa = Math.sin(a);
      const verts = [
        [-0.5, 0], [0.5, 0], [-0.33, 0.5], [0.33, 0.5], [0, 1],
      ];
      for (const [u, v] of verts) blade.push(ox + ca * u * 0.1, v, oz + sa * u * 0.1, b);
      idx.push(base, base + 1, base + 2, base + 1, base + 3, base + 2, base + 2, base + 3, base + 4);
    }
    const geo = new THREE.InstancedBufferGeometry();
    const p = new Float32Array(blade.length / 4 * 3);
    const bladeId = new Float32Array(blade.length / 4);
    for (let i = 0; i < blade.length / 4; i++) {
      p[i * 3] = blade[i * 4]; p[i * 3 + 1] = blade[i * 4 + 1]; p[i * 3 + 2] = blade[i * 4 + 2];
      bladeId[i] = blade[i * 4 + 3];
    }
    geo.setAttribute('position', new THREE.BufferAttribute(p, 3));
    geo.setAttribute('bladeId', new THREE.BufferAttribute(bladeId, 1));
    geo.setIndex(idx);
    const rnd = mulberry32(terrain.seed * 7 + 3);
    const offs = new Float32Array(clumps * 4);
    for (let i = 0; i < clumps; i++) {
      offs[i * 4] = rnd() * tile;
      offs[i * 4 + 1] = rnd() * tile;
      offs[i * 4 + 2] = rnd();
      offs[i * 4 + 3] = rnd();
    }
    geo.setAttribute('aOffset', new THREE.InstancedBufferAttribute(offs, 4));
    geo.instanceCount = clumps;
    this.trianglesPerClump = 9;
    this.clumps = clumps;

    this.uniforms = THREE.UniformsUtils.merge([THREE.UniformsLib.fog, {
      uCenter: { value: new THREE.Vector2() },
      uPlayer: { value: new THREE.Vector3() },
      uTile: { value: tile },
      uTime: { value: 0 },
      uTerrainSize: { value: terrain.size },
      uTerrainTexels: { value: terrain.res + 1 },
      uRoot: { value: new THREE.Color(0x3f8a3c) },
      uTip: { value: new THREE.Color(0xb6dc6a) },
      uTipAlt: { value: new THREE.Color(0xd8e27a) },
      uDull: { value: new THREE.Color(0x8d9670) },
      uGlow: { value: new THREE.Color(0xffe39a) },
      uSunDir: { value: new THREE.Vector3(0.5, 0.8, 0.3).normalize() },
    }]);
    this.uniforms.uTerrain = { value: terrainTex };
    this.uniforms.uLanterns = lanternUniform;

    const mat = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      fog: true,
      side: THREE.DoubleSide,
      vertexShader: /* glsl */ `
        #include <common>
        #include <fog_pars_vertex>
        ${terrainSampleGLSL}
        attribute vec4 aOffset;
        attribute float bladeId;
        uniform vec2 uCenter;
        uniform vec3 uPlayer;
        uniform float uTile;
        uniform float uTime;
        varying float vH;
        varying vec3 vWorld;
        varying float vVar;
        varying float vShade;
        float h1(float n) { return fract(sin(n) * 43758.5453); }
        void main() {
          vec2 wxz = aOffset.xy + uTile * floor((uCenter - aOffset.xy) / uTile + 0.5);
          vec4 ts = terrainSample(wxz);
          float dist = distance(wxz, uCenter);
          float fade = 1.0 - smoothstep(uTile * 0.32, uTile * 0.5, dist);
          float keep = step(aOffset.z, ts.g * 1.15);
          float scale = keep * fade * (0.35 + 0.65 * smoothstep(0.0, 0.5, ts.g));
          float rot = aOffset.w * 6.2831;
          float cr = cos(rot), sr = sin(rot);
          float bh = (0.2 + 0.3 * h1(aOffset.z * 91.0 + bladeId)) * scale;
          vec3 lp = vec3(position.x * scale, position.y * bh, position.z * scale);
          lp = vec3(cr * lp.x - sr * lp.z, lp.y, sr * lp.x + cr * lp.z);
          // Wind: slow travelling gusts plus a quick flutter, stronger at the tip.
          float gust = sin(uTime * 1.3 + wxz.x * 0.18 + wxz.y * 0.11) * 0.5 + 0.5;
          float flutter = sin(uTime * 4.7 + aOffset.z * 30.0 + bladeId) * 0.12;
          float bend = position.y * position.y * bh;
          vec2 windDir = normalize(vec2(1.0, 0.45));
          lp.xz += windDir * bend * (0.25 + 0.75 * gust + flutter);
          // Part the grass around the hero.
          vec2 away = wxz - uPlayer.xz;
          float pd = length(away);
          float push = (1.0 - smoothstep(0.2, 1.1, pd)) * step(abs(uPlayer.y - ts.r), 1.2);
          lp.xz += (pd > 1e-3 ? away / pd : vec2(0.0)) * push * bend * 1.4;
          lp.y -= push * bend * 0.4;
          vec3 world = vec3(wxz.x, ts.r - 0.02, wxz.y) + lp;
          vWorld = world;
          vH = position.y;
          vVar = ts.b;
          vShade = 0.85 + 0.3 * gust * position.y;
          vec4 mvPosition = viewMatrix * vec4(world, 1.0);
          gl_Position = projectionMatrix * mvPosition;
          #include <fog_vertex>
        }`,
      fragmentShader: /* glsl */ `
        #include <common>
        #include <fog_pars_fragment>
        uniform vec3 uRoot;
        uniform vec3 uTip;
        uniform vec3 uTipAlt;
        uniform vec3 uDull;
        uniform vec3 uGlow;
        uniform vec4 uLanterns[3];
        varying float vH;
        varying vec3 vWorld;
        varying float vVar;
        varying float vShade;
        void main() {
          vec3 tip = mix(uTip, uTipAlt, smoothstep(0.55, 0.8, vVar));
          vec3 c = mix(uRoot, tip, smoothstep(0.0, 1.0, vH));
          c *= vShade;
          // Near a dark lantern the meadow is faded; when lit it blooms warm.
          float dull = 0.0;
          float glow = 0.0;
          for (int i = 0; i < 3; i++) {
            vec4 L = uLanterns[i];
            float d = distance(vWorld.xz, L.xy);
            float near = 1.0 - smoothstep(6.0, 15.0, d);
            dull = max(dull, near * (1.0 - step(0.001, L.w)));
            float r = L.z * L.w;
            glow = max(glow, (1.0 - smoothstep(r * 0.55, r, d)) * step(0.001, L.w));
          }
          c = mix(c, uDull * (0.6 + 0.4 * vH), dull * 0.65);
          c += uGlow * glow * vH * 0.22;
          gl_FragColor = vec4(c, 1.0);
          #include <colorspace_fragment>
          #include <fog_fragment>
        }`,
    });
    this.mesh = new THREE.Mesh(geo, mat);
    this.mesh.frustumCulled = false;
    this.mesh.name = 'grass';
  }

  update(time, center, player) {
    this.uniforms.uTime.value = time;
    this.uniforms.uCenter.value.set(center.x, center.z);
    this.uniforms.uPlayer.value.copy(player);
  }
}
