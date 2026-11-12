#!/usr/bin/env python3
"""Draw the timeline of the cheap-model test (docs/figures/agent-test-timeline.png, .svg, .pdf).

    python docs/figures/make_agent_test_figure.py [results-dir]

Reads timeline.json from tests/agent/results/<model>/ (default: deepseek-v4.1-flash).
That file is a digest of the agent's event log: when each turn and each tool call
started and ended, and the GPU jobs from the run's own cost ledger. Needs matplotlib.
Same typeface as the pipeline figure: Nimbus Roman, the Times of the LaTeX templates.
"""
import glob
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch, Rectangle

HERE = Path(__file__).resolve().parent
RESULTS = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent.parent / "tests/agent/results/deepseek-v4.1-flash"

for path in glob.glob("/usr/share/fonts/opentype/urw-base35/NimbusRoman-*.otf"):
    font_manager.fontManager.addfont(path)
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Nimbus Roman", "Times New Roman", "TeX Gyre Termes", "Times", "STIXGeneral", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "svg.fonttype": "path",
    "pdf.fonttype": 42,
})

INK, MUTED = "#222222", "#555555"
FILL = {"image": "#DDEBF7", "mesh": "#FCE5CD", "blender": "#E2F0D9", "rig": "#E8E0F3", "music": "#F9DEDC",
        "game": "#FFF6D6", "agent": "#EDEDED"}
EDGE = {"image": "#3B75AF", "mesh": "#D9822B", "blender": "#5B9A46", "rig": "#7B5EA7", "music": "#C0504D",
        "game": "#BF9000", "agent": "#8A8A8A"}

data = json.loads((RESULTS / "timeline.json").read_text())
turns, calls, jobs = data["turns"], data["tool_calls"], data["gpu_jobs"]
m = lambda seconds: seconds / 60.0
end = max(t["end_s"] for t in turns)

fig = plt.figure(figsize=(12.8, 4.3))
ax = fig.add_axes([0.105, 0.215, 0.885, 0.675])
ax.set_xlim(-0.3, m(end) + 0.5)
ax.set_ylim(-0.62, 4.05)
LANES = {"agent": 3.25, "calls": 2.25, "gpu": 1.15, "cpu": 0.1}
H = 0.56


def bar(lane, start, stop, kind, label=None, size=10.5, hatch=None, lw=1.1, h=H, alpha=1.0, color=INK):
    y = LANES[lane]
    ax.add_patch(Rectangle((m(start), y - h / 2), m(stop - start), h, facecolor=FILL[kind], edgecolor=EDGE[kind],
                           linewidth=lw, hatch=hatch, alpha=alpha, zorder=3))
    if label:
        ax.text(m((start + stop) / 2), y, label, ha="center", va="center", fontsize=size, color=color, zorder=5,
                linespacing=1.15)


def note(lane, at, text, dx=0.0, above=True, size=9.5, ha="center", rise=0.17):
    y = LANES[lane] + (H / 2 if above else -H / 2)
    ty = y + (rise if above else -rise)
    ax.plot([m(at), m(at) + dx], [y, ty], color=MUTED, lw=0.7, zorder=4)
    ax.text(m(at) + dx, ty + (0.02 if above else -0.02), text, ha=ha, va="bottom" if above else "top",
            fontsize=size, color=MUTED, linespacing=1.1)


# ---- agent turns ---------------------------------------------------------------
for t in turns:
    cost = f"\\${t['api_cost_usd']:.3f}"
    wide = t["end_s"] - t["start_s"] > 400
    text = f"turn {t['turn']}: {t['steps']} model steps, {cost}" if wide else f"turn {t['turn']}"
    bar("agent", t["start_s"], t["end_s"], "agent", text)
note("agent", (turns[0]["start_s"] + turns[0]["end_s"]) / 2,
     f"{turns[0]['steps']} steps, \\${turns[0]['api_cost_usd']:.3f}", above=True)
gap0, gap1 = turns[0]["end_s"], turns[1]["start_s"]
ax.add_patch(Rectangle((m(gap0), LANES["agent"] - H / 2), m(gap1 - gap0), H, facecolor="white", edgecolor="#B0B0B0",
                       linewidth=0.9, hatch="////", zorder=2))
ax.text(m((gap0 + gap1) / 2), LANES["agent"] - H / 2 - 0.07,
        "the model ended its turn to wait;\nour harness failed to resume it", ha="center", va="top",
        fontsize=9.5, color=MUTED, linespacing=1.1)

# ---- tool calls ----------------------------------------------------------------
KIND = {"wait": ("#FFFFFF", "#8A8A8A"), "shell": ("#6E6E6E", "#6E6E6E"), "look": ("#3B75AF", "#3B75AF"),
        "write": ("#D9822B", "#D9822B"), "check": ("#BF9000", "#BF9000")}


def kind_of(call):
    what = call["what"]
    if call["tool"] == "read":
        return "look"
    if call["tool"] == "write":
        return "write"
    if "forge check" in what:
        return "check"
    if "forge wait" in what and call["end_s"] - call["start_s"] > 20:
        return "wait"
    return "shell"


for c in calls:
    k = kind_of(c)
    fill, edge = KIND[k]
    width = max(m(c["end_s"] - c["start_s"]), 0.075)
    ax.add_patch(Rectangle((m(c["start_s"]), LANES["calls"] - H / 2), width, H, facecolor=fill, edgecolor=edge,
                           linewidth=0.8, zorder=3 if k != "wait" else 2))

# ---- GPU jobs ------------------------------------------------------------------
gib = lambda mib: f"{mib / 1024:.1f} GB"
by_stage = {}
for j in jobs:
    by_stage.setdefault(j["stage"], []).append(j)
img = by_stage["image"][0]
bar("gpu", img["start_s"], img["end_s"], "image", f"5 images\n{m(img['seconds']):.1f} min, {gib(img['peak_mem_mib'])}", size=9.5)
mesh = by_stage["image_to_3d"][0]
bar("gpu", mesh["start_s"], mesh["end_s"], "mesh",
    f"4 meshes (hero at 1536, three at 1024): {m(mesh['seconds']):.1f} min, peak {gib(mesh['peak_mem_mib'])}")
for j in by_stage["rig"]:
    bar("gpu", j["start_s"], j["end_s"], "rig")
rig_s = sum(j["seconds"] for j in by_stage["rig"])
rig_mid = (by_stage["rig"][0]["start_s"] + by_stage["rig"][-1]["end_s"]) / 2
ax.text(m(rig_mid), LANES["gpu"] + H / 2 + 0.06,
        f"rig: {rig_s:.0f} s, {gib(max(j['peak_mem_mib'] for j in by_stage['rig']))}",
        ha="center", va="bottom", fontsize=9.5, color=MUTED)
bgm = by_stage["bgm"][0]
bar("gpu", bgm["start_s"], bgm["end_s"], "music",
    f"music\n{m(bgm['seconds']):.1f} min, {gib(bgm['peak_mem_mib'])}", size=9.5)

# ---- CPU steps (from the tool calls themselves) ----------------------------------
def find(text, tool="bash", nth=0):
    hits = [c for c in calls if c["tool"] == tool and text in c["what"]]
    return hits[nth]


review = find("forge review")
face = find("forge face")
install = find("forge install")
checks = [c for c in calls if "forge check" in c["what"]]
report = find("REPORT.md", tool="write")
bar("cpu", review["start_s"], review["end_s"], "blender")
bar("cpu", face["start_s"], face["end_s"], "blender")
bar("cpu", install["start_s"], install["end_s"], "blender")
for c in checks:
    bar("cpu", c["start_s"], c["end_s"], "game")
bar("cpu", report["start_s"] - 4, report["end_s"] + 4, "agent")
ax.text(m(install["end_s"]) + 0.05, LANES["cpu"] + H / 2 + 0.06, "review sheet, face projection, install",
        ha="right", va="bottom", fontsize=9.5, color=MUTED)
ax.text(m(end) - 0.12, LANES["cpu"] + H / 2 + 0.06,
        "play-through check, three runs of 44 s, all pass", ha="right", va="bottom", fontsize=9.5, color=MUTED)
note("cpu", report["start_s"], "writes REPORT.md", dx=-0.25, above=False, ha="right", rise=0.12)

# ---- axes ----------------------------------------------------------------------
for name, text in (("agent", "Agent"), ("calls", "Tool calls"), ("gpu", "GPU jobs\n(RTX 2080 Ti)"), ("cpu", "CPU steps")):
    ax.text(-0.75, LANES[name], text, ha="right", va="center", fontsize=11.5, color=INK, linespacing=1.1)
ax.set_yticks([])
ax.set_xticks(range(0, int(m(end)) + 1, 5))
ax.set_xticks(range(0, int(m(end)) + 2), minor=True)
ax.tick_params(axis="x", labelsize=10.5, colors=INK, length=4)
ax.tick_params(axis="x", which="minor", length=2, colors=MUTED)
ax.set_xlabel("minutes since the agent started", fontsize=11.5, color=INK, labelpad=3)
for side in ("top", "right", "left"):
    ax.spines[side].set_visible(False)
ax.spines["bottom"].set_color(MUTED)
for x in range(0, int(m(end)) + 1, 5):
    ax.axvline(x, color="#E6E6E6", lw=0.7, zorder=0)
ax.axvline(m(end), color=MUTED, lw=0.8, ls=(0, (3, 3)), zorder=1)
ax.text(m(end) - 0.12, LANES["agent"] + H / 2 + 0.19, f"done at {m(end):.1f} min", ha="right", va="bottom",
        fontsize=9.5, color=MUTED)

handles = [Patch(facecolor=KIND[k][0], edgecolor=KIND[k][1], label=label) for k, label in (
    ("shell", "shell command"), ("wait", "waiting on a background job"), ("look", "looking at an image"),
    ("write", "writing a file"), ("check", "running the game's check"))]
fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, fontsize=10, handlelength=1.3,
           columnspacing=1.6, bbox_to_anchor=(0.54, 0.0))

total_calls = len(calls)
total_steps = sum(t["steps"] for t in turns)
total_cost = sum(t["api_cost_usd"] for t in turns)
wh = sum(j["watt_hours"] for j in jobs)
fig.text(0.085, 0.955,
         f"One run of the autumn make-over task: {total_steps} model steps, {total_calls} tool calls, "
         f"\\${total_cost:.2f} of API cost, {wh:.1f} Wh on the GPU, no human input.",
         fontsize=12, color=INK, ha="left", va="center")

for ext in ("png", "svg", "pdf"):
    fig.savefig(HERE / f"agent-test-timeline.{ext}", dpi=200, facecolor="white")
print(HERE / "agent-test-timeline.png")
