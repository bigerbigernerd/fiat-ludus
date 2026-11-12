import * as THREE from 'three';

// Painterly sky dome: warm horizon, soft blue zenith, banded toon clouds and a
// sun glow. An equirect texture from the manifest replaces the procedural sky
// but keeps the same dome, so fog and colours still line up.
export class Sky {
  constructor(sunDir) {
    this.uniforms = {
      uTime: { value: 0 },
      uSun: { value: sunDir.clone() },
      uZenith: { value: new THREE.Color(0x5d9be0) },
      uMid: { value: new THREE.Color(0x9fcbee) },
      uHorizon: { value: new THREE.Color(0xf6e8c6) },
      uCloud: { value: new THREE.Color(0xfffbf2) },
      uCloudShade: { value: new THREE.Color(0xc9c4e4) },
      uTex: { value: null },
      uUseTex: { value: 0 },
    };
    const mat = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      side: THREE.BackSide,
      depthWrite: false,
      fog: false,
      vertexShader: /* glsl */ `
        varying vec3 vDir;
        void main() {
          vDir = position;
          vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          gl_Position = p.xyww; // always on the far plane
        }`,
      fragmentShader: /* glsl */ `
        #include <common>
        uniform float uTime;
        uniform vec3 uSun, uZenith, uMid, uHorizon, uCloud, uCloudShade;
        uniform sampler2D uTex;
        uniform float uUseTex;
        varying vec3 vDir;
        float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float noise(vec2 p) {
          vec2 i = floor(p), f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
        }
        float fbm(vec2 p) {
          float s = 0.0, a = 0.5;
          for (int i = 0; i < 5; i++) { s += a * noise(p); p = p * 2.07 + 13.1; a *= 0.5; }
          return s;
        }
        void main() {
          vec3 d = normalize(vDir);
          vec3 col;
          if (uUseTex > 0.5) {
            vec2 uv = vec2(atan(d.z, d.x) / (2.0 * PI) + 0.5, asin(clamp(d.y, -1.0, 1.0)) / PI + 0.5);
            col = texture2D(uTex, uv).rgb;
            // texture is sRGB-decoded by three; nothing else to do
          } else {
            float y = max(d.y, 0.0);
            col = mix(uHorizon, uMid, smoothstep(0.0, 0.22, y));
            col = mix(col, uZenith, smoothstep(0.2, 0.75, y));
            float sd = max(dot(d, normalize(uSun)), 0.0);
            col += vec3(1.0, 0.86, 0.6) * pow(sd, 18.0) * 0.35;
            col = mix(col, vec3(1.0, 0.98, 0.9), smoothstep(0.9975, 0.999, sd));
            // Clouds projected onto a high plane, posterised into two tones.
            if (d.y > 0.0) {
              vec2 uv = d.xz / (d.y + 0.12) * 1.3 + vec2(uTime * 0.006, uTime * 0.002);
              float n = fbm(uv * 0.9);
              float n2 = fbm(uv * 0.9 + normalize(uSun.xz) * 0.08);
              float body = smoothstep(0.54, 0.58, n);
              // Denser toward the sun means the cloud's underside: shade it.
              float shade = smoothstep(0.63, 0.66, n2);
              vec3 cc = mix(uCloud, uCloudShade, shade);
              float fadeH = smoothstep(0.02, 0.18, d.y);
              col = mix(col, cc, body * fadeH * 0.95);
            }
            // Below the horizon blend into the sea haze.
            col = mix(col, uHorizon * 0.92, smoothstep(0.0, -0.08, d.y));
          }
          gl_FragColor = vec4(col, 1.0);
          #include <colorspace_fragment>
        }`,
    });
    this.mesh = new THREE.Mesh(new THREE.SphereGeometry(500, 48, 24), mat);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = -10;
    this.mesh.name = 'sky';
  }

  setTexture(tex) {
    this.uniforms.uTex.value = tex;
    this.uniforms.uUseTex.value = tex ? 1 : 0;
  }

  update(time, camera) {
    this.uniforms.uTime.value = time;
    this.mesh.position.copy(camera.position);
  }
}
