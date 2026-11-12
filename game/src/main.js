import * as THREE from 'three';
import { CFG } from './config.js';
import { mulberry32 } from './rng.js';
import { setOutlineResolution, sharedUniforms, makeToonMaterial, fadeUniforms } from './render/toon.js';
import { Terrain } from './world/terrain.js';
import { Grass } from './world/grass.js';
import { Sky } from './world/sky.js';
import { Water } from './world/water.js';
import { Props, Colliders } from './world/props.js';
import { AssetLoader } from './assets/loader.js';
import { lumpy, piece, merge } from './assets/placeholders.js';
import { Player } from './player.js';
import { HeroVisual, findBone } from './hero.js';
import { Enemies } from './enemies.js';
import { Objectives } from './objectives.js';
import { Pickups } from './pickups.js';
import { FX } from './fx.js';
import { Input } from './input.js';
import { CameraRig } from './camera.js';
import { Audio } from './audio.js';
import { HUD } from './hud.js';
import { installTestHook } from './testhook.js';

const params = new URLSearchParams(location.search);
const TEST = params.get('test') === '1';
const quality = params.get('quality');
const MOBILE = quality ? quality === 'low' : (matchMedia('(pointer: coarse)').matches || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent));

const canvas = document.getElementById('game');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance', preserveDrawingBuffer: TEST });
if (MOBILE) CFG.render.maxTrianglesPerPropSet = CFG.render.maxTrianglesPerPropSetMobile;
const pixelRatio = Math.min(window.devicePixelRatio || 1, MOBILE ? CFG.render.pixelRatioMaxMobile : CFG.render.pixelRatioMax);
renderer.setPixelRatio(pixelRatio);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;

const scene = new THREE.Scene();
const FOG = new THREE.Color(0xe3ead8);
scene.fog = new THREE.Fog(FOG, CFG.render.fogNear, CFG.render.fogFar);
scene.background = FOG;
const camera = new THREE.PerspectiveCamera(CFG.camera.fov, innerWidth / innerHeight, 0.1, 900);

const sunDir = new THREE.Vector3(0.5, 0.78, 0.42).normalize();
const sun = new THREE.DirectionalLight(0xfff0d2, 2.3);
sun.castShadow = true;
const smap = MOBILE ? CFG.render.shadowMapSizeMobile : CFG.render.shadowMapSize;
sun.shadow.mapSize.set(smap, smap);
const ext = CFG.render.shadowExtent;
Object.assign(sun.shadow.camera, { left: -ext, right: ext, top: ext, bottom: -ext, near: 1, far: 140 });
sun.shadow.bias = -0.0006;
sun.shadow.normalBias = 0.04;
scene.add(sun, sun.target);
scene.add(new THREE.HemisphereLight(0xd8e8ff, 0x9a8a62, 1.15));

const input = new Input(canvas);
const audio = new Audio();
const hud = new HUD(document.getElementById('hud'), document.getElementById('overlays'), input, audio);
const debugEl = document.getElementById('debug');
let debugOn = params.get('debug') === '1';

const game = {
  mode: 'loading', simTime: 0, playTime: 0, hitStop: 0, frames: 0, fps: 0,
  stats: { drawCalls: 0, triangles: 0 }, seedStreak: 0, seedStreakT: 0,
};

let world, sky, water, grass, props, heroVis, cam, loader, fx;

async function boot() {
  hud.showTitle(input.usingTouch, 'Loading the island...');
  const level = await (await fetch(new URL('level.json', location.href), { cache: 'no-cache' })).json();
  loader = new AssetLoader(renderer, './assets/');
  const manifest = await loader.loadManifest();

  const terrain = new Terrain(level);
  const colliders = new Colliders();
  world = { level, manifest, terrain, colliders, rngSim: mulberry32(level.seed) };
  fx = new FX();
  world.fx = fx;
  const terrainTex = terrain.buildDataTexture();
  scene.add(terrain.buildMesh());
  water = new Water(terrain, terrainTex);
  sky = new Sky(sunDir);
  scene.add(water.mesh, sky.mesh, distantIslands());

  world.enemies = new Enemies(world, emit);
  world.objectives = new Objectives(world, emit);
  props = new Props(level, manifest, terrain, colliders);
  world.objectives.addPlacements(props);
  props.build();
  world.objectives.build(props);
  grass = new Grass(terrain, terrainTex, world.objectives.uniform, { mobile: MOBILE });
  world.player = new Player(world, emit);
  world.pickups = new Pickups(world, emit);
  heroVis = new HeroVisual(manifest.hero?.height ?? CFG.player.height);
  cam = new CameraRig(camera, terrain, colliders);
  cam.snap(world.player.pos, level.player.spawn.yaw ?? 0);
  scene.add(props.group, grass.mesh, world.enemies.group, world.objectives.group, world.pickups.group, heroVis.root, fx.group);

  onResize();
  renderer.setAnimationLoop(frame);
  game.mode = 'title';
  hud.setLoading('Loading generated assets...');

  // Real assets replace placeholders as they arrive; the game never waits.
  await loadAssets(false);
  hud.setLoading(assetLine());
  renderer.compile(scene, camera);
  render();
}

function assetLine() {
  const c = { loaded: 0, placeholder: 0, failed: 0 };
  for (const [k, v] of Object.entries(loader.status.entries)) if (k !== 'manifest') c[v.state] = (c[v.state] || 0) + 1;
  return `${c.loaded} generated assets, ${c.placeholder} placeholders${c.failed ? ', ' + c.failed + ' failed' : ''}`;
}

async function loadAssets(reload) {
  const m = reload ? await loader.loadManifest() : world.manifest;
  const jobs = [
    loader.loadHero(m.hero).then(async (res) => {
      const entries = m.hero?.attachments || [];
      if (!res) {
        entries.forEach((a, i) => loader.status.set(`hero.attachments.${a.id || '#' + i}`, 'placeholder', 'hero is the built-in rig, which carries its own sword', { file: a.file, bone: a.bone }));
        return;
      }
      heroVis.setModel(res, m.hero.clips);
      heroVis.attach(entries, await loader.loadAttachments(entries), loader.status);
      heroVis.setAxes(debugOn || params.get('axes') === '1');
    }),
    loader.loadStatic('enemies.mossling', m.enemies?.mossling, 'mossling').then(({ proto, source }) => { if (source === 'file') world.enemies.setProto(proto); }),
    props.loadAll(loader, reload),
    loader.loadSky(m.sky).then((tex) => sky.setTexture(tex)),
    loader.loadMusic(m.music).then((bufs) => audio.setMusic(bufs)),
  ];
  for (const [id, e] of Object.entries(m.enemies || {})) if (id !== 'mossling') loader.status.set('enemies.' + id, 'placeholder', 'enemy type not used yet', { file: e.file });
  await Promise.allSettled(jobs);
}

function distantIslands() {
  const rnd = mulberry32(99);
  const parts = [];
  for (let i = 0; i < 7; i++) {
    const a = (i / 7) * Math.PI * 2 + rnd() * 0.5;
    const d = 150 + rnd() * 40;
    const s = 14 + rnd() * 22;
    parts.push(piece(lumpy(new THREE.IcosahedronGeometry(1, 2), 0.12, i), i % 2 ? 0x7aa36a : 0x6e9a72, { p: [Math.cos(a) * d, -2, Math.sin(a) * d], s: [s * 1.6, s * (0.35 + rnd() * 0.3), s] }));
  }
  const mesh = new THREE.Mesh(merge(parts), makeToonMaterial({ vertexColors: true, rim: 0, nearFade: false }));
  mesh.name = 'distant-islands';
  return mesh;
}

// ---------- events: everything that should feel like something ----------
function emit(type, d = {}) {
  const pl = world.player;
  const F = CFG.feel;
  switch (type) {
    case 'step': fx.dust(pl.pos, 2); audio.play('step'); break;
    case 'jump': fx.dust(pl.pos, 5); audio.play('jump'); heroVis.onEvent('jump'); break;
    case 'land': fx.dust(pl.pos, 8); audio.play('land'); heroVis.onEvent('land'); break;
    case 'attack': fx.swing(pl.pos, pl.yaw); audio.play('swing'); heroVis.onEvent('attack'); break;
    case 'enemyHit': {
      const p = d.e.pos.clone(); p.y += 0.5;
      game.hitStop = F.hitStop; fx.addTrauma(F.traumaHit);
      fx.sparks(p); fx.moss(p, d.killed ? 26 : 8);
      audio.play('hit');
      if (d.killed) { audio.play('defeat'); world.pickups.spawn(p, CFG.enemies.mossling.seedDrop); }
      break;
    }
    case 'enemyTelegraph': if (d.e.pos.distanceTo(pl.pos) < 12) audio.play('telegraph'); break;
    case 'enemyLunge': if (d.e.pos.distanceTo(pl.pos) < 12) audio.play('lunge'); break;
    case 'enemyLand': if (d.e.pos.distanceTo(pl.pos) < 10) { fx.dust(d.e.pos, 2); audio.play('hop'); } break;
    case 'hurt': game.hitStop = F.hitStopHurt; fx.addTrauma(F.traumaHurt); audio.play('hurt'); fx.sparks(pl.pos.clone().setY(pl.pos.y + 0.8), 0xff8a7a, 12); break;
    case 'death': hud.showToast('Pim tumbles... back to the last light', 2400); break;
    case 'respawn': respawn(); break;
    case 'heal': audio.play('heal'); hud.showToast('A petal grows back', 1500); break;
    case 'seed':
      game.seedStreak = game.seedStreakT > 0 ? game.seedStreak + 1 : 0;
      game.seedStreakT = 1.2;
      audio.play('seed', { streak: game.seedStreak });
      fx.sparks(pl.pos.clone().setY(pl.pos.y + 0.8), 0xffe27a, 4);
      break;
    case 'lanternStart': audio.play('lanternStart'); break;
    case 'lanternLit': {
      const l = d.lantern;
      const p = new THREE.Vector3(l.x, world.terrain.heightAt(l.x, l.z) + 1.4, l.z);
      fx.embers(p, 46); fx.addTrauma(F.traumaLantern); audio.play('lantern');
      hud.showToast(d.count < 3 ? `Lantern relit  ${d.count} / 3` : 'All three lanterns burn!', 2600);
      break;
    }
    case 'gateOpening': audio.play('gate'); setTimeout(() => hud.showToast('The north gate is opening', 2600), 2700); break;
    case 'chestOpen': world.pickups.spawn(d.pos, d.seeds); audio.play('chest'); fx.sparks(d.pos, 0xffe27a, 16); break;
    case 'win': winGame(); break;
    default: break;
  }
}

function respawn() {
  const pl = world.player;
  const l = world.objectives.lastLit;
  const spot = l ? { x: l.x + 0, z: l.z + 2.6, yaw: 0 } : world.level.player.spawn;
  pl.reset(spot);
  pl.health = CFG.player.maxHealth;
  pl.invuln = 1.5;
  const lit = new Set(world.objectives.lanterns.filter((x) => x.lit).map((x) => x.id));
  world.enemies.regroup(lit);
  cam.snap(pl.pos, spot.yaw ?? 0);
}

// ---------- simulation ----------
function simStep(dt) {
  const pl = world.player;
  game.simTime += dt;
  sharedUniforms.uTime.value = game.simTime;
  if (game.mode === 'title') {
    world.enemies.update(dt);
    world.enemies.updateVisuals(game.simTime);
    return;
  }
  if (game.mode !== 'play') return;
  game.seedStreakT -= dt;
  if (game.hitStop > 0) {
    // Freeze the world for a few frames on impact; effects keep going.
    game.hitStop -= dt;
    fx.update(dt, CFG.feel.traumaDecay);
    return;
  }
  game.playTime += dt;
  pl.update(dt, input, cam.yaw);
  if (input.consume('interact')) world.objectives.tryInteract(pl);
  world.enemies.update(dt);
  world.pickups.update(dt);
  world.objectives.update(dt, pl);
  if (world.objectives.gate.open && world.objectives.gate.openT < 1) fx.addTrauma(dt * 0.6);
  fx.update(dt, CFG.feel.traumaDecay);
  heroVis.update(dt, pl);
  cam.update(dt, pl, input.takeCamera(), fx.trauma, game.simTime);
  let combat = 0;
  for (const e of world.enemies.list) if (e.alive && e.state !== 'idle' && e.state !== 'return' && e.pos.distanceTo(pl.pos) < 16) combat = 1;
  audio.setCombat(combat);
}

function updateVisuals() {
  const pl = world.player;
  const t = game.simTime;
  world.enemies.updateVisuals(t);
  world.pickups.updateVisuals(t);
  world.objectives.updateVisuals(t);
  grass.update(t, game.mode === 'title' ? camera.position : pl.pos, pl.pos);
  water.update(t);
  // Shadow frustum follows the hero, snapped to texels to avoid shimmering.
  const focus = game.mode === 'title' ? new THREE.Vector3(0, 0, 0) : pl.pos;
  const texel = (2 * CFG.render.shadowExtent) / smap;
  const fx_ = Math.round(focus.x / texel) * texel, fz = Math.round(focus.z / texel) * texel;
  sun.target.position.set(fx_, focus.y, fz);
  sun.position.set(fx_ + sunDir.x * 60, focus.y + sunDir.y * 60, fz + sunDir.z * 60);
}

const heroNdc = new THREE.Vector3();
function render() {
  updateVisuals();
  // Where the hero is on screen, for the see-through occluder fade.
  if (game.mode === 'play' || game.mode === 'paused') {
    heroNdc.copy(world.player.pos).setY(world.player.pos.y + 0.8).project(camera);
    const w = innerWidth * pixelRatio, h = innerHeight * pixelRatio;
    fadeUniforms.uHeroScreen.value.set((heroNdc.x * 0.5 + 0.5) * w, (heroNdc.y * 0.5 + 0.5) * h);
    fadeUniforms.uHeroDist.value = camera.position.distanceTo(world.player.pos);
    fadeUniforms.uFadeRadius.value = h * 0.26;
  } else {
    fadeUniforms.uHeroDist.value = 0;
  }
  sky.update(game.simTime, camera);
  renderer.render(scene, camera);
  game.stats.drawCalls = renderer.info.render.calls;
  game.stats.triangles = renderer.info.render.triangles;
}

let last = performance.now();
let acc = 0;
let fpsT = 0, fpsN = 0, dbgT = 0;
function frame(now) {
  const dt = Math.min((now - last) / 1000, CFG.maxFrameDt);
  last = now;
  fpsT += dt; fpsN++;
  if (fpsT > 0.5) { game.fps = fpsN / fpsT; fpsT = 0; fpsN = 0; }
  if (!TEST) {
    if (game.mode === 'play' || game.mode === 'title') {
      acc += dt;
      let n = 0;
      while (acc >= CFG.simDt && n < 6) { simStep(CFG.simDt); acc -= CFG.simDt; n++; }
      if (n === 6) acc = 0;
    } else acc = 0;
  }
  if (game.mode === 'title') cam.titleOrbit(TEST ? 0 : now / 1000, { x: 0, z: -4 });
  // In test mode frames are drawn only by step()/render() so slow software
  // GL is not busy re-rendering identical frames in the background.
  if (!TEST || game.dirty) { render(); game.dirty = false; }
  if (game.mode === 'play') hud.update(hudState());
  dbgT -= dt;
  if (debugOn && dbgT <= 0) { dbgT = 0.25; debugEl.textContent = debugText(); }
}

function hudState() {
  const pl = world.player;
  const o = world.objectives;
  const lit = o.lanterns.map((l) => l.lit);
  const n = lit.filter(Boolean).length;
  return {
    health: pl.health, seeds: pl.sunseeds, seedProgress: pl.seedsTowardPetal, lit,
    objective: n < lit.length ? `Relight the wind-lanterns  ${n} / ${lit.length}` : 'Walk through the north gate',
    prompt: pl.dead > 0 || pl.locked > 0 ? null : o.interactOption(pl),
  };
}

function debugText() {
  const s = publicState();
  const lines = [
    `fps ${s.stats.fps.toFixed(0)}  draw calls ${s.stats.drawCalls}  triangles ${s.stats.triangles}  px ratio ${pixelRatio.toFixed(2)}  ${MOBILE ? 'mobile' : 'desktop'} quality`,
    `grass clumps ${grass.clumps}  hero anim: ${s.heroAnimMode}  mode ${s.mode}${TEST ? ' (test)' : ''}`,
    `player ${s.player.x.toFixed(1)}, ${s.player.y.toFixed(1)}, ${s.player.z.toFixed(1)}  hp ${s.player.health}  seeds ${s.player.sunseeds}`,
    `lanterns lit ${s.lanternsLit}/3  enemies alive ${s.enemies.alive}/${s.enemies.total}`,
    '--- assets (` to hide) ---',
  ];
  for (const [k, v] of Object.entries(s.assets)) lines.push(`${v.state.padEnd(11)} ${k}${v.reason ? '  - ' + v.reason : ''}`);
  return lines.join('\n');
}

function publicState() {
  const pl = world?.player;
  if (!pl) return { mode: game.mode };
  const o = world.objectives;
  return {
    mode: game.mode,
    time: +game.playTime.toFixed(3),
    player: {
      x: pl.pos.x, y: pl.pos.y, z: pl.pos.z, yaw: pl.yaw, vx: pl.vel.x, vz: pl.vel.z, grounded: pl.grounded,
      health: pl.health, maxHealth: CFG.player.maxHealth, sunseeds: pl.sunseeds, hitsTaken: pl.hitsTaken, deaths: pl.deaths,
      attacking: pl.attackT >= 0, dead: pl.dead > 0, invuln: pl.invuln,
    },
    camera: { yaw: cam.yaw, pitch: cam.pitch, distance: cam.curDist, fov: camera.fov },
    lanterns: o.summary,
    lanternsLit: o.litCount(),
    gateOpen: o.gate.open,
    won: o.won,
    enemies: { alive: world.enemies.alive().length, total: world.enemies.list.length, list: world.enemies.stateSummary },
    seedsOnGround: world.pickups.seeds.length,
    prompt: o.interactOption(pl)?.kind || null,
    assets: loader.status.toJSON(),
    assetSummary: loader.status.summary(),
    heroAnimMode: heroVis.mode,
    stats: { fps: game.fps, drawCalls: game.stats.drawCalls, triangles: game.stats.triangles, pixelRatio, grassClumps: grass.clumps, mobileQuality: MOBILE },
  };
}

// ---------- test-only inspection of the loaded hero ----------
function heroDebug(boneName) {
  heroVis.root.updateMatrixWorld(true);
  const v = (o) => { const p = o.getWorldPosition(new THREE.Vector3()); return [p.x, p.y, p.z].map((n) => +n.toFixed(4)); };
  const meshes = [];
  heroVis.root.traverse((o) => {
    if (!o.isMesh || o.name === 'outline' || o.parent?.name === 'debug-axes') return;
    const ol = o.children.find((c) => c.name === 'outline');
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    meshes.push({
      name: o.name, skinned: !!o.isSkinnedMesh, frustumCulled: o.frustumCulled, castShadow: o.castShadow,
      materials: mats.map((m) => ({ type: m.type, hasMap: !!m.map, mapColorSpace: m.map?.colorSpace || null })),
      outline: ol ? { skinned: !!ol.isSkinnedMesh, sameSkeleton: !o.isSkinnedMesh || ol.skeleton === o.skeleton, frustumCulled: ol.frustumCulled } : null,
    });
  });
  const bone = boneName ? findBone(heroVis.model, boneName) : null;
  return {
    mode: heroVis.mode,
    root: v(heroVis.root),
    meshes,
    bone: bone ? { name: bone.name, world: v(bone) } : null,
    attachments: (heroVis.attached || []).map((a) => ({ id: a.id, bone: a.bone.name, hasModel: a.hasModel, boneWorld: v(a.bone), gripWorld: v(a.frame), tipWorld: v(a.tip) })),
  };
}

// Fraction of the hero's on-screen pixels that are actually visible in the
// final image: compare renders with and without the hero, against the hero
// drawn alone. Works with the dithered fades because it uses the real shaders.
let visRT = null;
function heroVisibility() {
  const w = Math.floor(innerWidth * pixelRatio), h = Math.floor(innerHeight * pixelRatio);
  if (!visRT || visRT.width !== w || visRT.height !== h) { visRT?.dispose(); visRT = new THREE.WebGLRenderTarget(w, h); }
  updateVisuals();
  heroVis.root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(heroVis.root, true);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < 8; i++) {
    const p = new THREE.Vector3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z).project(camera);
    x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y);
  }
  const rx = Math.max(0, Math.floor((x0 * 0.5 + 0.5) * w)), ry = Math.max(0, Math.floor((y0 * 0.5 + 0.5) * h));
  const rw = Math.min(w, Math.ceil((x1 * 0.5 + 0.5) * w)) - rx, rh = Math.min(h, Math.ceil((y1 * 0.5 + 0.5) * h)) - ry;
  if (rw <= 0 || rh <= 0) return { ratio: 0, heroPixels: 0, visiblePixels: 0, rect: [rx, ry, rw, rh] };
  const grab = () => {
    sky.update(game.simTime, camera);
    renderer.setRenderTarget(visRT);
    renderer.render(scene, camera);
    renderer.setRenderTarget(null);
    const buf = new Uint8Array(rw * rh * 4);
    renderer.readRenderTargetPixels(visRT, rx, ry, rw, rh, buf);
    return buf;
  };
  const others = scene.children.filter((o) => o !== heroVis.root && o.visible && !o.isLight);
  const full = grab();
  heroVis.root.visible = false;
  const without = grab();
  for (const o of others) o.visible = false;
  const empty = grab();
  heroVis.root.visible = true;
  const alone = grab();
  for (const o of others) o.visible = true;
  const diff = (a, b, i) => Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]) > 24;
  let heroPx = 0, visPx = 0;
  for (let i = 0; i < full.length; i += 4) {
    if (!diff(alone, empty, i)) continue;
    heroPx++;
    if (diff(full, without, i)) visPx++;
  }
  render();
  return { ratio: heroPx ? +(visPx / heroPx).toFixed(3) : 0, heroPixels: heroPx, visiblePixels: visPx, rect: [rx, ry, rw, rh] };
}

// ---------- flow ----------
function startGame() {
  if (game.mode !== 'title' || !world) return;
  audio.start();
  audio.play('ui');
  game.mode = 'play';
  hud.clearOverlay();
  hud.show(true);
  input.clearPresses();
  cam.snap(world.player.pos, world.level.player.spawn.yaw ?? 0);
  hud.showToast('Relight the three wind-lanterns', 2600);
  game.dirty = true;
}

function togglePause() {
  if (game.mode === 'play') {
    game.mode = 'paused';
    hud.showPause(input.usingTouch, togglePause, () => location.reload());
  } else if (game.mode === 'paused') {
    game.mode = 'play';
    hud.clearOverlay();
    input.clearPresses();
  }
}

function winGame() {
  game.mode = 'win';
  audio.play('win');
  audio.setCombat(0);
  const pl = world.player;
  hud.show(false);
  hud.showWin({ time: game.playTime, sunseeds: pl.sunseeds, hits: pl.hitsTaken }, () => location.reload());
}

input.on('gesture', () => { if (game.mode !== 'loading') audio.start(); });
// iOS only unlocks audio on some event types; try them all (start() is idempotent).
for (const ev of ['pointerup', 'touchend', 'click']) document.addEventListener(ev, () => { if (game.mode !== 'loading' && game.mode !== 'title') audio.start(); }, { passive: true });
input.on('anykey', () => { if (game.mode === 'title') startGame(); });
input.on('pause', () => togglePause());
input.on('mute', () => hud.setMuted(audio.toggleMute()));
input.on('debug', () => { debugOn = !debugOn; debugEl.classList.toggle('hidden', !debugOn); heroVis?.setAxes(debugOn || params.get('axes') === '1'); game.dirty = true; });
input.on('device', () => { hud.setTouch(input.usingTouch); if (game.mode === 'title') hud.showTitle(input.usingTouch, loader ? assetLine() : ''); });
document.getElementById('overlays').addEventListener('pointerdown', (e) => {
  if (game.mode === 'title') { input.setTouchMode(e.pointerType === 'touch'); startGame(); }
});
hud.muteBtn.addEventListener('click', () => { audio.start(); hud.setMuted(audio.toggleMute()); });
hud.pauseBtn.addEventListener('click', () => togglePause());
debugEl.classList.toggle('hidden', !debugOn);
document.addEventListener('visibilitychange', () => { if (document.hidden && game.mode === 'play' && !TEST) togglePause(); });

function onResize() {
  const w = innerWidth, h = innerHeight;
  camera.aspect = w / h;
  // Portrait screens are narrow: widen the vertical FOV and step back a little.
  camera.fov = w < h ? CFG.camera.fov + (1 - w / h) * 26 : CFG.camera.fov;
  if (cam) cam.wantDistance = CFG.camera.distance * (w < h ? 1.12 : 1);
  camera.updateProjectionMatrix();
  renderer.setSize(w, h, false);
  setOutlineResolution(w, h, pixelRatio);
  fx?.setViewport(h, camera.fov, pixelRatio);
  game.dirty = true;
}
addEventListener('resize', onResize);

const ready = boot().catch((e) => { console.error(e); hud.setLoading('Failed to start: ' + e.message); throw e; });

installTestHook({
  TEST, ready, CFG,
  state: publicState,
  step(dt, n, draw = true) {
    for (let i = 0; i < n; i++) simStep(dt);
    if (draw) render();
    if (game.mode === 'play') hud.update(hudState());
    return publicState();
  },
  input(name, value) {
    if (name === 'moveX' || name === 'moveY') input.test[name] = value;
    else if (name === 'cameraYaw') cam.lockYaw = value;
    else if (name in input.held) input.press(name, !!value);
    else throw new Error('unknown input ' + name);
  },
  teleport(x, z) { world.player.teleport(x, z); cam.snap(world.player.pos, cam.yaw); },
  start: startGame,
  probe(x, z) { const t = world.terrain; return { height: t.heightAt(x, z), walkable: t.isWalkable(x, z), water: t.isWater(x, z), slope: t.slopeAt(x, z) }; },
  reloadAssets: () => loadAssets(true).then(() => loader.status.toJSON()),
  render,
  placements(id) { return (props.placements.get(id) || []).map((r) => ({ x: r.x, z: r.z, scale: r.scale, rot: r.rot })); },
  heroDebug(boneName) { return heroDebug(boneName); },
  heroVisibility() { return heroVisibility(); },
  breakdown() {
    // Cost of each scene part in the current view: hide it, re-render, diff.
    render();
    const base = { ...game.stats };
    const parts = [];
    const groups = [...scene.children.filter((o) => o !== props.group), ...props.group.children];
    for (const o of groups) {
      if (!o.visible) continue;
      o.visible = false;
      render();
      parts.push({ name: o.name || o.type, drawCalls: base.drawCalls - game.stats.drawCalls, triangles: base.triangles - game.stats.triangles });
      o.visible = true;
    }
    render();
    return { total: base, parts: parts.filter((p) => p.drawCalls || p.triangles).sort((a, b) => b.triangles - a.triangles) };
  },
});
