# Game template build log: Lantern Meadow

2026-11-12. The playable shell in `game/`: a cel-shaded third-person meadow
adventure in three.js that runs today on placeholder shapes and swaps in
generated GLBs, sky and music listed in `game/public/assets/manifest.json`.

## Versions

three 0.187.0, vite 8.3.8, playwright-core 1.63.0 (matches the Chromium 1243
already in `~/.cache/ms-playwright`, so no browser download). Node 22.

## Decisions

- **No framework, few dependencies.** Plain ES modules; `three` is the only
  runtime dependency. Bundle: 773 KB JS (211 KB gzip), whole `dist/` about 2 MB
  (1.3 MB of that is three's DRACO decoders, only fetched for DRACO-compressed GLBs).
- **Placeholders go through the same normaliser as GLBs.** Every model id has a
  primitive builder; the result is scaled to the manifest `height` and its base
  centred exactly like a loaded file, so placeholder and real asset share the
  footprint and the manifest collider.
- **Prototype = list of parts.** Loaded static GLBs are baked into metres, merged
  per base-colour texture, and drawn with `InstancedMesh` (+ an outline
  `InstancedMesh` that shares the instance-matrix buffer). Swapping a prototype
  keeps all instances, which is how placeholders are replaced at runtime.
- **Outlines in clip space.** Inverted hull pushed along a smoothed normal
  (`onormal` attribute) in clip space, so the line width is in pixels whatever the
  model's scale. A world-space push would make a 100x-scaled GLB's outline vanish.
- **Grass is one draw call** with a fixed tile of instances wrapped around the
  hero in the vertex shader; height and density come from a half-float data
  texture of the terrain (also used by the water shader for depth and foam).
- **Mosslings, lantern pinwheels, gate doors and sunseeds are instanced**, with
  poses written as instance matrices each frame. Hit flash and angry eyes use
  per-part instance colours.
- **Deterministic test mode.** `?test=1` stops the real-time loop from advancing
  the simulation; `__game.step()` runs fixed 1/60 s steps with seeded RNG. The
  camera is updated inside the sim step because its yaw changes what "forward"
  means for movement.
- **Scatter split into 2x2 chunks.** One `InstancedMesh` per prop has one bounding
  sphere, so the whole set was always drawn (and drawn again in the shadow pass).
  Chunking cut triangles but raised draw calls; merging the trees' trunk and
  canopy into one part brought calls back to ~110.
- **Occluders.** Big colliders (rocks, pillars) pull the camera in; thin ones
  (trunks) do not, because snapping the camera onto the hero's back felt worse.
  Instead, props nearer than the hero inside an ellipse around the hero on screen
  are screen-door dithered, and anything within ~2.6 m of the camera fades.

## Problems and fixes

- **Headless WebGL.** On this machine headless Chromium gets the real GPU with
  `--use-angle=vulkan --enable-features=Vulkan --ignore-gpu-blocklist --enable-gpu`
  (ANGLE on Vulkan, RTX 3090). Fallback that also works:
  `--use-angle=swiftshader --enable-unsafe-swiftshader`. The check tries the GPU
  first, then SwiftShader (`CHECK_GL=swiftshader` forces it).
- **SwiftShader was too slow at first** (a screenshot timed out after 30 s): in
  test mode the rAF loop re-rendered every frame. Now test mode renders only in
  `step()`/`render()`, the check renders every 8th step, and screenshots call
  `render()` first. Desktop pass on SwiftShader: 18 s.
- **404 console errors.** Chrome logs "Failed to load resource" for each missing
  manifest file. That is the contract working, so the check counts those (only
  for paths listed in the manifest) separately from real console errors.
- **Vite dev server answers missing files with index.html (HTTP 200).** The
  loader treats `text/html` as missing and checks the GLB magic bytes.
- **three r187 specifics.** `PCFSoftShadowMap` is deprecated (warns, falls back),
  so `PCFShadowMap` is used. `DRACOLoader` now resolves its decoder via
  `import.meta.url`, which Vite emits automatically; a manual `setDecoderPath`
  copy was removed. `Timer` exists but the game uses its own fixed-step loop.
- **Vite `assets/` collided with `public/assets/`** (the manifest folder) in
  `dist/`; built files now go to `dist/build/`.
- **Triangles over budget (366k) on the first run**: grass 117k, ~50 trees always
  drawn plus shadow pass, flowers at 290 triangles each. Fixed with lighter
  placeholders, chunking and 10.5k grass clumps on desktop.
- **Phone portrait**: the lantern row overlapped the seed counter and the camera
  felt too close. The lantern row now drops below the top bar on narrow screens;
  portrait widens the vertical FOV and steps the camera back. The check now also
  fails on overlapping HUD controls, not only covered centres.
- **Layout probe false alarm**: the full-screen `#touch` container covered every
  HUD read-out once pointer-events were forced on; the probe now enables
  pointer-events only on the element under test.
- **Facing assertion**: bounds are centred by design, so they cannot show the sign
  of the facing rotation; the fixture check asserts the rotation (long axis moves
  from X to Z) and the sign is covered by the rotation table in the loader.

## What the check covers

`npm run check` (build + check) in about 30 s on the GPU path, 126 assertions:
three viewports (1280x720, 390x844 and 844x390 with touch) each run title, start,
pause/resume, blank-canvas, movement, deceleration, water stop, jump, sword hit,
a full scripted run (all guards, three lanterns, gate, win), no page scroll,
every control inside the viewport, uncovered and non-overlapping on title, play,
pause and win screens, and draw call / triangle budgets. Two fixture passes with
generated GLBs check normalisation, re-shading input, facing, clip mapping, the
static-hero fallback, `loaded/placeholder/failed`, sky and music loading. A
respawn pass lets the guards win.

Not covered: real frame rate on real phones (headless numbers are meaningless),
whether audio sounds right, game feel, iOS Safari specifics (audio unlock,
`ogg` support), and real generated assets (only fixtures). The scripted run
teleports between objectives, so pathing across the whole island on foot is not
exercised.

## Final stats (placeholder build)

| | desktop 1280x720 | phone 390x844 | phone 844x390 |
| --- | --- | --- | --- |
| peak draw calls (incl. shadow pass) | 112 | 104 | 112 |
| peak triangles (incl. shadow pass) | 275k | 223k | 224k |
| check time | 9.4 s | 8.9 s | 8.8 s |

Whole check 32 s. Manifest entries: 20, all placeholders today (15 props, hero,
mossling, sky, two music layers).

## Follow-up (same day): hand attachments, occlusion, rigged hero

- **`hero.attachments`** in the manifest: a static GLB parented to a bone. The
  longest axis is the length axis with the handle at its minimum end, scaled to
  `length`, grip point at the origin, length axis turned to +Y, then `rotation`
  (degrees) and `offset` (metres) in the bone frame. The bone's accumulated scale
  (the hero is scaled down from its export size) is cancelled by an extra group
  so length and offset stay in metres. Verified by reading the rig-stage GLB:
  GLTFLoader turns `DEF-hand.R` into `DEF-handR` (`PropertyBinding.sanitizeNodeName`
  drops `.`), so lookup goes exact -> case-insensitive -> sanitised -> alphanumeric.
  Default `rotation [90,0,0]`, `offset [0,0.08,0.02]` chosen from four candidate
  screenshots with debug axes on both stand-ins.
- **Clip timing**: the knight's `attack` clip is 1.53 s and `jump` 5.17 s
  (start+loop+land back to back), far longer than the game's 0.34 s swing and
  ~0.7 s airtime. Clips are sped up to ~0.6 s / ~0.9 s; the hit window still
  comes from config. The attack clip now fades out when it finishes instead of
  being cut at the end of the hit window.
- **Spawn facing bug**: the hero spawned facing the camera (player yaw 0 = +Z).
  `spawn.yaw` is now the camera yaw and the hero faces away from it.
- **Occlusion**: the trunk in the earlier midgame shot was not pulled in for
  because thin colliders (r < 0.3 m after scaling) were skipped and the single
  centre ray missed it. Now five rays from the hero, all colliders >= 0.15 m,
  plus a wider dither (anything nearer than the hero within ~4.5 m of the lens).
  New check: tree between camera and hero; the hero is 100% visible (camera
  pulls in to 1.9 m); open-ground baseline 98%. Visibility is measured by
  rendering with/without the hero and the hero alone, so dithering counts.
- **Skinned hero sanity** (knight: 3 SkinnedMeshes, 53 bones; voxel: 1, textured):
  outlines are SkinnedMeshes bound to the same skeleton; all hero meshes and
  outlines have culling off (bind-pose bounds are wrong in T-pose rigs); they
  cast shadows (three's depth material skins them; the attack screenshot shows
  the arm and sword in the shadow); the voxel hero keeps its sRGB base-colour map.
- Check: 156 assertions, ~37 s (GPU). Budgets unchanged (112 calls / 275k desktop).

## Known weak spots

- The placeholder hero is primitive; no real run cycle beyond swinging legs.
- Shadows following the pose are checked structurally and by eye, not by pixels.
- The facing check only proves the rotation, not its sign (bounds are centred).
- The camera pull-in for trunks can bring it close (1.9 m) behind trees.
- Shadows cast by grass are not drawn; the meadow relies on terrain shadows.
- Only one enemy type; the second variant was not built.
- Real-device performance is unmeasured; budgets are enforced by count only.
