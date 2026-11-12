import { CFG } from './config.js';

// Action-mapped input: keyboard, mouse, touch and the test hook all write the
// same action state. Buttons have a held flag plus a press counter so a tap
// shorter than one sim step is never lost.
export class Input {
  constructor(canvas) {
    this.canvas = canvas;
    this.held = { jump: false, attack: false, interact: false };
    this.presses = { jump: 0, attack: 0, interact: 0 };
    this.keys = new Set();
    this.stick = { x: 0, y: 0 };
    this.test = { moveX: null, moveY: null };
    this.walk = false;
    this.camDX = 0;
    this.camDY = 0;
    this.zoom = 0;
    this.lastCamInput = -99;
    this.yawOverride = null;
    this.handlers = {};
    this.usingTouch = matchMedia('(pointer: coarse)').matches;
    this.bindKeyboard();
    this.bindPointer();
  }

  on(name, fn) { this.handlers[name] = fn; }
  fire(name) { this.handlers[name]?.(); }

  press(name, down) {
    if (down && !this.held[name]) this.presses[name]++;
    this.held[name] = down;
    if (down) this.fire('gesture');
  }

  consume(name) {
    if (this.presses[name] > 0) { this.presses[name]--; return true; }
    return false;
  }

  clearPresses() { for (const k in this.presses) this.presses[k] = 0; }

  get moveX() {
    if (this.test.moveX != null) return this.test.moveX;
    const k = (this.keys.has('KeyD') || this.keys.has('ArrowRight') ? 1 : 0) - (this.keys.has('KeyA') || this.keys.has('ArrowLeft') ? 1 : 0);
    return k || this.stick.x;
  }
  get moveY() {
    if (this.test.moveY != null) return this.test.moveY;
    const k = (this.keys.has('KeyW') || this.keys.has('ArrowUp') ? 1 : 0) - (this.keys.has('KeyS') || this.keys.has('ArrowDown') ? 1 : 0);
    return k || this.stick.y;
  }

  bindKeyboard() {
    const map = { Space: 'jump', KeyJ: 'attack', KeyK: 'attack', KeyE: 'interact', KeyF: 'interact', Enter: 'interact' };
    window.addEventListener('keydown', (e) => {
      if (e.target instanceof HTMLInputElement) return;
      this.setTouchMode(false);
      if (e.code === 'Backquote') { this.fire('debug'); return; }
      if (e.code === 'Escape' || e.code === 'KeyP') { this.fire('pause'); return; }
      if (e.code === 'KeyM') { this.fire('mute'); return; }
      this.fire('anykey');
      if (e.repeat) return;
      if (e.code === 'ShiftLeft' || e.code === 'ShiftRight') this.walk = true;
      if (map[e.code]) { this.press(map[e.code], true); e.preventDefault(); }
      else { this.keys.add(e.code); this.fire('gesture'); }
      if (e.code.startsWith('Arrow')) e.preventDefault();
    });
    window.addEventListener('keyup', (e) => {
      if (e.code === 'ShiftLeft' || e.code === 'ShiftRight') this.walk = false;
      if (map[e.code]) this.press(map[e.code], false);
      this.keys.delete(e.code);
    });
    window.addEventListener('blur', () => { this.keys.clear(); for (const k in this.held) this.held[k] = false; this.stick.x = this.stick.y = 0; });
  }

  setTouchMode(on) {
    if (this.usingTouch === on) return;
    this.usingTouch = on;
    this.fire('device');
  }

  // Mouse: drag to orbit, short click to attack, wheel to zoom.
  // Touch: left half = floating stick, right half = camera drag.
  bindPointer() {
    const c = this.canvas;
    const ptrs = new Map();
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    c.addEventListener('pointerdown', (e) => {
      this.fire('gesture');
      this.fire('anykey');
      const touch = e.pointerType === 'touch' || e.pointerType === 'pen';
      this.setTouchMode(touch);
      c.setPointerCapture?.(e.pointerId);
      const left = e.clientX < window.innerWidth * 0.45;
      const role = touch ? (left && !this.stickId ? 'stick' : 'camera') : 'mouse';
      ptrs.set(e.pointerId, { role, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, moved: 0, t0: performance.now(), button: e.button });
      if (role === 'stick') { this.stickId = e.pointerId; this.fire('stickStart', { x: e.clientX, y: e.clientY }); }
    });
    c.addEventListener('pointermove', (e) => {
      const p = ptrs.get(e.pointerId);
      if (!p) return;
      const dx = e.clientX - p.x, dy = e.clientY - p.y;
      p.x = e.clientX; p.y = e.clientY;
      p.moved += Math.abs(dx) + Math.abs(dy);
      if (p.role === 'stick') {
        const R = 56;
        let sx = (e.clientX - p.x0) / R, sy = -(e.clientY - p.y0) / R;
        const l = Math.hypot(sx, sy);
        if (l > 1) { sx /= l; sy /= l; }
        // Small dead zone, then rescale so the edge is still full speed.
        const dl = Math.hypot(sx, sy);
        const k = dl < 0.12 ? 0 : (dl - 0.12) / 0.88 / dl;
        this.stick.x = sx * k; this.stick.y = sy * k;
        this.fire('stickMove', { x: p.x0 + sx * R, y: p.y0 - sy * R });
      } else if (p.role === 'camera' || (p.role === 'mouse' && p.moved > 5)) {
        const s = p.role === 'camera' ? CFG.camera.touchSensitivity : CFG.camera.dragSensitivity;
        this.camDX += dx * s;
        this.camDY += dy * s;
        this.lastCamInput = performance.now();
      }
    });
    const end = (e) => {
      const p = ptrs.get(e.pointerId);
      if (!p) return;
      ptrs.delete(e.pointerId);
      if (p.role === 'stick') { this.stickId = null; this.stick.x = this.stick.y = 0; this.fire('stickEnd'); }
      if (p.role === 'mouse' && p.button === 0 && p.moved <= 5 && e.type === 'pointerup') {
        this.press('attack', true);
        this.press('attack', false);
      }
    };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
    c.addEventListener('wheel', (e) => { this.zoom += Math.sign(e.deltaY) * 0.6; e.preventDefault(); }, { passive: false });
  }

  // On-screen buttons (touch) call this.
  bindButton(el, action) {
    const down = (e) => { e.preventDefault(); e.stopPropagation(); this.setTouchMode(e.pointerType !== 'mouse'); this.press(action, true); el.classList.add('down'); };
    const up = (e) => { e.preventDefault(); this.press(action, false); el.classList.remove('down'); };
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('pointerleave', up);
  }

  takeCamera() {
    const d = { dx: this.camDX, dy: this.camDY, zoom: this.zoom };
    this.camDX = this.camDY = this.zoom = 0;
    return d;
  }
}
