#!/usr/bin/env python
"""One picture to judge a folder of generated models: concept image on the left,
four views of the model on the right, one labelled row per asset.

    "$FORGE_RUNTIME/envs/krea/bin/python" tools/review_models.py \
        --models assets/models/raw --concepts assets/concepts/out --out review.jpg \
        [--only id,id] [--size 256] [--cols 2]

Needs Pillow (any of the stage environments has it) and Blender ($FORGE_BLENDER).
The views are rendered by pipeline/blender/render_preview.py: base colour only,
neutral light, camera 15 degrees above the horizon, first view = front.
Rows are split into pages of --rows so that a vision model can still read them.
Prints one JSON line listing the pages written.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="folder of <id>.glb files")
    ap.add_argument("--concepts", help="folder of <id>.png concept images")
    ap.add_argument("--out", required=True, help="output .jpg; more pages get -2, -3, ...")
    ap.add_argument("--only", help="comma-separated ids")
    ap.add_argument("--size", type=int, default=256, help="pixels per view")
    ap.add_argument("--cols", type=int, default=2, help="assets per row of the sheet")
    ap.add_argument("--rows", type=int, default=6, help="sheet rows per page")
    ap.add_argument("--frame", type=int, help="animation frame for rigged models")
    args = ap.parse_args()

    blender = os.environ.get("FORGE_BLENDER")
    if not blender:
        sys.exit("FORGE_BLENDER is not set; run `. ./env.sh` first")
    only = set(filter(None, (args.only or "").split(",")))
    models = sorted(p for p in Path(args.models).glob("*.glb") if not only or p.stem in only)
    if not models:
        sys.exit(f"no .glb files in {args.models}")

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 15)
    except OSError:
        font = ImageFont.load_default()

    cells, failed = [], []
    with tempfile.TemporaryDirectory(prefix="review_") as tmp:
        for model in models:
            png = Path(tmp) / f"{model.stem}.png"
            cmd = [blender, "-b", "--python", str(ROOT / "pipeline/blender/render_preview.py"), "--",
                   "--in", str(model), "--out", str(png), "--size", str(args.size)]
            if args.frame is not None:
                cmd += ["--frame", str(args.frame)]
            run = subprocess.run(cmd, capture_output=True, text=True)
            if run.returncode != 0 or not png.exists():
                failed.append(model.stem)
                continue
            views = Image.open(png).convert("RGB")
            cell = Image.new("RGB", (args.size + views.width, args.size + 22), "white")
            concept = Path(args.concepts or "") / f"{model.stem}.png"
            if args.concepts and concept.exists():
                cell.paste(Image.open(concept).convert("RGB").resize((args.size, args.size), Image.LANCZOS), (0, 22))
            cell.paste(views, (args.size, 22))
            triangles = ""
            sidecar = model.with_suffix(".json")
            if sidecar.exists():
                meta = json.loads(sidecar.read_text())
                faces = (meta.get("mesh") or {}).get("faces")
                if faces:
                    triangles = f"   {faces} triangles, generated at {meta.get('effective_resolution', '?')}"
            ImageDraw.Draw(cell).text((6, 2), model.stem + triangles, fill=(170, 0, 0), font=font)
            cells.append(cell)

    pages = []
    per_page = args.cols * args.rows
    out = Path(args.out)
    for n in range(0, len(cells), per_page):
        chunk = cells[n:n + per_page]
        w, h = chunk[0].size
        rows = (len(chunk) + args.cols - 1) // args.cols
        sheet = Image.new("RGB", (w * args.cols, h * rows), "white")
        for i, cell in enumerate(chunk):
            sheet.paste(cell, ((i % args.cols) * w, (i // args.cols) * h))
        path = out if n == 0 else out.with_name(f"{out.stem}-{n // per_page + 1}{out.suffix}")
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(path, quality=88)
        pages.append(str(path))
    print(json.dumps({"stage": "review", "pages": pages, "assets": len(cells), "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
