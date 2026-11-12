// Builds small, deliberately awkward GLB files in the browser (three's
// GLTFExporter) to exercise the asset contract without real generated assets:
//  - huge scale and a far-off origin (normalisation)
//  - PBR materials with a base-colour texture plus normal/metal maps (re-shading)
//  - a skinned hero facing -X with clips named "Idle"/"Walk"/"Attack" (mapping, missing run/jump)
//  - a hero with no clips (procedural fallback)
//  - a corrupt file (status "failed", placeholder used)
// Nothing here is shipped; files go to check-output/fixtures/.
import fs from 'node:fs';
import path from 'node:path';

export const FIXTURE_PAGE = `<!doctype html><html><body><script type="importmap">
{ "imports": { "three": "/__three/build/three.module.js", "three/addons/": "/__three/examples/jsm/" } }
</script><script type="module">
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
const exp = new GLTFExporter();
const b64 = (ab) => { let s = ''; const u = new Uint8Array(ab); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000)); return btoa(s); };
function checker(a, b) {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d');
  for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) { g.fillStyle = (x + y) % 2 ? a : b; g.fillRect(x * 8, y * 8, 8, 8); }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
async function glb(obj, animations = []) { return b64(await exp.parseAsync(obj, { binary: true, animations })); }

window.makeFixtures = async () => {
  const out = {};
  // Static tree: 100x scale, origin far away, PBR with extra maps that must be ignored.
  const tree = new THREE.Group();
  const bark = new THREE.MeshStandardMaterial({ map: checker('#7a5236', '#8e6444'), normalMap: checker('#8080ff', '#7070ff'), metalness: 1, roughness: 0.1 });
  const leaf = new THREE.MeshStandardMaterial({ map: checker('#4f9a3c', '#6cb84d'), metalness: 0.8, roughness: 0.2 });
  const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.4, 3, 8), bark); trunk.position.y = 1.5;
  const crown = new THREE.Mesh(new THREE.IcosahedronGeometry(2, 1), leaf); crown.position.y = 4.2;
  tree.add(trunk, crown);
  tree.scale.setScalar(100); tree.position.set(500, -200, 300);
  out['models/tree_broadleaf.glb'] = await glb(tree);

  // Static enemy blob with vertex colours only.
  const geo = new THREE.IcosahedronGeometry(3, 2);
  const col = new Float32Array(geo.attributes.position.count * 3);
  for (let i = 0; i < geo.attributes.position.count; i++) { const y = geo.attributes.position.getY(i); col.set(y > 0.8 ? [0.35, 0.6, 0.2] : [0.5, 0.5, 0.45], i * 3); }
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const blob = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true }));
  blob.position.set(0, 3, 0);
  out['models/mossling.glb'] = await glb(blob);

  // Skinned hero facing -X: body + nose box on -X, two bones, clips with odd names.
  function hero(withClips) {
    const g = new THREE.CylinderGeometry(0.25, 0.35, 1.6, 8, 4); g.translate(0, 0.8, 0);
    const nose = new THREE.BoxGeometry(0.3, 0.15, 0.15); nose.translate(-0.35, 1.3, 0);
    const pos = g.attributes.position;
    const si = [], sw = [];
    for (let i = 0; i < pos.count; i++) { const w = Math.min(1, Math.max(0, (pos.getY(i) - 0.6) / 0.6)); si.push(0, 1, 0, 0); sw.push(1 - w, w, 0, 0); }
    g.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(si, 4));
    g.setAttribute('skinWeight', new THREE.Float32BufferAttribute(sw, 4));
    const root = new THREE.Bone(); root.name = 'root';
    const spine = new THREE.Bone(); spine.name = 'spine'; spine.position.y = 0.8; root.add(spine);
    const mesh = new THREE.SkinnedMesh(g, new THREE.MeshStandardMaterial({ map: checker('#f3e6c8', '#d2483c') }));
    mesh.add(root); mesh.bind(new THREE.Skeleton([root, spine]));
    const noseMesh = new THREE.Mesh(nose, new THREE.MeshStandardMaterial({ color: 0xd2483c }));
    const grp = new THREE.Group(); grp.name = 'hero'; grp.add(mesh, noseMesh);
    grp.scale.setScalar(0.01); // centimetre export
    const q = (a) => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), a).toArray();
    const clip = (name, amp, dur) => new THREE.AnimationClip(name, dur, [new THREE.QuaternionKeyframeTrack('spine.quaternion', [0, dur / 2, dur], [...q(-amp), ...q(amp), ...q(-amp)])]);
    return { grp, clips: withClips ? [clip('Idle', 0.05, 2), clip('Walk', 0.25, 0.8), clip('Attack', 0.8, 0.4)] : [] };
  }
  // Equirect sky: a plain gradient with a stripe, as JPEG.
  const sc = document.createElement('canvas'); sc.width = 512; sc.height = 256;
  const sg = sc.getContext('2d'); const grd = sg.createLinearGradient(0, 0, 0, 256);
  grd.addColorStop(0, '#3d7fd0'); grd.addColorStop(0.5, '#f6dca8'); grd.addColorStop(1, '#3a6a80');
  sg.fillStyle = grd; sg.fillRect(0, 0, 512, 256); sg.fillStyle = '#ffffff'; sg.fillRect(0, 70, 512, 6);
  out['textures/sky.jpg'] = sc.toDataURL('image/jpeg', 0.85).split(',')[1];

  // Stick sword: handle at the bottom, blade up +Y, exported at 30x scale.
  const sword = new THREE.Group();
  const wood = new THREE.MeshStandardMaterial({ color: 0x6f4a2e });
  const steel = new THREE.MeshStandardMaterial({ map: checker('#d8d8e0', '#b0b0c0'), metalness: 1 });
  const grip = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.2, 6), wood); grip.position.y = 0.1;
  const guard = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.04, 0.05), wood); guard.position.y = 0.21;
  const blade = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.78, 0.015), steel); blade.position.y = 0.62;
  sword.add(grip, guard, blade);
  sword.scale.setScalar(30);
  out['models/sword.glb'] = await glb(sword);

  let h = hero(true); out['models/hero.glb'] = await glb(h.grp, h.clips);
  h = hero(false); out['static/hero.glb'] = await glb(h.grp, []);
  return out;
};
window.fixturesReady = true;
</script></body></html>`;

export async function makeFixtures(browser, base, dir) {
  const page = await browser.newPage();
  await page.goto(base + '__fixtures.html');
  await page.waitForFunction(() => window.fixturesReady);
  const files = await page.evaluate(() => window.makeFixtures());
  await page.close();
  for (const [rel, data] of Object.entries(files)) {
    const f = path.join(dir, rel);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, Buffer.from(data, 'base64'));
  }
  // One second of a quiet tone as 16-bit WAV (decoders sniff content, not the .ogg name).
  const rate = 22050, n = rate, buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8); buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22); buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28);
  buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(Math.sin((i / rate) * 2 * Math.PI * 220) * 3000), 44 + i * 2);
  fs.mkdirSync(path.join(dir, 'audio'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'audio/field.ogg'), buf);
  // Rigged stand-ins from the rig stage (copied, never shipped).
  const smoke = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../../assets/_smoke/rig');
  for (const n of ['knight.glb', 'male_apose_voxel.glb']) {
    if (fs.existsSync(path.join(smoke, n))) {
      fs.mkdirSync(path.join(dir, 'rigged'), { recursive: true });
      fs.copyFileSync(path.join(smoke, n), path.join(dir, 'rigged', n));
    }
  }
  const bad = path.join(dir, 'models/rock_large.glb');
  fs.writeFileSync(bad, 'this is not a glb file');
  return Object.keys(files).concat('models/rock_large.glb');
}
