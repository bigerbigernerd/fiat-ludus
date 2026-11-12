# Autumn make-over of Lantern Meadow

All work was done through `./forge`; nothing under `game/src/` was edited. The
game's asset manifest and level file are the only game files changed, plus the
generated asset files they point at.

## What was made

| Asset | Concept seed | Model seed | Model settings | Final game asset |
| --- | ---: | ---: | --- | --- |
| Hero | 21 | 78 | resolution 1536, 20k faces, 2048 texture, remesh on | 19,274 triangles, 53 bones, 5 clips (`idle`, `walk`, `run`, `jump`, `attack`), height 1.75 |
| `mossling` | 31 | 79 | 4,000 faces | 2,500 triangles (installed), height 0.9 |
| `tree_broadleaf` | 41 | 80 | 2,500 faces | 999 triangles (installed), height 6.0 |
| `pumpkin` | 51 | 81 | 1,500 faces | 800 triangles (installed), height 0.6 |
| Sky | 61 | - | sky preset, 2048×1024 | `textures/sky.jpg`, 2048×1024 |
| Field music | - | 14 | 60 s, instrumental | `audio/field.ogg`, 60.0 s loop |

### Hero prompt (concept seed 21)

> An original young woman adventurer, about twenty years old, short wavy copper-red
> hair with a thin braid over one temple and warm freckles across her nose, bright
> hazel eyes. She wears a mustard-yellow quilted sleeveless vest over a cream
> long-sleeve shirt with rolled cuffs, a rust-orange neckerchief, dark forest-green
> slim trousers and tall brown leather boots, fingerless brown leather gloves on
> both hands, a small leather belt pouch at her hip. Slim silhouette, no cloak, no
> skirt, no satchel bag, short hair that stays above the shoulders.

This is a deliberately different silhouette and palette from the original hero
(cream poncho / red scarf / silver hair): copper-red hair, mustard vest, green
trousers, brown boots. Face projection fitted at correlation 0.735; the rig report
said `ok: true`, 53 bones, 5 clips, `verify.unweighted: 0`.

### Enemy prompt (id kept as `mossling`, concept seed 31)

> A small plump autumn leaf sprite, round body built from layered russet, amber and
> scarlet fallen leaves, two large round glowing amber eyes, a tiny pointed
> acorn-cap hat, two short twig arms ending in simple three-finger paws, two stubby
> twig legs, a cheerful mischievous expression, no tail

### Autumn tree prompt (concept seed 41)

> A broadleaf autumn tree with a thick dark brown trunk and five big rounded clumps
> of leaves in vivid orange, scarlet red and warm golden yellow, a few falling leaves
> around the crown

### Pumpkin prompt (new prop, concept seed 51)

> A plump ripe orange pumpkin with deep vertical ribs, a short curled green stem and
> two small dried brown autumn leaves resting at its base

Added to `manifest.props` as `pumpkin` (`height` 0.6, 800 triangles, cylinder
collider radius 0.32) and placed eight instances around the spawn at (0, 40):
(2.8, 38.6), (3.7, 40.0), (-2.9, 38.9), (-4.0, 40.4), (2.2, 42.8), (-2.4, 43.3),
(4.7, 42.0), (-4.7, 42.6). The corridor north of the spawn was left clear so the
movement test still runs.

### Sky prompt (concept seed 61)

> An early-evening autumn sky just after sunset, deep blue-violet overhead fading
> to warm apricot, dusty rose and soft gold near the horizon, thin streaky clouds
> lit pink and amber, a few faint early stars high in the sky

### Music prompt (seed 14)

> warm autumn folk instrumental, gentle fingerpicked acoustic guitar, soft cello and
> muted trumpet, light hand percussion, 72 bpm, nostalgic calm evening mood

The stage rendered 84.0 s of source audio and cut a 60.0 s, 35-bar loop with a
2.0 s crossfade (reported `"seconds": 60.0` in its loop metadata).

## What went wrong and how it was fixed

- No stage failed and no asset had to be regenerated. The model batch took
  13.9 GPU minutes for four assets; all four models returned `ok: true` with clean
  meshes (`bad_edge_ratio` 0.0 to 0.029).
- The autumn tree is airier than the concept because its leaf clumps are open
  sheets (`open_edge_ratio` 0.0458 after install). That is allowed for foliage;
  `./forge install` marked it double-sided, and it reads correctly in the game.
- The music stage's raw render was longer than the requested 60 s, but the loop it
  wrote is exactly 60.0 s, so no retry was needed.
- `./forge install` reported the other original props as `missing` because those raw
  GLBs were not in this copy; the existing installed files stayed in place and the
  final check loaded every manifest entry with 0 placeholders.

## `./forge cost`

```text
stage        card         jobs  failed  GPU minutes  Wh    cost @ 0.15/kWh  peak MiB
image        RTX 2080 Ti  1     0       3.6          12.8  0.0019           9879
image_to_3d  RTX 2080 Ti  1     0       13.9         48.6  0.0073           10031
rig          RTX 2080 Ti  2     0       1.1          1.3   0.0002           6969
bgm          RTX 2080 Ti  1     0       3.3          7.9   0.0012           6507
total                     5     0       21.9         70.6  0.0106
```

## Verification

`./forge check` exits 0. All three viewports pass 34/34 assertions, the fixture
passes pass 13/13 each, and the respawn pass passes 3/3. The final summary reports
`"ok": true`, 23 generated assets loaded, 0 placeholders, 0 failed. Desktop peaked
at 112 draw calls / 887,065 triangles; both phone viewports stayed under their
budgets. Hero animation mode is `clips`.

## Could not verify

- How the new music actually sounds (the check does not play audio); only the
  stage's technical report (60.0 s loop, loudness, vocal check) was available.
- Real-time frame rate and feel on a physical phone; the check uses headless
  Chromium with GPU/ANGLE.
- The rigged stand-in hero passes were skipped because `assets/_smoke/rig/` is not
  present in this copy, so the full 53-bone/attachment fixture path was not run.
  The real generated hero did load with five clips and a worked `DEF-handR` sword
  attachment in the main check.
- Subjective model quality from every in-game camera angle; judgement used the
  review sheet, model health numbers and the check screenshots.
