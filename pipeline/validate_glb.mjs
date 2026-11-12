// Validate a rigged character GLB the way the game will see it.
//
//   NODE_PATH=<dir with three + gltf-validator installed> node pipeline/validate_glb.mjs char.glb [--json out.json]
//
// 1. Khronos glTF validator: error / warning counts and the first messages.
// 2. three.js GLTFLoader (the loader the game uses): clip names + durations, skinned meshes,
//    bone count, rest-pose bounding box (height, feet on the ground), and the skinned
//    bounding box at the middle of each clip (catches exploded or collapsed rigs).
// Images are stubbed out because Node has no image decoder; texture *presence* is still
// checked through the parsed materials.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.join(process.env.NODE_PATH || process.cwd(), "x.js"));
const validator = require("gltf-validator");
// Load the ES module build: require.resolve("three") points at the CommonJS build, which
// newer three.js releases ship only as a deprecated shim, and GLTFLoader imports the ES one.
const THREE = await import(
  path.join(path.dirname(require.resolve("three")), "three.module.js")
);
const { GLTFLoader } = await import(
  require.resolve("three/examples/jsm/loaders/GLTFLoader.js")
);

const file = process.argv[2];
const jsonOut = process.argv.includes("--json") ? process.argv[process.argv.indexOf("--json") + 1] : null;
const buf = fs.readFileSync(file);
const out = { file: path.basename(file), bytes: buf.length };

// --- Khronos validator ---------------------------------------------------------------
const rep = await validator.validateBytes(new Uint8Array(buf), { maxIssues: 50 });
out.validator = {
  errors: rep.issues.numErrors,
  warnings: rep.issues.numWarnings,
  infos: rep.issues.numInfos,
  messages: rep.issues.messages
    .filter((m) => m.severity <= 1)
    .slice(0, 12)
    .map((m) => `${m.severity === 0 ? "E" : "W"} ${m.code} ${m.pointer || ""} ${m.message}`),
};

// --- three.js ---------------------------------------------------------------------------
// Stub image loading: three's GLTFLoader uses ImageBitmapLoader/TextureLoader -> DOM.
globalThis.self = globalThis;
globalThis.createImageBitmap = async () => ({ width: 1, height: 1, close() {} });
globalThis.fetch = globalThis.fetch;
const manager = new THREE.LoadingManager();
manager.setURLModifier((u) => u);
const loader = new GLTFLoader(manager);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const gltf = await new Promise((res, rej) => loader.parse(ab, "", res, rej));

const scene = gltf.scene;
scene.updateMatrixWorld(true);
const skinned = [];
const textures = new Set();
scene.traverse((o) => {
  if (o.isSkinnedMesh) skinned.push(o);
  if (o.material) {
    for (const m of [].concat(o.material)) {
      for (const k of ["map", "normalMap", "roughnessMap", "metalnessMap", "emissiveMap"]) {
        if (m[k]) textures.add(`${m.name}.${k}`);
      }
    }
  }
});
out.three = {
  clips: gltf.animations.map((c) => ({ name: c.name, duration: +c.duration.toFixed(3), tracks: c.tracks.length })),
  skinnedMeshes: skinned.length,
  bones: skinned.length ? skinned[0].skeleton.bones.length : 0,
  textures: [...textures],
};

function skinnedBox(mesh) {
  const box = new THREE.Box3();
  const v = new THREE.Vector3();
  const pos = mesh.geometry.attributes.position;
  for (let i = 0; i < pos.count; i += 3) {
    mesh.getVertexPosition(i, v);
    v.applyMatrix4(mesh.matrixWorld);
    box.expandByPoint(v);
  }
  return box;
}
function boxInfo(b) {
  const s = new THREE.Vector3();
  b.getSize(s);
  return { min: b.min.toArray().map((x) => +x.toFixed(3)), size: s.toArray().map((x) => +x.toFixed(3)) };
}
if (skinned.length) {
  const rest = new THREE.Box3();
  for (const m of skinned) rest.union(skinnedBox(m));
  out.three.restBox = boxInfo(rest);
  // facing: the toes stick out in the facing direction, so compare the extent of the lowest
  // 5% of vertices along +Z and -Z around the hips' z.
  const mixer = new THREE.AnimationMixer(scene);
  out.three.clipBoxes = {};
  for (const clip of gltf.animations) {
    mixer.stopAllAction();
    const act = mixer.clipAction(clip);
    act.play();
    mixer.setTime(clip.duration * 0.5);
    scene.updateMatrixWorld(true);
    const b = new THREE.Box3();
    for (const m of skinned) b.union(skinnedBox(m));
    out.three.clipBoxes[clip.name] = boxInfo(b);
    act.stop();
  }
}
const txt = JSON.stringify(out, null, 1);
if (jsonOut) fs.writeFileSync(jsonOut, txt);
console.log(txt);
process.exit(out.validator.errors > 0 ? 1 : 0);
