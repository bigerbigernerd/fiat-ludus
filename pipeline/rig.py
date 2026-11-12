#!/usr/bin/env python3
"""Static character mesh -> rigged, animated game character (one GLB).

    . ./env.sh
    python3 pipeline/rig.py --mesh in.glb --out rigged.glb \
        [--animations assets/animations] [--height 1.7] [--gpu 1] \
        [--route fit|unirig] [--weights unirig|heat] [--yaw 0] [--seed 12345] \
        [--workdir DIR] [--keep] [--sheet sheet.png] [--stills PREFIX] [--no-validate]
        [--image-format WEBP|AUTO|JPEG]

Chain (each step is a separate process; only the two UniRig inference steps hold the GPU
lock, via tools/gpu_run.sh):

 1. normalize_mesh.py (Blender)   join parts, scale to --height, feet on the origin
 2. UniRig extract (CPU)          mesh -> UniRig's npz
 3. UniRig skeleton (GPU)         autoregressive skeleton prediction -> skeleton.fbx
 4. UniRig extract (CPU)          skeleton.fbx -> npz with joints
 5. UniRig skin (GPU)             skin-weight prediction for that skeleton -> skin.fbx
 6. UniRig merge (CPU)            skin.fbx weights onto the normalised (textured) mesh
 7. --route fit (default):        fit_skeleton.py puts the animation library's own skeleton
                                  onto UniRig's joints; weights are UniRig's, re-assigned to
                                  the library bones (--weights unirig) or Blender bone heat
                                  (--weights heat)
    --route unirig:               keep UniRig's own skeleton (generic bone names)
 8. build_character.py (Blender)  retarget clips, height-normalise, export GLB
 9. verify (Blender) + validate_glb.mjs (three.js + Khronos validator) when Node is available

Prints one JSON line at the end; exits non-zero on failure.

Environment: FORGE_ROOT, FORGE_RUNTIME, FORGE_BLENDER from env.sh. UniRig lives in
$FORGE_RUNTIME/repos/UniRig with its Python env in $FORGE_RUNTIME/envs/unirig.
FORGE_NODE_TOOLS may point at a folder with `three` and `gltf-validator` installed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("FORGE_ROOT") or os.path.dirname(HERE)
RUNTIME = os.environ.get("FORGE_RUNTIME") or os.path.expanduser("~/forge-runtime")
BLENDER = os.environ.get("FORGE_BLENDER") or os.path.join(RUNTIME, "blender/current/blender")
UNIRIG = os.environ.get("FORGE_UNIRIG") or os.path.join(RUNTIME, "repos/UniRig")
UNIRIG_PY = os.environ.get("FORGE_UNIRIG_PY") or os.path.join(RUNTIME, "envs/unirig/bin/python")
NODE_TOOLS = os.environ.get("FORGE_NODE_TOOLS", "")
BL = os.path.join(HERE, "blender")

# UniRig runs with our config overrides: no FlashAttention-2 (its kernels need Ampere or newer;
# the 2080 Ti is Turing). See pipeline/unirig/ and docs/logs/unirig-setup.md.
OVERRIDES = {
    "configs/model/forge_unirig_ar_350m.yaml": "unirig/forge_unirig_ar_350m.yaml",
    "configs/model/forge_unirig_skin.yaml": "unirig/forge_unirig_skin.yaml",
    "configs/task/forge_skeleton_task.yaml": "unirig/forge_skeleton_task.yaml",
    "configs/task/forge_skin_task.yaml": "unirig/forge_skin_task.yaml",
}


class StepError(RuntimeError):
    pass


def log(msg):
    print(f"[rig] {msg}", file=sys.stderr, flush=True)


def run(cmd, label, timings, cwd=None, env=None, gpu=None, capture_prefix=None):
    """Run a step, record wall time; with gpu!=None go through tools/gpu_run.sh."""
    if gpu is not None:
        cmd = [os.path.join(ROOT, "tools/gpu_run.sh"), "rig", label, "--gpu", str(gpu), "--"] + cmd
    t = time.time()
    log(f"{label}: {' '.join(cmd)[:300]}")
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    timings[label] = round(time.time() - t, 1)
    out = p.stdout
    if p.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-25:])
        raise StepError(f"step {label} failed (exit {p.returncode}):\n{tail}")
    found = None
    if capture_prefix:
        for line in out.splitlines():
            if line.startswith(capture_prefix):
                found = json.loads(line[len(capture_prefix):])
    return out, found


def blender(script, args, label, timings, prefix):
    # --python-exit-code: without it Blender exits 0 even when the script raised.
    found = run([BLENDER, "-b", "--factory-startup", "--python-exit-code", "1",
                 "-P", os.path.join(BL, script), "--"] + args, label, timings, capture_prefix=prefix)[1]
    if found is None:
        raise StepError(f"step {label}: Blender script printed no '{prefix.strip()}' result line")
    return found


def install_overrides():
    """Copy our UniRig config overrides in and apply the PTv3 attention patch once."""
    for dst, src in OVERRIDES.items():
        shutil.copyfile(os.path.join(HERE, src), os.path.join(UNIRIG, dst))
    target = os.path.join(UNIRIG, "src/model/pointcept/models/PTv3Object.py")
    if "ai-game-forge patch" not in open(target).read():
        p = subprocess.run(["git", "-C", UNIRIG, "apply", os.path.join(HERE, "unirig/ptv3-sdpa.patch")],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if p.returncode != 0:
            raise StepError(f"could not apply pipeline/unirig/ptv3-sdpa.patch: {p.stdout.strip()}")


def unirig_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = UNIRIG
    env.setdefault("PYTHONNOUSERSITE", "1")
    return env


def unirig_gpu(task, inp, outp, seed, label, timings, env, gpu, extra=""):
    """One UniRig inference step on the GPU, under tools/gpu_run.sh.

    Paths go in through environment variables and the command is a fixed bash string, so
    the cost ledger records `$FORGE_RUNTIME/...` instead of machine-specific paths.
    TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD: PyTorch >= 2.6 defaults torch.load to weights_only=True,
    and the skin checkpoint pickles a python-box Box object (checkpoint from VAST-AI/UniRig)."""
    env = dict(env, FORGE_RUNTIME=RUNTIME, RIG_IN=inp, RIG_OUT=outp, RIG_SEED=str(seed),
               RIG_TASK=task, RIG_EXTRA=extra, TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1")
    script = ('cd "$FORGE_RUNTIME/repos/UniRig" && exec "$FORGE_RUNTIME/envs/unirig/bin/python" run.py '
              '--task="configs/task/$RIG_TASK" --seed="$RIG_SEED" --input="$RIG_IN" --output="$RIG_OUT" '
              '--npz_dir=tmp $RIG_EXTRA')
    return run(["bash", "-c", script], label, timings, env=env, gpu=gpu)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--animations", default=os.path.join(ROOT, "assets/animations"))
    ap.add_argument("--height", type=float, default=1.7)
    ap.add_argument("--gpu", type=int, default=int(os.environ.get("FORGE_GPU_AUX", "1")))
    ap.add_argument("--route", choices=["fit", "unirig"], default="fit")
    ap.add_argument("--weights", choices=["unirig", "heat"], default="unirig")
    ap.add_argument("--yaw", type=float, default=0.0, help="turn the input mesh first (deg)")
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--workdir", default="")
    ap.add_argument("--keep", action="store_true", help="keep the work folder")
    ap.add_argument("--sheet", default="", help="also render a contact sheet PNG")
    ap.add_argument("--stills", default="", help="also render posed stills with this prefix")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--image-format", default="WEBP", choices=["AUTO", "JPEG", "WEBP"],
                    help="texture encoding in the output GLB (AUTO keeps PNG)")
    a = ap.parse_args()

    t0 = time.time()
    timings = {}
    warnings = []
    mesh = os.path.abspath(a.mesh)
    out = os.path.abspath(a.out)
    anim_dir = os.path.abspath(a.animations)
    clips = os.path.join(anim_dir, "clips.json")
    library = os.path.join(anim_dir, json.load(open(clips)).get("library", "ual_standard.glb"))
    work = os.path.abspath(a.workdir) if a.workdir else tempfile.mkdtemp(prefix="forge-rig-")
    os.makedirs(work, exist_ok=True)
    result = {"ok": False, "mesh": os.path.basename(mesh), "out": a.out, "route": a.route,
              "weights": a.weights if a.route == "fit" else "unirig"}
    try:
        for p in (BLENDER, UNIRIG_PY):
            if not os.path.exists(p):
                raise StepError(f"missing {p}")
        install_overrides()
        env = unirig_env()

        # 1. normalise
        norm = os.path.join(work, "norm.glb")
        result["normalize"] = blender("normalize_mesh.py", ["--in", mesh, "--out", norm,
                                      "--height", str(a.height), "--yaw", str(a.yaw)],
                                      "normalize", timings, "NORMALIZE ")

        # 2-3. UniRig skeleton
        npz = os.path.join(work, "npz")
        skel_fbx = os.path.join(work, "skeleton.fbx")
        run([UNIRIG_PY, "-m", "src.data.extract", "--config=configs/data/quick_inference.yaml",
             "--require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm", "--force_override=true",
             "--num_runs=1", "--id=0", "--time=forge", "--faces_target_count=50000",
             f"--input={norm}", f"--output_dir={npz}"], "extract_mesh", timings, cwd=UNIRIG, env=env)
        unirig_gpu("forge_skeleton_task.yaml", norm, skel_fbx, a.seed, "unirig_skeleton", timings, env, a.gpu)
        if not os.path.exists(skel_fbx):
            raise StepError("UniRig skeleton step wrote no skeleton.fbx")

        # 4-5. UniRig skin
        npz2 = os.path.join(work, "npz_skin")
        skin_fbx = os.path.join(work, "skin.fbx")
        run([UNIRIG_PY, "-m", "src.data.extract", "--config=configs/data/quick_inference.yaml",
             "--require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm", "--force_override=true",
             "--num_runs=1", "--id=0", "--time=forge", "--faces_target_count=50000",
             f"--input={skel_fbx}", f"--output_dir={npz2}"], "extract_skeleton", timings, cwd=UNIRIG, env=env)
        unirig_gpu("forge_skin_task.yaml", skel_fbx, skin_fbx, a.seed, "unirig_skin", timings, env, a.gpu,
                   extra="--data_name=raw_data.npz")
        if not os.path.exists(skin_fbx):
            raise StepError("UniRig skin step wrote no skin.fbx")

        # 6. merge onto the textured mesh
        merged = os.path.join(work, "unirig_rigged.glb")
        run([UNIRIG_PY, "-m", "src.inference.merge", "--require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm",
             "--num_runs=1", "--id=0", f"--source={skin_fbx}", f"--target={norm}", f"--output={merged}"],
            "merge", timings, cwd=UNIRIG, env=env)
        if not os.path.exists(merged):
            raise StepError("UniRig merge wrote no output")

        # 7. skeleton route
        rigged = merged
        if a.route == "fit":
            rigged = os.path.join(work, "fitted.glb")
            fit = blender("fit_skeleton.py", ["--mesh", norm, "--joints", merged, "--template", library,
                                              "--weights", "transfer" if a.weights == "unirig" else "heat",
                                              "--out", rigged], "fit_skeleton", timings, "FIT ")
            result["unirig_bones"] = fit.get("source_bones")
            result["unirig_labels"] = fit.get("source_labels")
            if fit.get("heat_unweighted_vertices"):
                warnings.append(f"bone heat left {fit['heat_unweighted_vertices']} vertices unweighted; "
                                "filled from nearest weighted vertex")

        # 8. retarget + export
        bargs = ["--rigged", rigged, "--anim", library, "--clips", clips, "--out", out,
                 "--height", str(a.height), "--report", os.path.join(work, "build.json")]
        if a.sheet:
            bargs += ["--sheet", os.path.abspath(a.sheet)]
        bargs += ["--image-format", a.image_format]
        build = blender("build_character.py", bargs, "build", timings, "BUILD ")
        result.update({"bones": build["bones"], "clips": build["clips"],
                       "labels_from": build["labels_from"], "mapped_bones": build["mapped_bones"]})
        warnings += build.get("warnings", [])

        # 9. verify
        vargs = ["--in", out]
        if a.stills:
            vargs += ["--stills", os.path.abspath(a.stills)]
        ver = blender("verify_rig.py", vargs, "verify", timings, "VERIFY ")
        sk = [m for m in ver["skinned_meshes"] if m["armature_modifier"]]
        result["verify"] = {k: sum(m[k] for m in sk) for k in ("vertices", "unweighted", "sum_not_1")}
        if result["verify"]["unweighted"] or result["verify"]["sum_not_1"]:
            warnings.append(f"weights: {result['verify']}")
        if not a.no_validate and NODE_TOOLS and shutil.which("node"):
            venv = dict(os.environ, NODE_PATH=NODE_TOOLS)
            p = subprocess.run(["node", os.path.join(HERE, "validate_glb.mjs"), out], env=venv,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                v = json.loads(p.stdout)
                result["validator"] = {"errors": v["validator"]["errors"], "warnings": v["validator"]["warnings"],
                                       "three_clips": [c["name"] for c in v["three"]["clips"]]}
                if v["validator"]["errors"]:
                    raise StepError(f"glTF validator errors: {v['validator']['messages']}")
            except (json.JSONDecodeError, KeyError):
                warnings.append(f"validator did not run cleanly: {p.stderr.strip()[-300:]}")
        elif not a.no_validate:
            warnings.append("glTF validation skipped (set FORGE_NODE_TOOLS to a folder with three + gltf-validator)")
        result["ok"] = True
    except StepError as e:
        result["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - report anything as one JSON line
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        result["timings_s"] = timings
        result["seconds"] = round(time.time() - t0, 1)
        result["warnings"] = warnings
        if not a.keep and not a.workdir and result["ok"]:
            shutil.rmtree(work, ignore_errors=True)
        else:
            result["workdir"] = work
        print(json.dumps(result))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
