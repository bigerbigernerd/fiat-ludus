You are working in a copy of a repository that builds a small 3D browser game from text
prompts. Read `AGENTS.md` first. Then read the parts of `game/README.md` that describe
the asset manifest and the level file.

Your task: give the demo game "Lantern Meadow" an autumn make-over, using the pipeline
in this repository. Do all of the following, and keep going until `./forge check` passes.

1. **Hero.** Design a new, original hero who looks clearly different from the current
   one. Generate the concept image, turn it into a model, fix the face, rig it, and
   install it as the game's hero.
2. **Enemy.** Replace the mossling with a new small creature of your own design. Keep
   the manifest id `mossling` so the game code does not change.
3. **Props.** Replace `tree_broadleaf` with an autumn tree (orange and red leaves). Add
   one new prop with the id `pumpkin` and place at least six of them in the level near
   the player's spawn point.
4. **Sky.** An early-evening sky.
5. **Music.** A new 60-second instrumental field theme that fits autumn, installed as
   the game's field music.
6. Run `./forge check` and make it pass. Fix problems by fixing assets, the manifest or
   the level file. Do not edit anything under `game/src/`.
7. Write `REPORT.md` in the repository root: what you made (ids, prompts, seeds), what
   went wrong and how you fixed it, the table printed by `./forge cost`, and anything
   you could not verify.

Rules: follow `AGENTS.md`. Image, model and music jobs take minutes: start them with
`./forge bg` and poll with `./forge wait`. Do not ask questions; decide yourself. You
are done when `REPORT.md` exists and `./forge check` exits with status 0.
