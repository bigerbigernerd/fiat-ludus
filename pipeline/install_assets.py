#!/usr/bin/env python3
"""Put generated models where the game looks for them.

    python3 pipeline/install_assets.py [--from assets/models/raw] [--only id,id]
                                       [--manifest game/public/assets/manifest.json]
                                       [--texture-size 1024]

For every static model the game's manifest names (props, enemies, the hero's
attachments) and that exists as <from>/<id>.glb, runs the Blender clean-up
(join, weld, base centre to the origin, scale to the manifest height, textures
to WebP) and writes it to the manifest's file path. If the manifest entry has
"triangles", a model above that count is decimated to it: things the game draws
many times (trees, bushes, flowers) want far fewer triangles per copy than a
one-off gate, and that is the game's call, not the generator's.

Orientation: image-to-3D output has no reliable front. If the manifest entry has
"yaw" (degrees), the model is turned by that much. Otherwise, when the entry's
box collider says the object is wider than deep (a gate, a fence, a door) and
the model came out deeper than wide, it is turned 90 degrees. Which of the two
long sides is the front still has to be judged from a preview.

The sky: if the manifest has a "sky" entry and <concepts>/sky.png exists, the
panorama is written to the manifest's sky file as JPEG.

The hero is not handled here: `forge rig` writes the rigged file itself.
Prints one JSON line: what was installed, what the manifest names but is missing.
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def footprint(collider):
    """+1 if the collider says wider (x) than deep (z), -1 if deeper, 0 if it does not say."""
    boxes = [c for c in (collider if isinstance(collider, list) else [collider])
             if isinstance(c, dict) and c.get("type") == "box"]
    if not boxes:
        return 0
    x0 = min(c.get("x", 0) - c["width"] / 2 for c in boxes)
    x1 = max(c.get("x", 0) + c["width"] / 2 for c in boxes)
    z0 = min(c.get("z", 0) - c["depth"] / 2 for c in boxes)
    z1 = max(c.get("z", 0) + c["depth"] / 2 for c in boxes)
    width, depth = x1 - x0, z1 - z0
    if width > 1.2 * depth:
        return 1
    if depth > 1.2 * width:
        return -1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default=os.path.join(ROOT, "assets/models/raw"))
    ap.add_argument("--manifest", default=os.path.join(ROOT, "game/public/assets/manifest.json"))
    ap.add_argument("--only", help="comma-separated ids")
    ap.add_argument("--texture-size", type=int, default=1024)
    ap.add_argument("--concepts", default=os.path.join(ROOT, "assets/concepts/out"),
                    help="folder with sky.png (the sky panorama from the image stage)")
    args = ap.parse_args()

    blender = os.environ.get("FORGE_BLENDER")
    if not blender:
        sys.exit("FORGE_BLENDER is not set; run through ./forge or `. ./env.sh` first")
    with open(args.manifest) as fh:
        manifest = json.load(fh)
    public = os.path.dirname(os.path.abspath(args.manifest))

    wanted = {}   # id -> (file, height or None, manifest entry)
    for group in ("props", "enemies"):
        for key, entry in manifest.get(group, {}).items():
            wanted[key] = (entry["file"], entry.get("height"), entry)
    for item in manifest.get("hero", {}).get("attachments", []):
        wanted[item["id"]] = (item["file"], item.get("length"), item)
    only = set(filter(None, (args.only or "").split(",")))

    installed, missing, failed = [], [], []
    for key, (file, height, entry) in wanted.items():
        if only and key not in only:
            continue
        src = os.path.join(args.src, f"{key}.glb")
        if not os.path.exists(src):
            missing.append(key)
            continue
        dst = os.path.join(public, file)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        cmd = [blender, "-b", "--factory-startup", "--python-exit-code", "1",
               "--python", os.path.join(ROOT, "pipeline/blender/clean_asset.py"), "--",
               "--in", src, "--out", dst, "--faces", str(int(entry.get("triangles", 0))),
               "--texture-size", str(args.texture_size)]
        if height:
            cmd += ["--height", str(height)]

        def clean(yaw):
            run = subprocess.run(cmd + (["--yaw", str(yaw)] if yaw else []), capture_output=True, text=True)
            line = next((l for l in reversed(run.stdout.splitlines()) if l.startswith('{"stage"')), None)
            if run.returncode != 0 or line is None or "error" in json.loads(line):
                return None, (json.loads(line).get("error") if line else run.stdout[-400:])
            return json.loads(line), None

        yaw = float(entry.get("yaw", 0))
        info, error = clean(yaw)
        if info and "yaw" not in entry:
            wide = footprint(entry.get("collider"))
            sx, sy = info["size_m"][0], info["size_m"][1]     # Blender X = width, Y = depth
            if wide and (wide > 0) != (sx >= sy) and max(sx, sy) > 1.2 * min(sx, sy):
                yaw = 90.0
                info, error = clean(yaw)
        if info is None:
            failed.append({"id": key, "error": error})
            continue
        installed.append({"id": key, "file": file, "triangles": info["after"]["triangles"],
                          "size_m": info["size_m"], "yaw": yaw, "double_sided": info["double_sided"],
                          "open_edge_ratio": info["open_edge_ratio"], "bytes": info["bytes"]})
    sky = None
    sky_src = os.path.join(args.concepts, "sky.png")
    sky_file = (manifest.get("sky") or {}).get("file")
    if sky_file and os.path.exists(sky_src) and (not only or "sky" in only):
        # Blender converts it, so this step needs no Python package of its own.
        dst = os.path.join(public, sky_file)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        code = ("import bpy,sys; a=sys.argv[sys.argv.index('--')+1:]; im=bpy.data.images.load(a[0]); "
                "s=bpy.context.scene; s.render.image_settings.file_format='JPEG'; s.render.image_settings.quality=90; "
                "s.view_settings.view_transform='Standard'; im.save_render(a[1], scene=s); print('SKY', *im.size)")
        run = subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python-expr", code,
                              "--", sky_src, dst], capture_output=True, text=True)
        line = next((l for l in run.stdout.splitlines() if l.startswith("SKY ")), None)
        if run.returncode != 0 or line is None or not os.path.exists(dst):
            failed.append({"id": "sky", "error": run.stdout[-300:]})
        else:
            sky = {"file": sky_file, "size": [int(v) for v in line.split()[1:3]], "bytes": os.path.getsize(dst)}
    print(json.dumps({"stage": "install", "installed": installed, "sky": sky, "missing": missing, "failed": failed,
                      "total_bytes": sum(i["bytes"] for i in installed)}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
