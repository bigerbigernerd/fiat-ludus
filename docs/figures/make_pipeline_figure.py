#!/usr/bin/env python3
"""Draw the pipeline overview figure (docs/figures/pipeline.png, .svg, .pdf).

    python docs/figures/make_pipeline_figure.py

Needs matplotlib, numpy and Pillow. Text is set in Nimbus Roman, the Times face
that the LaTeX `times` package (and so the ICLR and NeurIPS templates) uses;
falls back to any Times-like serif. The thumbnails in pipeline-parts/ are real
outputs of the stages: concept images, model renders, the repainted face, two
frames of the run clip, two screenshots and the loudness envelope of the field
theme. The numbers are the ones measured on the RTX 2080 Ti (see the README).
"""
import glob
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from PIL import Image

HERE = Path(__file__).resolve().parent
PARTS = HERE / "pipeline-parts"

for path in glob.glob("/usr/share/fonts/opentype/urw-base35/NimbusRoman-*.otf"):
    font_manager.fontManager.addfont(path)
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Nimbus Roman", "Times New Roman", "TeX Gyre Termes", "Times", "STIXGeneral", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "svg.fonttype": "path",      # text as outlines: the SVG looks the same everywhere
    "pdf.fonttype": 42,
})

W, H = 128.0, 57.5               # one unit = 0.1 inch
INK = "#222222"
MUTED = "#555555"
STYLE = {                        # fill, edge
    "image": ("#DDEBF7", "#3B75AF"),
    "mesh": ("#FCE5CD", "#D9822B"),
    "blender": ("#E2F0D9", "#5B9A46"),
    "rig": ("#E8E0F3", "#7B5EA7"),
    "music": ("#F9DEDC", "#C0504D"),
    "game": ("#FFF6D6", "#BF9000"),
    "text": ("#F6F6F6", "#9A9A9A"),
}

fig = plt.figure(figsize=(W / 10, H / 10))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")


def rounded(x0, y0, w, h, kind, lw=1.3, radius=1.3, z=2, dashed=False):
    fill, edge = STYLE[kind]
    ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                                fc=fill, ec=edge, lw=lw, zorder=z, linestyle="--" if dashed else "-"))


def process(xc, yc, kind, name, what, cost, w=15.0, h=9.0):
    rounded(xc - w / 2, yc - h / 2, w, h, kind)
    ax.text(xc, yc + 2.35, name, ha="center", va="center", fontsize=12, fontweight="bold", color=INK, zorder=3)
    ax.text(xc, yc - 0.25, what, ha="center", va="center", fontsize=9.5, color=INK, zorder=3)
    ax.text(xc, yc - 2.7, cost, ha="center", va="center", fontsize=9, fontstyle="italic", color=MUTED, zorder=3)


def arrow(x0, y0, x1, y1, lw=1.2, z=1):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=11,
                                 lw=lw, color=INK, shrinkA=0, shrinkB=0, zorder=z))


def picture(name, x0, y0, w, h, z=2):
    """Paste an image into the box (x0, y0, w, h), keeping its aspect ratio, centred."""
    img = Image.open(PARTS / name).convert("RGB")
    k = min(w / img.width, h / img.height)
    pw, ph = img.width * k, img.height * k
    px, py = x0 + (w - pw) / 2, y0 + (h - ph) / 2
    ax.imshow(np.asarray(img), extent=[px, px + pw, py, py + ph], zorder=z, interpolation="lanczos", aspect="auto")


def frame(x0, y0, w, h):
    ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle="round,pad=0,rounding_size=0.8",
                                fc="white", ec="#BBBBBB", lw=0.8, zorder=1))


def caption(xc, y, text):
    ax.text(xc, y, text, ha="center", va="center", fontsize=8.8, fontstyle="italic", color=MUTED)


def prompt(yc, quote):
    rounded(1.0, yc - 5.5, 13.0, 11.0, "text", lw=0.9)
    ax.text(7.5, yc + 3.4, "text prompt", ha="center", va="center", fontsize=10, fontweight="bold", color=INK, zorder=3)
    ax.text(7.5, yc - 1.3, quote, ha="center", va="center", fontsize=8.6, fontstyle="italic", color=INK,
            zorder=3, linespacing=1.25)


MAIN, HERO, MUSIC = 49.0, 30.0, 9.5

# ---- main lane: prompt -> image -> mesh -> game-ready props -------------------------------
prompt(MAIN, "“A stone wind-lantern\nshrine: a short carved\nstone pillar …”")
arrow(14.0, MAIN, 16.0, MAIN)
process(23.5, MAIN, "image", "Krea 2 Turbo", r"text $\rightarrow$ image", "33 s · 9.2 GB")
arrow(31.0, MAIN, 34.0, MAIN)
frame(34.0, MAIN - 6, 11.0, 12.0)
picture("concept_hero.png", 34.3, MAIN - 5.7, 5.0, 11.4)
picture("concept_lantern.png", 39.6, MAIN - 5.7, 5.1, 11.4)
caption(39.5, MAIN - 7.6, "concept images")
arrow(45.0, MAIN, 48.0, MAIN)
process(55.5, MAIN, "mesh", "TRELLIS.2", r"image $\rightarrow$ textured mesh", "0.8–7.4 min · 10.2 GB")
arrow(63.0, MAIN, 66.0, MAIN)
frame(66.0, MAIN - 6, 11.0, 12.0)
picture("mesh_hero.png", 66.3, MAIN - 5.7, 5.0, 11.4)
picture("mesh_lantern.png", 71.6, MAIN - 5.7, 5.1, 11.4)
caption(71.5, MAIN - 7.6, "raw meshes")
arrow(77.0, MAIN, 80.0, MAIN)
process(87.5, MAIN, "blender", "Blender", "scale · orient · budget", "headless · CPU only")
arrow(95.0, MAIN, 97.5, MAIN)
frame(97.5, MAIN - 6, 11.0, 12.0)
for name, col, row in (("mesh_mossling.png", 0, 1), ("mesh_tree_pine.png", 1, 1),
                       ("mesh_crate.png", 0, 0), ("mesh_lantern.png", 1, 0)):
    picture(name, 97.9 + col * 5.3, MAIN - 5.6 + row * 5.7, 4.9, 5.4)
caption(103.0, MAIN - 7.6, "props and enemies")
arrow(108.5, MAIN, 112.0, MAIN)

# ---- hero lane: face, then rig ---------------------------------------------------------
arrow(71.5, MAIN - 9.0, 71.5, HERO + 4.7)
ax.text(72.6, MAIN - 11.6, "hero only", ha="left", va="center", fontsize=9.5, fontstyle="italic", color=INK)
process(69.5, HERO, "blender", "Face projection", r"concept $\rightarrow$ head texture", "7 s · CPU only")
ax.plot([69.5, 69.5], [HERO - 4.6, HERO - 8.0], ls=(0, (1, 2)), lw=1.0, color=MUTED, zorder=1)
picture("face_before.png", 62.9, HERO - 14.6, 6.3, 6.3)
picture("face_after.png", 69.8, HERO - 14.6, 6.3, 6.3)
caption(66.05, HERO - 15.8, "before")
caption(72.95, HERO - 15.8, "after")
arrow(77.0, HERO, 80.0, HERO)
process(87.5, HERO, "rig", "UniRig", "skeleton · skin · 5 clips", "1.8 min · 7.0 GB")
arrow(95.0, HERO, 97.5, HERO)
frame(97.5, HERO - 4.4, 11.0, 8.8)
picture("rig_1.png", 97.8, HERO - 4.1, 5.1, 8.2)
picture("rig_2.png", 103.1, HERO - 4.1, 5.1, 8.2)
caption(103.0, HERO - 6.0, "rigged hero")
arrow(108.5, HERO, 112.0, HERO)

# ---- music lane ------------------------------------------------------------------------
prompt(MUSIC, "“Light breezy\ncountryside adventure\ntheme, clarinet …”")
arrow(14.0, MUSIC, 16.0, MUSIC)
process(23.5, MUSIC, "music", "YuE2", r"text $\rightarrow$ instrumental loop", "2.7 min · 5.2 GB")
arrow(31.0, MUSIC, 34.0, MUSIC)
frame(34.0, MUSIC - 3.6, 24.0, 7.2)
wave = json.loads((PARTS / "waveform.json").read_text())
env = np.asarray(wave["rms_envelope"])
xs = np.linspace(34.9, 57.1, len(env))
ax.fill_between(xs, MUSIC - env * 3.0, MUSIC + env * 3.0, color=STYLE["music"][1], lw=0, zorder=2)
caption(46.0, MUSIC - 5.2, f"field theme, {wave['seconds']:.0f} s loop")
arrow(58.0, MUSIC, 112.0, MUSIC)

# ---- what it cost (fills the space the hero lane leaves on the left) ---------------------
ax.add_patch(FancyBboxPatch((16.0, HERO - 8.3), 40.0, 16.6, boxstyle="round,pad=0,rounding_size=1.3",
                            fc="white", ec="#9A9A9A", lw=0.9, linestyle=(0, (4, 3)), zorder=1))
ax.text(18.0, HERO + 5.6, "Measured on one RTX 2080 Ti (11 GB)", ha="left", va="center",
        fontsize=10.5, fontweight="bold", color=INK)
rows = (("All assets of the demo: 20 images, 18 models, 1 rig, 2 tracks",
         "71 GPU-minutes · 251 Wh · \\$0.04 of electricity"),
        ("Autumn make-over driven by a cheap model (DeepSeek V4.1 Flash)",
         "31.5 min · \\$0.03 of API calls + \\$0.01 of electricity"))
for i, (what, cost) in enumerate(rows):
    y = HERO + 2.2 - i * 5.6
    ax.text(18.0, y, what, ha="left", va="center", fontsize=9.3, color=INK)
    ax.text(18.0, y - 2.3, cost, ha="left", va="center", fontsize=9.3, fontstyle="italic", color=MUTED)

# ---- the game ----------------------------------------------------------------------------
rounded(112.0, 4.0, 15.0, 52.0, "game", lw=1.5, radius=1.6)
ax.text(119.5, 53.5, "three.js game", ha="center", va="center", fontsize=12, fontweight="bold", color=INK, zorder=3)
picture("game_fight.jpg", 112.7, 43.6, 13.6, 7.65, z=3)
picture("game_gate.jpg", 112.7, 35.2, 13.6, 7.65, z=3)
ax.text(119.5, 33.2, "Lantern Meadow", ha="center", va="center", fontsize=9.5, fontstyle="italic", color=MUTED, zorder=3)
for i, line in enumerate(("asset manifest", "toon shading, outline", "grass, water, sky")):
    ax.text(119.5, 29.4 - i * 2.6, line, ha="center", va="center", fontsize=9.5, color=INK, zorder=3)
ax.add_patch(FancyBboxPatch((113.1, 6.6), 12.8, 12.6, boxstyle="round,pad=0,rounding_size=1.0",
                            fc="white", ec=STYLE["game"][1], lw=1.0, zorder=3))
ax.text(119.5, 16.5, "play-through test", ha="center", va="center", fontsize=10.5, fontweight="bold", color=INK, zorder=4)
ax.text(119.5, 13.7, "headless browser", ha="center", va="center", fontsize=9.2, color=INK, zorder=4)
ax.text(119.5, 11.3, "walk · fight · light · win", ha="center", va="center", fontsize=9.2, color=INK, zorder=4)
ax.text(119.5, 8.7, "156 assertions", ha="center", va="center", fontsize=9, fontstyle="italic", color=MUTED, zorder=4)

# ---- footer ------------------------------------------------------------------------------
ax.text(56.0, 1.3, "Every stage is one command that prints one line of JSON, and every GPU job is metered into a cost ledger.",
        ha="center", va="center", fontsize=9.2, color=MUTED)

for suffix, kw in ((".png", {"dpi": 200}), (".svg", {}), (".pdf", {})):
    fig.savefig(HERE / f"pipeline{suffix}", facecolor="white", **kw)
print("wrote", HERE / "pipeline.png")
