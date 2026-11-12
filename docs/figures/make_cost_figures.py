#!/usr/bin/env python3
"""Draw the cost figures (docs/figures/cost-*.png and .svg) and write cost-numbers.json.

    python docs/figures/make_cost_figures.py

Reads only files in this repository:
  cost/ledger.jsonl                         every GPU job of the building day (tools/gpu_run.sh)
  assets/models/raw/*.json, raw-512/*.json  per-asset reports of the image-to-3D stage
  cost/agent-tokens.json                    token use of the session that built the repository
  tests/agent/results/deepseek-v4.1-flash/  the cheap-model run (summary, ledger, timeline)
  docs/setup.md                             one-time setup numbers (copied below, with their labels)

Every number shown in a figure or quoted in docs/cost-analysis.md is written to
docs/figures/cost-numbers.json, so the text and the figures cannot drift apart.
Needs matplotlib and numpy. Typeface: Nimbus Roman, the Times of the LaTeX templates.
"""
import glob
import json
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CHEAP = ROOT / "tests/agent/results/deepseek-v4.1-flash"

for path in glob.glob("/usr/share/fonts/opentype/urw-base35/NimbusRoman-*.otf"):
    font_manager.fontManager.addfont(path)
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Nimbus Roman", "Times New Roman", "TeX Gyre Termes", "Times", "STIXGeneral", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "svg.fonttype": "path",
    "pdf.fonttype": 42,
    "font.size": 10.5,
    "axes.edgecolor": "#555555",
    "axes.labelcolor": "#222222",
    "xtick.color": "#222222",
    "ytick.color": "#222222",
    "axes.titlesize": 12,
    "axes.titleweight": "normal",
    "axes.titlelocation": "left",
})

INK, MUTED, GRID = "#222222", "#555555", "#E6E6E6"
FILL = {"image": "#DDEBF7", "mesh": "#FCE5CD", "blender": "#E2F0D9", "rig": "#E8E0F3", "music": "#F9DEDC",
        "game": "#FFF6D6", "neutral": "#EDEDED"}
EDGE = {"image": "#3B75AF", "mesh": "#D9822B", "blender": "#5B9A46", "rig": "#7B5EA7", "music": "#C0504D",
        "game": "#BF9000", "neutral": "#8A8A8A"}
STAGE_KIND = {"image": "image", "image_to_3d": "mesh", "rig": "rig", "bgm": "music", "check": "neutral"}
KWH = 0.15  # US dollars per kWh, used everywhere in this repository
N = {"kwh_price_usd": KWH}  # every number that appears in the figures or in the text


def usd(wh):
    return wh / 1000.0 * KWH


def mmss(seconds):
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    return f"{s // 60} min {s % 60:02d} s"


def clean(ax, left=False):
    for side in ("top", "right") + (("left",) if not left else ()):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=3, labelsize=10)


def save(fig, name):
    for ext in ("png", "svg"):
        fig.savefig(HERE / f"cost-{name}.{ext}", dpi=200, facecolor="white")
    plt.close(fig)
    print(HERE / f"cost-{name}.png")


# =====================================================================================
# Data
# =====================================================================================
ledger = [json.loads(line) for line in (ROOT / "cost/ledger.jsonl").read_text().splitlines() if line.strip()]
for i, j in enumerate(ledger):
    j["i"] = i
    j["t0"] = datetime.fromisoformat(j["time"])
    j["card"] = "2080 Ti" if "2080" in j["gpu_name"] else "3090"

N["day_jobs"] = len(ledger)
N["day_gpu_min"] = round(sum(j["seconds"] for j in ledger) / 60, 1)
N["day_wh"] = round(sum(j["watt_hours"] for j in ledger), 1)
N["day_usd"] = round(usd(sum(j["watt_hours"] for j in ledger)), 4)
N["day_nonzero_exit_jobs"] = sum(j["exit"] != 0 for j in ledger)
N["day_nonzero_exit_min"] = round(sum(j["seconds"] for j in ledger if j["exit"]) / 60, 1)
N["day_nonzero_exit_wh"] = round(sum(j["watt_hours"] for j in ledger if j["exit"]), 1)
N["day_estimated_jobs"] = [j["i"] for j in ledger if "note" in j]
for card in ("2080 Ti", "3090"):
    k = card.replace(" ", "").lower()
    N[f"day_{k}_jobs"] = sum(j["card"] == card for j in ledger)
    N[f"day_{k}_min"] = round(sum(j["seconds"] for j in ledger if j["card"] == card) / 60, 1)
    N[f"day_{k}_wh"] = round(sum(j["watt_hours"] for j in ledger if j["card"] == card), 1)
N["day_first_job"] = ledger[0]["t0"].strftime("%H:%M")
N["day_last_job"] = max(j["t0"] for j in ledger).strftime("%H:%M")

# the jobs that made the demo's final assets on the RTX 2080 Ti (0-based ledger lines)
FINAL = {"images": [63], "hero mesh": [119], "prop meshes": [128], "rig": [124, 125], "music": [52, 65]}
REPLACED = {"sky image (2080 Ti)": 68, "hero concept (3090)": 84}
final_ids = [i for ids in FINAL.values() for i in ids]
assert all(ledger[i]["card"] == "2080 Ti" and ledger[i]["exit"] == 0 for i in final_ids)
stage_rows = []
for name, ids in FINAL.items():
    js = [ledger[i] for i in ids]
    stage_rows.append({"stage": name, "ids": ids, "seconds": sum(j["seconds"] for j in js),
                       "wh": sum(j["watt_hours"] for j in js), "peak_mib": max(j["peak_mem_mib"] for j in js)})
fin_s = sum(r["seconds"] for r in stage_rows)
fin_wh = sum(r["wh"] for r in stage_rows)
N["final_jobs"] = final_ids
N["final_seconds"] = round(fin_s, 1)
N["final_min"] = round(fin_s / 60, 1)
N["final_wh"] = round(fin_wh, 1)
N["final_usd"] = round(usd(fin_wh), 4)
for r in stage_rows:
    k = r["stage"].replace(" ", "_")
    N[f"final_{k}_seconds"] = round(r["seconds"], 1)
    N[f"final_{k}_min"] = round(r["seconds"] / 60, 2)
    N[f"final_{k}_wh"] = round(r["wh"], 1)
    N[f"final_{k}_peak_gib"] = round(r["peak_mib"] / 1024, 2)
    N[f"final_{k}_share_time_pct"] = round(100 * r["seconds"] / fin_s, 1)
    N[f"final_{k}_share_energy_pct"] = round(100 * r["wh"] / fin_wh, 1)
mesh_s = sum(r["seconds"] for r in stage_rows if "mesh" in r["stage"])
mesh_wh = sum(r["wh"] for r in stage_rows if "mesh" in r["stage"])
N["final_image_to_3d_share_time_pct"] = round(100 * mesh_s / fin_s, 1)
N["final_image_to_3d_share_energy_pct"] = round(100 * mesh_wh / fin_wh, 1)
rep_s = sum(ledger[i]["seconds"] for i in REPLACED.values())
rep_wh = sum(ledger[i]["watt_hours"] for i in REPLACED.values())
for name, i in REPLACED.items():
    k = "sky" if "sky" in name else "hero_concept"
    N[f"replacement_{k}_line"] = i
    N[f"replacement_{k}_seconds"] = round(ledger[i]["seconds"], 1)
    N[f"replacement_{k}_wh"] = round(ledger[i]["watt_hours"], 1)
N["final_with_replacements_min"] = round((fin_s + rep_s) / 60, 1)
N["final_with_replacements_wh"] = round(fin_wh + rep_wh, 1)
N["final_with_replacements_usd"] = round(usd(fin_wh + rep_wh), 4)
N["final_share_of_day_min_pct"] = round(100 * fin_s / 60 / N["day_gpu_min"], 1)
N["final_share_of_day_wh_pct"] = round(100 * fin_wh / N["day_wh"], 1)

# per-asset reports of the image-to-3D stage
def load_reports(folder):
    out = {}
    for f in sorted(glob.glob(str(ROOT / folder / "*.json"))):
        j = json.loads(Path(f).read_text())
        out[j["id"]] = j
    return out


r1024 = load_reports("assets/models/raw")
r512 = load_reports("assets/models/raw-512")
props = sorted(k for k in r1024 if k != "hero")
assert sorted(r512) == props
oom = [k for k in props if r1024[k].get("out_of_memory_at")]
N["props_count"] = len(props)
N["props_oom"] = oom
N["props_1024_item_s_sum"] = round(sum(r1024[k]["item_s"] for k in props), 1)
N["props_512_item_s_sum"] = round(sum(r512[k]["item_s"] for k in props), 1)
for k in oom:
    j = r1024[k]
    lost = j["item_s"] - j["generate_s"] - j["export_s"] - j.get("preprocess_s", 0) - j.get("preview_s", 0)
    N[f"oom_{k}_item_s"] = round(j["item_s"], 1)
    N[f"oom_{k}_effective_resolution"] = j["effective_resolution"]
    N[f"oom_{k}_lost_s"] = round(lost, 0)
    N[f"oom_{k}_512_batch_item_s"] = round(r512[k]["item_s"], 1)
slow = max(props, key=lambda k: r1024[k]["item_s"] if k not in oom else 0)
fast = min(props, key=lambda k: r1024[k]["item_s"])
N["props_1024_slowest"] = slow
N["props_1024_slowest_s"] = round(r1024[slow]["item_s"], 1)
N["props_1024_slowest_tokens"] = r1024[slow]["shape_tokens"]
N["props_1024_slowest_peak_gib"] = r1024[slow]["torch_peak_reserved_gib"]
N["props_1024_fastest"] = fast
N["props_1024_fastest_s"] = round(r1024[fast]["item_s"], 1)
ok1024 = [k for k in props if k not in oom]
x_tok = np.array([r1024[k]["shape_tokens"] for k in ok1024], float)
y_gen = np.array([r1024[k]["generate_s"] for k in ok1024], float)
slope, icpt = np.polyfit(x_tok, y_gen, 1)
r2 = np.corrcoef(x_tok, y_gen)[0, 1] ** 2
N["props_1024_ok_count"] = len(ok1024)
N["props_512_item_s_min"] = round(min(r512[k]["item_s"] for k in props), 1)
N["props_512_item_s_max"] = round(max(r512[k]["item_s"] for k in props), 1)
N["props_512_shape_tokens_max"] = max(r512[k]["shape_tokens"] for k in props)
N["props_1024_slow_fast_ratio"] = round(r1024[slow]["item_s"] / r1024[fast]["item_s"], 1)
N["fit_generate_s_per_1000_tokens"] = round(slope * 1000, 2)
N["fit_intercept_s"] = round(icpt, 1)
N["fit_r2"] = round(r2, 3)
mem = np.array([r1024[k]["torch_peak_reserved_gib"] for k in ok1024])
N["props_1024_peak_reserved_gib_max"] = float(mem.max())
N["props_1024_peak_reserved_gib_min"] = float(mem.min())
N["gpu_total_gib_pytorch"] = r1024[props[0]]["gpu_total_gib"]
N["hero_1536_item_s"] = round(r1024["hero"]["item_s"], 1)
N["hero_1536_faces"] = r1024["hero"]["mesh"]["faces"]
b512, b1024 = ledger[118], ledger[128]
N["batch512_line"], N["batch1024_line"] = 118, 128
N["batch512_seconds"], N["batch1024_seconds"] = b512["seconds"], b1024["seconds"]
N["batch512_mmss"], N["batch1024_mmss"] = mmss(b512["seconds"]), mmss(b1024["seconds"])
N["batch512_wh"], N["batch1024_wh"] = round(b512["watt_hours"], 1), round(b1024["watt_hours"], 1)
N["batch512_peak_gib"] = round(b512["peak_mem_mib"] / 1024, 2)
N["batch1024_peak_gib"] = round(b1024["peak_mem_mib"] / 1024, 2)
N["batch_time_ratio"] = round(b1024["seconds"] / b512["seconds"], 1)
N["batch_energy_ratio"] = round(b1024["watt_hours"] / b512["watt_hours"], 1)
N["day_2080ti_max_peak_gib"] = round(max(j["peak_mem_mib"] for j in ledger if j["card"] == "2080 Ti") / 1024, 2)

# tokens of the building session
tok = json.loads((ROOT / "cost/agent-tokens.json").read_text())
prices = tok["prices_usd_per_million_tokens"]
TYPES = [("output", "output (incl. reasoning)"), ("cache_read", "cache read"), ("cache_write", "cache write"),
         ("input", "uncached input")]


def split_usd(row):
    p = prices[row["model"]]
    return {"input": row["input_tokens"] * p["input"] / 1e6,
            "output": row["output_tokens"] * p["output"] / 1e6,
            "cache_read": row["cache_read_input_tokens"] * p["cache_read"] / 1e6,
            "cache_write": (row["cache_write_5m"] * p["cache_write_5m"] + row["cache_write_1h"] * p["cache_write_1h"]) / 1e6}


tot = tok["total"]
type_tokens = {"input": tot["input_tokens"], "output": tot["output_tokens"],
               "cache_read": tot["cache_read_input_tokens"], "cache_write": tot["cache_write_5m"] + tot["cache_write_1h"]}
type_usd = {k: 0.0 for k in type_tokens}
for row in tok["by_agent"]:
    for k, v in split_usd(row).items():
        type_usd[k] += v
N["tok_responses"] = tot["responses"]
N["tok_usd_list"] = tot["usd_at_list_price"]
N["tok_usd_recomputed"] = round(sum(type_usd.values()), 2)
for k in type_tokens:
    N[f"tok_{k}_tokens"] = type_tokens[k]
    N[f"tok_{k}_usd"] = round(type_usd[k], 2)
    N[f"tok_{k}_usd_share_pct"] = round(100 * type_usd[k] / sum(type_usd.values()), 1)
all_tok = sum(type_tokens.values())
N["tok_all_tokens"] = all_tok
N["tok_cache_read_share_pct"] = round(100 * type_tokens["cache_read"] / all_tok, 1)
N["tok_counted_until"] = tok["counted_until_local"]
N["tok_session_start"] = tok["milestones_local"]["session_start"]
N["tok_cheap_test_window"] = tok["milestones_local"]["cheap_model_test"]
hours = sorted({r["hour_local"][11:16] for r in tok["by_agent_and_hour"]})
before = sum(r["usd_at_list_price"] for r in tok["by_agent_and_hour"] if r["hour_local"][11:13] < "17")
after = sum(r["usd_at_list_price"] for r in tok["by_agent_and_hour"] if r["hour_local"][11:13] >= "17")
N["tok_usd_before_17"] = round(before, 2)
N["tok_usd_from_17"] = round(after, 2)
AGENTS = [  # (name in the json, short label, colour kind)
    ("orchestrator", "orchestrator (Fable 5.2)", "image"),
    ("sub-agent: image-to-3D stage", "image-to-3D stage", "mesh"),
    ("sub-agent: rigging stage", "rigging stage", "rig"),
    ("sub-agent: music stage", "music stage", "music"),
    ("sub-agent: game", "game", "game"),
    ("sub-agent: setup scripts and guide", "setup scripts and guide", "blender"),
    ("sub-agent: background research", "background research", "neutral"),
]
by_agent = {r["agent"]: r for r in tok["by_agent"]}
for name, short, _ in AGENTS:
    r = by_agent[name]
    k = short.split(" (")[0].replace(" ", "_").replace("-", "_")
    N[f"agent_{k}_usd"] = r["usd_at_list_price"]
    N[f"agent_{k}_responses"] = r["responses"]
    N[f"agent_{k}_output_tokens"] = r["output_tokens"]
    N[f"agent_{k}_window"] = [r["first_local"], r["last_local"]]
N["tok_subagents_usd"] = round(sum(by_agent[a]["usd_at_list_price"] for a, _, _ in AGENTS[1:]), 2)
N["tok_peak_hour"] = max(hours, key=lambda h: sum(r["usd_at_list_price"] for r in tok["by_agent_and_hour"]
                                                    if r["hour_local"][11:16] == h))
N["tok_peak_hour_usd"] = round(sum(r["usd_at_list_price"] for r in tok["by_agent_and_hour"]
                                   if r["hour_local"][11:16] == N["tok_peak_hour"]), 2)
N["published_shooter_usd"] = 550
N["published_shooter_output_tokens"] = 2.7e6
N["published_shooter_input_tokens"] = 445e6
N["published_shooter_hours"] = 8

# the cheap-model run
cs = json.loads((CHEAP / "agent-summary.json").read_text())
cl = [json.loads(line) for line in (CHEAP / "ledger.jsonl").read_text().splitlines() if line.strip()]
ct = json.loads((CHEAP / "timeline.json").read_text())
CHEAP_PRICE = {"input": 0.15, "output": 0.60}  # US dollars per million tokens, listed price of the model
ctok = cs["tokens"]
c_in = ctok["input"] * CHEAP_PRICE["input"] / 1e6
c_out = (ctok["output"] + ctok["reasoning"]) * CHEAP_PRICE["output"] / 1e6
c_cache = cs["api_cost_usd"] - c_in - c_out
c_stage = {}
for j in cl:
    c_stage[j["stage"]] = c_stage.get(j["stage"], 0.0) + j["watt_hours"]
c_wh = sum(c_stage.values())
N["cheap_steps"] = cs["steps"]
N["cheap_tool_calls"] = sum(cs["tool_calls"].values())
N["cheap_wall_s"] = cs["wall_seconds"]
N["cheap_wall_min"] = round(cs["wall_seconds"] / 60, 1)
N["cheap_api_usd"] = cs["api_cost_usd"]
N["cheap_api_input_usd"] = round(c_in, 4)
N["cheap_api_output_usd"] = round(c_out, 4)
N["cheap_api_cache_usd_implied"] = round(c_cache, 4)
N["cheap_cache_price_implied_per_m"] = round(c_cache / ctok["cache_read"] * 1e6, 4)
N["cheap_gpu_jobs"] = len(cl)
N["cheap_gpu_min"] = round(sum(j["seconds"] for j in cl) / 60, 1)
N["cheap_gpu_s"] = round(sum(j["seconds"] for j in cl), 1)
N["cheap_wh"] = round(c_wh, 1)
N["cheap_elec_usd"] = round(usd(c_wh), 4)
N["cheap_total_usd"] = round(cs["api_cost_usd"] + usd(c_wh), 4)
for st, wh in c_stage.items():
    N[f"cheap_elec_{st}_wh"] = round(wh, 2)
    N[f"cheap_elec_{st}_usd"] = round(usd(wh), 5)
for k, v in ctok.items():
    N[f"cheap_tokens_{k}"] = v
c_read = ctok["input"] + ctok["cache_read"]
N["cheap_tokens_read_total"] = c_read
N["cheap_cache_hit_pct"] = round(100 * ctok["cache_read"] / c_read, 1)
for t in ct["turns"]:
    N[f"cheap_turn{t['turn']}_usd"] = t["api_cost_usd"]
    N[f"cheap_turn{t['turn']}_steps"] = t["steps"]
N["cheap_started"] = ct["started"][11:16]
N["ratio_frontier_to_cheap"] = round(tot["usd_at_list_price"] / N["cheap_total_usd"], -2)
N["ratio_frontier_to_final_assets"] = round(tot["usd_at_list_price"] / N["final_usd"], -2)

# one-time setup, copied from docs/setup.md with its labels
GIB = 1024 ** 3 / 1e9
SETUP_TIME = [  # (step, seconds, label, cold seconds or None)
    ("Blender", 54, "measured", None),
    ("image env (krea)", 6 * 60 + 42, "measured", None),
    ("image-to-3D env (trellis2)", 9 * 60 + 38, "measured, most downloads local", 55 * 60),
    ("rigging env (unirig)", 16 * 60 + 47, "measured", None),
    ("music env (yue2)", 8 * 60 + 2, "measured, conda packages local", None),
]
SETUP_WEIGHTS_S_EST = 1.5 * 3600  # "about 100 s per GB at 10 MB/s, so 1.5 h or more" [estimate]
SETUP_DISK = [  # (part, environment GB, weights GB); envs are du -sh in GiB, converted; weights in GB
    ("Blender", 1.2 * GIB, 0.0),
    ("image (krea)", 6.5 * GIB, 9.4 + 8.9),
    ("image-to-3D (trellis2)", 9.0 * GIB + 832 / 1024 * GIB, 16.6),
    ("rigging (unirig)", 8.8 * GIB + 46 / 1024 * GIB, 5.8),
    ("music (yue2)", 8.8 * GIB + 41 / 1024 * GIB, 7.8),
]
SETUP_SECOND_GGUF_GB = 7.5
SETUP_PKG_CACHE_GB = 26 * GIB
N["setup_measured_s"] = sum(s for _, s, _, _ in SETUP_TIME)
N["setup_measured_min"] = round(N["setup_measured_s"] / 60, 1)
N["setup_trellis2_cold_min"] = 55
N["setup_cold_min_with_trellis_cold"] = round((N["setup_measured_s"] - 578 + 55 * 60) / 60, 1)
N["setup_weights_download_h_estimate"] = 1.5
N["setup_env_gb"] = round(sum(e for _, e, _ in SETUP_DISK), 1)
N["setup_weights_gb_one_gguf"] = round(sum(w for _, _, w in SETUP_DISK), 1)
N["setup_disk_gb_one_gguf"] = round(sum(e + w for _, e, w in SETUP_DISK), 1)
N["setup_disk_gb_both_gguf"] = round(N["setup_disk_gb_one_gguf"] + SETUP_SECOND_GGUF_GB, 1)
N["setup_pkg_cache_gb"] = round(SETUP_PKG_CACHE_GB, 1)
N["setup_disk_gb_with_cache"] = round(N["setup_disk_gb_both_gguf"] + SETUP_PKG_CACHE_GB, 1)
N["setup_md_disk_stated_gb"] = 95
N["readme_disk_stated_gb"] = 105
N["session_wall_h_until_count"] = round((datetime.strptime(tok["counted_until_local"], "%H:%M")
                                         - datetime.strptime(tok["milestones_local"]["session_start"], "%H:%M")
                                         ).seconds / 3600, 2)


# =====================================================================================
# Figure 1: overview, dollars on one log axis
# =====================================================================================
def fig_overview():
    fig = plt.figure(figsize=(12.8, 4.6))
    ax = fig.add_axes([0.235, 0.15, 0.70, 0.72])
    rows = [
        ("Electricity for all of the demo's\nfinal assets (RTX 2080 Ti)", [(N["final_usd"], "mesh", None)],
         f"\\${N['final_usd']:.3f}   {N['final_min']:.0f} GPU-min, {N['final_wh']:.0f} Wh"),
        ("Cheap model's whole make-over\n(API + electricity)",
         [(N["cheap_api_usd"], "neutral", None), (N["cheap_total_usd"], "music", None)],
         f"\\${N['cheap_total_usd']:.3f}   = \\${N['cheap_api_usd']:.3f} API + \\${N['cheap_elec_usd']:.3f} electricity"),
        ("All 130 GPU jobs of the day,\nevery failed experiment included", [(N["day_usd"], "image", None)],
         f"\\${N['day_usd']:.2f}   {N['day_gpu_min']:.0f} GPU-min, {N['day_wh']:.0f} Wh"),
        ("Frontier-model session that built\neverything (API list price)",
         [(N["tok_usd_before_17"], "rig", None), (N["tok_usd_list"], "game", None)],
         f"\\${N['tok_usd_list']:.0f}   = \\${N['tok_usd_before_17']:.0f} building + \\${N['tok_usd_from_17']:.0f} after 17:00"),
        ("For scale: a published one-shot 3D\nshooter by a frontier model", [(N["published_shooter_usd"], "neutral", "////")],
         "\\$550   someone else's figure, API pricing"),
    ]
    lo = 0.004
    for y, (label, segs, text) in enumerate(reversed(rows)):
        start = lo
        for value, kind, hatch in segs:
            ax.barh(y, value - start, left=start, height=0.56, color=FILL[kind], edgecolor=EDGE[kind], lw=1.1,
                    hatch=hatch, zorder=3)
            start = value
        ax.text(start * 1.18, y, text, va="center", ha="left", fontsize=10.5, color=INK)
        ax.text(-0.012, y, label, va="center", ha="right", fontsize=10.5, color=INK, transform=ax.get_yaxis_transform(),
                linespacing=1.1)
    # labels inside the split bars
    ax.text(np.sqrt(lo * N["cheap_api_usd"]), 3, "API", ha="center", va="center", fontsize=9.5, color=MUTED, zorder=4)
    ax.text(np.sqrt(lo * N["tok_usd_before_17"]), 1, "before 17:00: pipeline, game, assets, draft write-up",
            ha="center", va="center", fontsize=9.5, color=MUTED, zorder=4)
    ax.set_xscale("log")
    ax.set_xlim(lo, 20000)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_yticks([])
    ticks = [0.01, 0.1, 1, 10, 100, 1000]
    ax.set_xticks(ticks)
    ax.set_xticklabels(["\\$0.01", "\\$0.10", "\\$1", "\\$10", "\\$100", "\\$1,000"])
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlabel("US dollars (log scale); electricity at \\$0.15 per kWh, GPU draw only", fontsize=10.5)
    for t in ticks:
        ax.axvline(t, color=GRID, lw=0.7, zorder=0)
    clean(ax)
    fig.text(0.02, 0.95, f"What each part cost. Building with a frontier model came to about "
             f"{N['ratio_frontier_to_cheap']:,.0f} times one re-theme by a cheap model (at list prices).",
             fontsize=12.5, color=INK, ha="left", va="center")
    save(fig, "overview")


# =====================================================================================
# Figure 2: the demo's final assets by stage
# =====================================================================================
def fig_stages():
    fig, axes = plt.subplots(1, 3, figsize=(12.8, 3.9), sharey=True)
    fig.subplots_adjust(left=0.105, right=0.985, top=0.80, bottom=0.17, wspace=0.12)
    kinds = {"images": "image", "hero mesh": "mesh", "prop meshes": "mesh", "rig": "rig", "music": "music"}
    names = [r["stage"] for r in stage_rows]
    ys = np.arange(len(names))[::-1]
    cols = [("GPU time (minutes)", [r["seconds"] / 60 for r in stage_rows], fin_s / 60, "{:.1f} min"),
            ("energy (Wh)", [r["wh"] for r in stage_rows], fin_wh, "{:.1f} Wh"),
            ("peak GPU memory (GiB)", [r["peak_mib"] / 1024 for r in stage_rows], None, "{:.1f} GiB")]
    for ax, (title, vals, total, fmt) in zip(axes, cols):
        xmax = max(vals) * 1.42 if total else 12.2
        for y, name, v in zip(ys, names, vals):
            k = kinds[name]
            ax.barh(y, v, height=0.62, color=FILL[k], edgecolor=EDGE[k], lw=1.1, zorder=3)
            label = fmt.format(v)
            if total:
                pct = 100 * v / total
                label += f"  ({pct:.0f} %)" if pct >= 1 else f"  ({pct:.1f} %)"
                ax.text(v + xmax * 0.015, y, label, va="center", fontsize=10, color=INK)
            else:
                ax.text(v - 0.15, y, label, va="center", ha="right", fontsize=10, color=INK, zorder=5)
        ax.set_xlim(0, xmax)
        ax.set_xlabel(title, fontsize=10.5)
        clean(ax)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    axes[0].set_yticks(ys)
    axes[0].set_yticklabels([f"{n}" for n in names], fontsize=10.5)
    cap = N["gpu_total_gib_pytorch"]
    axes[2].axvline(cap, color=EDGE["music"], lw=1.2, ls=(0, (4, 2)), zorder=4)
    axes[2].text(cap - 0.15, -0.62, f"card total: {cap} GiB (as PyTorch reports it)", ha="right", va="center",
                 fontsize=9.5, color=EDGE["music"], linespacing=1.05)
    for a in axes:
        a.set_ylim(-0.95, len(names) - 0.4)
    axes[0].set_title(f"total {N['final_min']:.1f} min", fontsize=10.5, color=MUTED, loc="right")
    axes[1].set_title(f"total {N['final_wh']:.0f} Wh = \\${N['final_usd']:.3f}", fontsize=10.5, color=MUTED, loc="right")
    axes[2].set_title("highest of the stage's jobs, nvidia-smi", fontsize=10.5, color=MUTED, loc="right")
    fig.text(0.02, 0.94, f"The demo's final assets on the RTX 2080 Ti, by stage. Image to 3D (hero + props) takes "
             f"{N['final_image_to_3d_share_time_pct']:.0f} % of the GPU time and "
             f"{N['final_image_to_3d_share_energy_pct']:.0f} % of the energy.",
             fontsize=12.5, color=INK, ha="left", va="center")
    save(fig, "stages")


# =====================================================================================
# Figure 3: where the image-to-3D time goes
# =====================================================================================
def fig_props():
    fig = plt.figure(figsize=(12.8, 5.6))
    gs = fig.add_gridspec(3, 3, width_ratios=[1.25, 1.0, 0.62], left=0.085, right=0.985, top=0.86, bottom=0.10,
                          wspace=0.42, hspace=0.95)
    ax = fig.add_subplot(gs[:, 0])
    order = sorted(props, key=lambda k: r1024[k]["item_s"])
    for y, k in enumerate(order):
        j = r1024[k]
        gen, exp = j["generate_s"], j["export_s"]
        rest = j["item_s"] - gen - exp
        if k in oom:
            ax.barh(y, rest, height=0.66, color="white", edgecolor=EDGE["music"], hatch="////", lw=1.0, zorder=3)
            ax.barh(y, gen, left=rest, height=0.66, color=FILL["mesh"], edgecolor=EDGE["mesh"], lw=1.0, zorder=3)
            ax.barh(y, exp, left=rest + gen, height=0.66, color=EDGE["mesh"], edgecolor=EDGE["mesh"], lw=1.0, zorder=3)
            ax.text(rest / 2, y + 0.02, f"out of memory at 1024: {N['oom_' + k + '_lost_s']:.0f} s lost",
                    ha="center", va="center", fontsize=9.2, color=EDGE["music"], zorder=5,
                    bbox=dict(facecolor="white", edgecolor="none", pad=0.6))
            ax.text(j["item_s"] + 8, y, f"{j['item_s']:.0f} s, redone at 512", va="center", fontsize=9.5, color=INK)
        else:
            ax.barh(y, gen, height=0.66, color=FILL["mesh"], edgecolor=EDGE["mesh"], lw=1.0, zorder=3)
            ax.barh(y, exp, left=gen, height=0.66, color=EDGE["mesh"], edgecolor=EDGE["mesh"], lw=1.0, zorder=3)
            ax.text(j["item_s"] + 8, y, f"{j['item_s']:.0f} s", va="center", fontsize=9.5, color=INK)
        ax.plot(r512[k]["item_s"], y, marker="o", ms=4.2, mfc="white", mec=INK, mew=0.9, zorder=6)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([k.replace("_", " ") for k in order], fontsize=10)
    ax.set_xlim(0, 700)
    ax.set_ylim(-0.6, len(order) - 0.4)
    ax.set_xlabel("seconds per asset (model load of 25 s not included)", fontsize=10.5)
    ax.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    clean(ax)
    ax.tick_params(axis="y", length=0)
    ax.set_title("(a) time per prop at resolution 1024", fontsize=11.5)
    handles = [Patch(facecolor=FILL["mesh"], edgecolor=EDGE["mesh"], label="generate"),
               Patch(facecolor=EDGE["mesh"], edgecolor=EDGE["mesh"], label="export"),
               Line2D([], [], marker="o", ls="none", mfc="white", mec=INK, label="same prop at 512")]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=9.5, handlelength=1.2,
              bbox_to_anchor=(1.0, 0.0))

    bx = fig.add_subplot(gs[:, 1])
    xs512 = [r512[k]["shape_tokens"] / 1000 for k in props]
    ys512 = [r512[k]["generate_s"] for k in props]
    bx.scatter(xs512, ys512, s=26, facecolor="white", edgecolor=INK, lw=0.9, zorder=4, label="at 512")
    bx.scatter(x_tok / 1000, y_gen, s=34, facecolor=FILL["mesh"], edgecolor=EDGE["mesh"], lw=1.1, zorder=5,
               label="at 1024")
    xx = np.linspace(0, 37, 10)
    bx.plot(xx, icpt + slope * xx * 1000, color=EDGE["mesh"], lw=0.9, ls=(0, (4, 2)), zorder=3)
    bx.text(25.5, icpt + slope * 25500 - 4, f"{slope * 1000:.1f} s per 1,000 tokens\n$R^2$ = {r2:.2f}",
            fontsize=9.5, color=EDGE["mesh"], ha="left", va="top")
    for k in ok1024:
        if r1024[k]["shape_tokens"] > 19000:
            bx.annotate(k.replace("_", " "), (r1024[k]["shape_tokens"] / 1000, r1024[k]["generate_s"]),
                        xytext=(-6, 4), textcoords="offset points", ha="right", fontsize=9, color=MUTED)
    for k in oom:
        bx.scatter([r1024[k]["shape_tokens"] / 1000], [r1024[k]["generate_s"]], s=60, marker="x",
                   color=EDGE["music"], lw=1.4, zorder=6)
        bx.annotate(f"{k}: fell back to 512", (r1024[k]["shape_tokens"] / 1000, r1024[k]["generate_s"]),
                    xytext=(10, -16), textcoords="offset points", fontsize=9, color=EDGE["music"])
    bx.set_xlim(0, 37)
    bx.set_ylim(0, 440)
    bx.set_xlabel("shape tokens (thousands)", fontsize=10.5)
    bx.set_ylabel("generate time (s)", fontsize=10.5)
    bx.grid(color=GRID, lw=0.7, zorder=0)
    clean(bx, left=True)
    bx.legend(loc="upper left", frameon=False, fontsize=9.5, handletextpad=0.3)
    bx.set_title("(b) time follows the number of shape tokens", fontsize=11.5)

    rows = [("GPU minutes", b512["seconds"] / 60, b1024["seconds"] / 60, "{:.1f}"),
            ("Wh", b512["watt_hours"], b1024["watt_hours"], "{:.0f}"),
            ("peak GPU memory (GiB)", b512["peak_mem_mib"] / 1024, b1024["peak_mem_mib"] / 1024, "{:.1f}")]
    for r, (unit, v5, v10, fmt) in enumerate(rows):
        cx = fig.add_subplot(gs[r, 2])
        cx.barh(1, v5, height=0.62, color="white", edgecolor=INK, lw=1.0, zorder=3)
        cx.barh(0, v10, height=0.62, color=FILL["mesh"], edgecolor=EDGE["mesh"], lw=1.1, zorder=3)
        top = max(v5, v10)
        cx.text(v5 + top * 0.03, 1, fmt.format(v5), va="center", fontsize=9.5)
        if unit.startswith("peak"):
            cap = N["gpu_total_gib_pytorch"]
            cx.axvline(cap, color=EDGE["music"], lw=1.1, ls=(0, (4, 2)))
            cx.text(cap + 0.25, 0, fmt.format(v10), va="center", fontsize=9.5)
            cx.text(cap + 0.25, 1, f"card {cap}", va="center", fontsize=9, color=EDGE["music"])
            cx.set_xlim(0, cap * 1.32)
        else:
            cx.text(v10 + top * 0.03, 0, fmt.format(v10), va="center", fontsize=9.5)
            cx.set_xlim(0, top * 1.32)
        cx.set_yticks([1, 0])
        cx.set_yticklabels(["512", "1024"], fontsize=9.5)
        cx.set_ylim(-0.55, 1.55)
        cx.set_xlabel(unit, fontsize=10, labelpad=1)
        cx.tick_params(axis="y", length=0)
        cx.tick_params(axis="x", labelsize=9)
        clean(cx)
        if r == 0:
            cx.set_title("(c) all 17 props, one job", fontsize=11.5)
    fig.text(0.02, 0.955, f"Image to 3D: {N['props_count']} props in one job took {N['batch1024_mmss']} at resolution "
             f"1024 and {N['batch512_mmss']} at 512; the time per prop tracks its shape tokens.",
             fontsize=12.5, color=INK, ha="left", va="center")
    save(fig, "props")


# =====================================================================================
# Figure 4: the whole day
# =====================================================================================
def fig_day():
    fig = plt.figure(figsize=(12.8, 8.2))
    ax = fig.add_axes([0.13, 0.43, 0.83, 0.48])
    bx = fig.add_axes([0.13, 0.245, 0.83, 0.15], sharex=ax)
    t_ref = ledger[0]["t0"].replace(hour=0, minute=0, second=0)
    hr = lambda t: (t - t_ref).total_seconds() / 3600
    lanes = [("image", "2080 Ti"), ("image", "3090"), ("image_to_3d", "2080 Ti"), ("image_to_3d", "3090"),
             ("rig", "2080 Ti"), ("rig", "3090"), ("bgm", "2080 Ti"), ("bgm", "3090"), ("check", "3090"),
             ("cheap", "2080 Ti")]
    pretty = {"image": "images", "image_to_3d": "image to 3D", "rig": "rig", "bgm": "music", "check": "import check",
              "cheap": "cheap-model run"}
    ypos = {lane: len(lanes) - 1 - n for n, lane in enumerate(lanes)}
    H = 0.62
    minw = 1.0 / 60  # one minute: very short jobs stay visible
    for j in ledger:
        y = ypos[(j["stage"], j["card"])]
        k = STAGE_KIND[j["stage"]]
        x0 = hr(j["t0"])
        w = max(j["seconds"] / 3600, minw)
        if j["exit"] != 0:
            ax.add_patch(Rectangle((x0, y - H / 2), w, H, facecolor="white", edgecolor=EDGE[k], hatch="/////",
                                   lw=0.8, zorder=3))
        elif j["i"] in final_ids:
            ax.add_patch(Rectangle((x0, y - H / 2), w, H, facecolor=EDGE[k], edgecolor=INK, lw=1.0, zorder=4))
        else:
            ax.add_patch(Rectangle((x0, y - H / 2), w, H, facecolor=FILL[k], edgecolor=EDGE[k], lw=0.8, zorder=3))
    for j in cl:  # the cheap-model run's jobs, from its own ledger
        y = ypos[("cheap", "2080 Ti")]
        k = STAGE_KIND[j["stage"]]
        x0 = hr(datetime.fromisoformat(j["time"]))
        ax.add_patch(Rectangle((x0, y - H / 2), max(j["seconds"] / 3600, minw), H, facecolor=FILL[k],
                               edgecolor=EDGE[k], lw=0.8, zorder=3))
    ax.text(hr(datetime.fromisoformat(cl[-1]["time"])) + cl[-1]["seconds"] / 3600 + 0.05, ypos[("cheap", "2080 Ti")],
            f"{N['cheap_gpu_jobs']} jobs, {N['cheap_gpu_min']:.1f} min, {N['cheap_wh']:.1f} Wh;\nseparate ledger, "
            f"not in the 130", va="center", fontsize=9.3, color=MUTED, linespacing=1.05)
    ax.set_yticks([ypos[l] for l in lanes])
    ax.set_yticklabels([f"{pretty[s]}, {c}" if s != "cheap" else f"{pretty[s]}, {c}" for s, c in lanes], fontsize=10)
    ax.tick_params(axis="y", length=0)
    x_lo, x_hi = 11.0, 18.95
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(-0.7, len(lanes) - 0.3)
    ax.set_xticks(np.arange(11, 19))
    ax.set_xticklabels([f"{h}:00" for h in range(11, 19)])
    ax.set_xticks(np.arange(11, 19, 0.25), minor=True)
    ax.tick_params(axis="x", labelbottom=False)
    for h in range(11, 19):
        ax.axvline(h, color=GRID, lw=0.7, zorder=0)
    ax.axhline(ypos[("check", "3090")] - 0.5, color="#BBBBBB", lw=0.7)
    clean(ax)
    # cumulative energy
    e = sorted(ledger, key=lambda j: j["t0"])
    ends = sorted([(hr(j["t0"]) + j["seconds"] / 3600, j["watt_hours"], j["i"] in final_ids) for j in e])
    tx = [x_lo] + [t for t, _, _ in ends]
    cum = np.concatenate([[0], np.cumsum([w for _, w, _ in ends])])
    cumf = np.concatenate([[0], np.cumsum([w if f else 0 for _, w, f in ends])])
    bx.step(tx + [x_hi], list(cum) + [cum[-1]], where="post", color=INK, lw=1.3, zorder=6)
    bx.step(tx + [x_hi], list(cumf) + [cumf[-1]], where="post", color=INK, lw=1.1, ls=(0, (3, 2)), zorder=6)
    bx.set_ylim(0, 1150)
    bx.set_yticks([0, 250, 500, 750, 1000])
    bx.set_ylabel("cumulative\nenergy (Wh)", fontsize=10.5)
    clean(bx, left=True)
    bx.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    for h in range(11, 19):
        bx.axvline(h, color=GRID, lw=0.7, zorder=0)
    bx.set_xlabel("local time on 2026-11-12 (UTC+3); bar length = job duration, shortest jobs drawn as one minute",
                  fontsize=10.5)
    bx.text(18.9, cum[-1] + 18, f"all 130 jobs: {N['day_wh']:.0f} Wh", ha="right", va="bottom", fontsize=10, color=INK)
    bx.text(18.9, cumf[-1] + 18, f"final-asset jobs: {N['final_wh']:.0f} Wh", ha="right", va="bottom", fontsize=10,
            color=INK)
    handles = [Patch(facecolor=EDGE["mesh"], edgecolor=INK, lw=1.0, label="made a final asset"),
               Patch(facecolor=FILL["mesh"], edgecolor=EDGE["mesh"], label="experiment, exit 0"),
               Patch(facecolor="white", edgecolor=EDGE["mesh"], hatch="/////", label="non-zero exit: failed, stopped or flagged"),
               Line2D([], [], color=INK, lw=1.3, label="cumulative energy, all jobs"),
               Line2D([], [], color=INK, lw=1.1, ls=(0, (3, 2)), label="cumulative energy, final-asset jobs")]
    ax.legend(handles=handles, loc="lower left", frameon=False, fontsize=9.8, ncol=5, handlelength=1.6,
              bbox_to_anchor=(-0.01, 0.995), columnspacing=1.3)

    # bottom strip: what the 335 GPU-minutes were
    sx = fig.add_axes([0.13, 0.065, 0.83, 0.05])
    rep_ids = list(REPLACED.values())
    groups = [("made a final asset", fin_s / 60, EDGE["mesh"], INK, None),
              ("replaced\nimages", rep_s / 60, FILL["mesh"], INK, None),
              ("experiments that finished (exit 0)",
               sum(j["seconds"] for j in ledger if j["exit"] == 0 and j["i"] not in final_ids + rep_ids) / 60,
               FILL["neutral"], EDGE["neutral"], None),
              ("non-zero exit", N["day_nonzero_exit_min"], "white", EDGE["neutral"], "/////")]
    left = 0
    for name, v, fc, ec, hatch in groups:
        sx.barh(0, v, left=left, height=0.8, color=fc, edgecolor=ec, hatch=hatch, lw=1.0)
        txt_color = "white" if fc == EDGE["mesh"] else INK
        if v > 20:
            sx.text(left + v / 2, 0, f"{name}: {v:.0f} min", ha="center", va="center", fontsize=10, color=txt_color,
                    bbox=None if hatch is None else dict(facecolor="white", edgecolor="none", pad=0.5))
        else:
            sx.text(left + v / 2, 0.62, f"{name.replace(chr(10), ' ')}: {v:.1f} min", ha="center", va="bottom",
                    fontsize=9.3, color=MUTED)
        left += v
    N["day_experiments_exit0_min"] = round(groups[2][1], 1)
    N["day_replaced_min"] = round(groups[1][1], 1)
    sx.set_xlim(0, N["day_gpu_min"])
    sx.set_ylim(-0.5, 0.5)
    sx.set_yticks([0])
    sx.set_yticklabels([f"{N['day_gpu_min']:.0f} GPU-min"], fontsize=10)
    sx.tick_params(axis="y", length=0)
    sx.set_xlabel("GPU minutes, both cards", fontsize=10.5)
    clean(sx)
    fig.text(0.02, 0.965, f"The whole day: {N['day_jobs']} GPU jobs, {N['day_gpu_min']:.0f} GPU-minutes. "
             f"{N['final_min']:.0f} of them made what shipped; the rest was finding out how.",
             fontsize=12.5, color=INK, ha="left", va="center")
    save(fig, "day")


# =====================================================================================
# Figure 5: tokens of the building session
# =====================================================================================
def fig_tokens():
    fig = plt.figure(figsize=(12.8, 6.4))
    ax = fig.add_axes([0.065, 0.40, 0.56, 0.48])
    xs = np.arange(len(hours))
    bottom = np.zeros(len(hours))
    for name, short, kind in AGENTS:
        vals = np.array([sum(r["usd_at_list_price"] for r in tok["by_agent_and_hour"]
                             if r["agent"] == name and r["hour_local"][11:16] == h) for h in hours])
        ax.bar(xs, vals, bottom=bottom, width=0.72, color=FILL[kind], edgecolor=EDGE[kind], lw=1.0, zorder=3)
        bottom += vals
    for x, v in zip(xs, bottom):
        ax.text(x, v + 0.5, f"\\${v:.0f}", ha="center", va="bottom", fontsize=10)
    ax.axvline(6.5, color=MUTED, lw=0.9, ls=(0, (4, 2)))
    ax.text(6.42, 44, f"before 17:00: \\${N['tok_usd_before_17']:.0f}", ha="right", va="top", fontsize=10.5)
    ax.text(6.58, 44, f"from 17:00: \\${N['tok_usd_from_17']:.0f}", ha="left", va="top", fontsize=10.5)
    ax.set_xticks(xs)
    ax.set_xticklabels([h if h != "18:00" else f"18:00\u2013{N['tok_counted_until']}" for h in hours], fontsize=10)
    ax.set_xlabel("hour of the day, local time (the session started at "
                  f"{N['tok_session_start']}; counted until {N['tok_counted_until']})", fontsize=10.5)
    ax.set_ylabel("US dollars at API list price", fontsize=10.5)
    ax.set_ylim(0, 45)
    ax.set_xlim(-0.6, len(hours) - 0.4)
    ax.grid(axis="y", color=GRID, lw=0.7, zorder=0)
    clean(ax, left=True)
    ax.set_title("(a) list-price dollars per hour, by agent", fontsize=11.5)
    handles = [Patch(facecolor=FILL[k], edgecolor=EDGE[k],
                     label=f"{s}: \\${by_agent[n]['usd_at_list_price']:.0f}, {by_agent[n]['responses']} responses")
               for n, s, k in AGENTS]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.645, 0.885), frameon=False, fontsize=10,
               handlelength=1.3, title="agent: list-price total, model responses", title_fontsize=10,
               alignment="left", labelspacing=0.55)
    # tokens and dollars by type
    order = ["cache_read", "cache_write", "output", "input"]
    label = dict(TYPES)
    for n, (unit, vals, fmt, xmax) in enumerate([
            ("tokens (millions)", [type_tokens[k] / 1e6 for k in order], None, 520),
            ("US dollars at API list price", [type_usd[k] for k in order], None, 92)]):
        cx = fig.add_axes([0.13 + n * 0.46, 0.085, 0.36, 0.19])
        ys = np.arange(len(order))[::-1]
        for y, k, v in zip(ys, order, vals):
            cx.barh(y, v, height=0.62, color=FILL["neutral"] if k != "cache_read" else FILL["image"],
                    edgecolor=EDGE["neutral"] if k != "cache_read" else EDGE["image"], lw=1.0, zorder=3)
            if n == 0:
                txt = f"{type_tokens[k]:,}" if type_tokens[k] < 1e5 else f"{v:,.1f} M"
            else:
                pct = 100 * v / sum(vals)
                txt = f"\\${v:.2f}  ({pct:.0f} %)" if pct >= 1 else f"\\${v:.2f}  (under 0.1 %)"
            cx.text(v + xmax * 0.012, y, txt, va="center", fontsize=10)
        cx.set_yticks(ys)
        cx.set_yticklabels([label[k] for k in order], fontsize=10)
        cx.set_xlim(0, xmax)
        cx.set_xlabel(unit, fontsize=10.5)
        cx.tick_params(axis="y", length=0)
        cx.grid(axis="x", color=GRID, lw=0.7, zorder=0)
        clean(cx)
        cx.set_title("(b) tokens by type: cache reads are "
                     f"{N['tok_cache_read_share_pct']:.1f} %" if n == 0 else "(c) dollars by type", fontsize=11.5)
    fig.text(0.02, 0.955, f"The frontier-model session that built everything: {N['tok_responses']:,} responses, "
             f"\\${N['tok_usd_list']:.2f} at API list prices (it ran on a subscription; this was not paid).",
             fontsize=12.5, color=INK, ha="left", va="center")
    save(fig, "tokens")


# =====================================================================================
# Figure 6: the cheap-model run
# =====================================================================================
def fig_cheap():
    fig = plt.figure(figsize=(12.8, 4.2))
    ax = fig.add_axes([0.20, 0.17, 0.33, 0.66])
    rows = [("API: uncached input", c_in * 100, "neutral", None),
            ("API: output + reasoning", c_out * 100, "neutral", None),
            ("API: cache reads (implied)", c_cache * 100, "neutral", "///"),
            ("electricity: images", usd(c_stage["image"]) * 100, "image", None),
            ("electricity: image to 3D", usd(c_stage["image_to_3d"]) * 100, "mesh", None),
            ("electricity: rig", usd(c_stage["rig"]) * 100, "rig", None),
            ("electricity: music", usd(c_stage["bgm"]) * 100, "music", None)]
    ys = np.arange(len(rows))[::-1]
    for y, (name, v, k, hatch) in zip(ys, rows, ):
        ax.barh(y, v, height=0.62, color=FILL[k], edgecolor=EDGE[k], hatch=hatch, lw=1.0, zorder=3)
        ax.text(v + 0.02, y, f"{v:.2f} \u00a2", va="center", fontsize=10)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=10)
    ax.set_xlim(0, 1.55)
    ax.set_xlabel("US cents", fontsize=10.5)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    clean(ax)
    ax.set_title(f"(a) {N['cheap_total_usd'] * 100:.2f} \u00a2 in all: API {N['cheap_api_usd'] * 100:.2f} \u00a2, "
                 f"electricity {N['cheap_elec_usd'] * 100:.2f} \u00a2", fontsize=11.5)

    bx = fig.add_axes([0.70, 0.17, 0.28, 0.66])
    trows = [("cache read", ctok["cache_read"], "image"), ("uncached input", ctok["input"], "neutral"),
             ("reasoning", ctok["reasoning"], "neutral"), ("output", ctok["output"], "neutral")]
    ys = np.arange(len(trows))[::-1]
    for y, (name, v, k) in zip(ys, trows):
        bx.barh(y, v / 1000, height=0.62, color=FILL[k], edgecolor=EDGE[k], lw=1.0, zorder=3)
        bx.text(v / 1000 + 40, y, f"{v:,}", va="center", fontsize=10)
    bx.set_yticks(ys)
    bx.set_yticklabels([r[0] for r in trows], fontsize=10)
    bx.set_xlim(0, 3500)
    bx.set_xlabel("tokens (thousands)", fontsize=10.5)
    bx.tick_params(axis="y", length=0)
    bx.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    clean(bx)
    bx.set_title(f"(b) tokens: {N['cheap_cache_hit_pct']:.0f} % of what it read was cache hits", fontsize=11.5)
    fig.text(0.02, 0.95, f"The cheap model's autumn make-over: {N['cheap_steps']} steps, {N['cheap_tool_calls']} tool "
             f"calls, {N['cheap_wall_min']:.1f} min wall clock, {N['cheap_gpu_jobs']} GPU jobs. "
             f"Prices: \\$0.15 per M input, \\$0.60 per M output tokens.",
             fontsize=12.5, color=INK, ha="left", va="center")
    save(fig, "cheap-run")


# =====================================================================================
# Figure 7: one-time setup
# =====================================================================================
def fig_setup():
    fig = plt.figure(figsize=(12.8, 4.3))
    ax = fig.add_axes([0.17, 0.17, 0.32, 0.66])
    kinds = ["neutral", "image", "mesh", "rig", "music"]
    ys = np.arange(len(SETUP_TIME) + 1)[::-1]
    for y, (name, s, lab, cold), k in zip(ys, SETUP_TIME, kinds):
        if cold:
            ax.barh(y, cold / 60, height=0.62, color="white", edgecolor=EDGE[k], hatch="////", lw=0.9, zorder=2)
            ax.text(cold / 60 + 1, y, f"{mmss(s)} measured, most downloads local;\n{cold / 60:.0f} min fully cold (log)",
                    va="center", fontsize=9.5, color=MUTED, linespacing=1.05)
        ax.barh(y, s / 60, height=0.62, color=FILL[k], edgecolor=EDGE[k], lw=1.0, zorder=3)
        if not cold:
            ax.text(s / 60 + 1, y, mmss(s), va="center", fontsize=10)
    yw = ys[-1]
    ax.barh(yw, SETUP_WEIGHTS_S_EST / 60, height=0.62, color="white", edgecolor=EDGE["neutral"], hatch="////", lw=0.9)
    ax.text(SETUP_WEIGHTS_S_EST / 60 + 1, yw, "1.5 h or more (estimate,\n~10 MB/s link)", va="center", fontsize=9.5,
            color=MUTED, linespacing=1.05)
    ax.set_yticks(ys)
    ax.set_yticklabels([s[0] for s in SETUP_TIME] + ["weights download (49 GB)"], fontsize=10)
    ax.set_xlim(0, 125)
    ax.set_xlabel("minutes on the reference machine (solid: measured)", fontsize=10.5)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    clean(ax)
    ax.set_title(f"(a) install time: {N['setup_measured_min']:.0f} min measured, plus the download", fontsize=11.5)

    bx = fig.add_axes([0.665, 0.17, 0.31, 0.66])
    names = [d[0] for d in SETUP_DISK]
    ys = np.arange(len(SETUP_DISK) + 1)[::-1]
    for y, (name, env, w), k in zip(ys, SETUP_DISK, kinds):
        bx.barh(y, env, height=0.62, color=FILL[k], edgecolor=EDGE[k], lw=1.0, zorder=3)
        bx.barh(y, w, left=env, height=0.62, color=EDGE[k], edgecolor=EDGE[k], lw=1.0, zorder=3)
        extra = 0
        if "krea" in name:
            bx.barh(y, SETUP_SECOND_GGUF_GB, left=env + w, height=0.62, color="white", edgecolor=EDGE[k],
                    hatch="////", lw=0.9, zorder=3)
            extra = SETUP_SECOND_GGUF_GB
        bx.text(env + w + extra + 0.6, y, f"{env + w:.1f} GB" + ("\n+ 7.5 GB, second GGUF" if extra else ""),
                linespacing=1.05,
                va="center", fontsize=9.8)
    yc = ys[-1]
    bx.barh(yc, SETUP_PKG_CACHE_GB, height=0.62, color="white", edgecolor=EDGE["neutral"], hatch="////", lw=0.9)
    bx.text(SETUP_PKG_CACHE_GB + 0.6, yc, f"{SETUP_PKG_CACHE_GB:.0f} GB, while installing", va="center", fontsize=9.8,
            color=MUTED)
    bx.set_yticks(ys)
    bx.set_yticklabels(names + ["package cache"], fontsize=10)
    bx.set_xlim(0, 48)
    bx.set_xlabel("disk (GB); light: environment and code, dark: weights", fontsize=10.5)
    bx.tick_params(axis="y", length=0)
    bx.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    clean(bx)
    bx.set_title(f"(b) disk: {N['setup_disk_gb_one_gguf']:.0f} GB installed, "
                 f"{N['setup_disk_gb_both_gguf']:.0f} GB with both GGUFs", fontsize=11.5)
    fig.text(0.02, 0.95, "One-time setup, from docs/setup.md. No GPU is used while installing.",
             fontsize=12.5, color=INK, ha="left", va="center")
    save(fig, "setup")


# =====================================================================================
# Figure 8: one-time versus the next game
# =====================================================================================
def fig_next():
    fig = plt.figure(figsize=(12.8, 4.3))
    rows = [  # label, dollars, hours, kind, hatch, group
        ("build the pipeline, game and assets:\nfrontier-model session (list price)", N["tok_usd_list"],
         N["session_wall_h_until_count"], "rig", None, "once"),
        ("all GPU experiments of that day\n(130 jobs, electricity)", N["day_usd"], N["day_gpu_min"] / 60, "image", None,
         "once"),
        ("install environments and weights\n(no GPU; time measured + estimate)", None,
         N["setup_measured_min"] / 60 + N["setup_weights_download_h_estimate"], "neutral", None, "once"),
        ("re-theme a finished game:\ncheap model + GPU (measured)", N["cheap_total_usd"], N["cheap_wall_min"] / 60,
         "music", None, "next"),
        ("a full asset set like the demo's:\nGPU electricity only (measured)", N["final_usd"], N["final_min"] / 60,
         "mesh", None, "next"),
    ]
    ax = fig.add_axes([0.255, 0.17, 0.33, 0.68])
    bx = fig.add_axes([0.66, 0.17, 0.31, 0.68])
    ys = [5.4, 4.4, 3.4, 1.4, 0.4]
    lo = 0.004
    for y, (name, d, h, k, hatch, g) in zip(ys, rows):
        ax.text(-0.012, y, name, ha="right", va="center", fontsize=10.3, transform=ax.get_yaxis_transform(),
                linespacing=1.1)
        if d is None:
            ax.text(lo * 1.3, y, "\\$0 on the GPU; whole-machine power not counted", va="center", fontsize=9.5,
                    color=MUTED)
        else:
            ax.barh(y, d - lo, left=lo, height=0.62, color=FILL[k], edgecolor=EDGE[k], lw=1.0, zorder=3)
            ax.text(d * 1.2, y, f"\\${d:.2f}" if d >= 0.1 else f"\\${d:.3f}", va="center", fontsize=10.3)
        est = "install" in name
        bx.barh(y, h, height=0.62, color=FILL[k] if not est else "white", edgecolor=EDGE[k],
                hatch="////" if est else None, lw=1.0, zorder=3)
        if est:
            bx.barh(y, N["setup_measured_min"] / 60, height=0.62, color=FILL[k], edgecolor=EDGE[k], lw=1.0, zorder=4)
        txt = f"{h:.1f} h" if h >= 1 else f"{h * 60:.0f} min"
        note = {"once": "", "next": ""}[g]
        if "session" in name:
            txt += " wall clock (still running)"
        elif "experiments" in name:
            txt += " of GPU time"
        elif est:
            txt = f"{N['setup_measured_min']:.0f} min measured + 1.5 h download (est.)"
        elif "re-theme" in name:
            txt += f" wall clock, {N['cheap_gpu_min']:.0f} min of it on the GPU"
        else:
            txt += " of GPU time"
        bx.text(max(h, 2.2 if est else h) + 0.15, y, txt + note, va="center", fontsize=10)
    for a in (ax, bx):
        a.set_ylim(-0.3, 6.0)
        a.set_yticks([])
        a.axhline(2.4, color="#BBBBBB", lw=0.8)
        clean(a)
    ax.text(-0.012, 2.55, "ONE-TIME", transform=ax.get_yaxis_transform(), ha="right", va="bottom", fontsize=10,
            color=MUTED)
    ax.text(-0.012, 2.25, "EACH NEXT GAME", transform=ax.get_yaxis_transform(), ha="right", va="top", fontsize=10,
            color=MUTED)
    ax.set_xscale("log")
    ax.set_xlim(lo, 3000)
    ax.set_xticks([0.01, 0.1, 1, 10, 100, 1000])
    ax.set_xticklabels(["\\$0.01", "\\$0.10", "\\$1", "\\$10", "\\$100", "\\$1,000"])
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlabel("US dollars (log scale)", fontsize=10.5)
    ax.set_title("(a) money", fontsize=11.5)
    bx.set_xlim(0, 13)
    bx.set_xlabel("hours", fontsize=10.5)
    bx.set_title("(b) time", fontsize=11.5)
    for a in (ax, bx):
        a.grid(axis="x", color=GRID, lw=0.7, zorder=0)
    fig.text(0.02, 0.95, "One-time cost of building the pipeline against the measured cost of using it again.",
             fontsize=12.5, color=INK, ha="left", va="center")
    save(fig, "next-game")


fig_overview()
fig_stages()
fig_props()
fig_day()
fig_tokens()
fig_cheap()
fig_setup()
fig_next()
(HERE / "cost-numbers.json").write_text(json.dumps(N, indent=1, ensure_ascii=False) + "\n")
print(HERE / "cost-numbers.json")
