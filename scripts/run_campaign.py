"""
Runs a fault-injection campaign across many services and severities,
producing the labeled dataset Phase 4 (ML model) trains on:
(failed_service, affected_service, severity, time_to_impact, graph_distance, ...)

This is deliberately a single long-running script (services start once,
many trials run in sequence, services stop at the end) so it works in
sandboxes where background processes don't persist across tool calls, and
so you're not paying ~18-service startup cost per trial.

Usage:
    python scripts/run_campaign.py                  # default scan
    python scripts/run_campaign.py --targets svc-10 svc-05   # subset, for testing
    python scripts/run_campaign.py --variations 4    # more severity/mode variety per target
"""
import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_services import start_all_and_wait, stop_all
from fault_injector import run_trial, SERVICES

ROOT = Path(__file__).parent.parent
OUT_CSV = ROOT / "data" / "failure_dataset.csv"

DEFAULT_VARIATIONS = [
    ("down", 1.0),
    ("down", 0.5),
    ("slow", 0.8),
    ("slow", 0.3),
]

FIELDNAMES = [
    "failed_service", "failed_mode", "injected_severity",
    "affected_service", "graph_distance",
    "baseline_error_rate", "fault_window_error_rate", "severity_delta",
    "was_impacted", "time_to_impact_ms",
    "baseline_latency_ms", "fault_window_latency_ms", "latency_delta_ms",
]


async def run_campaign(targets, variations, warmup_s, fault_window_s, interval_s):
    all_rows = []
    total = len(targets) * len(variations)
    i = 0
    t_start = time.time()
    for target in targets:
        for mode, severity in variations:
            i += 1
            rows = await run_trial(
                target, mode, severity,
                warmup_s=warmup_s, fault_window_s=fault_window_s, interval_s=interval_s,
            )
            all_rows.extend(rows)
            impacted = sum(1 for r in rows if r["was_impacted"])
            elapsed = time.time() - t_start
            print(f"[{i}/{total}] {target} mode={mode} sev={severity} "
                  f"-> {impacted}/{len(rows)} impacted  ({elapsed:.0f}s elapsed)")
    return all_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", nargs="*", default=None,
                         help="subset of services to inject faults into (default: all)")
    parser.add_argument("--variations", type=int, default=len(DEFAULT_VARIATIONS),
                         help="how many (mode, severity) variations per target, from DEFAULT_VARIATIONS")
    parser.add_argument("--warmup", type=float, default=1.5)
    parser.add_argument("--window", type=float, default=4.0)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--append", action="store_true",
                         help="append to existing CSV instead of overwriting (for running the campaign in batches)")
    args = parser.parse_args()

    targets = args.targets or list(SERVICES.keys())
    variations = DEFAULT_VARIATIONS[:args.variations]

    procs = start_all_and_wait()
    try:
        rows = asyncio.run(run_campaign(targets, variations, args.warmup, args.window, args.interval))
    finally:
        stop_all(procs)

    write_header = not (args.append and OUT_CSV.exists())
    mode = "a" if args.append else "w"
    with open(OUT_CSV, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUT_CSV} (append={args.append})")
    n_impacted = sum(1 for r in rows if r["was_impacted"])
    print(f"Positive examples (was_impacted=True): {n_impacted} ({100*n_impacted/len(rows):.1f}%)")


if __name__ == "__main__":
    main()
