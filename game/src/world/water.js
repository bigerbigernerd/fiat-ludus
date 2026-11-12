import * as THREE from 'three';
import { CFG } from '../config.js';
import { terrainSampleGLSL } from './grass.js';

// Flat stylised sea: colour by depth (read from the terrain texture), animated
// foam bands along the shore, and a few drifting glints.
export class Water {
  constructor(terrain, terrainTex) {
    this.uniforms = THREE.UniformsUtils.merge([THREE.UniformsLib.fog, {
      uTime: { value: 0 },
      uTerrainSize: { value: terrain.size },
      uTerrainTexels: { value: terrain.res + 1 },
      uShallow: { value: new THREE.Color(0x7fd9cf) },
      uMidC: { value: new THREE.Color(0x3cabc6) },
      uDeep: { value: new THREE.Color(0x2e78b0) },
      uFoam: { value: new THREE.Color(0xf8fbf4) },
      uLevel: { value: CFG.waterLevel },
    }]);
    this.uniforms.uTerrain = { value: terrainTex };
    const mat = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      fog: true,
      vertexShader: /* glsl */ `
        #include <common>
        #include <fog_pars_vertex>
        varying vec3 vWorld;
        void main() {
          vec4 w = modelMatrix * vec4(position, 1.0);
          vWorld = w.xyz;
          vec4 mvPosition = viewMatrix * w;
          gl_Position = projectionMatrix * mvPosition;
          #include <fog_vertex>
        }`,
      fragmentShader: /* glsl */ `
        #include <common>
        #include <fog_pars_fragment>
        ${terrainSampleGLSL}
        uniform float uTime, uLevel;
        uniform vec3 uShallow, uMidC, uDeep, uFoam;
        varying vec3 vWorld;
        float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float noise(vec2 p) {
          vec2 i = floor(p), f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
        }
        void main() {
          float h = terrainSample(vWorld.xz).r;
          float depth = max(uLevel - h, 0.0);
          vec3 c = mix(uShallow, uMidC, smoothstep(0.2, 1.4, depth));
          c = mix(c, uDeep, smoothstep(1.6, 4.5, depth));
          // Posterise depth a little for the painted look.
          float n = noise(vWorld.xz * 0.35 + vec2(uTime * 0.15, uTime * 0.07));
          float n2 = noise(vWorld.xz * 0.9 - vec2(uTime * 0.21, -uTime * 0.11));
          // Shore foam: a solid lip plus bands that roll in.
          float lip = 1.0 - smoothstep(0.06, 0.16, depth + (n - 0.5) * 0.1);
          float band = fract(depth * 1.6 - uTime * 0.35 + n * 0.4);
          float bands = step(0.86, band) * (1.0 - smoothstep(0.3, 1.1, depth));
          float foam = max(lip, bands * 0.85);
          // Sparse glints on open water.
          float glint = step(0.82, n2) * step(0.72, n) * smoothstep(1.0, 3.0, depth) * 0.5;
          c = mix(c, uFoam, max(foam, glint));
          gl_FragColor = vec4(c, 1.0);
          #include <colorspace_fragment>
          #include <fog_fragment>
        }`,
    });
    const geo = new THREE.PlaneGeometry(900, 900, 1, 1);
    geo.rotateX(-Math.PI / 2);
    this.mesh = new THREE.Mesh(geo, mat);
    this.mesh.position.y = CFG.waterLevel;
    this.mesh.name = 'water';
  }

  update(time) { this.uniforms.uTime.value = time; }
}
