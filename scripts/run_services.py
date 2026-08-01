"""
Launches every service in data/topology.json as its own uvicorn subprocess
(one Python process per service, real network calls over localhost between
them). This stands in for "15-20 small services" without needing Docker
running inside this environment -- the topology/config/tracing logic here
is identical to what you'd containerize later (a Dockerfile is included
separately for that step).

Usage:
    python scripts/run_services.py            # start all, block until Ctrl+C
    python scripts/run_services.py --daemon    # start all, return immediately,
                                                # write PIDs to data/pids.json
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
TOPOLOGY_PATH = ROOT / "data" / "topology.json"
PIDS_PATH = ROOT / "data" / "pids.json"
SERVICES_DIR = ROOT / "services"


def start_all():
    topo = json.loads(TOPOLOGY_PATH.read_text())
    procs = {}
    for name, cfg in topo["services"].items():
        env = os.environ.copy()
        env["SERVICE_NAME"] = name
        env["PYTHONPATH"] = str(SERVICES_DIR)
        cmd = [
            sys.executable, "-m", "uvicorn", "app:app",
            "--host", "127.0.0.1", "--port", str(cfg["port"]),
            "--log-level", "warning",
        ]
        proc = subprocess.Popen(
            cmd, cwd=str(SERVICES_DIR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs[name] = {"pid": proc.pid, "port": cfg["port"]}
        print(f"started {name} on port {cfg['port']} (pid {proc.pid})")
    return procs


def wait_healthy(procs, timeout_s=20):
    import httpx
    deadline = time.time() + timeout_s
    pending = set(procs)
    while pending and time.time() < deadline:
        for name in list(pending):
            try:
                r = httpx.get(f"http://127.0.0.1:{procs[name]['port']}/admin/health", timeout=0.5)
                if r.status_code == 200:
                    pending.discard(name)
            except Exception:
                pass
        if pending:
            time.sleep(0.5)
    if pending:
        print(f"WARNING: services not healthy after {timeout_s}s: {pending}")
    else:
        print(f"All {len(procs)} services healthy.")


def stop_all(procs):
    for name, info in procs.items():
        try:
            os.kill(info["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    print("stopped all services")


def start_all_and_wait():
    """For use as a library call within a single script/process that also
    does work and tears down -- required in sandboxes where background
    processes don't survive across separate tool invocations."""
    procs = start_all()
    wait_healthy(procs)
    return procs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()

    procs = start_all()
    wait_healthy(procs)
    PIDS_PATH.write_text(json.dumps(procs, indent=2))

    if args.daemon:
        print(f"Running in background. PIDs written to {PIDS_PATH}")
        sys.exit(0)

    try:
        print("Services running. Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_all(procs)

