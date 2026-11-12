#!/usr/bin/env python3
"""Draw what the running game costs the player's machine (docs/figures/game-runtime.png, .svg, .pdf).

    python docs/figures/make_runtime_figure.py [game-runtime.json]

Reads the report written by game/scripts/measure.mjs: one row per scenario, each a
60-second real-time play session in headless Chromium, sampled once a second.
Needs matplotlib. Same typeface as the other figures: Nimbus Roman.
"""
import glob
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "game-runtime.json"

for path in glob.glob("/usr/share/fonts/opentype/urw-base35/NimbusRoman-*.otf"):
    font_manager.fontManager.addfont(path)
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Nimbus Roman", "Times New Roman", "TeX Gyre Termes", "Times", "STIXGeneral", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "svg.fonttype": "path",
    "pdf.fonttype": 42,
})
INK, MUTED, GRID = "#222222", "#555555", "#E6E6E6"
BLUE, ORANGE, GREEN, PURPLE, RED, GOLD, GREY = "#3B75AF", "#D9822B", "#5B9A46", "#7B5EA7", "#C0504D", "#BF9000", "#8A8A8A"
LIGHT = {BLUE: "#DDEBF7", ORANGE: "#FCE5CD", GREEN: "#E2F0D9", PURPLE: "#E8E0F3", RED: "#F9DEDC", GOLD: "#FFF6D6", GREY: "#EDEDED"}

report = json.loads(SRC.read_text())
rows = report["scenarios"]
NAMES = {
    "title-720p": "title screen\n1280×720",
    "play-720p": "playing\n1280×720",
    "play-1080p": "playing\n1920×1080",
    "play-phone": "playing, phone layout\n390×844 at 2×",
    "play-720p-uncapped": "playing, 1280×720\nframe rate not capped",
    "play-720p-software": "playing, 1280×720\nno GPU (software)",
}
labels = [NAMES.get(r["name"], r["label"]) for r in rows]
y = list(range(len(rows)))[::-1]


def group(r, key):
    """Split a per-process quantity into page, GPU process and the rest of the browser."""
    page = gpu = rest = 0.0
    for name, p in r["byProcess"].items():
        v = p[key]
        if name == "renderer":
            page += v
        elif name == "gpu-process":
            gpu += v
        else:
            rest += v
    return page, gpu, rest


fig, axes = plt.subplots(1, 5, figsize=(12.8, 4.5), sharey=True,
                         gridspec_kw={"left": 0.145, "right": 0.985, "top": 0.80, "bottom": 0.16, "wspace": 0.16})


def style(ax, title, unit, xmax):
    ax.set_title(title, fontsize=12, color=INK, pad=24, loc="left")
    ax.text(0, 1.035, unit, transform=ax.transAxes, fontsize=10, color=MUTED, ha="left", va="bottom")
    ax.set_xlim(0, xmax)
    ax.set_ylim(-1.0, len(rows) - 0.4)
    ax.tick_params(axis="x", labelsize=10, colors=INK, length=3)
    ax.tick_params(axis="y", length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.xaxis.grid(True, color=GRID, lw=0.7)
    ax.set_axisbelow(True)


def broken(ax, yi, at, xmax):
    """Two slanted strokes: this bar is longer than the axis."""
    for dx in (0.0, 0.035):
        x = at - xmax * (0.05 + dx)
        ax.plot([x - xmax * 0.012, x + xmax * 0.012], [yi - 0.36, yi + 0.36], color="white", lw=3.2, zorder=5, solid_capstyle="butt")
        ax.plot([x - xmax * 0.012, x + xmax * 0.012], [yi - 0.36, yi + 0.36], color=INK, lw=0.8, zorder=6)


def stacked(ax, parts, colours, fmt, xmax, cap=None):
    for yi, values in zip(y, parts):
        total = sum(values)
        scale = cap / total if cap and total > cap else 1.0
        left = 0.0
        for v, c in zip(values, colours):
            ax.barh(yi, v * scale, left=left, height=0.56, color=LIGHT[c], edgecolor=c, linewidth=1.0)
            left += v * scale
        if scale < 1.0:
            broken(ax, yi, left, xmax)
        ax.text(left + xmax * 0.02, yi, fmt(total), va="center", ha="left", fontsize=10.5, color=INK)


def single(ax, values, colour, fmt, xmax, skip=None, cap=None):
    for yi, v, r in zip(y, values, rows):
        if skip and skip(r):
            ax.text(xmax * 0.02, yi, "none", va="center", ha="left", fontsize=10.5, color=MUTED)
            continue
        shown = min(v, cap) if cap else v
        ax.barh(yi, shown, height=0.56, color=LIGHT[colour], edgecolor=colour, linewidth=1.0)
        if shown < v:
            broken(ax, yi, shown, xmax)
        ax.text(shown + xmax * 0.02, yi, fmt(v), va="center", ha="left", fontsize=10.5, color=INK)


def room(values):
    return max(values) * 1.32 if max(values) > 0 else 1.0


# 1. frame rate
fps = [r["fps"] for r in rows]
style(axes[0], "Frame rate", "frames per second", 100)
single(axes[0], fps, GOLD, lambda v: f"{v:,.0f}", 100, cap=72)
axes[0].axvline(60, color=MUTED, lw=0.8, ls=(0, (3, 3)), zorder=0)
axes[0].set_yticks(y)
axes[0].set_yticklabels(labels, fontsize=10.5, color=INK, linespacing=1.1)

# 2. CPU
cpu = [group(r, "cpuCores") for r in rows]
style(axes[1], "CPU", "cores busy, of %d" % report["machine"]["logicalCores"], 1.4)
stacked(axes[1], cpu, (BLUE, ORANGE, GREY), lambda v: f"{v:.2f}" if v < 10 else f"{v:.1f}", 1.4, cap=1.0)

# 3. memory
mem = [group(r, "memPssMb") for r in rows]
xmax = room([sum(m) for m in mem])
style(axes[2], "Memory", "MB of RAM (PSS)", xmax)
stacked(axes[2], mem, (BLUE, ORANGE, GREY), lambda v: f"{v:.0f}", xmax)
empty = report.get("emptyTab")
if empty:
    axes[2].axvline(empty["memPssMb"], color=MUTED, lw=0.8, ls=(0, (3, 3)), zorder=0)
    axes[2].text(empty["memPssMb"], -0.52, " empty tab", fontsize=9.5, color=MUTED, ha="left", va="top")

# 4. GPU utilisation
no_gpu = lambda r: r["gpu"] is None
util = [(r["gpu"] or {}).get("utilPct", 0) for r in rows]
style(axes[3], "GPU load", "percent busy", room(util + [1]))
single(axes[3], util, PURPLE, lambda v: f"{v:.0f} %", room(util + [1]), skip=no_gpu)

# 5. GPU memory
vram = [(r["gpu"] or {}).get("memMiBAboveIdle", 0) for r in rows]
style(axes[4], "GPU memory", "MiB above idle", room(vram + [1]))
single(axes[4], vram, RED, lambda v: f"{v:.0f}", room(vram + [1]), skip=no_gpu)
if empty:
    axes[4].axvline(empty["gpuMemMiBAboveIdle"], color=MUTED, lw=0.8, ls=(0, (3, 3)), zorder=0)
    axes[4].text(empty["gpuMemMiBAboveIdle"], -0.52, " empty tab", fontsize=9.5, color=MUTED, ha="left", va="top")

handles = [plt.Rectangle((0, 0), 1, 1, facecolor=LIGHT[c], edgecolor=c) for c in (BLUE, ORANGE, GREY)]
fig.legend(handles, ["the page (game code)", "browser's GPU process", "rest of the browser"], loc="lower center", ncol=3,
           frameon=False, fontsize=10.5, handlelength=1.3, columnspacing=2.0, bbox_to_anchor=(0.47, 0.0))

card = next((r["gpu"]["name"] for r in rows if r["gpu"]), "no GPU").replace("NVIDIA GeForce ", "")
cpu_name = report["machine"]["cpu"].replace("(R)", "").replace("CPU ", "").replace("  ", " ")
fig.text(0.012, 0.955, f"The running game in Chromium, {report['secondsPerScenario']} s of real-time play per row. "
         f"Graphics on the {card}, processor {cpu_name}.", fontsize=12, color=INK, ha="left", va="center")

for ext in ("png", "svg", "pdf"):
    fig.savefig(HERE / f"game-runtime.{ext}", dpi=200, facecolor="white")
print(HERE / "game-runtime.png")
