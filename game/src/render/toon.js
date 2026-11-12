import * as THREE from 'three';
import { CFG } from '../config.js';

// Stepped light ramp shared by every toon surface. MeshToonMaterial looks up
// (N.L * 0.5 + 0.5) in this texture's red channel.
let ramp = null;
export function toonRamp() {
  if (ramp) return ramp;
  const steps = [92, 92, 92, 150, 150, 214, 255, 255];
  const data = new Uint8Array(steps.length * 4);
  steps.forEach((v, i) => data.set([v, v, v, 255], i * 4));
  ramp = new THREE.DataTexture(data, steps.length, 1, THREE.RGBAFormat);
  ramp.minFilter = ramp.magFilter = THREE.NearestFilter;
  ramp.generateMipmaps = false;
  ramp.needsUpdate = true;
  return ramp;
}

export const sharedUniforms = {
  uRimColor: { value: new THREE.Color(0xfff2d6) },
  uRimStrength: { value: 0.32 },
  uTime: { value: 0 },
};

// Toon material with a soft rim light. Keeps only base colour (map + vertex colours).
// Screen-door fade for anything that gets right in front of the camera (a
// trunk between camera and hero), so it never fills the view. Off for the hero.
// Also: anything nearer than the hero inside an ellipse around the hero on
// screen turns mostly see-through, so trunks and rocks never hide Pim.
export const fadeUniforms = {
  uHeroScreen: { value: new THREE.Vector2(-9999, -9999) }, // device pixels, origin bottom-left
  uHeroDist: { value: 0 },
  uFadeRadius: { value: 200 },
};
const NEAR_FADE_PARS = 'uniform vec2 uHeroScreen;\nuniform float uHeroDist;\nuniform float uFadeRadius;\n';
const NEAR_FADE_GLSL = /* glsl */ `
  #ifdef NEAR_FADE
  {
    float fadeT = clamp((uCamDist - 0.9) / 1.7, 0.0, 1.0);
    if (uCamDist < uHeroDist - 0.8) {
      // Nearer than the hero: see-through around the hero on screen, and
      // anything close to the lens (a trunk filling a third of the view) too.
      float rr = length((gl_FragCoord.xy - uHeroScreen) / vec2(0.8, 1.0)) / uFadeRadius;
      float around = 0.3 + 0.7 * smoothstep(0.55, 1.0, rr);
      float nearLens = 0.3 + 0.7 * smoothstep(2.6, 4.6, uCamDist);
      fadeT = min(fadeT, min(around, nearLens));
    }
    float ign = fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))));
    if (fadeT < ign) discard;
  }
  #endif`;

export function makeToonMaterial({ color = 0xffffff, map = null, vertexColors = false, rim = 1, side = THREE.FrontSide, emissive = 0x000000, nearFade = true } = {}) {
  const mat = new THREE.MeshToonMaterial({ color, map, vertexColors, gradientMap: toonRamp(), side, emissive });
  mat.userData.rim = rim;
  if (nearFade) mat.defines = { NEAR_FADE: '' };
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uRimColor = sharedUniforms.uRimColor;
    shader.uniforms.uRimStrength = sharedUniforms.uRimStrength;
    shader.uniforms.uRimScale = { value: rim };
    Object.assign(shader.uniforms, fadeUniforms);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform vec3 uRimColor;\nuniform float uRimStrength;\nuniform float uRimScale;\n' + NEAR_FADE_PARS)
      .replace('#include <clipping_planes_fragment>', '#include <clipping_planes_fragment>\nfloat uCamDist = length(vViewPosition);' + NEAR_FADE_GLSL)
      .replace(
        '#include <opaque_fragment>',
        `{
          vec3 vd = normalize(vViewPosition);
          float rimF = 1.0 - clamp(dot(normal, vd), 0.0, 1.0);
          rimF = smoothstep(0.62, 0.72, rimF);
          // Rim is strongest on the upper side of surfaces so it reads as sky light.
          float up = clamp(normal.y * 0.5 + 0.6, 0.0, 1.0);
          outgoingLight += uRimColor * diffuseColor.rgb * rimF * up * uRimStrength * uRimScale;
        }
        #include <opaque_fragment>`
      );
  };
  mat.customProgramCacheKey = () => 'toon-rim-' + rim + (nearFade ? '-nf' : '');
  return mat;
}

// Inverted-hull outline pushed out in clip space so the line keeps a roughly
// constant pixel width whatever the model's scale. Uses the smoothed 'onormal'
// attribute so hard-edged boxes do not crack at the corners.
const outlineUniforms = {
  uWidth: { value: CFG.render.outlinePx },
  uResolution: { value: new THREE.Vector2(1280, 720) },
};
export function setOutlineResolution(w, h, pixelRatio) {
  outlineUniforms.uResolution.value.set(w * pixelRatio, h * pixelRatio);
  outlineUniforms.uWidth.value = CFG.render.outlinePx * pixelRatio;
}

const outlineMats = new Map();
export function makeOutlineMaterial(color = 0x2b2420, nearFade = true) {
  const key = color + (nearFade ? ':nf' : '');
  if (outlineMats.has(key)) return outlineMats.get(key);
  const mat = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uColor: { value: new THREE.Color(color) } }]),
    vertexShader: /* glsl */ `
      #include <common>
      #include <skinning_pars_vertex>
      #include <fog_pars_vertex>
      attribute vec3 onormal;
      uniform float uWidth;
      uniform vec2 uResolution;
      varying float vCamDist;
      void main() {
        vec3 objectNormal = onormal;
        #include <skinbase_vertex>
        #include <skinnormal_vertex>
        #include <begin_vertex>
        #include <skinning_vertex>
        #include <project_vertex>
        vec3 n = objectNormal;
        #ifdef USE_INSTANCING
          n = mat3(instanceMatrix) * n;
        #endif
        vec3 vn = normalize(normalMatrix * n);
        vec4 cn = projectionMatrix * vec4(vn, 0.0);
        vec2 dir = cn.xy;
        float len = length(dir);
        dir = len > 1e-5 ? dir / len : vec2(0.0);
        // Thinner with distance so far trees do not turn into ink blots.
        float w = uWidth * clamp(14.0 / max(gl_Position.w, 1.0), 0.45, 1.0);
        gl_Position.xy += dir * w * 2.0 / uResolution * gl_Position.w;
        vCamDist = length(mvPosition.xyz);
        #include <fog_vertex>
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      varying float vCamDist;
      ${NEAR_FADE_PARS}
      #include <common>
      #include <fog_pars_fragment>
      void main() {
        float uCamDist = vCamDist;
        ${NEAR_FADE_GLSL}
        gl_FragColor = vec4(uColor, 1.0);
        #include <colorspace_fragment>
        #include <fog_fragment>
      }`,
    side: THREE.BackSide,
    fog: true,
    defines: nearFade ? { NEAR_FADE: '' } : {},
  });
  Object.assign(mat.uniforms, fadeUniforms);
  mat.uniforms.uWidth = outlineUniforms.uWidth;
  mat.uniforms.uResolution = outlineUniforms.uResolution;
  outlineMats.set(key, mat);
  return mat;
}

// Average normals of vertices that share a position (rounded) into 'onormal'.
export function addOutlineNormals(geometry) {
  if (geometry.getAttribute('onormal')) return geometry;
  const pos = geometry.getAttribute('position');
  let nor = geometry.getAttribute('normal');
  if (!nor) { geometry.computeVertexNormals(); nor = geometry.getAttribute('normal'); }
  const map = new Map();
  const keys = new Array(pos.count);
  const q = 1e4;
  for (let i = 0; i < pos.count; i++) {
    const k = `${Math.round(pos.getX(i) * q)}_${Math.round(pos.getY(i) * q)}_${Math.round(pos.getZ(i) * q)}`;
    keys[i] = k;
    let acc = map.get(k);
    if (!acc) { acc = [0, 0, 0]; map.set(k, acc); }
    acc[0] += nor.getX(i); acc[1] += nor.getY(i); acc[2] += nor.getZ(i);
  }
  const out = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) {
    const a = map.get(keys[i]);
    const l = Math.hypot(a[0], a[1], a[2]) || 1;
    out[i * 3] = a[0] / l; out[i * 3 + 1] = a[1] / l; out[i * 3 + 2] = a[2] / l;
  }
  geometry.setAttribute('onormal', new THREE.BufferAttribute(out, 3));
  return geometry;
}

// Radial soft-glow texture for additive sprites.
let glowTex = null;
export function glowTexture() {
  if (glowTex) return glowTex;
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.25, 'rgba(255,255,255,0.55)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  glowTex = new THREE.CanvasTexture(c);
  glowTex.colorSpace = THREE.SRGBColorSpace;
  return glowTex;
}
