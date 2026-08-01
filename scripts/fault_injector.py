"""
Core fault-injection logic. Assumes services are already running (started
by the caller). For a chosen target service:

  1. Poll every service's /work directly for a short warmup window to
     establish baseline (healthy) error rate.
  2. Flip the target's fault mode via POST /admin/fault (down or slow).
  3. Keep polling every service's /work for the fault window, tagging
     each poll with time-since-fault-start.
  4. Restore the target to healthy.
  5. For every OTHER service, compute:
       - severity: (error rate during fault window) - (baseline error rate)
       - time_to_impact_ms: time from fault start to that service's FIRST
         degraded poll (None if it was never impacted)
       - latency_delta_ms: avg latency during fault vs baseline

Why poll /work directly on every node (not just entrypoints): each
service's /work handler already calls its own downstream deps and
degrades if enough of them are unhealthy (see services/app.py). Polling
every node directly gives us a ground-truth health reading per node per
moment, independent of whether an entrypoint happened to route through it
during that window -- this is what makes clean (source, target, severity,
time_to_impact) labels possible.
"""
import asyncio
import json
import time
from pathlib import Path

import httpx
import networkx as nx

ROOT = Path(__file__).parent.parent
TOPOLOGY_PATH = ROOT / "data" / "topology.json"

topo = json.loads(TOPOLOGY_PATH.read_text())
SERVICES = topo["services"]


def build_call_graph():
    G = nx.DiGraph()
    G.add_nodes_from(SERVICES)
    for src, cfg in SERVICES.items():
        for c in cfg["calls"]:
            G.add_edge(src, c["target"])
    return G


CALL_GRAPH = build_call_graph()


def graph_distance(affected: str, failed: str):
    """Hops from `affected` to `failed` following call edges -- i.e. is
    `affected` transitively dependent on `failed`, and how deep. None if
    `affected` never reaches `failed` through calls (not dependent)."""
    try:
        return nx.shortest_path_length(CALL_GRAPH, source=affected, target=failed)
    except nx.NetworkXNoPath:
        return None


async def _poll_once(client, name, port):
    start = time.perf_counter()
    try:
        r = await client.get(f"http://127.0.0.1:{port}/work", timeout=3)
        ok = r.status_code == 200
    except Exception:
        ok = False
    latency_ms = (time.perf_counter() - start) * 1000
    return name, ok, latency_ms


async def _poll_all_once(client):
    tasks = [_poll_once(client, name, cfg["port"]) for name, cfg in SERVICES.items()]
    results = await asyncio.gather(*tasks)
    return {name: {"ok": ok, "latency_ms": lat} for name, ok, lat in results}


async def _poll_window(client, duration_s, interval_s):
    """Poll every service repeatedly for duration_s, return list of
    (elapsed_s, {name: {ok, latency_ms}}) samples."""
    samples = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration_s:
        elapsed = time.perf_counter() - t0
        reading = await _poll_all_once(client)
        samples.append((elapsed, reading))
        await asyncio.sleep(interval_s)
    return samples


async def set_fault(client, service, mode, severity):
    port = SERVICES[service]["port"]
    await client.post(f"http://127.0.0.1:{port}/admin/fault", json={"mode": mode, "severity": severity})


def _summarize(samples, service):
    """error rate + avg latency for one service across a set of samples."""
    readings = [s[1][service] for s in samples if service in s[1]]
    if not readings:
        return {"error_rate": None, "avg_latency_ms": None}
    err = sum(1 for r in readings if not r["ok"]) / len(readings)
    lat = sum(r["latency_ms"] for r in readings) / len(readings)
    return {"error_rate": round(err, 3), "avg_latency_ms": round(lat, 1)}


def _time_to_impact(samples, service, baseline_err):
    """Elapsed seconds (since fault start) of the first sample where this
    service is unhealthy AND baseline wasn't already failing it. None if
    it never degrades during the window."""
    for elapsed, reading in samples:
        r = reading.get(service)
        if r and not r["ok"]:
            return round(elapsed * 1000, 1)  # ms
    return None


async def run_trial(target: str, mode: str, severity: float,
                     warmup_s=2.0, fault_window_s=6.0, interval_s=0.2):
    """Runs one fault-injection trial against `target` and returns a list
    of result rows, one per OTHER service in the topology."""
    async with httpx.AsyncClient() as client:
        # 1. baseline
        baseline_samples = await _poll_window(client, warmup_s, interval_s)

        # 2. inject fault
        await set_fault(client, target, mode, severity)
        fault_start_wall = time.perf_counter()

        # 3. observe
        fault_samples = await _poll_window(client, fault_window_s, interval_s)

        # 4. restore
        await set_fault(client, target, "healthy", 0.0)
        # brief settle time so the next trial starts clean
        await asyncio.sleep(0.5)

    rows = []
    for service in SERVICES:
        if service == target:
            continue
        baseline = _summarize(baseline_samples, service)
        during = _summarize(fault_samples, service)
        base_err = baseline["error_rate"] or 0.0
        during_err = during["error_rate"] or 0.0
        severity_delta = round(max(0.0, during_err - base_err), 3)

        rows.append({
            "failed_service": target,
            "failed_mode": mode,
            "injected_severity": severity,
            "affected_service": service,
            "graph_distance": graph_distance(service, target),
            "baseline_error_rate": base_err,
            "fault_window_error_rate": during_err,
            "severity_delta": severity_delta,
            "was_impacted": severity_delta > 0.05,  # >5pp error rate increase
            "time_to_impact_ms": _time_to_impact(fault_samples, service, base_err),
            "baseline_latency_ms": baseline["avg_latency_ms"],
            "fault_window_latency_ms": during["avg_latency_ms"],
            "latency_delta_ms": (
                round(during["avg_latency_ms"] - baseline["avg_latency_ms"], 1)
                if during["avg_latency_ms"] is not None and baseline["avg_latency_ms"] is not None
                else None
            ),
        })
    return rows
