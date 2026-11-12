# Animation and test-mesh sources

Everything in this folder is redistributable. Only CC0 material is used, so no attribution is
legally required; we credit the authors anyway.

## ual_standard.glb

- **What:** the skeleton (53 bones, Rigify-style `DEF-` names, plus a `root` bone) and 7 clips:
  `Idle_Loop`, `Walk_Loop`, `Jog_Fwd_Loop`, `Jump_Start`, `Jump_Loop`, `Jump_Land`,
  `Sword_Attack`. Trimmed from the Godot GLB of the Standard (free) edition of the library with
  `pipeline/blender/trim_library.py`. The mannequin mesh is not included.
- **Source:** Quaternius, *Universal Animation Library* (Standard edition, 45 animations).
  - Pack page: https://quaternius.com/packs/universalanimationlibrary.html
  - Download used: https://opengameart.org/content/universal-animation-library
    (`universal_animation_librarystandard.zip`, 14,541,205 bytes, downloaded 2026-11-12;
    the same zip is on https://quaternius.itch.io/universal-animation-library)
- **Licence:** CC0 1.0 Universal (public domain dedication), stated in the pack's
  `License.txt` and on the OpenGameArt page.
  https://creativecommons.org/publicdomain/zero/1.0/
- **Rest pose:** T-pose, character faces glTF +Z, about 1.83 m tall (hips at 0.917 m).

## clips.json

Our own mapping from the game's clip names (`idle`, `walk`, `run`, `jump`, `attack`) to the
library animations above. `jump` is `Jump_Start` + `Jump_Loop` + `Jump_Land` played back to back.

## Test meshes (not shipped here, used for the smoke tests in `assets/_smoke/rig/`)

- **Quaternius, *Universal Base Characters* (Standard edition)**, `Superhero_Male_FullBody.gltf`
  with its textures. CC0 1.0 (`License_Standard.txt` in the zip).
  https://quaternius.com/packs/universalbasecharacters.html ,
  downloaded from https://quaternius.itch.io/universal-base-characters (free "name your own
  price" download, `Universal Base Characters[Standard].zip`, 128,968,391 bytes, 2026-11-12).
  We strip its skeleton (`pipeline/blender/prep_test_mesh.py`) to get a static mesh, and make two
  "AI-like" variants from it: a voxel remesh (fused watertight surface, one baked texture) and
  an arms-lowered (70 degrees, A-pose) voxel remesh in which the arms are fused to the torso.
  Its original skeleton also serves as ground truth for checking the retargeting code.
- **Quaternius, *LowPoly Animated Knight*** (2018), `FBX/KnightCharacter.fbx`: a chunky,
  big-headed 956-triangle character with flat material colours, used as the "stylised
  proportions" test. CC0 1.0, https://opengameart.org/content/lowpoly-animated-knight
  (`Knight Character by @Quaternius.zip`, 2026-11-12). Its FBX materials import with alpha 0;
  `prep_test_mesh.py` forces them opaque.
