import { chromium } from 'playwright-core';
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
const dist=path.join(path.dirname(new URL(import.meta.url).pathname),'..','dist');
const MIME={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json'};
const srv=http.createServer((q,r)=>{let f=path.join(dist,new URL(q.url,'http://x').pathname);if(f.endsWith('/'))f+='index.html';if(!fs.existsSync(f)||fs.statSync(f).isDirectory()){r.writeHead(404);r.end();return;}r.writeHead(200,{'content-type':MIME[path.extname(f)]||'application/octet-stream'});fs.createReadStream(f).pipe(r);});
await new Promise(r=>srv.listen(0,r));
const b=await chromium.launch({args:['--use-angle=vulkan','--enable-features=Vulkan','--ignore-gpu-blocklist','--enable-gpu']});
const pg=await b.newPage({viewport:{width:1280,height:720}});
pg.on('console',m=>{if(m.type()==='error'||m.type()==='warning')console.log('console',m.type(),m.text())});
await pg.goto(`http://127.0.0.1:${srv.address().port}/?test=1`);
await pg.evaluate(()=>window.__game.ready);
await pg.evaluate(()=>window.__game.start());
const spots=JSON.parse(process.argv[2]||'[[0,40,0],[-30,13,0],[8,-11,0],[31,5,0],[0,-34,0],[0,0,0],[0,0,1.57]]');
for(const [x,z,yaw] of spots){
  const r=await pg.evaluate(([x,z,yaw])=>{window.__game.input('cameraYaw',yaw);window.__game.teleport(x,z);window.__game.step(1/60,30);return window.__game.breakdown();},[x,z,yaw]);
  console.log(x,z,yaw,JSON.stringify(r.total),r.parts.map(p=>`${p.name}:${p.drawCalls}/${(p.triangles/1000).toFixed(1)}k`).join(' '));
}
if(process.argv[3]) await pg.screenshot({path:process.argv[3]});
await b.close(); srv.close();
