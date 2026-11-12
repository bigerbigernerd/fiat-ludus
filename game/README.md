# Lantern Meadow (game shell)

A small cel-shaded third-person adventure in three.js. Pim, a courier in a cream
poncho and red scarf, lands on a meadow island where three stone wind-lanterns
have gone dark. Clear the mosslings guarding each lantern, light all three, and
walk through the north gate. A run takes 2 to 5 minutes.

The game is fully playable with built-in placeholder shapes. Every external asset
(models, sky, music) is listed in `public/assets/manifest.json`; when a listed
file exists and loads, it replaces its placeholder automatically. The game never
waits for assets.

## Run

```bash
npm install          # three, vite, playwright-core (uses the browsers in ~/.cache/ms-playwright)
npm run dev          # http://localhost:5173  (add ?debug=1 for the debug overlay)
npm run build        # static site in dist/, relative paths, host under any sub-path
npm run check        # build + headless verification (see below)
npm run measure      # build + what the running game costs: fps, CPU, memory, GPU (Linux + NVIDIA)
```

URL flags: `?debug=1` debug overlay (also toggled with the backquote key; it
also draws the hand-attachment axes), `?axes=1` attachment axes only,
`?test=1` deterministic test mode, `?quality=low|high` force mobile/desktop quality.

Controls: WASD/arrows run (Shift walks), mouse drag or right-side touch drag
orbits the camera, wheel zooms, click/J attacks, Space jumps, E lights a
lantern or opens a chest, Esc/P pauses, M mutes. On touch screens: floating
stick on the left half, sword/jump/flame buttons on the right.

## Layout

```
index.html               page shell (canvas, HUD root, overlays, debug panel)
public/level.json        level data: terrain, lanterns, gate, enemies, props, scatter rules
public/assets/manifest.json   asset contract (files may or may not exist)
public/assets/{models,textures,audio}/   where generated files go
src/config.js            ALL gameplay/render tuning numbers
src/main.js              boot, fixed-step loop, event wiring (feel, audio, HUD), flow
src/assets/loader.js     manifest, GLB load, normalise, re-shade, status
src/assets/placeholders.js  primitive stand-ins for every model id
src/render/toon.js       toon material (stepped ramp + rim), outline material
src/world/terrain.js     heightfield island, walkability, data texture
src/world/grass.js       instanced wind grass (one draw call)
src/world/sky.js, water.js   painterly sky dome, toon water with shore foam
src/world/instanced.js   InstancedModel: prototype parts -> InstancedMeshes + outlines
src/world/props.js       prop placement/scatter, 2x2 chunking, colliders
src/player.js, hero.js   hero simulation / hero visuals and animation modes
src/enemies.js           mosslings (procedural hop, telegraph, lunge)
src/objectives.js        lanterns, gate, chests
src/pickups.js, fx.js    sunseeds / particles, slash arc, screen shake
src/input.js, camera.js  action-mapped input (keys, mouse, touch, test) / orbit camera
src/audio.js, hud.js     WebAudio synth + music layers / DOM HUD and overlays
src/testhook.js          window.__game
scripts/check.mjs        headless verification; scripts/fixtures.mjs builds test GLBs
```

## Coordinates and units

Metres, Y up. The island is centred on the origin; north is -Z, the gate is at the
north end, the hero spawns in the south. Water level is y = 0. Every model's front
is +Z after loading (glTF convention). Prop rotations in `level.json` are radians
about Y.

## Asset manifest (`public/assets/manifest.json`)

```json
{
  "version": 1,
  "hero":    { "file": "models/hero.glb", "height": 1.5, "facing": "+z",
               "clips": { "idle": "idle", "walk": "walk", "run": "run", "jump": "jump", "attack": "attack" },
               "attachments": [ { "id": "sword", "file": "models/sword.glb", "bone": "DEF-hand.R",
                                  "length": 0.75, "grip": 0.18, "rotation": [90, 0, 0], "offset": [0, 0.08, 0.02] } ] },
  "enemies": { "mossling": { "file": "models/mossling.glb", "height": 0.9, "facing": "+z" } },
  "props":   { "tree_broadleaf": { "file": "models/tree_broadleaf.glb", "height": 6.0,
                                   "collider": { "type": "cylinder", "radius": 0.45 } },
               "gate": { "file": "models/gate.glb", "height": 5.5, "collider": [
                          { "type": "box", "x": -2.9, "z": 0, "width": 1.3, "depth": 1.3 },
                          { "type": "box", "x":  2.9, "z": 0, "width": 1.3, "depth": 1.3 } ] } },
  "sky":     { "file": "textures/sky.jpg", "type": "equirect" },
  "music":   { "field": "audio/field.ogg", "combat": ["audio/combat.ogg", "audio/combat.m4a"] }
}
```

Fields:

- `file`: path relative to `public/assets/`. `.glb` or `.gltf`; DRACO and meshopt
  compression are supported. Missing file means placeholder.
- `height` (metres): the model is uniformly scaled so its bounding-box height is
  exactly this. Its own scale and origin are ignored.
- `triangles` (optional): how many triangles one copy may have. The game does not
  read it; `./forge install` does, and decimates a heavier model to it. Things that
  are drawn many times get a small number here, one-off set pieces a large one.
- `yaw` (optional, degrees): `./forge install` turns the model by this much about the
  vertical axis before writing it. Without it, flat things (doors, fences, gates)
  are turned 90 degrees when their footprint contradicts their box collider.
- `facing`: which axis the model's front points along in the file:
  `+z` (default, glTF convention), `-z`, `+x`, `-x`. It is rotated so the front
  becomes game forward (+Z).
- `collider` (props only): `{type:"cylinder", radius}`, `{type:"box", width, depth}`,
  `{type:"none"}`, or a list of those. Optional `x`, `z` offsets are in the model's
  local frame (metres at scale 1) and rotate/scale with the placed instance.
  Colliders never come from mesh geometry.
- `clips` (hero only): game action -> clip name in the GLB. Matching is exact, then
  case-insensitive, then "contains" (so `idle` finds `Idle_Loop`).
- `attachments` (hero only, optional): hand-held items parented to a bone, see below.
- `sky.type`: only `equirect` (2:1 panorama, sRGB JPG/PNG/WebP).
- `music.<layer>`: a path or a list of candidates; the first that decodes wins.
  `field` loops always; `combat` fades in while mosslings chase the hero.
  Ship `.ogg` plus `.m4a` if older iPhones matter.

Required prop ids used by the level: `tree_broadleaf`, `tree_pine`, `rock_large`,
`rock_small`, `bush`, `flowers`, `ruin_pillar`, `ruin_arch`, `fence`, `signpost`,
`crate`, `chest`, `lantern`, `gate`, `gate_door`. The procedural grass, water, sky
dome, sunseeds, lantern flames and pinwheels are not manifest entries.

### What the loader does to every model

1. Fetch the file. HTTP error or an HTML answer: status `placeholder`.
   Parse or normalise error: status `failed` (placeholder used, reason recorded).
2. Bake all mesh transforms, compute the bounding box, scale to `height`, put the
   base centre at the origin, rotate by `facing`.
3. Re-shade: keep only the base-colour texture (`map`), the material colour and
   vertex colours. Metallic/roughness/normal/emissive maps are dropped. Textures
   are sRGB, mipmapped, anisotropic (up to 8x).
4. Static models: meshes sharing a texture are merged, so a model costs one draw
   call per texture plus one per outline. Props that repeat are instanced; large
   scattered sets are split into 2x2 spatial chunks for culling. If
   `triangles x 2 (outline) x instances` exceeds `CFG.render.maxTrianglesPerPropSet`
   (110k, 50k on phone quality) the scattered instances are thinned (hand-placed ones
   stay) and a warning is logged. A material the glTF marks `doubleSided` stays
   double-sided, which open generated meshes (leaf sheets) need.
5. Hero: keeps its scene graph. With clips: AnimationMixer, idle/walk/run chosen
   by speed with 0.2 s cross-fades, `jump` and `attack` as one-shots. Missing
   `run` uses a faster `walk`; missing `walk` uses `run`; no locomotion clips at all
   means procedural. No clips (a static statue): procedural bob, lean, hop and
   sword-arc effect. The mode is logged (`[hero] animation mode: ...`) and shown
   in the status.

### Hero attachments (sword in hand)

The generated hero has empty hands; the sword is its own static GLB.

| field | meaning |
| --- | --- |
| `id` | status key `hero.attachments.<id>` |
| `file` | static GLB |
| `bone` | bone (or any node) to parent to, e.g. `DEF-hand.R` |
| `length` | metres along the item's longest axis after scaling |
| `grip` | 0..1, where the hand holds it, measured from the handle end |
| `handle` | `"min"` (default) or `"max"`: which end of the longest axis is the handle |
| `rotation` | degrees, XYZ Euler, in the bone's local frame |
| `offset` | metres, in the bone's local frame (the bone's own scale is cancelled) |

Normalisation: the longest bounding-box axis is the length axis, the handle is
at its minimum end (so model the sword **blade along +Y, handle at the bottom**,
or set `handle: "max"`); it is scaled to `length`, the grip point (centred on the
other two axes) goes to the origin, and the length axis is turned to +Y. Then
`rotation` and `offset` place it in the bone frame. It is re-shaded with the toon
material + outline, casts shadows, and follows the animation because it is a
child of the bone.

Bone lookup: exact name, then case-insensitive, then sanitised. GLTFLoader strips
`. : / [ ]` from node names, so `DEF-hand.R` arrives as `DEF-handR`; both match.
The status shows the name that matched.

Fallbacks: hero file missing (built-in rig with its own sword) -> `placeholder`;
attachment file missing -> `placeholder`; bone not found or file broken ->
`failed`. In every case the game keeps the procedural slash arc. Attack hit
timing always comes from `CFG.player.attack`; a long `attack` clip is only sped
up to about `clipTime` (0.6 s) so the swing reads in time, and a long `jump` clip
to about `jumpClipTime` (0.9 s).

**Tuning rotation/offset:** open `?debug=1` (or `?axes=1`). At the bone you get
long axes (red X, green Y, blue Z, 22 cm) and at the item origin short axes
(10 cm); the blade runs along the short green axis. For a Rigify hand, Y runs
from wrist to fingers, so `rotation: [90, 0, 0]` points the blade along the
bone's +Z (out of the fist, forward in the idle pose) and `offset: [0, 0.08, 0.02]`
moves the grip from the wrist into the palm. Change one number, reload, compare
screenshots (the check's `check-output/rigged-*-attack.png` shows the axes).
The defaults were tuned on the two rig-stage stand-ins.

### Asset status

`__game.state().assets` (and the debug overlay) lists every entry:

```json
{ "props.tree_broadleaf": { "state": "loaded", "file": "models/tree_broadleaf.glb",
                            "triangles": 1240, "size": [3.9, 6, 3.9], "minY": 0 },
  "hero": { "state": "loaded", "mode": "clips", "clips": ["idle","walk","run","jump","attack"], "size": [0.7, 1.5, 0.5], "minY": 0 },
  "props.rock_large": { "state": "failed", "reason": "not a glTF/GLB file" },
  "sky": { "state": "placeholder", "reason": "file not found (HTTP 404)" } }
```

`size` and `minY` are measured after normalisation, so an agent can confirm a new
file came in at the right height with its base on the ground.

### Targets for the asset side

Per-copy triangle budgets are in the manifest (`triangles`), because they depend on
how often the level draws a thing. Every triangle is drawn up to three times a frame
(colour, outline, shadow).

| asset | triangles per copy | copies in the level | notes |
| --- | --- | --- | --- |
| hero | <= 20k | 1 | one skinned mesh, one base-colour texture, clips named as in the manifest |
| mossling | 2.5k | 11 | static is fine (animated procedurally); front = +Z |
| trees | 1k | 50 broadleaf, 28 pine | |
| bush | 500 | 60 | |
| rocks | 400 small, 600 large | 40 small, 16 large | |
| flowers | 150 | 160 | no outline |
| one-off props | 1.5k to 3k | 1 to a few | gate, arch, pillar, lantern, chest, crate, fence, signpost |

The image-to-3D stage may deliver more than this (it bakes its texture for the mesh
it writes); `./forge install` reduces the copy that goes into the game.

Textures: 1024 px is plenty (512 for small props); PNG/JPG/WebP or KTX2-free GLB
embedded images. Base colour should already carry the painted shading you want
(the toon ramp adds light on top). Models should rest on their base; whatever
sticks out lowest becomes y = 0.

## Level file (`public/level.json`)

```json
{
  "version": 1, "seed": 20261112,
  "terrain": { "size": 120, "resolution": 128, "radius": 54, "baseHeight": 1.3, "noiseAmp": 0.7,
               "features": [ { "type": "hill", "x": 8, "z": -18, "radius": 15, "height": 5.5 },
                             { "type": "plateau", "x": 30, "z": -24, "radius": 9, "height": 3.6, "edge": 1.6, "sx": 1.3 },
                             { "type": "pond", "x": 19, "z": 24, "radius": 7.5, "depth": 2.8 } ] },
  "player":  { "spawn": { "x": 0, "z": 40, "yaw": 0 } },
  "lanterns": [ { "id": "west", "x": -30, "z": 6 } ],
  "gate":    { "x": 0, "z": -38, "rot": 0 },
  "paths":   [ [[0, 42], [1, 32], [-3, 22]] ],
  "enemyGroups": [ { "lantern": "west", "type": "mossling", "count": 3, "radius": 3.6 },
                   { "lantern": null, "type": "mossling", "count": 2, "radius": 1.6, "x": -7, "z": 18 } ],
  "props":   [ { "id": "chest", "x": -20, "z": 20, "rot": 2.4, "seeds": 6 },
               { "id": "crate", "x": -3.6, "z": 37, "lift": 0.8 } ],
  "seeds":   [ { "x": -12, "z": 28, "count": 3 } ],
  "clearings": [ { "x": 0, "z": -32, "r": 6 } ],
  "scatter": [ { "id": "tree_broadleaf", "count": 50, "spacing": 4.5, "scale": [0.8, 1.25], "cluster": 0.7,
                 "where": { "heightMin": 1.0, "slopeMax": 0.6, "pathMin": 2.5 } } ]
}
```

- Terrain: radial island (`radius`) plus features. `plateau` makes steep rock
  edges you cannot climb; `pond` digs below water. Lanterns, the gate and the
  spawn get flattened pads automatically. Slopes steeper than
  `CFG.player.slopeMax` and anything below y = 0.12 are not walkable.
- `paths` are polylines painted as dirt (grass and scatter avoid them).
- Enemy groups attached to a lantern guard it; the lantern cannot be lit while
  any of them is alive. Groups with `lantern: null` use their own `x`, `z`.
- `props` are hand-placed (`rot` radians, optional `scale`, `lift` metres above
  ground; chests take `seeds`). `scatter` rules place `count` instances by seeded
  rejection sampling: `where` limits height/slope/distance from paths,
  `spacing` keeps solid props apart (`"solid": false` skips that), `cluster`
  (0..1) groups them into woods.
- Unknown prop ids (not in `manifest.props`) are skipped with a console warning.

## Test hook (`window.__game`)

Start the page with `?test=1`: the simulation then advances only through
`step()`, on a fixed 1/60 s step, with seeded randomness; frames are drawn only by
`step()`/`render()`. Same inputs, same result.

| call | does |
| --- | --- |
| `await __game.ready` | resolves after the level is built and every manifest entry has settled |
| `state()` | `{ mode, time, player{x,y,z,yaw,vx,vz,grounded,health,sunseeds,hitsTaken,deaths,attacking,dead}, camera, lanterns[{id,x,z,lit,guarded}], lanternsLit, gateOpen, won, enemies{alive,total,list}, prompt, assets, assetSummary, heroAnimMode, stats{fps,drawCalls,triangles,...} }` |
| `step(dt=1/60, n=1, draw=true)` | runs n simulation steps, renders once, returns `state()` |
| `input(name, value)` | `moveX`/`moveY` in -1..1 (null releases; `moveY` 1 = away from camera), `jump`/`attack`/`interact` true/false, `cameraYaw` radians (locks the camera; null releases) |
| `teleport(x, z)` | moves the hero onto the ground there |
| `start()` | leaves the title screen (same as a click) |
| `probe(x, z)` | `{height, walkable, water, slope}` |
| `breakdown()` | draw calls and triangles per scene part in the current view |
| `placements(id)` | placed instances of a prop id `[{x,z,scale,rot}]` |
| `heroDebug(bone?)` | hero meshes (skinned, culling, shadow, materials, outline skeleton), the named bone's world position, attachments' bone/grip/tip world positions |
| `heroVisibility()` | fraction of the hero's pixels visible in the final image (renders with/without the hero and the hero alone) |
| `reloadAssets()` | re-reads the manifest and swaps models (no page reload) |

With `cameraYaw` 0 the camera sits south of the hero looking north, so
`moveY: 1` runs north (-Z) and `moveX: 1` runs east (+X).

## Check script

`npm run check` builds, serves `dist/` with real 404s, and drives headless
Chromium (`playwright-core`; GPU via ANGLE/Vulkan, falling back to SwiftShader;
force with `CHECK_GL=swiftshader`). For 1280x720, 390x844 and 844x390 (the
phone sizes with touch emulation) it asserts:

- no console errors (404s for manifest files are counted, not errors)
- title screen, start by click/tap, pause/resume buttons
- canvas is not blank (luma variance)
- movement while input is held, deceleration, stopping at the water line
- jumping leaves the ground and lands
- a sword hit damages a mossling
- a scripted run clears all guards, lights all three lanterns, opens the gate and reaches the win screen
- no page scroll; every HUD/touch control inside the viewport, not covered
  (elementFromPoint at its centre) and not overlapping another control, on the
  title, play, pause and win screens
- a tree between camera and hero: at least 70% of the hero's pixels stay visible
- peak draw calls <= 150 and triangles <= 1.1M (desktop) or 750k (phone quality) during play

Then two passes with generated fixture GLBs (`scripts/fixtures.mjs`: a 100x
scaled off-origin textured tree, a vertex-coloured enemy, a skinned hero facing
-X with clips "Idle"/"Walk"/"Attack", the same hero without clips, a corrupt GLB,
a JPEG sky and a WAV music file) check normalisation, facing, clip mapping,
`loaded/placeholder/failed` statuses, the static-hero fallback and the
missing-bone attachment fallback. Two rigged passes load the rig stage's
stand-ins (`assets/_smoke/rig/knight.glb`, `male_apose_voxel.glb`, copied into
`check-output/fixtures/`) as the hero with a 30x-scaled stick sword fixture on
`DEF-hand.R`: clips, sword status and length, skinned outline on the same
skeleton, no frustum culling, shadow casting, toon re-shade with the base-colour
texture kept (voxel), grip staying on the hand bone over 42 frames of
idle/run/attack while hand and blade tip move, and the hero visible on screen;
they save `rigged-*-attack.png` mid-swing. A respawn pass lets the guards win to
check the petal/respawn flow.

Output: `check-output/*.png` (title, midgame, lantern-lighting, lantern-lit,
gate-open, win per viewport, plus fixture shots), `check-output/summary.json`,
and a JSON summary on stdout. Exit code 1 on any failed assertion.
Env: `CHECK_VIEWPORTS=desktop,phone-portrait`, `CHECK_SKIP_FIXTURES=1`.

Not covered: real-time frame rate on real phones, audio output, how the game
feels to play, and real generated assets (only fixtures).

## Camera occlusion

Two layers: (1) the camera arm casts five rays from the hero (head, shoulders,
knees, chest) toward the camera; any prop collider taller than the ray pulls the
camera in front of it, the same way terrain does. (2) Toon and outline shaders
screen-door dither anything nearer to the camera than the hero inside an ellipse
around the hero on screen or within ~4.5 m of the lens, and everything within
~2.6 m of the lens. The hero itself never fades.

## Performance budget

- <= 150 draw calls and <= 1.1M triangles per frame on desktop, <= 750k on phone
  quality, shadow and outline passes included. Measured with the generated assets
  installed (2026-11-12, headless Chromium): 109 calls / 881k desktop, 101 to 109 /
  710k to 727k phone quality. With placeholder boxes it was 110 / 275k: most of the
  frame is the generated props, drawn three times each (colour, outline, shadow).
  Frame rate on real phones was not measured.
- Pixel ratio capped at 2 (1.6 on mobile quality).
- One shadow-casting directional light, 2048 map (1024 mobile), frustum follows
  the hero and is snapped to texels.
- Grass: one instanced draw call, 10.5k clumps (31.5k blades) on desktop, 4.8k on
  mobile, in a 40 m tile that wraps around the hero.
- `?debug=1` prints fps, draw calls, triangles and asset status; `__game.breakdown()`
  attributes cost to scene parts.

## How to extend

**Add a prop**: add an entry under `manifest.props` (file, height, collider), add a
placeholder builder with the same id in `src/assets/placeholders.js` (otherwise a
generic box is used), then place it in `level.json` (`props` or a `scatter` rule).

**Add an enemy type**: add `manifest.enemies.<id>`, a placeholder builder, tuning
under `CFG.enemies.<id>`, and either branch the state machine in `src/enemies.js`
or give the type its own class with the same interface (`update`, `updateVisuals`,
`playerSwing`, `aliveInGroup`, `nearestAlive`). Reference it from `enemyGroups[].type`.
Today only `mossling` is simulated; other ids report status "enemy type not used yet".

**Add a hero clip**: add the action to `manifest.hero.clips`, extend the name list
in `HeroVisual.setModel`, and trigger it from `HeroVisual.onEvent` (one-shot) or
`animateClips` (locomotion). Give the procedural rig an equivalent pose in
`animateRig` so the placeholder still shows it.

**Tune feel**: everything is in `src/config.js` (speeds, jump, coyote time, buffer,
attack timing, enemy timings, camera, hit-stop, shake, grass density, budgets).
