import { CFG } from './config.js';

const ICON = {
  seed: '<svg viewBox="0 0 24 24"><path d="M12 2c4 5 6 9 6 12a6 6 0 0 1-12 0c0-3 2-7 6-12z" fill="#f2c23b" stroke="#2f2620" stroke-width="2"/><path d="M10 12c0 2 1 3 2 3.5" stroke="#fff6c0" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
  lantern: '<svg viewBox="0 0 24 24"><path d="M4 8h16l-3-4H7z" fill="#b9b1a3" stroke="#2f2620" stroke-width="1.8" stroke-linejoin="round"/><rect class="lamp" x="7" y="9" width="10" height="7" rx="1" stroke="#2f2620" stroke-width="1.8"/><path d="M9 16v4h6v-4M7 21h10" stroke="#2f2620" stroke-width="1.8" fill="#b9b1a3"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor"/></svg>',
  sound: '<svg viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z" fill="currentColor"/><path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
  muted: '<svg viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z" fill="currentColor"/><path d="M16 9l5 6M21 9l-5 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  sword: '<svg viewBox="0 0 24 24"><path d="M19 3l2 0 0 2-10 10-2-2z" fill="#e4c08a" stroke="#2f2620" stroke-width="1.8" stroke-linejoin="round"/><path d="M6 13l5 5M8 16l-4 4" stroke="#2f2620" stroke-width="2.4" stroke-linecap="round"/></svg>',
  jump: '<svg viewBox="0 0 24 24"><path d="M12 4l7 8h-4v7H9v-7H5z" fill="#cfe7a8" stroke="#2f2620" stroke-width="1.8" stroke-linejoin="round"/></svg>',
  hand: '<svg viewBox="0 0 24 24"><path d="M12 3c2 3 4 5 4 8a4 4 0 0 1-8 0c0-1.5.7-2.6 1.5-3.5.2 1.2.8 2 1.5 2.3C11 8 11 5.5 12 3z" fill="#ffc35a" stroke="#2f2620" stroke-width="1.8" stroke-linejoin="round"/><path d="M7 17h10v4H7z" fill="#b9b1a3" stroke="#2f2620" stroke-width="1.8"/></svg>',
};

function flowerSVG() {
  let petals = '';
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * 360;
    petals += `<g transform="rotate(${a} 50 50)"><ellipse class="petal" data-i="${i}" cx="50" cy="27" rx="13" ry="20"/></g>`;
  }
  return `<svg class="flower" viewBox="0 0 100 100" aria-label="health">${petals}<circle class="core" cx="50" cy="50" r="13"/></svg>`;
}

const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };

// DOM HUD and overlays. Updated from game state each frame, but DOM writes
// only happen when a value actually changes.
export class HUD {
  constructor(root, overlays, input, audio) {
    this.root = root;
    this.overlays = overlays;
    this.input = input;
    this.audio = audio;
    this.prev = {};
    root.innerHTML = '';
    this.tl = el(`<div class="hud-tl">${flowerSVG()}<div class="chip" data-control="seeds">${ICON.seed}<span class="n">0</span><span class="seedbar"><i></i></span></div></div>`);
    this.tc = el(`<div class="hud-tc"><div class="lanterns">${'<span>' + ICON.lantern + '</span>'}${'<span>' + ICON.lantern + '</span>'}${'<span>' + ICON.lantern + '</span>'}</div><div class="objective">Relight the three wind-lanterns</div></div>`);
    this.tr = el(`<div class="hud-tr"><button class="iconbtn" data-control="mute" aria-label="Mute">${ICON.sound}</button><button class="iconbtn" data-control="pause" aria-label="Pause">${ICON.pause}</button></div>`);
    this.prompt = el('<div class="prompt off" data-control="prompt"></div>');
    this.toast = el('<div class="toast"></div>');
    this.touch = el(`<div id="touch">
      <div class="stickhint" data-control="stick">MOVE</div>
      <div class="stickbase hidden"></div><div class="stickknob hidden"></div>
      <div class="tbtns">
        <button class="tbtn interact dim" data-control="interact" aria-label="Interact">${ICON.hand}</button>
        <button class="tbtn jump" data-control="jump" aria-label="Jump">${ICON.jump}</button>
        <button class="tbtn attack" data-control="attack" aria-label="Attack">${ICON.sword}</button>
      </div></div>`);
    root.append(this.tl, this.tc, this.tr, this.prompt, this.toast, this.touch);
    this.petals = [...this.tl.querySelectorAll('.petal')];
    this.flower = this.tl.querySelector('.flower');
    this.seedChip = this.tl.querySelector('.chip');
    this.seedN = this.tl.querySelector('.n');
    this.seedBar = this.tl.querySelector('.seedbar i');
    this.lanternEls = [...this.tc.querySelectorAll('.lanterns span')];
    this.objective = this.tc.querySelector('.objective');
    this.muteBtn = this.tr.querySelector('[data-control=mute]');
    this.pauseBtn = this.tr.querySelector('[data-control=pause]');
    this.stickBase = this.touch.querySelector('.stickbase');
    this.stickKnob = this.touch.querySelector('.stickknob');
    this.stickHint = this.touch.querySelector('.stickhint');
    for (const a of ['attack', 'jump', 'interact']) input.bindButton(this.touch.querySelector(`[data-control=${a}]`), a);
    this.interactBtn = this.touch.querySelector('[data-control=interact]');
    input.on('stickStart', (p) => { this.stickBase.classList.remove('hidden'); this.stickKnob.classList.remove('hidden'); this.stickHint.classList.add('hidden'); this.place(this.stickBase, p); this.place(this.stickKnob, p); });
    input.on('stickMove', (p) => this.place(this.stickKnob, p));
    input.on('stickEnd', () => { this.stickBase.classList.add('hidden'); this.stickKnob.classList.add('hidden'); this.stickHint.classList.remove('hidden'); });
    this.setMuted(audio.muted);
    this.setTouch(input.usingTouch);
  }

  place(node, p) { node.style.left = p.x + 'px'; node.style.top = p.y + 'px'; }

  setTouch(on) {
    document.body.classList.toggle('touch', on);
    this.touch.classList.toggle('hidden', !on);
    this.touchOn = on;
  }

  setMuted(m) { this.muteBtn.innerHTML = m ? ICON.muted : ICON.sound; this.muteBtn.setAttribute('aria-label', m ? 'Unmute' : 'Mute'); }

  show(on) { this.root.classList.toggle('hidden', !on); }

  showToast(text, ms = 2200) {
    this.toast.textContent = text;
    this.toast.classList.add('show');
    clearTimeout(this.toastT);
    this.toastT = setTimeout(() => this.toast.classList.remove('show'), ms);
  }

  update(s) {
    const p = this.prev;
    if (p.health !== s.health) {
      this.petals.forEach((el, i) => {
        const lost = i >= s.health;
        if (el.classList.contains('lost') && !lost) { el.classList.add('pop'); setTimeout(() => el.classList.remove('pop'), 500); }
        el.classList.toggle('lost', lost);
      });
      if (p.health != null && s.health < p.health) { this.flower.classList.remove('hurt'); void this.flower.getBoundingClientRect(); this.flower.classList.add('hurt'); }
      p.health = s.health;
    }
    if (p.seeds !== s.seeds) {
      this.seedN.textContent = s.seeds;
      if (p.seeds != null) { this.seedChip.classList.remove('bump'); void this.seedChip.offsetWidth; this.seedChip.classList.add('bump'); }
      p.seeds = s.seeds;
    }
    const bar = Math.min(1, s.seedProgress / CFG.player.seedsPerPetal);
    if (p.bar !== bar) { this.seedBar.style.width = bar * 100 + '%'; p.bar = bar; }
    const litKey = s.lit.join(',');
    if (p.lit !== litKey) { this.lanternEls.forEach((e, i) => e.classList.toggle('on', !!s.lit[i])); p.lit = litKey; }
    if (p.objective !== s.objective) { this.objective.textContent = s.objective; p.objective = s.objective; }
    const promptKey = s.prompt ? s.prompt.kind + s.prompt.text + this.touchOn : '';
    if (p.prompt !== promptKey) {
      p.prompt = promptKey;
      if (!s.prompt) this.prompt.classList.add('off');
      else {
        const key = s.prompt.kind === 'guarded' ? '' : this.touchOn ? '' : '<kbd>E</kbd>';
        this.prompt.innerHTML = key + s.prompt.text;
        this.prompt.classList.toggle('warn', s.prompt.kind === 'guarded');
        this.prompt.classList.remove('off');
      }
      const ready = s.prompt && s.prompt.kind !== 'guarded';
      this.interactBtn.classList.toggle('dim', !ready);
      this.interactBtn.classList.toggle('ready', !!ready);
    }
  }

  // ----- overlays -----
  clearOverlay() { this.overlays.innerHTML = ''; }

  controlsHTML(touch) {
    return touch
      ? `<div class="controls"><b>Left thumb</b><span>move</span><b>Right drag</b><span>look around</span><b>Sword</b><span>attack</span><b>Arrow</b><span>jump</span><b>Flame</b><span>light / open</span></div>`
      : `<div class="controls"><b>WASD / arrows</b><span>run (Shift walks)</span><b>Drag mouse</b><span>look around</span><b>Click / J</b><span>sword</span><b>Space</b><span>jump</span><b>E</b><span>light lantern / open</span><b>Esc</b><span>pause</span></div>`;
  }

  showTitle(touch, loading) {
    this.overlays.innerHTML = `<div class="overlay title" data-screen="title"><div class="card">
      <h1>Lantern <span>Meadow</span></h1>
      <p>Pim the courier lands on a quiet island. The three wind-lanterns have gone dark and the old north gate is shut. Relight them.</p>
      ${this.controlsHTML(touch)}
      <div class="start" data-control="start">${touch ? 'Tap to start' : 'Click or press any key to start'}</div>
      <div class="loading">${loading || ''}</div>
    </div></div>`;
  }

  setLoading(text) { const l = this.overlays.querySelector('.loading'); if (l) l.textContent = text; }

  showPause(touch, onResume, onRestart) {
    this.overlays.innerHTML = `<div class="overlay" data-screen="pause"><div class="card">
      <h2>Paused</h2>${this.controlsHTML(touch)}
      <button class="btn" data-control="resume">Resume</button><button class="btn ghost" data-control="restart">Restart</button>
    </div></div>`;
    this.overlays.querySelector('[data-control=resume]').addEventListener('click', onResume);
    this.overlays.querySelector('[data-control=restart]').addEventListener('click', onRestart);
  }

  showWin(stats, onAgain) {
    const t = stats.time;
    const mm = Math.floor(t / 60), ss = Math.floor(t % 60).toString().padStart(2, '0');
    this.overlays.innerHTML = `<div class="overlay" data-screen="win"><div class="card">
      <h2>The north gate is open</h2>
      <p>Wind sings through three bright lanterns. Pim's delivery can go on.</p>
      <div class="stats"><div><b>${mm}:${ss}</b><span>time</span></div><div><b>${stats.sunseeds}</b><span>sunseeds</span></div><div><b>${stats.hits}</b><span>hits taken</span></div></div>
      <button class="btn" data-control="again">Play again</button>
    </div></div>`;
    this.overlays.querySelector('[data-control=again]').addEventListener('click', onAgain);
  }
}
