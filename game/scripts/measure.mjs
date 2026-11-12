// What does the running game cost the player's machine?
//
//   npm run build && node scripts/measure.mjs [--seconds 60] [--only name,name] [--out file.json]
//
// Plays the built game in headless Chromium in real time (no ?test=1: the game's own
// loop runs) while an autopilot keeps the hero running in circles and swinging the
// sword next to the first lantern. Once a second it records, for the whole Chromium
// process tree: CPU time, memory (PSS, so shared pages are not counted twice), and for
// the GPU: utilisation, memory and power as nvidia-smi reports them. In the page it
// records every frame's timestamp, the renderer's draw calls and triangles, and the
// JavaScript heap. Linux and NVIDIA only (it reads /proc and calls nvidia-smi).
//
// MEASURE_GPU=<vendor:device> picks the Vulkan device through Mesa's device-select
// layer (for example 10de:1e07 for an RTX 2080 Ti); unset = whatever Chromium picks.
import { chromium } from 'playwright-core';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'dist');
const argv = process.argv.slice(2);
const arg = (name, fallback) => { const i = argv.indexOf('--' + name); return i >= 0 ? argv[i + 1] : fallback; };
const SECONDS = Number(arg('seconds', 60));
const OUT = arg('out', path.join(root, 'check-output', 'runtime.json'));
const ONLY = arg('only', '').split(',').filter(Boolean);
const TICKS = 100; // USER_HZ on Linux

const GPU = ['--use-angle=vulkan', '--enable-features=Vulkan', '--ignore-gpu-blocklist', '--enable-gpu'];
const SCENARIOS = [
  { name: 'title-720p', label: 'title screen, 1280x720', gl: GPU, width: 1280, height: 720, play: false },
  { name: 'play-720p', label: 'playing, 1280x720', gl: GPU, width: 1280, height: 720, play: true },
  { name: 'play-1080p', label: 'playing, 1920x1080', gl: GPU, width: 1920, height: 1080, play: true },
  { name: 'play-phone', label: 'playing, phone layout 390x844 at 2x', gl: GPU, width: 390, height: 844, touch: true, play: true },
  { name: 'play-720p-uncapped', label: 'playing, 1280x720, frame rate not capped', gl: [...GPU, '--disable-frame-rate-limit', '--disable-gpu-vsync'], width: 1280, height: 720, play: true },
  { name: 'play-720p-software', label: 'playing, 1280x720, no GPU (software rendering)', gl: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'], width: 1280, height: 720, play: true, software: true },
].filter((s) => !ONLY.length || ONLY.includes(s.name));

// ---------- static server that counts what it sends ----------
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.glb': 'model/gltf-binary', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.ogg': 'audio/ogg', '.mp3': 'audio/mpeg', '.wasm': 'application/wasm', '.svg': 'image/svg+xml' };
let sent = { bytes: 0, files: 0 };
const server = http.createServer((req, res) => {
  const url = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = path.join(dist, url === '/' ? 'index.html' : url);
  if (!file.startsWith(dist) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
  sent.bytes += fs.statSync(file).size; sent.files += 1;
  res.writeHead(200, { 'content-type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-store' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}/`;
if (!fs.existsSync(path.join(dist, 'index.html'))) { console.error('dist/ missing: run npm run build first'); process.exit(2); }

// ---------- process tree and GPU sampling ----------
const read = (file) => { try { return fs.readFileSync(file, 'utf8'); } catch { return ''; } };
function tree(rootPid) {
  const parent = new Map();
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    const stat = read(`/proc/${d}/stat`);
    const close = stat.lastIndexOf(')');
    if (close < 0) continue;
    const f = stat.slice(close + 2).split(' ');
    parent.set(Number(d), { ppid: Number(f[1]), cpu: Number(f[11]) + Number(f[12]) });
  }
  const out = [];
  for (const [pid, p] of parent) {
    let q = pid, ok = false;
    for (let i = 0; i < 12 && q > 1; i++) { if (q === rootPid) { ok = true; break; } q = parent.get(q)?.ppid ?? 0; }
    if (!ok) continue;
    // Chromium rewrites the command line of its children into one string, so match on the text.
    const cmd = read(`/proc/${pid}/cmdline`).replaceAll('\0', ' ');
    const type = (cmd.match(/--type=([\w-]+)/) || [0, 'browser'])[1];
    const sub = (cmd.match(/--utility-sub-type=([\w.]+)/) || [0, ''])[1];
    const roll = read(`/proc/${pid}/smaps_rollup`);
    const kb = (key) => Number((roll.match(new RegExp(`^${key}:\\s+(\\d+)`, 'm')) || [0, 0])[1]);
    out.push({ pid, type: type === 'utility' && sub ? `utility:${sub.split('.').pop()}` : type, cpuTicks: p.cpu, pssKb: kb('Pss'), rssKb: kb('Rss') });
  }
  return out;
}
function gpus() {
  const text = execFileSync('nvidia-smi', ['--query-gpu=index,name,utilization.gpu,memory.used,power.draw', '--format=csv,noheader,nounits'], { encoding: 'utf8' });
  return text.trim().split('\n').map((l) => { const [index, name, util, mem, watts] = l.split(',').map((s) => s.trim()); return { index: Number(index), name, util: Number(util), memMiB: Number(mem), watts: Number(watts) }; });
}
function gpuProcesses() { // per-process GPU memory, when the driver reports it
  const out = new Map();
  try {
    const text = execFileSync('nvidia-smi', ['pmon', '-c', '1', '-s', 'm'], { encoding: 'utf8' });
    for (const l of text.split('\n')) {
      const f = l.trim().split(/\s+/);
      if (/^\d+$/.test(f[0]) && /^\d+$/.test(f[1]) && /^\d+$/.test(f[3])) out.set(Number(f[1]), { gpu: Number(f[0]), fbMiB: Number(f[3]) });
    }
  } catch { /* pmon not available */ }
  return out;
}
const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
const pct = (a, p) => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
const round = (v, d = 1) => Number(v.toFixed(d));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function idleBaseline(seconds = 5) {
  const samples = [];
  for (let i = 0; i < seconds; i++) { samples.push(gpus()); await sleep(1000); }
  return samples[0].map((g, k) => ({ index: g.index, name: g.name, util: mean(samples.map((s) => s[k].util)), memMiB: mean(samples.map((s) => s[k].memMiB)), watts: mean(samples.map((s) => s[k].watts)) }));
}

async function run(sc) {
  const env = { ...process.env };
  if (process.env.MEASURE_GPU && !sc.software) { env.MESA_VK_DEVICE_SELECT = process.env.MEASURE_GPU + '!'; env.MESA_VK_DEVICE_SELECT_FORCE_DEFAULT_DEVICE = '1'; }
  const idle = await idleBaseline();
  const srv = await chromium.launchServer({ headless: true, args: sc.gl, env });
  const rootPid = srv.process().pid;
  const browser = await chromium.connect(srv.wsEndpoint());
  const ctx = await browser.newContext({ viewport: { width: sc.width, height: sc.height }, deviceScaleFactor: sc.touch ? 2 : 1, hasTouch: !!sc.touch, isMobile: !!sc.touch });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  sent = { bytes: 0, files: 0 };
  const t0 = Date.now();
  await page.goto(base, { waitUntil: 'load' });
  await page.evaluate(() => window.__game.ready);
  const loadMs = Date.now() - t0;
  const download = { ...sent };
  const renderer = await page.evaluate(() => { const c = document.createElement('canvas').getContext('webgl2'); const d = c.getExtension('WEBGL_debug_renderer_info'); return c.getParameter(d ? d.UNMASKED_RENDERER_WEBGL : c.RENDERER); });
  if (sc.play) {
    await page.evaluate(() => {
      const g = window.__game;
      g.start();
      const l = (g.state().lanterns || [])[0];
      if (l && Number.isFinite(l.x)) g.teleport(l.x, l.z + 6);
      let yaw = 0, tick = 0;
      setInterval(() => { tick += 1; yaw += 0.06; g.input('cameraYaw', yaw); g.input('moveY', 1); g.input('attack', tick % 15 === 0); }, 100);
    });
  }
  await page.evaluate(() => { window.__frames = []; const loop = (now) => { window.__frames.push(now); requestAnimationFrame(loop); }; requestAnimationFrame(loop); });
  await sleep(3000); // let shaders compile and the first frames settle
  await page.evaluate(() => { window.__frames.length = 0; });

  const rows = [];
  let prev = tree(rootPid), prevT = Date.now();
  const frameTimes = [];
  for (let s = 0; s < SECONDS; s++) {
    await sleep(1000);
    const now = Date.now(), procs = tree(rootPid), g = gpus(), gp = gpuProcesses();
    const dt = (now - prevT) / 1000;
    const before = new Map(prev.map((p) => [p.pid, p.cpuTicks]));
    const byType = {};
    for (const p of procs) {
      const t = (byType[p.type] ||= { cores: 0, pssMb: 0, rssMb: 0, gpuFbMiB: 0 });
      t.cores += Math.max(0, p.cpuTicks - (before.get(p.pid) ?? p.cpuTicks)) / TICKS / dt;
      t.pssMb += p.pssKb / 1024; t.rssMb += p.rssKb / 1024;
      if (gp.has(p.pid)) t.gpuFbMiB += gp.get(p.pid).fbMiB;
    }
    const inPage = await page.evaluate(() => {
      const f = window.__frames.splice(0);
      const st = window.__game.state();
      return { frames: f, stats: st.stats, mode: st.mode, heapMb: performance.memory ? performance.memory.usedJSHeapSize / 1048576 : null };
    });
    for (let i = 1; i < inPage.frames.length; i++) frameTimes.push(inPage.frames[i] - inPage.frames[i - 1]);
    rows.push({ byType, gpus: g, frames: inPage.frames.length / dt, drawCalls: inPage.stats.drawCalls, triangles: inPage.stats.triangles, heapMb: inPage.heapMb, mode: inPage.mode, pixelRatio: inPage.stats.pixelRatio, mobileQuality: inPage.stats.mobileQuality });
    prev = procs; prevT = now;
  }
  await browser.close();
  await srv.close();

  const types = [...new Set(rows.flatMap((r) => Object.keys(r.byType)))];
  const perType = Object.fromEntries(types.map((t) => [t, {
    cpuCores: round(mean(rows.map((r) => r.byType[t]?.cores ?? 0)), 3),
    memPssMb: round(mean(rows.map((r) => r.byType[t]?.pssMb ?? 0))),
    gpuMemMiB: round(mean(rows.map((r) => r.byType[t]?.gpuFbMiB ?? 0))),
  }]));
  const sum = (key) => rows.map((r) => Object.values(r.byType).reduce((a, t) => a + t[key], 0));
  // the card that did the work is the one whose memory grew most
  const delta = idle.map((b, k) => mean(rows.map((r) => r.gpus[k].memMiB)) - b.memMiB);
  const k = sc.software ? -1 : delta.indexOf(Math.max(...delta));
  const card = k >= 0 ? {
    name: idle[k].name,
    utilPct: round(mean(rows.map((r) => r.gpus[k].util))), utilPctPeak: Math.max(...rows.map((r) => r.gpus[k].util)),
    memMiBAboveIdle: round(delta[k]), memMiBPeakAboveIdle: round(Math.max(...rows.map((r) => r.gpus[k].memMiB)) - idle[k].memMiB),
    wattsAboveIdle: round(mean(rows.map((r) => r.gpus[k].watts)) - idle[k].watts), wattsIdle: round(idle[k].watts),
    memMiBByProcess: round(mean(sum('gpuFbMiB'))),
  } : null;
  return {
    name: sc.name, label: sc.label, viewport: `${sc.width}x${sc.height}`, pixelRatio: rows.at(-1).pixelRatio, mobileQuality: rows.at(-1).mobileQuality,
    renderer, seconds: SECONDS, mode: rows.at(-1).mode, errors,
    fps: round(mean(rows.map((r) => r.frames))), frameMs: { median: round(pct(frameTimes, 0.5), 2), p95: round(pct(frameTimes, 0.95), 2), p99: round(pct(frameTimes, 0.99), 2), max: round(Math.max(0, ...frameTimes), 1) },
    drawCalls: Math.round(mean(rows.map((r) => r.drawCalls))), triangles: Math.round(mean(rows.map((r) => r.triangles))),
    cpuCores: round(mean(sum('cores')), 2), cpuCoresPeak: round(Math.max(...sum('cores')), 2),
    memPssMb: round(mean(sum('pssMb'))), memPssMbPeak: round(Math.max(...sum('pssMb'))), memRssMb: round(mean(sum('rssMb'))),
    jsHeapMb: round(mean(rows.map((r) => r.heapMb ?? 0))),
    gpu: card, byProcess: perType,
    load: { ms: loadMs, files: download.files, megabytes: round(download.bytes / 1048576, 2) },
  };
}

// An empty tab in the same browser, so a reader can tell the game from the browser around it.
async function emptyTab() {
  const env = { ...process.env };
  if (process.env.MEASURE_GPU) { env.MESA_VK_DEVICE_SELECT = process.env.MEASURE_GPU + '!'; env.MESA_VK_DEVICE_SELECT_FORCE_DEFAULT_DEVICE = '1'; }
  const idle = await idleBaseline(3);
  const srv = await chromium.launchServer({ headless: true, args: GPU, env });
  const browser = await chromium.connect(srv.wsEndpoint());
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 720 } })).newPage();
  await page.goto('about:blank');
  await page.evaluate(() => { const c = document.createElement('canvas'); document.body.appendChild(c); c.getContext('webgl2'); });
  await sleep(4000);
  const procs = tree(srv.process().pid), g = gpus();
  await browser.close();
  await srv.close();
  const k = g.map((x, i) => x.memMiB - idle[i].memMiB);
  return { memPssMb: round(procs.reduce((a, q) => a + q.pssKb, 0) / 1024), gpuMemMiBAboveIdle: round(Math.max(...k)), note: 'about:blank with one empty WebGL2 canvas, 1280x720' };
}

const results = [];
for (const sc of SCENARIOS) {
  process.stderr.write(`[measure] ${sc.name} ...\n`);
  const r = await run(sc);
  results.push(r);
  process.stderr.write(`[measure] ${sc.name}: ${r.fps} fps, ${r.cpuCores} cores, ${r.memPssMb} MB` + (r.gpu ? `, GPU ${r.gpu.utilPct} %, ${r.gpu.memMiBAboveIdle} MiB, +${r.gpu.wattsAboveIdle} W (${r.gpu.name})` : ', no GPU') + '\n');
}
server.close();
const cpu = (read('/proc/cpuinfo').match(/^model name\s*:\s*(.+)$/m) || [0, 'unknown'])[1];
const report = { stage: 'measure', machine: { cpu, logicalCores: os.cpus().length, memoryGb: Math.round(os.totalmem() / 2 ** 30) }, secondsPerScenario: SECONDS, emptyTab: await emptyTab(), scenarios: results };
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(report, null, 1));
console.log(JSON.stringify({ stage: 'measure', out: path.relative(root, OUT), scenarios: results.map((r) => ({ name: r.name, fps: r.fps, cpuCores: r.cpuCores, memMb: r.memPssMb, gpuPct: r.gpu?.utilPct ?? null, gpuMemMiB: r.gpu?.memMiBAboveIdle ?? null })) }));
