// WebAudio: synthesised placeholder effects plus optional looping music from
// the manifest (field + combat layers cross-faded). The context is created on
// the first user gesture only.
export class Audio {
  constructor() {
    this.ctx = null;
    this.muted = false;
    try { this.muted = localStorage.getItem('lm-muted') === '1'; } catch { /* storage blocked */ }
    this.music = {};
    this.layers = {};
    this.combat = 0;
  }

  start() {
    if (this.ctx) { if (this.ctx.state === 'suspended') this.ctx.resume(); return; }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    this.ctx = new AC();
    this.master = this.ctx.createGain();
    this.master.gain.value = this.muted ? 0 : 0.8;
    this.master.connect(this.ctx.destination);
    this.sfx = this.ctx.createGain();
    this.sfx.gain.value = 0.9;
    this.sfx.connect(this.master);
    this.musicBus = this.ctx.createGain();
    this.musicBus.gain.value = 0.55;
    this.musicBus.connect(this.master);
    const len = this.ctx.sampleRate;
    this.noise = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
    const d = this.noise.getChannelData(0);
    let seed = 1234567;
    for (let i = 0; i < len; i++) { seed = (seed * 16807) % 2147483647; d[i] = (seed / 2147483647) * 2 - 1; }
    this.startMusic();
  }

  setMusic(buffers) { this.music = buffers || {}; if (this.ctx) this.startMusic(); }

  startMusic() {
    for (const [name, buf] of Object.entries(this.music)) {
      if (this.layers[name] || !buf) continue;
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.loop = true;
      const g = this.ctx.createGain();
      g.gain.value = name === 'field' ? 1 : 0;
      src.connect(g).connect(this.musicBus);
      src.start();
      this.layers[name] = { src, gain: g };
    }
  }

  // 0..1: how much the combat layer replaces the field layer.
  setCombat(v) {
    if (!this.ctx || Math.abs(v - this.combat) < 0.01) return;
    this.combat = v;
    const t = this.ctx.currentTime;
    const f = this.layers.field, c = this.layers.combat;
    if (c) {
      c.gain.gain.setTargetAtTime(v, t, 0.6);
      if (f) f.gain.gain.setTargetAtTime(1 - v * 0.7, t, 0.6);
    }
  }

  toggleMute() {
    this.muted = !this.muted;
    try { localStorage.setItem('lm-muted', this.muted ? '1' : '0'); } catch { /* ignore */ }
    if (this.master) this.master.gain.setTargetAtTime(this.muted ? 0 : 0.8, this.ctx.currentTime, 0.05);
    return this.muted;
  }

  // --- building blocks ---
  tone(freq, dur, { type = 'sine', vol = 0.3, attack = 0.005, slide = 0, delay = 0, detune = 0 } = {}) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime + delay;
    const o = this.ctx.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    o.detune.value = detune;
    if (slide) o.frequency.exponentialRampToValueAtTime(Math.max(20, freq * slide), t + dur);
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(vol, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(this.sfx);
    o.start(t);
    o.stop(t + dur + 0.05);
  }

  noiseBurst(dur, { freq = 1200, q = 1, vol = 0.3, type = 'bandpass', slide = 0, delay = 0 } = {}) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime + delay;
    const src = this.ctx.createBufferSource();
    src.buffer = this.noise;
    const f = this.ctx.createBiquadFilter();
    f.type = type;
    f.frequency.setValueAtTime(freq, t);
    if (slide) f.frequency.exponentialRampToValueAtTime(Math.max(40, freq * slide), t + dur);
    f.Q.value = q;
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(vol, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(f).connect(g).connect(this.sfx);
    src.start(t, Math.random() * 0.5);
    src.stop(t + dur + 0.05);
  }

  // --- game sounds ---
  play(name, opt = {}) {
    if (!this.ctx || this.muted) return;
    const r = () => 0.94 + Math.random() * 0.12;
    switch (name) {
      case 'step': this.noiseBurst(0.07, { freq: 700 * r(), q: 0.8, vol: 0.09, type: 'lowpass' }); break;
      case 'jump': this.tone(330 * r(), 0.16, { type: 'triangle', vol: 0.12, slide: 1.8 }); break;
      case 'land': this.noiseBurst(0.12, { freq: 400, q: 0.7, vol: 0.18, type: 'lowpass' }); break;
      case 'swing': this.noiseBurst(0.16, { freq: 900 * r(), q: 2.5, vol: 0.22, slide: 3 }); break;
      case 'hit':
        this.tone(140, 0.14, { type: 'square', vol: 0.12, slide: 0.5 });
        this.noiseBurst(0.1, { freq: 2200, q: 1.2, vol: 0.25 });
        this.tone(880 * r(), 0.08, { type: 'triangle', vol: 0.08, delay: 0.01 });
        break;
      case 'defeat':
        this.noiseBurst(0.25, { freq: 500, q: 0.8, vol: 0.25, slide: 0.4, type: 'lowpass' });
        [523, 659, 784].forEach((f, i) => this.tone(f, 0.18, { type: 'triangle', vol: 0.1, delay: 0.05 + i * 0.06 }));
        break;
      case 'telegraph': this.tone(220, 0.35, { type: 'sawtooth', vol: 0.05, slide: 1.6 }); break;
      case 'lunge': this.noiseBurst(0.18, { freq: 600, q: 1.5, vol: 0.12, slide: 2 }); break;
      case 'hop': this.tone(180 * r(), 0.07, { type: 'sine', vol: 0.05, slide: 0.6 }); break;
      case 'hurt':
        this.tone(220, 0.3, { type: 'sawtooth', vol: 0.12, slide: 0.5 });
        this.tone(233, 0.3, { type: 'sawtooth', vol: 0.08, slide: 0.5 });
        break;
      case 'seed': {
        const n = opt.streak || 0;
        this.tone(1046 * Math.pow(1.0595, Math.min(n, 12)), 0.18, { type: 'sine', vol: 0.12 });
        this.tone(2093 * Math.pow(1.0595, Math.min(n, 12)), 0.1, { type: 'sine', vol: 0.04, delay: 0.02 });
        break;
      }
      case 'heal': [784, 988, 1175, 1568].forEach((f, i) => this.tone(f, 0.3, { vol: 0.08, delay: i * 0.07 })); break;
      case 'lanternStart': this.noiseBurst(1.0, { freq: 300, q: 0.6, vol: 0.12, slide: 6 }); break;
      case 'lantern':
        // Wind-chime chord with a long tail.
        [523.25, 659.25, 783.99, 1046.5, 1318.5].forEach((f, i) => {
          this.tone(f, 2.6, { vol: 0.09, delay: i * 0.09, attack: 0.01 });
          this.tone(f * 2.01, 1.2, { vol: 0.025, delay: i * 0.09 });
        });
        break;
      case 'gate':
        this.noiseBurst(3.0, { freq: 120, q: 0.5, vol: 0.35, type: 'lowpass' });
        [261.6, 392, 523.3].forEach((f, i) => this.tone(f, 3.2, { type: 'triangle', vol: 0.07, delay: 0.4 + i * 0.25, attack: 0.3 }));
        break;
      case 'win': [523, 659, 784, 1046, 784, 1046, 1318].forEach((f, i) => this.tone(f, 0.5, { type: 'triangle', vol: 0.1, delay: i * 0.12 })); break;
      case 'chest': [392, 523, 659, 784].forEach((f, i) => this.tone(f, 0.25, { type: 'triangle', vol: 0.09, delay: i * 0.05 })); break;
      case 'ui': this.tone(660, 0.07, { type: 'triangle', vol: 0.08 }); break;
      default: break;
    }
  }
}
