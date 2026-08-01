"""
Continuously hits the entrypoint services to generate realistic call
traffic (and therefore realistic traces). Run this WHILE services are up.

Usage:
    python scripts/load_generator.py --duration 30 --rps 5
"""
import argparse
import asyncio
import json
import random
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
TOPOLOGY_PATH = ROOT / "data" / "topology.json"


async def hit_entrypoint(client, name, port):
    try:
        r = await client.get(f"http://127.0.0.1:{port}/work", timeout=5)
        return name, r.status_code
    except Exception as e:
        return name, f"error:{type(e).__name__}"


async def run(duration_s, rps):
    topo = json.loads(TOPOLOGY_PATH.read_text())
    entrypoints = [(n, topo["services"][n]["port"]) for n in topo["entrypoints"]]

    end = time.time() + duration_s
    results = {"ok": 0, "bad": 0}
    async with httpx.AsyncClient() as client:
        while time.time() < end:
            batch = [random.choice(entrypoints) for _ in range(rps)]
            outcomes = await asyncio.gather(*[hit_entrypoint(client, n, p) for n, p in batch])
            for _, status in outcomes:
                if status == 200:
                    results["ok"] += 1
                else:
                    results["bad"] += 1
            await asyncio.sleep(1)
    print(f"Load run done: {results}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=20)
    parser.add_argument("--rps", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(run(args.duration, args.rps))
