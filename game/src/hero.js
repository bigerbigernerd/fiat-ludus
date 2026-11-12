import * as THREE from 'three';
import { piece, lumpy, merge, COL, SPH, CYL, BOX } from './assets/placeholders.js';
import { makeToonMaterial, makeOutlineMaterial, addOutlineNormals } from './render/toon.js';
import { CFG } from './config.js';
import { damp, clamp } from './rng.js';

const bodyMat = () => makeToonMaterial({ vertexColors: true, nearFade: false });

function rigMesh(geo) {
  addOutlineNormals(geo);
  const mesh = new THREE.Mesh(geo, bodyMat());
  mesh.castShadow = true;
  const ol = new THREE.Mesh(geo, makeOutlineMaterial(undefined, false));
  ol.name = 'outline';
  mesh.add(ol);
  return mesh;
}

// Primitive courier: cream poncho, red scarf, wooden sword. Built 1.5 m tall,
// facing +Z, with pivots for procedural animation.
function buildPlaceholderRig() {
  const root = new THREE.Group();
  const body = rigMesh(merge([
    piece(new THREE.ConeGeometry(0.44, 0.7, 10, 1, true), COL.cream, { p: [0, 0.72, 0] }),
    piece(new THREE.CylinderGeometry(0.44, 0.45, 0.06, 10), 0xb98a5a, { p: [0, 0.38, 0] }),
    piece(CYL(0.16, 0.2, 8), 0xe9d9b6, { p: [0, 0.75, 0], s: [1, 0.5, 1] }),
    piece(SPH(2), COL.skin, { p: [0, 1.2, 0.01], s: [0.46, 0.44, 0.44] }),
    piece(lumpy(SPH(1), 0.02, 4), COL.hair, { p: [0, 1.29, -0.035], s: [0.5, 0.36, 0.48] }),
    piece(SPH(1), COL.hair, { p: [0.09, 1.33, 0.17], s: [0.2, 0.1, 0.14], r: [0.3, 0, -0.3] }),
    piece(SPH(1), 0x2a1d17, { p: [-0.085, 1.19, 0.21], s: [0.05, 0.075, 0.04] }),
    piece(SPH(1), 0x2a1d17, { p: [0.085, 1.19, 0.21], s: [0.05, 0.075, 0.04] }),
    piece(SPH(1), 0xf2a08a, { p: [-0.13, 1.13, 0.19], s: [0.06, 0.03, 0.03] }),
    piece(SPH(1), 0xf2a08a, { p: [0.13, 1.13, 0.19], s: [0.06, 0.03, 0.03] }),
    piece(new THREE.TorusGeometry(0.16, 0.065, 6, 12), COL.red, { p: [0, 1.0, 0], r: [Math.PI / 2, 0, 0] }),
    piece(BOX, 0x8a5a36, { p: [-0.36, 0.55, 0.05], s: [0.12, 0.2, 0.22] }),
    piece(BOX, 0x6f4a2e, { p: [0, 0.82, 0.0], s: [0.9, 0.04, 0.05], r: [0, 0, -0.75] }),
  ]));
  root.add(body);

  const leg = (x) => {
    const pivot = new THREE.Group();
    pivot.position.set(x, 0.42, 0);
    const m = rigMesh(merge([
      piece(CYL(0.065, 0.06, 6), 0x5a4636, { p: [0, -0.17, 0], s: [1, 0.3, 1] }),
      piece(SPH(1), 0x6e4a30, { p: [0, -0.36, 0.04], s: [0.15, 0.12, 0.22] }),
    ]));
    pivot.add(m);
    root.add(pivot);
    return pivot;
  };
  const legL = leg(-0.12), legR = leg(0.12);

  const armR = new THREE.Group();
  armR.position.set(0.34, 0.88, 0.02);
  const sword = rigMesh(merge([
    piece(SPH(1), COL.skin, { p: [0, -0.2, 0.06], s: [0.12, 0.12, 0.12] }),
    piece(BOX, 0x6f4a2e, { p: [0, -0.2, 0.06], s: [0.05, 0.05, 0.16] }),
    piece(BOX, 0x8b5e3a, { p: [0, -0.2, 0.15], s: [0.2, 0.04, 0.04] }),
    piece(BOX, 0xd9b27c, { p: [0, -0.2, 0.47], s: [0.07, 0.035, 0.6] }),
    piece(new THREE.ConeGeometry(0.035, 0.1, 4), 0xd9b27c, { p: [0, -0.2, 0.82], r: [Math.PI / 2, 0, 0], s: [1, 1, 0.5] }),
  ]));
  armR.add(sword);
  root.add(armR);

  const armL = new THREE.Group();
  armL.position.set(-0.34, 0.88, 0.02);
  armL.add(rigMesh(piece(SPH(1), COL.skin, { p: [0, -0.2, 0.04], s: [0.12, 0.12, 0.12] })));
  root.add(armL);

  const scarf = new THREE.Group();
  scarf.position.set(0.05, 1.0, -0.15);
  scarf.add(rigMesh(merge([
    piece(BOX, COL.red, { p: [0, -0.02, -0.17], s: [0.12, 0.035, 0.34] }),
    piece(BOX, 0xb3372e, { p: [0, -0.02, -0.35], s: [0.12, 0.04, 0.05] }),
  ])));
  root.add(scarf);
  return { root, body, legL, legR, armR, armL, scarf };
}

// Owns how the hero looks and animates. Three modes:
//  'placeholder' - primitive rig, procedural animation
//  'static'      - generated mesh without clips, procedural bob/lean/hop
//  'clips'       - generated rig with AnimationMixer and cross-fades
export class HeroVisual {
  constructor(height = CFG.player.height) {
    this.root = new THREE.Group();
    this.root.name = 'hero';
    this.pivot = new THREE.Group(); // procedural squash/lean lives here
    this.root.add(this.pivot);
    this.rig = buildPlaceholderRig();
    this.rig.root.scale.setScalar(height / 1.5);
    this.pivot.add(this.rig.root);
    this.mode = 'placeholder';
    this.phase = 0;
    this.squash = 0;
    this.squashVel = 0;
    this.lean = 0;
    this.roll = 0;
    this.scarfSwing = 0;
    this.lastYaw = 0;
    console.info('[hero] animation mode: placeholder (procedural)');
  }

  setModel(result, clipMap = {}) {
    if (!result) return;
    this.pivot.remove(this.rig?.root);
    if (this.model) this.pivot.remove(this.model);
    this.model = result.root;
    this.pivot.add(this.model);
    this.mixer = null;
    this.actions = {};
    if (result.mode === 'clips') {
      this.mixer = new THREE.AnimationMixer(this.model);
      this.mixer.addEventListener('finished', (ev) => {
        if (ev.action === this.actions.attack) { ev.action.fadeOut(0.15); this.attackPlaying = false; }
      });
      const find = (want) => {
        if (!want) return null;
        const w = String(want).toLowerCase();
        return result.clips.find((c) => c.name === want)
          || result.clips.find((c) => c.name.toLowerCase() === w)
          || result.clips.find((c) => c.name.toLowerCase().includes(w));
      };
      for (const name of ['idle', 'walk', 'run', 'jump', 'attack']) {
        const clip = find(clipMap[name] ?? name);
        if (clip) this.actions[name] = this.mixer.clipAction(clip);
      }
      // Degrade gracefully when individual clips are missing.
      const a = this.actions;
      this.loco = {
        idle: a.idle || a.walk || a.run || null,
        walk: a.walk || a.run || a.idle || null,
        run: a.run || a.walk || a.idle || null,
      };
      this.runFromWalk = !a.run && !!a.walk;
      if (!this.loco.idle) {
        this.mode = 'static';
        this.mixer = null;
      } else {
        this.mode = 'clips';
        for (const act of new Set(Object.values(this.loco))) { act.play(); act.setEffectiveWeight(0); }
        this.current = this.loco.idle;
        this.current.setEffectiveWeight(1);
        if (a.attack) { a.attack.setLoop(THREE.LoopOnce, 1); a.attack.clampWhenFinished = true; }
        if (a.jump) { a.jump.setLoop(THREE.LoopOnce, 1); a.jump.clampWhenFinished = true; }
      }
    } else {
      this.mode = 'static';
    }
    const have = Object.keys(this.actions).join(', ') || 'none';
    console.info(`[hero] animation mode: ${this.mode}${this.mode === 'clips' ? ' (clips: ' + have + ')' : ' (procedural)'}`);
  }

  // Parent hand-held items to bones of the loaded hero. `loaded` holds the
  // attachments whose files loaded; entries without one still get debug axes
  // at their bone so offsets can be tuned before the art exists.
  attach(entries, loaded, status) {
    for (const a of this.attached || []) a.comp.parent?.remove(a.comp);
    this.attached = [];
    (entries || []).forEach((entry, i) => {
      const id = entry.id || `#${i}`;
      const key = `hero.attachments.${id}`;
      const item = loaded.find((l) => l.id === id);
      if (this.mode === 'placeholder') {
        if (item) status.set(key, 'placeholder', 'hero is the built-in rig, which carries its own sword', { file: entry.file, bone: entry.bone });
        return;
      }
      const bone = findBone(this.model, entry.bone);
      if (!bone) {
        status.set(key, item ? 'failed' : status.entries[key]?.state || 'placeholder', `bone "${entry.bone}" not found in hero; procedural sword arc only`, { file: entry.file, bone: entry.bone });
        return;
      }
      // Undo the bone's accumulated scale so length/offset stay in metres.
      this.model.updateMatrixWorld(true);
      const rel = new THREE.Matrix4().copy(this.model.matrixWorld).invert().multiply(bone.matrixWorld);
      const bs = new THREE.Vector3();
      rel.decompose(new THREE.Vector3(), new THREE.Quaternion(), bs);
      const comp = new THREE.Group();
      comp.name = 'attach:' + id;
      comp.scale.setScalar(1 / (bs.x || 1));
      const frame = new THREE.Group();
      frame.position.fromArray(entry.offset || [0, 0, 0]);
      const r = (entry.rotation || [0, 0, 0]).map((d) => (d * Math.PI) / 180);
      frame.rotation.set(r[0], r[1], r[2]);
      comp.add(frame);
      const boneAxes = axesHelper(0.22);
      const itemAxes = axesHelper(0.1);
      comp.add(boneAxes);
      frame.add(itemAxes);
      const tip = new THREE.Object3D();
      if (item) {
        for (const part of item.proto.parts) {
          addOutlineNormals(part.geometry);
          const mesh = new THREE.Mesh(part.geometry, part.material);
          mesh.castShadow = true;
          mesh.frustumCulled = false;
          const ol = new THREE.Mesh(part.geometry, makeOutlineMaterial(undefined, false));
          ol.frustumCulled = false;
          ol.name = 'outline';
          mesh.add(ol);
          frame.add(mesh);
        }
        tip.position.y = (entry.length ?? 0.75) * (1 - (entry.grip ?? 0.15));
        frame.add(tip);
        const { state: _s, reason: _r, ...info } = status.entries[key] || {};
        status.set(key, 'loaded', '', { ...info, bone: bone.name });
      }
      boneAxes.visible = itemAxes.visible = !!this.showAxes;
      bone.add(comp);
      this.attached.push({ id, bone, comp, frame, tip, axes: [boneAxes, itemAxes], hasModel: !!item });
    });
    this.hasHeldSword = this.attached.some((a) => a.hasModel);
  }

  setAxes(on) {
    this.showAxes = on;
    for (const a of this.attached || []) for (const h of a.axes) h.visible = on;
  }

  fadeTo(action, dur = 0.2) {
    if (!action || action === this.current) return;
    action.reset().setEffectiveWeight(1).play();
    if (this.current) this.current.crossFadeTo(action, dur, false);
    this.current = action;
  }

  onEvent(type) {
    if (type === 'land') { this.squashVel = -5.5; }
    if (type === 'jump') { this.squashVel = 6; }
    if (this.mode === 'clips') {
      const a = this.actions;
      // Long generated clips are sped up to fit the game's timing; the hit
      // window itself always comes from CFG.player.attack, never the clip.
      if (type === 'attack' && a.attack) {
        a.attack.timeScale = Math.max(1, a.attack.getClip().duration / CFG.player.attack.clipTime);
        a.attack.reset().setEffectiveWeight(1).fadeIn(0.05).play(); this.attackPlaying = true;
      }
      if (type === 'jump' && a.jump) {
        a.jump.timeScale = Math.max(1, a.jump.getClip().duration / CFG.player.jumpClipTime);
        a.jump.reset().setEffectiveWeight(1).fadeIn(0.08).play(); this.jumpPlaying = true;
      }
      if (type === 'land' && a.jump && this.jumpPlaying) { a.jump.fadeOut(0.12); this.jumpPlaying = false; }
    }
  }

  update(dt, pl) {
    const P = CFG.player;
    const speed = Math.hypot(pl.vel.x, pl.vel.z);
    const sn = clamp(speed / P.runSpeed, 0, 1);
    this.root.position.copy(pl.pos);
    this.root.rotation.y = pl.yaw;
    // Spring for squash and stretch.
    this.squashVel += (-this.squash * 180 - this.squashVel * 14) * dt;
    this.squash += this.squashVel * dt;
    const sq = clamp(this.squash * 0.06, -0.22, 0.22);
    const airStretch = pl.grounded ? 0 : clamp(pl.vel.y * 0.012, -0.08, 0.1);
    const sy = 1 + sq + airStretch;
    this.pivot.scale.set(1 / Math.sqrt(sy), sy, 1 / Math.sqrt(sy));
    // Lean into movement and into turns.
    let dyaw = pl.yaw - this.lastYaw;
    dyaw = Math.atan2(Math.sin(dyaw), Math.cos(dyaw));
    this.lastYaw = pl.yaw;
    this.lean = damp(this.lean, sn * 0.16, 10, dt);
    this.roll = damp(this.roll, clamp(-dyaw / Math.max(dt, 1e-4) * 0.02 * sn, -0.25, 0.25), 8, dt);
    this.pivot.rotation.set(this.lean, 0, this.roll);
    this.root.visible = !(pl.invuln > 0 && Math.floor(pl.invuln * 14) % 2 === 0);

    const atk = pl.attackT >= 0 ? pl.attackT / P.attack.duration : -1;
    if (this.mode === 'placeholder') this.animateRig(dt, pl, speed, sn, atk);
    else if (this.mode === 'static') this.animateStatic(dt, pl, speed, sn, atk);
    else this.animateClips(dt, pl, speed, atk);
  }

  animateRig(dt, pl, speed, sn, atk) {
    const r = this.rig;
    this.phase += speed * dt * 2.4;
    const swing = Math.sin(this.phase) * 0.9 * sn;
    if (pl.grounded) {
      r.legL.rotation.x = swing;
      r.legR.rotation.x = -swing;
      r.body.position.y = Math.abs(Math.cos(this.phase)) * 0.06 * sn + Math.sin(performanceSafeTime(pl) * 2.2) * 0.008 * (1 - sn);
    } else {
      r.legL.rotation.x = damp(r.legL.rotation.x, -0.7, 12, dt);
      r.legR.rotation.x = damp(r.legR.rotation.x, 0.3, 12, dt);
      r.body.position.y = 0;
    }
    r.armL.rotation.x = -swing * 0.6;
    if (atk >= 0) {
      // Wind-up then a fast horizontal arc from right to left.
      const t = atk;
      const wind = clamp(t / 0.18, 0, 1), strike = clamp((t - 0.15) / 0.4, 0, 1);
      const e = 1 - Math.pow(1 - strike, 3);
      r.armR.rotation.set(-0.3 - 0.9 * wind + 0.9 * e, 1.3 * wind - 2.6 * e, 0);
      r.body.rotation.y = 0.35 * wind - 0.7 * e;
    } else {
      r.armR.rotation.x = damp(r.armR.rotation.x, 0.25 + swing * 0.4, 12, dt);
      r.armR.rotation.y = damp(r.armR.rotation.y, 0.25, 12, dt);
      r.body.rotation.y = damp(r.body.rotation.y, 0, 12, dt);
    }
    this.scarfSwing = damp(this.scarfSwing, -0.2 - sn * 0.9 + (pl.grounded ? 0 : -0.4) + Math.sin(this.phase * 1.3) * 0.15 * sn, 8, dt);
    r.scarf.rotation.x = this.scarfSwing;
  }

  animateStatic(dt, pl, speed, sn, atk) {
    this.phase += speed * dt * 2.4;
    const m = this.model;
    m.position.y = pl.grounded ? Math.abs(Math.sin(this.phase)) * 0.07 * sn : 0;
    m.rotation.z = Math.sin(this.phase) * 0.06 * sn;
    m.rotation.y = atk >= 0 ? Math.sin(atk * Math.PI) * -0.6 : damp(m.rotation.y, 0, 12, dt);
  }

  animateClips(dt, pl, speed, atk) {
    const P = CFG.player;
    let target = this.loco.idle;
    let ts = 1;
    if (speed > 0.35) {
      if (speed < (P.walkSpeed + P.runSpeed) / 2) { target = this.loco.walk; ts = clamp(speed / P.walkSpeed, 0.6, 1.4); }
      else { target = this.loco.run; ts = this.runFromWalk ? clamp(speed / P.walkSpeed, 1.2, 2.2) : clamp(speed / P.runSpeed, 0.7, 1.3); }
    }
    this.fadeTo(target, 0.2);
    if (this.current) this.current.timeScale = ts;
    this.mixer.update(dt);
  }
}

// Bone lookup: exact, case-insensitive, then sanitised (GLTFLoader strips
// '.', ':', '/', '[', ']' from node names, so "DEF-hand.R" arrives as "DEF-handR").
export function findBone(root, name) {
  if (!name || !root) return null;
  const all = [];
  root.traverse((o) => { if (o.name) all.push(o); });
  all.sort((a, b) => (b.isBone ? 1 : 0) - (a.isBone ? 1 : 0));
  const san = (n) => THREE.PropertyBinding.sanitizeNodeName(n).toLowerCase();
  const loose = (n) => n.toLowerCase().replace(/[^a-z0-9]/g, '');
  const lower = name.toLowerCase();
  return all.find((o) => o.name === name)
    || all.find((o) => o.name.toLowerCase() === lower)
    || all.find((o) => san(o.name) === san(name))
    || all.find((o) => loose(o.name) === loose(name))
    || null;
}

function axesHelper(size) {
  const h = new THREE.AxesHelper(size);
  h.material.depthTest = false;
  h.material.transparent = true;
  h.renderOrder = 999;
  h.name = 'debug-axes';
  return h;
}

// Idle breathing is purely visual; tie it to sim time carried on the player.
function performanceSafeTime(pl) { return pl.time || 0; }
