// Headless verification of the built game (dist/). Usage:
//   node scripts/check.mjs                 all viewports
//   CHECK_VIEWPORTS=desktop node scripts/check.mjs
//   CHECK_GL=swiftshader node scripts/check.mjs   force software WebGL
// Exit code is non-zero if any assertion fails. Screenshots + summary.json
// land in check-output/.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';
import { FIXTURE_PAGE, makeFixtures } from './fixtures.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const dist = path.join(root, 'dist');
const out = path.join(root, 'check-output');
fs.mkdirSync(out, { recursive: true });
const t0 = Date.now();

// ---------- static server with honest 404s ----------
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.bin': 'application/octet-stream', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.ogg': 'audio/ogg', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.wasm': 'application/wasm', '.svg': 'image/svg+xml', '.ktx2': 'image/ktx2' };
// In fixture passes, files under /assets/ are served from an overlay folder first.
let overlay = null;
const threeDir = path.join(root, 'node_modules/three');
const server = http.createServer((req, res) => {
  const url = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  if (url === '/__fixtures.html') { res.writeHead(200, { 'content-type': 'text/html' }); res.end(FIXTURE_PAGE); return; }
  let file = path.join(dist, url === '/' ? 'index.html' : url);
  if (url.startsWith('/__three/')) file = path.join(threeDir, url.slice('/__three/'.length));
  else if (overlay && url.startsWith('/assets/') && fs.existsSync(path.join(overlay, url.slice(8)))) file = path.join(overlay, url.slice(8));
  // A fixture set is self-contained: models, audio and textures it does not provide are
  // missing, even when the real game assets have them. Otherwise these passes would
  // change their outcome every time a real asset is added.
  else if (overlay && /^\/assets\/(models|audio|textures)\//.test(url)) { res.writeHead(404, { 'content-type': 'text/plain' }); res.end('not found'); return; }
  if (!(file.startsWith(dist) || file.startsWith(threeDir) || (overlay && file.startsWith(overlay))) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404, { 'content-type': 'text/plain' }); res.end('not found'); return; }
  res.writeHead(200, { 'content-type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-cache' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}/`;
if (!fs.existsSync(path.join(dist, 'index.html'))) { console.error('dist/ missing: run npm run build first'); process.exit(2); }
const manifest = JSON.parse(fs.readFileSync(path.join(dist, 'assets/manifest.json'), 'utf8'));
const assetFiles = new Set();
const collect = (v) => { if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) { if (k === 'file' && typeof x === 'string') assetFiles.add('assets/' + x); else collect(x); } };
collect(manifest);
for (const v of Object.values(manifest.music || {})) for (const f of [v].flat()) assetFiles.add('assets/' + f);

// ---------- browser ----------
// GPU via ANGLE/Vulkan works headless on this machine; SwiftShader is the fallback.
const GL_FLAGS = {
  gpu: ['--use-angle=vulkan', '--enable-features=Vulkan', '--ignore-gpu-blocklist', '--enable-gpu'],
  swiftshader: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
};
async function launch() {
  const order = process.env.CHECK_GL ? [process.env.CHECK_GL] : ['gpu', 'swiftshader'];
  for (const mode of order) {
    const browser = await chromium.launch({ headless: true, args: GL_FLAGS[mode] });
    const page = await browser.newPage();
    const gl = await page.evaluate(() => {
      const c = document.createElement('canvas').getContext('webgl2');
      if (!c) return null;
      const d = c.getExtension('WEBGL_debug_renderer_info');
      return c.getParameter(d ? d.UNMASKED_RENDERER_WEBGL : c.RENDERER);
    });
    await page.close();
    if (gl) return { browser, mode, gl };
    await browser.close();
  }
  throw new Error('no WebGL2 in headless Chromium');
}
const { browser, mode: glMode, gl: glRenderer } = await launch();
console.log(`[check] WebGL: ${glMode} - ${glRenderer}`);

const VIEWPORTS = [
  { name: 'desktop', width: 1280, height: 720, touch: false },
  { name: 'phone-portrait', width: 390, height: 844, touch: true },
  { name: 'phone-landscape', width: 844, height: 390, touch: true },
].filter((v) => !process.env.CHECK_VIEWPORTS || process.env.CHECK_VIEWPORTS.split(',').includes(v.name));

const summary = { ok: true, webgl: { mode: glMode, renderer: glRenderer }, viewports: {}, assets: null, placeholders: 0 };

for (const vp of VIEWPORTS) {
  const vt = Date.now();
  const res = { ok: true, assertions: [], screenshots: [], consoleErrors: [], missingAssetRequests: [], stats: {} };
  summary.viewports[vp.name] = res;
  const assert = (name, cond, detail = '') => {
    res.assertions.push({ name, ok: !!cond, detail });
    if (!cond) { res.ok = false; summary.ok = false; }
    console.log(`  ${cond ? 'PASS' : 'FAIL'} [${vp.name}] ${name}${detail ? ' - ' + detail : ''}`);
  };
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: vp.touch ? 2 : 1, hasTouch: vp.touch, isMobile: vp.touch });
  const page = await ctx.newPage();
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const url = m.location()?.url || '';
    const rel = url.replace(base, '');
    // A 404 for a manifest file is the asset contract working: placeholder used.
    if (/Failed to load resource/.test(m.text()) && assetFiles.has(rel)) { res.missingAssetRequests.push(rel); return; }
    res.consoleErrors.push(`${m.text()} ${url}`);
  });
  page.on('pageerror', (e) => res.consoleErrors.push('pageerror: ' + e.message));

  const shot = async (name) => {
    const file = path.join(out, `${vp.name}-${name}.png`);
    await page.evaluate(() => window.__game.render());
    await page.screenshot({ path: file, timeout: 180000 });
    res.screenshots.push(path.relative(root, file));
    const cs = await page.evaluate(() => window.__game.state());
    if (cs.camera) console.log(`  shot ${vp.name}-${name}: player ${cs.player.x.toFixed(1)},${cs.player.z.toFixed(1)} camera dist ${cs.camera.distance.toFixed(2)} yaw ${cs.camera.yaw.toFixed(2)}`);
  };
  const G = (fn, arg) => page.evaluate(fn, arg);
  const peak = { drawCalls: 0, triangles: 0 };
  // Only some steps draw a frame (budget stats + screenshots); software GL is slow.
  let drawEvery = 0;
  const step = async (n, dt = 1 / 60) => {
    const draw = drawEvery++ % 8 === 0;
    const st = await G(([n, dt, draw]) => window.__game.step(dt, n, draw), [n, dt, draw]);
    if (draw && st.mode === 'play') { peak.drawCalls = Math.max(peak.drawCalls, st.stats.drawCalls); peak.triangles = Math.max(peak.triangles, st.stats.triangles); }
    return st;
  };
  const inp = (name, v) => G(([name, v]) => window.__game.input(name, v), [name, v]);
  const state = () => G(() => window.__game.state());

  try {
    await page.goto(base + '?test=1', { waitUntil: 'load' });
    await G(() => window.__game.ready);
    await page.waitForTimeout(150);
    const st0 = await state();
    assert('title screen shown', st0.mode === 'title', st0.mode);
    await shot('title');
    await checkLayout(page, vp, assert, '[data-screen=title] .card', 'title');

    // Start like a player would.
    if (vp.touch) await page.tap('[data-screen=title]', { position: { x: vp.width / 2, y: vp.height / 2 } });
    else await page.mouse.click(vp.width / 2, vp.height / 2);
    const st1 = await state();
    assert('starts on click/tap', st1.mode === 'play', st1.mode);
    await inp('cameraYaw', 0);
    await step(30);

    // Canvas is not blank.
    const variance = await G(() => {
      const src = document.getElementById('game');
      const c = document.createElement('canvas');
      c.width = 160; c.height = 90;
      const g = c.getContext('2d');
      g.drawImage(src, 0, 0, 160, 90);
      const d = g.getImageData(0, 0, 160, 90).data;
      let s = 0, s2 = 0, n = 0;
      for (let i = 0; i < d.length; i += 4) { const l = 0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2]; s += l; s2 += l * l; n++; }
      const m = s / n;
      return s2 / n - m * m;
    });
    assert('canvas is not blank', variance > 80, `luma variance ${variance.toFixed(0)}`);
    await checkLayout(page, vp, assert, null, 'play');

    // Pause overlay via the on-screen button, then resume.
    await page.click('[data-control=pause]');
    let ps = await state();
    assert('pause button pauses', ps.mode === 'paused', ps.mode);
    await checkLayout(page, vp, assert, '[data-screen=pause] .card', 'pause');
    await page.click('[data-control=resume]');
    ps = await state();
    assert('resume button resumes', ps.mode === 'play', ps.mode);

    // Movement: hold forward (north) for one second.
    let s = await state();
    const p0 = { x: s.player.x, z: s.player.z };
    await inp('moveY', 1);
    await step(60);
    s = await state();
    const moved = Math.hypot(s.player.x - p0.x, s.player.z - p0.z);
    assert('player moves while input is held', moved > 2.5, `${moved.toFixed(2)} m in 1 s`);
    await inp('moveY', null);
    await step(40);
    s = await state();
    assert('player decelerates to a stop', Math.hypot(s.player.vx, s.player.vz) < 0.05, `speed ${Math.hypot(s.player.vx, s.player.vz).toFixed(3)}`);

    // Water: run south from near the spawn until the shore stops us.
    let shoreOk = false, shoreDetail = '';
    for (const x of [0, 2.5, -2.5, 5, -5, 8]) {
      await G(([x]) => window.__game.teleport(x, 40), [x]);
      await inp('moveY', -1);
      await step(420);
      const a = await state();
      await step(30);
      const b = await state();
      await inp('moveY', null);
      const ahead = await G(([x, z]) => window.__game.probe(x, z + 0.9), [b.player.x, b.player.z]);
      const here = await G(([x, z]) => window.__game.probe(x, z), [b.player.x, b.player.z]);
      const still = Math.hypot(b.player.x - a.player.x, b.player.z - a.player.z);
      shoreDetail = `x=${x} stopped at z=${b.player.z.toFixed(2)} y=${b.player.y.toFixed(2)}, drift ${still.toFixed(3)}, ahead water=${ahead.water}, here water=${here.water}`;
      if (!here.water && ahead.water && still < 0.05 && b.player.y > 0) { shoreOk = true; break; }
    }
    assert('player stops at the water line', shoreOk, shoreDetail);
    await step(20);

    // Jump on flat ground.
    await G(() => window.__game.teleport(0, 36));
    await step(5);
    s = await state();
    const g0 = s.player.y;
    await inp('jump', true);
    let top = g0;
    for (let i = 0; i < 24; i++) { const q = await step(1); top = Math.max(top, q.player.y); }
    await inp('jump', false);
    await step(60);
    s = await state();
    assert('jump leaves the ground and lands', top - g0 > 0.9 && s.player.grounded, `peak +${(top - g0).toFixed(2)} m, grounded after: ${s.player.grounded}`);

    // Attack damages an enemy (the roaming pair near the path).
    s = await state();
    const roam = s.enemies.list.find((e) => e.group === null && e.alive);
    await G(([x, z]) => window.__game.teleport(x, z + 1.3), [roam.x, roam.z]);
    await inp('attack', true); await step(1); await inp('attack', false);
    await step(20);
    s = await state();
    const after = s.enemies.list.find((e) => e.id === roam.id);
    assert('sword attack damages an enemy', after.hp < 3, `hp 3 -> ${after.hp}`);

    // Occlusion: put a tree between the camera and the hero; most of the hero
    // must stay visible (camera pull-in and/or dithered fade).
    {
      const trees = await G(() => window.__game.placements('tree_broadleaf'));
      const open = await G(() => { const g = window.__game; g.teleport(0, 37); g.input('cameraYaw', 0); g.step(1 / 60, 90); return g.heroVisibility(); });
      let occ = null, used = null;
      for (const t of trees) {
        const spot = [t.x, t.z - 2.4];
        const pr = await G(([x, z]) => window.__game.probe(x, z), spot);
        if (!pr.walkable) continue;
        const r = await G(([x, z]) => { const g = window.__game; g.teleport(x, z); g.input('cameraYaw', 0); const st = g.step(1 / 60, 90); return { vis: g.heroVisibility(), dist: st.camera.distance, px: st.player.x, pz: st.player.z }; }, spot);
        // Only count it if the hero really stands behind the tree (not pushed aside).
        if (Math.abs(r.px - t.x) > 0.6 || r.pz > t.z - 1.5) continue;
        occ = r; used = t;
        break;
      }
      if (occ) await shot('occlusion');
      assert('hero stays visible behind a tree', occ && occ.vis.ratio >= 0.7 && occ.vis.ratio >= open.ratio - 0.2,
        occ ? `visible ${(occ.vis.ratio * 100).toFixed(0)}% (open ground ${(open.ratio * 100).toFixed(0)}%), camera dist ${occ.dist.toFixed(2)}, tree at ${used.x.toFixed(1)},${used.z.toFixed(1)}` : 'no usable tree found');
      await inp('cameraYaw', 0);
    }

    // Full scripted run: clear each lantern's guards, light it, walk through the gate.
    let litShot = false, midShot = false;
    const lanterns = s.lanterns;
    for (const l of lanterns) {
      await G(([x, z]) => window.__game.teleport(x, z + 7), [l.x, l.z]);
      for (let iter = 0; iter < 900; iter++) {
        s = await state();
        const guards = s.enemies.list.filter((e) => e.alive && e.group === l.id);
        if (!guards.length) break;
        if (s.player.dead) { await step(10); continue; }
        if (Math.hypot(s.player.x - l.x, s.player.z - l.z) > 16) { await G(([x, z]) => window.__game.teleport(x, z + 6), [l.x, l.z]); continue; }
        guards.sort((a, b) => Math.hypot(a.x - s.player.x, a.z - s.player.z) - Math.hypot(b.x - s.player.x, b.z - s.player.z));
        const t = guards[0];
        const dx = t.x - s.player.x, dz = t.z - s.player.z, d = Math.hypot(dx, dz);
        if (d > 1.4) {
          await inp('moveX', dx / d); await inp('moveY', -dz / d);
          await step(4);
        } else {
          await inp('moveX', null); await inp('moveY', null);
          await inp('attack', true); await step(1); await inp('attack', false);
          await step(12);
          if (!midShot) { await shot('midgame'); midShot = true; }
        }
      }
      await inp('moveX', null); await inp('moveY', null);
      s = await state();
      assert(`lantern "${l.id}" guards cleared`, s.enemies.list.filter((e) => e.alive && e.group === l.id).length === 0);
      await step(90); // let sunseeds settle
      // Collect nearby seeds by walking over the drop area.
      await G(([x, z]) => window.__game.teleport(x, z + 1.9), [l.x, l.z]);
      await step(30);
      s = await state();
      assert(`interact prompt at lantern "${l.id}"`, s.prompt === 'lantern', `prompt=${s.prompt}`);
      await inp('interact', true); await step(1); await inp('interact', false);
      await step(36);
      if (!litShot) { await shot('lantern-lighting'); litShot = true; }
      await step(60);
      s = await state();
      assert(`lantern "${l.id}" lit`, s.lanterns.find((x) => x.id === l.id).lit);
      if (l === lanterns[0]) { await step(60); await shot('lantern-lit'); }
    }
    s = await state();
    assert('all three lanterns lit', s.lanternsLit === 3, `${s.lanternsLit}/3`);
    assert('gate opens', s.gateOpen);
    await step(60 * 3.5);
    // Walk north through the gate.
    const gx = 0, gz = -38;
    await G(([x, z]) => window.__game.teleport(x, z + 4), [gx, gz]);
    await step(5);
    await shot('gate-open');
    await inp('moveY', 1);
    for (let i = 0; i < 30; i++) { s = await step(10); if (s.mode === 'win') break; }
    await inp('moveY', null);
    assert('reaches the win state', s.mode === 'win', `mode=${s.mode} z=${s.player.z.toFixed(2)}`);
    await page.waitForTimeout(200);
    await shot('win');
    await checkLayout(page, vp, assert, '[data-screen=win] .card', 'win');
    res.final = { time: s.time, sunseeds: s.player.sunseeds, hitsTaken: s.player.hitsTaken, deaths: s.player.deaths };
    res.stats = { ...s.stats, peakDrawCalls: peak.drawCalls, peakTriangles: peak.triangles };
    assert('draw calls within budget (<= 150)', peak.drawCalls <= 150, `peak ${peak.drawCalls}`);
    // Budgets set from measurement with the generated assets installed (2026-11-12):
    // desktop peaked near 0.9M triangles per frame (shadow pass and outlines included),
    // phone quality lower because scattered props are thinned out there.
    const triBudget = vp.touch ? 750000 : 1100000;
    assert(`triangles within budget (<= ${triBudget / 1000}k)`, peak.triangles <= triBudget, `peak ${peak.triangles}`);
    res.heroAnimMode = s.heroAnimMode;
    // Stats from a gameplay frame (the win overlay hides nothing in 3D, but use a play frame).
    summary.assets = s.assetSummary;
    summary.assetStatus = s.assets;
  } catch (e) {
    assert('script ran without exceptions', false, e.stack || e.message);
  }
  assert('no console errors', res.consoleErrors.length === 0, res.consoleErrors.slice(0, 5).join(' | '));
  res.seconds = +((Date.now() - vt) / 1000).toFixed(1);
  await ctx.close();
}

// ---------- asset-contract passes with generated fixture GLBs ----------
if (!process.env.CHECK_SKIP_FIXTURES) {
  const fixDir = path.join(out, 'fixtures');
  fs.rmSync(fixDir, { recursive: true, force: true });
  await makeFixtures(browser, base, fixDir);
  const passes = [
    { name: 'fixtures-clips', hero: 'models/hero.glb', expectMode: 'clips' },
    { name: 'fixtures-static', hero: 'static/hero.glb', expectMode: 'static' },
  ];
  for (const fp of passes) {
    const res = { ok: true, assertions: [], screenshots: [], consoleErrors: [] };
    summary.viewports[fp.name] = res;
    const assert = (name, cond, detail = '') => {
      res.assertions.push({ name, ok: !!cond, detail });
      if (!cond) { res.ok = false; summary.ok = false; }
      console.log(`  ${cond ? 'PASS' : 'FAIL'} [${fp.name}] ${name}${detail ? ' - ' + detail : ''}`);
    };
    const m = structuredClone(manifest);
    m.hero.file = fp.hero;
    m.hero.facing = '-x';
    fs.writeFileSync(path.join(fixDir, 'manifest.json'), JSON.stringify(m, null, 2));
    overlay = fixDir;
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await ctx.newPage();
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      const rel = (msg.location()?.url || '').replace(base, '');
      if (/Failed to load resource/.test(msg.text()) && assetFiles.has(rel)) return;
      res.consoleErrors.push(msg.text());
    });
    page.on('pageerror', (e) => res.consoleErrors.push('pageerror: ' + e.message));
    try {
      await page.goto(base + '?test=1');
      await page.evaluate(() => window.__game.ready);
      let s = await page.evaluate(() => window.__game.state());
      const A = s.assets;
      const near = (a, b, tol = 0.03) => Math.abs(a - b) <= tol;
      assert('hero GLB loaded', A.hero?.state === 'loaded', JSON.stringify(A.hero));
      assert(`hero animation mode is ${fp.expectMode}`, s.heroAnimMode === fp.expectMode, s.heroAnimMode);
      assert('hero normalised to manifest height', near(A.hero?.size?.[1], m.hero.height) && near(A.hero?.minY, 0), `size ${A.hero?.size} minY ${A.hero?.minY}`);
      // The fixture is wider along X (nose on -X). facing "-x" turns it a quarter, so
      // its long axis must end up along Z. (Bounds are centred, so this checks the
      // rotation, not its sign.)
      assert('hero facing rotation applied (long axis now along Z)', A.hero?.size?.[2] > A.hero?.size?.[0] + 0.05, `size ${A.hero?.size}`);
      const t = A['props.tree_broadleaf'];
      assert('100x-scaled, off-origin tree normalised', t?.state === 'loaded' && near(t.size[1], m.props.tree_broadleaf.height) && near(t.minY, 0), JSON.stringify(t));
      const e = A['enemies.mossling'];
      assert('enemy GLB loaded and normalised', e?.state === 'loaded' && near(e.size[1], m.enemies.mossling.height), JSON.stringify(e));
      assert('corrupt GLB reported as failed', A['props.rock_large']?.state === 'failed', JSON.stringify(A['props.rock_large']));
      assert('missing files reported as placeholder', A['props.bush']?.state === 'placeholder' && A['music.combat']?.state === 'placeholder');
      assert('sky texture loaded', A.sky?.state === 'loaded', JSON.stringify(A.sky));
      assert('attachment with a missing bone falls back and is reported', A['hero.attachments.sword']?.state === 'failed' && /bone/.test(A['hero.attachments.sword'].reason), JSON.stringify(A['hero.attachments.sword']));
      assert('music loaded and decoded', A['music.field']?.state === 'loaded', JSON.stringify(A['music.field']));
      await page.mouse.click(640, 360);
      await page.evaluate(() => { const g = window.__game; g.input('cameraYaw', 0); g.input('moveY', 1); g.step(1 / 60, 50); g.input('moveY', null); g.step(1 / 60, 20); });
      await page.evaluate(() => { const g = window.__game; g.input('attack', true); g.step(1 / 60, 1); g.input('attack', false); g.step(1 / 60, 8); });
      s = await page.evaluate(() => window.__game.state());
      assert('game runs with generated assets', s.mode === 'play' && s.player.attacking, `mode ${s.mode}`);
      const file = path.join(out, `${fp.name}.png`);
      await page.screenshot({ path: file });
      res.screenshots.push(path.relative(root, file));
    } catch (err) {
      assert('fixture pass ran without exceptions', false, err.stack || err.message);
    }
    assert('no console errors', res.consoleErrors.length === 0, res.consoleErrors.slice(0, 5).join(' | '));
    overlay = null;
    await ctx.close();
  }
}

// ---------- rigged stand-in heroes with a sword attachment ----------
if (!process.env.CHECK_SKIP_FIXTURES) {
  const fixDir = path.join(out, 'fixtures');
  for (const heroFile of ['rigged/knight.glb', 'rigged/male_apose_voxel.glb']) {
    if (!fs.existsSync(path.join(fixDir, heroFile))) { console.log(`  SKIP rigged pass: ${heroFile} not available`); continue; }
    const name = 'rigged-' + path.basename(heroFile, '.glb');
    const res = { ok: true, assertions: [], screenshots: [], consoleErrors: [] };
    summary.viewports[name] = res;
    const assert = (n, cond, detail = '') => {
      res.assertions.push({ name: n, ok: !!cond, detail });
      if (!cond) { res.ok = false; summary.ok = false; }
      console.log(`  ${cond ? 'PASS' : 'FAIL'} [${name}] ${n}${detail ? ' - ' + detail : ''}`);
    };
    const m = structuredClone(manifest);
    m.hero.file = heroFile;
    const att = m.hero.attachments[0];
    fs.writeFileSync(path.join(fixDir, 'manifest.json'), JSON.stringify(m, null, 2));
    overlay = fixDir;
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await ctx.newPage();
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      const rel = (msg.location()?.url || '').replace(base, '');
      if (/Failed to load resource/.test(msg.text()) && assetFiles.has(rel)) return;
      res.consoleErrors.push(msg.text());
    });
    page.on('pageerror', (e) => res.consoleErrors.push('pageerror: ' + e.message));
    try {
      await page.goto(base + '?test=1&axes=1');
      await page.evaluate(() => window.__game.ready);
      await page.evaluate(() => window.__game.start());
      const s = await page.evaluate(() => window.__game.state());
      const A = s.assets;
      assert('rigged hero loads with clips', A.hero?.state === 'loaded' && s.heroAnimMode === 'clips' && ['idle', 'walk', 'run', 'jump', 'attack'].every((c) => A.hero.clips.includes(c)), JSON.stringify(A.hero));
      const sa = A['hero.attachments.sword'];
      assert(`sword attached to "${att.bone}"`, sa?.state === 'loaded' && /hand/i.test(sa.bone), JSON.stringify(sa));
      assert('sword normalised to its length', Math.abs(sa?.size?.[1] - att.length) < 0.01, `size ${sa?.size}`);
      const hd = await page.evaluate((b) => window.__game.heroDebug(b), att.bone);
      const skinned = hd.meshes.filter((x) => x.skinned);
      assert('hero has skinned meshes', skinned.length > 0, `${skinned.length} skinned of ${hd.meshes.length}`);
      assert('skinned outline uses the same skeleton', skinned.every((x) => x.outline?.skinned && x.outline.sameSkeleton), JSON.stringify(skinned.map((x) => x.outline)));
      assert('hero meshes and outlines are never frustum-culled', hd.meshes.every((x) => !x.frustumCulled && (!x.outline || !x.outline.frustumCulled)));
      assert('hero casts shadows (skinned depth pass)', skinned.every((x) => x.castShadow));
      assert('hero re-shaded with the toon material', hd.meshes.every((x) => x.materials.every((mm) => mm.type === 'MeshToonMaterial')), JSON.stringify(hd.meshes.map((x) => x.materials)));
      if (heroFile.includes('voxel')) {
        assert('base-colour texture kept (sRGB)', hd.meshes.some((x) => x.materials.some((mm) => mm.hasMap && mm.mapColorSpace === 'srgb')), JSON.stringify(hd.meshes.map((x) => x.materials)));
      }
      // Track the sword against the hand bone through idle, run and an attack.
      const track = await page.evaluate((b) => {
        const g = window.__game;
        g.teleport(0, 36); g.input('cameraYaw', 1.4);
        const samples = [];
        const rel = (p, r) => [p[0] - r[0], p[1] - r[1], p[2] - r[2]];
        const take = (phase) => { const d = g.heroDebug(b); const a = d.attachments[0]; samples.push({ phase, hand: rel(a.boneWorld, d.root), grip: rel(a.gripWorld, d.root), tip: rel(a.tipWorld, d.root), gripToBone: Math.hypot(...rel(a.gripWorld, a.boneWorld)) }); };
        for (let i = 0; i < 12; i++) { g.step(1 / 60, 10, false); take('idle'); }
        g.input('moveY', 1);
        for (let i = 0; i < 16; i++) { g.step(1 / 60, 4, false); take('run'); }
        g.input('moveY', null); g.step(1 / 60, 40, false);
        g.input('attack', true); g.step(1 / 60, 1, false); g.input('attack', false);
        for (let i = 0; i < 14; i++) { g.step(1 / 60, 2, false); take('attack'); }
        return samples;
      }, att.bone);
      const off = Math.hypot(...att.offset);
      const dev = Math.max(...track.map((t) => Math.abs(t.gripToBone - off)));
      assert('sword grip stays on the hand bone across frames', dev < 0.02 && Math.max(...track.map((t) => t.gripToBone)) < 0.15, `grip-to-bone ${off.toFixed(3)} m expected, max deviation ${(dev * 100).toFixed(1)} cm over ${track.length} frames`);
      const spread = (arr) => Math.max(...arr.map((a) => Math.max(...arr.map((b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])))));
      const handSpread = spread(track.map((t) => t.hand));
      const tipAttack = spread(track.filter((t) => t.phase === 'attack').map((t) => t.tip));
      assert('hand moves with the animation and the sword follows', handSpread > 0.15 && tipAttack > 0.3, `hand travel ${handSpread.toFixed(2)} m, sword tip travel in attack ${tipAttack.toFixed(2)} m`);
      // Mid-swing screenshot, closer camera.
      // Side-on and zoomed in so the blade and the debug axes read in the image.
      for (let i = 0; i < 8; i++) { await page.mouse.move(640, 360); await page.mouse.wheel(0, -300); }
      await page.evaluate(() => { const g = window.__game; const y = g.state().player.yaw; g.input('cameraYaw', y + Math.PI / 2 + 0.35); g.step(1 / 60, 90, false); g.input('attack', true); g.step(1 / 60, 1, false); g.input('attack', false); g.step(1 / 60, 9); });
      const vis = await page.evaluate(() => window.__game.heroVisibility());
      assert('rigged hero is on screen (not culled)', vis.ratio > 0.5 && vis.heroPixels > 500, JSON.stringify(vis));
      const file = path.join(out, `${name}-attack.png`);
      await page.evaluate(() => window.__game.render());
      await page.screenshot({ path: file });
      res.screenshots.push(path.relative(root, file));
    } catch (err) {
      assert('rigged pass ran without exceptions', false, err.stack || err.message);
    }
    assert('no console errors', res.consoleErrors.length === 0, res.consoleErrors.slice(0, 5).join(' | '));
    overlay = null;
    await ctx.close();
  }
}

// ---------- losing every petal respawns the hero ----------
{
  const res = { ok: true, assertions: [], screenshots: [], consoleErrors: [] };
  summary.viewports.respawn = res;
  const assert = (name, cond, detail = '') => {
    res.assertions.push({ name, ok: !!cond, detail });
    if (!cond) { res.ok = false; summary.ok = false; }
    console.log(`  ${cond ? 'PASS' : 'FAIL'} [respawn] ${name}${detail ? ' - ' + detail : ''}`);
  };
  const ctx = await browser.newContext({ viewport: { width: 960, height: 540 } });
  const page = await ctx.newPage();
  await page.goto(base + '?test=1');
  await page.evaluate(() => window.__game.ready);
  await page.evaluate(() => window.__game.start());
  // Stand still among the west guards until all five petals are gone.
  const r = await page.evaluate(() => {
    const g = window.__game;
    const l = g.state().lanterns.find((x) => x.id === 'west');
    g.teleport(l.x, l.z + 3.2);
    let died = false, minHp = 5, s;
    for (let i = 0; i < 300; i++) {
      s = g.step(1 / 60, 6);
      minHp = Math.min(minHp, s.player.health);
      if (s.player.dead) died = true;
      if (died && !s.player.dead) break;
    }
    return { died, minHp, hp: s.player.health, deaths: s.player.deaths, z: s.player.z, hits: s.player.hitsTaken };
  });
  assert('mossling lunges hurt the hero', r.hits >= 5, `hits ${r.hits}`);
  assert('hero falls when the petals run out', r.died && r.minHp === 0, JSON.stringify(r));
  assert('hero respawns at the start with full petals', r.deaths === 1 && r.hp === 5 && r.z > 35, JSON.stringify(r));
  await ctx.close();
}

async function checkLayout(page, vp, assert, cardSel, phase) {
  const r = await page.evaluate(([cardSel, touch]) => {
    const W = innerWidth, H = innerHeight;
    window.scrollTo(0, 9999);
    document.scrollingElement.scrollTop = 9999;
    const se = document.scrollingElement;
    const noScroll = se.scrollTop === 0 && window.scrollY === 0 && se.scrollHeight <= H + 1 && se.scrollWidth <= W + 1;
    // HUD read-outs are pointer-events:none, which elementFromPoint skips. Make
    // only the element under test hittable so any visible cover still wins.
    const mark = 'data-hit-probe';
    const style = document.createElement('style');
    style.textContent = `[${mark}], [${mark}] * { pointer-events: auto !important; }`;
    document.head.appendChild(style);
    const sels = cardSel ? [cardSel, ...[...document.querySelectorAll(cardSel + ' .btn')].map((_, i) => `${cardSel} .btn:nth-of-type(${i + 1})`)]
      : ['.flower', '[data-control=seeds]', '.lanterns', '.objective', '[data-control=mute]', '[data-control=pause]', ...(touch ? ['[data-control=attack]', '[data-control=jump]', '[data-control=interact]', '[data-control=stick]'] : [])];
    const bad = [];
    const optional = new Set(['.objective']); // hidden on purpose on short screens
    for (const sel of [...sels]) {
      const el = document.querySelector(sel);
      if (!el) { bad.push(sel + ' missing'); continue; }
      if (optional.has(sel) && getComputedStyle(el).display === 'none') { sels.splice(sels.indexOf(sel), 1); continue; }
      const b = el.getBoundingClientRect();
      if (b.width === 0 || b.height === 0) { bad.push(sel + ' zero size'); continue; }
      if (b.left < 0 || b.top < 0 || b.right > W + 0.5 || b.bottom > H + 0.5) bad.push(`${sel} outside viewport (${b.left.toFixed(0)},${b.top.toFixed(0)},${b.right.toFixed(0)},${b.bottom.toFixed(0)})`);
      el.setAttribute(mark, '');
      const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      el.removeAttribute(mark);
      if (!hit || !(el === hit || el.contains(hit))) bad.push(`${sel} covered by ${hit ? hit.tagName + '.' + hit.className : 'nothing'}`);
    }
    style.remove();
    // Visible HUD pieces must not overlap each other either.
    const rects = sels.map((sel) => [sel, document.querySelector(sel)?.getBoundingClientRect()]).filter(([, b]) => b && b.width);
    for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
      const [sa, a] = rects[i], [sb, b] = rects[j];
      if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1 && !document.querySelector(sa).contains(document.querySelector(sb)) && !document.querySelector(sb).contains(document.querySelector(sa))) bad.push(`${sa} overlaps ${sb}`);
    }
    return { noScroll, bad, count: sels.length };
  }, [cardSel, vp.touch]);
  assert(`no page scroll (${phase})`, r.noScroll);
  assert(`${r.count} ${phase} controls inside viewport and uncovered`, r.bad.length === 0, r.bad.join('; '));
}

await browser.close();
server.close();
summary.placeholders = summary.assets ? (summary.assets.placeholder || 0) : null;
summary.seconds = +((Date.now() - t0) / 1000).toFixed(1);
summary.bundle = bundleStats();
fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summary, null, 2));
const brief = {
  ok: summary.ok,
  webgl: summary.webgl,
  seconds: summary.seconds,
  placeholders: summary.placeholders,
  assets: summary.assets,
  bundle: summary.bundle,
  viewports: Object.fromEntries(Object.entries(summary.viewports).map(([k, v]) => [k, {
    ok: v.ok, passed: v.assertions.filter((a) => a.ok).length, failed: v.assertions.filter((a) => !a.ok).map((a) => a.name + (a.detail ? ': ' + a.detail : '')),
    peakDrawCalls: v.stats?.peakDrawCalls, peakTriangles: v.stats?.peakTriangles, heroAnimMode: v.heroAnimMode, run: v.final, seconds: v.seconds, screenshots: v.screenshots,
    missingAssetRequests: v.missingAssetRequests?.length,
  }])),
};
console.log(JSON.stringify(brief, null, 2));
process.exit(summary.ok ? 0 : 1);

function bundleStats() {
  let js = 0, total = 0;
  const walk = (d) => { for (const f of fs.readdirSync(d)) { const p = path.join(d, f); const st = fs.statSync(p); if (st.isDirectory()) walk(p); else { total += st.size; if (/index-.*\.js$/.test(f)) js += st.size; } } };
  walk(dist);
  return { mainJsKB: Math.round(js / 1024), distKB: Math.round(total / 1024) };
}
