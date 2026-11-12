# Working in this repository (for coding agents)

This repository turns text into a playable 3D browser game on one consumer GPU.
You drive it through one command, `./forge`. Every stage reads files, writes
files, prints one JSON line last, and exits non-zero when it failed. Read that
line; do not assume a stage worked.

```
text prompt ──images──▶ concept PNG ──models──▶ raw GLB ──install──▶ game/public/assets/models/*.glb
                                   └─(hero) face ─▶ rig ─▶ hero.glb with skeleton and clips
text prompt ──music───▶ game/public/assets/audio/*.ogg
                                                         ./forge check  = build the game + play it in a headless browser
```

## Rules

1. **Describe, never name.** Prompts describe a look by its attributes. They never
   contain the name of a game, a franchise, a character, a studio, an artist, a band
   or a composer. "cel-shaded adventure game art, chunky shapes, warm flat colours" is
   fine; "in the style of <title>" is not.
2. **GPU jobs go through `./forge`.** It takes a per-GPU lock and writes the cost of
   each job to `cost/ledger.jsonl`. Never call the stage scripts' Python directly and
   never start two GPU jobs for the same card yourself; the second one just waits.
3. **Batch, do not loop.** `images` and `models` load several GB of weights per call.
   Put all assets in one JSON Lines file and call the stage once. Both skip what is
   already up to date: an image made from the same prompt, seed and size, a model made
   from the same image and settings. Change a line and only that asset is redone.
4. **Long jobs run in the background.** Image, model and music jobs take minutes,
   longer than most shell tools wait. Start them with `./forge bg <name> <stage ...>`
   (for example `./forge bg models models assets/models/mygame.jsonl`) and poll with
   `./forge wait <name>` until it says `"state": "finished"`; its exit status is the
   job's. A job that is killed half-way writes nothing to the cost ledger.
5. **Look before you install.** `./forge review` makes one picture per folder with
   the concept next to four views of the model. If you can read images, read it. If
   you cannot, read the numbers instead (see "Judging a model without eyes").
6. **Do not edit `game/src/` to make a check pass.** If `./forge check` fails after
   you added assets, the asset or the manifest entry is wrong.
7. Secrets (`HF_TOKEN`, API keys) live in the environment or `.env.local`. Never
   print them, never write them into a file in the repository.

## Stage by stage

### 1. Concept images: `./forge images <file.jsonl>`

One JSON object per line; `#` starts a comment line. Output: `assets/concepts/out/<id>.png`
and `<id>.json`. About 30 s per 1024×1024 image on an 11 GB card, plus about a
minute to load the model once per call.

```json
{"id": "bench", "preset": "prop", "seed": 7, "subject": "A rustic wooden park bench with two plank seats and iron feet"}
```

- `preset`: `prop` (one object, three-quarter view, white background), `character`
  (full body, front view, A-pose with a gap between arms and torso: required for
  rigging), `creature`, `sky` (2048×1024 panorama), `texture` (tileable).
- `subject` is wrapped by the preset's wording; use `prompt` instead to write the
  whole prompt yourself.
- One object per image, the whole object inside the frame, nothing held in a
  character's hands (weapons are separate props), no tails, capes or long skirts on
  anything that will be rigged.
- A different `seed` gives a different drawing of the same description.

### 2. Models: `./forge models <file.jsonl>`

Output: `assets/models/raw/<id>.glb`, `<id>.json`, `<id>_preview.png`.

```json
{"id": "bench", "image": "assets/concepts/out/bench.png", "faces": 2000}
{"id": "hero", "image": "assets/concepts/out/hero.png", "resolution": 1536, "faces": 20000, "texture_size": 2048, "remesh": "on"}
```

- `faces`: the triangle budget. Take it from the game (see the table in
  `game/README.md`), not from taste.
- `resolution`: 1024 by default. 512 is four times faster and fine for rocks and
  planks, but it shifts colours (grey stone turned tan, amber eyes turned dark red in
  our tests). 1536 for the hero. If the GPU runs out of memory the stage steps down
  by itself and says so in `out_of_memory_at`.
- The stage exports each mesh two ways and keeps the cleaner one; `export.health`
  in the JSON says how clean it is.

### 3. Review: `./forge review assets/models/raw`

Writes `assets/models/raw/_review.jpg` (and `_review-2.jpg`, ... for more than twelve
assets): concept on the left, then front, right, back, left views.

**Judging a model without eyes.** In `assets/models/raw/<id>.json`:

- `ok` must be true and `mesh.faces` close to the budget.
- `export.health.bad_edge_ratio` is the share of edges that are not shared by exactly
  two triangles. Our clean models are below 0.02. Above 0.2 the object is made of
  loose sheets (foliage is allowed to be; a crate is not): try another seed.
- `mesh.bbox_min` / `bbox_max`: the proportions should match the object (a fence is
  wide and thin, a pillar is tall). A cube-shaped box for a flat object means the
  background was not removed; regenerate the concept on a plain white background.

### 4. Hero only: face, then rig

```
./forge face assets/models/raw/hero.glb assets/concepts/out/hero.png assets/models/work/hero_face.glb --debug assets/models/work/hero_face_check.png
./forge rig  assets/models/work/hero_face.glb game/public/assets/models/hero.glb --height 1.75 --sheet assets/models/rigged/hero_sheet.png
```

- `face` paints the concept image back onto the head (generated faces are blurry).
  Check `fit.correlation` in its JSON: ours was 0.73. If it is below about 0.5 the
  head was probably not found; pass `--box left,top,right,bottom` (fractions of the
  image) around the head and look at the `--debug` picture.
- `rig` needs about 110 s. Its JSON must say `"ok": true`, 53 bones, five clips
  (`idle`, `walk`, `run`, `jump`, `attack`) and `verify.unweighted` 0.

### 5. Install: `./forge install`

Reads `game/public/assets/manifest.json`. For every prop, enemy and hero attachment
it names that exists in `assets/models/raw/`, it scales the model to the manifest's
`height`, reduces it to the manifest's `triangles`, turns flat objects to match
their collider, converts textures to WebP and writes the file the manifest points to.
It also writes the sky: `assets/concepts/out/sky.png` (preset `sky`) becomes the
manifest's sky file. To add a new prop, add an entry to the manifest first (copy a
similar one), then place it in `game/public/level.json`. Both formats are documented
in `game/README.md`.

### 6. Music: `./forge music --prompt "<style>" --seconds 75 --instrumental --out game/public/assets/audio/field.ogg`

Describe genre, instruments, tempo and mood. Always pass `--instrumental` for
background music. Exit code 1 means "generated, but the piece came out shorter than
asked or was cut off" (`"status": "needs_review"` in the JSON); try another `--seed`.

### 7. Check: `./forge check`

Builds the game and plays it in a headless browser at three screen sizes: loads
every asset, walks, fights, lights the lanterns, opens the gate, wins. It prints
which assertions failed and writes screenshots to `game/check-output/`. The line
`N generated assets, M placeholders` on the title screen tells you how many manifest
entries still have no file. A placeholder is not an error; a `failed` asset is.

### 8. Cost: `./forge cost`

GPU minutes, watt-hours and electricity cost per stage so far.

## When something fails

| What you see | What it means | What to do |
|---|---|---|
| `missing .../envs/<name>/bin/python` | that stage is not installed | `setup/check.sh`, then the matching `setup/env_*.sh` |
| `out of memory` in a stage's JSON | the card is too small for that setting | nothing if `ok` is true (it already stepped down); otherwise lower `resolution` |
| a stage prints nothing for minutes | another GPU job holds the lock | wait; `nvidia-smi` shows who |
| HTTP 401/403 while downloading | a gated model | accept its licence on the model page with the account that owns `HF_TOKEN` |
| `./forge check`: `triangles within budget` | an asset has more triangles than the game asked for | set `triangles` in its manifest entry and run `./forge install` again |
| a prop is black or see-through in the game | inside-out or open mesh | run `./forge install` again (it marks open meshes double-sided); if the JSON says `bad_edge_ratio` > 0.2, regenerate |

More detail per stage: `docs/setup.md`, `game/README.md`, the docstring at the top
of each file in `pipeline/`.
