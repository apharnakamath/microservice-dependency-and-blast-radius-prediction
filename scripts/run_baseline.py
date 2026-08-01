"""
Self-contained: start all services, run load generator, stop all services.
Everything happens inside this one process so it works in sandboxed
environments where background processes get reaped between shell calls.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_services import start_all_and_wait, stop_all
import asyncio
from load_generator import run as run_load


def main(duration, rps):
    procs = start_all_and_wait()
    try:
        asyncio.run(run_load(duration, rps))
    finally:
        stop_all(procs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=15)
    parser.add_argument("--rps", type=int, default=5)
    args = parser.parse_args()
    main(args.duration, args.rps)
