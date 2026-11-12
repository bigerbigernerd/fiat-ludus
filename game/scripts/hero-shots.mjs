// Close-ups of the hero in the running game, for judging the model, the rig and the
// sword attachment by eye:  node scripts/hero-shots.mjs [out-dir] [--axes]
// Needs a build (npm run build). Writes hero-idle-front.png, hero-idle-side.png,
// hero-run.png and hero-attack.png (each a crop around the hero).
import { chromium } from 'playwright-core';
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
const here = path.dirname(new URL(import.meta.url).pathname);
const dist = path.join(here, '..', 'dist');
const out = process.argv[2] && !process.argv[2].startsWith('--') ? process.argv[2] : path.join(here, '..', 'check-output');
const axes = process.argv.includes('--axes');
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json' };
const srv = http.createServer((q, r) => {
  let f = path.join(dist, new URL(q.url, 'http://x').pathname);
  if (f.endsWith('/')) f += 'index.html';
  if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) { r.writeHead(404); r.end(); return; }
  r.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(r);
});
await new Promise((r) => srv.listen(0, r));
const browser = await chromium.launch({ args: ['--use-angle=vulkan', '--enable-features=Vulkan', '--ignore-gpu-blocklist', '--enable-gpu'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 2 });
page.on('console', (m) => { if (m.type() === 'error') console.log('console error', m.text()); });
await page.goto(`http://127.0.0.1:${srv.address().port}/?test=1${axes ? '&axes=1' : ''}`);
await page.evaluate(() => window.__game.ready);
await page.evaluate(() => window.__game.start());
const clip = { x: 390, y: 110, width: 500, height: 500 };
const shot = (name) => page.screenshot({ path: path.join(out, `hero-${name}.png`), clip });
const G = (fn, arg) => page.evaluate(fn, arg);
fs.mkdirSync(out, { recursive: true });

await G(() => { const g = window.__game; g.teleport(0, 36); const y = g.state().player.yaw; g.input('cameraYaw', y + Math.PI); g.step(1 / 60, 120); });
await shot('idle-front');
await G(() => { const g = window.__game; const y = g.state().player.yaw; g.input('cameraYaw', y + Math.PI / 2 + 0.35); g.step(1 / 60, 90); });
await shot('idle-side');
await G(() => { const g = window.__game; g.input('cameraYaw', 0.9); g.input('moveY', 1); g.step(1 / 60, 50); });
await shot('run');
await G(() => { const g = window.__game; g.input('moveY', null); g.step(1 / 60, 40); const y = g.state().player.yaw; g.input('cameraYaw', y + Math.PI / 2 + 0.35); g.step(1 / 60, 90, false); g.input('attack', true); g.step(1 / 60, 1, false); g.input('attack', false); g.step(1 / 60, 9); });
await shot('attack');
const state = await G(() => { const g = window.__game; const s = g.state(); return { hero: s.assets.hero, sword: s.assets['hero.attachments.sword'], mode: s.heroAnimMode }; });
console.log(JSON.stringify(state));
await browser.close(); srv.close();
