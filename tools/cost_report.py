#!/usr/bin/env python3
"""Summarise cost/ledger.jsonl: GPU time and energy per stage and per card.

    python3 tools/cost_report.py [--ledger cost/ledger.jsonl] [--since 2026-11-12T15:00]
                                 [--label PREFIX] [--kwh-price 0.15] [--markdown]

Each line of the ledger is one GPU job written by tools/gpu_run.sh. Electricity
is the measured GPU draw only (nvidia-smi, 1 Hz), not the whole machine.
--kwh-price turns watt-hours into money in whatever currency you price a kWh in.
Prints a table (or Markdown with --markdown) and one JSON line last.
"""
import argparse
import json
import os
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(ROOT, "cost/ledger.jsonl"))
    ap.add_argument("--since", help="ISO time; ignore jobs that started earlier")
    ap.add_argument("--label", help="only jobs whose label starts with this")
    ap.add_argument("--kwh-price", type=float, default=0.15)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    groups = OrderedDict()
    with open(args.ledger) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            job = json.loads(line)
            if args.since and job["time"] < args.since:
                continue
            if args.label and not job["label"].startswith(args.label):
                continue
            key = (job["stage"], job["gpu_name"].replace("NVIDIA GeForce ", ""))
            g = groups.setdefault(key, {"jobs": 0, "failed": 0, "seconds": 0.0, "wait": 0.0, "wh": 0.0, "peak_mib": 0})
            g["jobs"] += 1
            g["failed"] += job["exit"] != 0
            g["seconds"] += job["seconds"]
            g["wait"] += job.get("wait_seconds", 0)
            g["wh"] += job["watt_hours"]
            g["peak_mib"] = max(g["peak_mib"], job["peak_mem_mib"])

    rows = []
    for (stage, card), g in groups.items():
        rows.append([stage, card, g["jobs"], g["failed"], f"{g['seconds'] / 60:.1f}", f"{g['wh']:.1f}",
                     f"{g['wh'] / 1000 * args.kwh_price:.4f}", g["peak_mib"]])
    total_s = sum(g["seconds"] for g in groups.values())
    total_wh = sum(g["wh"] for g in groups.values())
    rows.append(["total", "", sum(g["jobs"] for g in groups.values()), sum(g["failed"] for g in groups.values()),
                 f"{total_s / 60:.1f}", f"{total_wh:.1f}", f"{total_wh / 1000 * args.kwh_price:.4f}", ""])
    head = ["stage", "card", "jobs", "failed", "GPU minutes", "Wh", f"cost @ {args.kwh_price}/kWh", "peak MiB"]
    if args.markdown:
        print("| " + " | ".join(head) + " |")
        print("|" + "---|" * len(head))
        for r in rows:
            print("| " + " | ".join(str(c) for c in r) + " |")
    else:
        widths = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
        for r in [head] + rows:
            print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    print(json.dumps({"stage": "cost", "jobs": rows[-1][2], "gpu_minutes": round(total_s / 60, 1),
                      "watt_hours": round(total_wh, 1), "cost": round(total_wh / 1000 * args.kwh_price, 4),
                      "kwh_price": args.kwh_price}))


if __name__ == "__main__":
    sys.exit(main())
