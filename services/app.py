"""
Generic microservice. One process = one service. Which service it is comes
from the SERVICE_NAME env var; behavior (who it calls, timeouts, retries)
comes from data/topology.json. This is what "runs" as each of the 15-20
services instead of hand-writing near-identical files per service.

Each instance exposes:
  GET  /work         -> does its job: calls all downstream deps, returns result
  POST /admin/fault   -> {"mode": "down"|"slow"|"healthy", "severity": 0-1}
                          used by the fault injector (Phase 3) to simulate
                          degradation without literally killing the process,
                          so we get controllable, repeatable severity levels.
  GET  /admin/health  -> current fault state (for the injector to poll)
"""
import asyncio
import json
import os
import random
import time
from pathlib import Path

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from tracing import get_tracer

SERVICE_NAME = os.environ["SERVICE_NAME"]
TOPOLOGY_PATH = Path(__file__).parent.parent / "data" / "topology.json"
topology = json.loads(TOPOLOGY_PATH.read_text())
CONFIG = topology["services"][SERVICE_NAME]
CALLS = CONFIG["calls"]

tracer = get_tracer(SERVICE_NAME)
app = FastAPI()

# --- fault state, mutated via /admin/fault -------------------------------
fault_state = {"mode": "healthy", "severity": 0.0}


class FaultRequest(BaseModel):
    mode: str  # "healthy" | "down" | "slow"
    severity: float = 1.0  # 0-1, how bad ("slow" -> extra latency scale, "down" -> error probability)


@app.post("/admin/fault")
def set_fault(req: FaultRequest):
    fault_state["mode"] = req.mode
    fault_state["severity"] = req.severity
    return {"ok": True, "state": fault_state}


@app.get("/admin/health")
def health():
    return {"service": SERVICE_NAME, "fault_state": fault_state}


def _service_url(target: str) -> str:
    port = topology["services"][target]["port"]
    # In docker-compose each service is its own container reachable by its
    # compose service name (Docker's embedded DNS resolves it); locally
    # (run_services.py) every service is 127.0.0.1 on a distinct port.
    # SERVICE_HOST=docker switches the mode; unset/local is the old default.
    if os.environ.get("SERVICE_HOST") == "docker":
        return f"http://{target}:{port}/work"
    return f"http://127.0.0.1:{port}/work"


async def _call_downstream(client: httpx.AsyncClient, edge: dict):
    target = edge["target"]
    timeout_s = edge["timeout_ms"] / 1000
    attempts = edge["retries"] + 1
    last_status = "ok"
    retry_count_used = 0

    for attempt in range(attempts):
        start = time.perf_counter()
        with tracer.start_as_current_span(f"call:{target}") as span:
            span.set_attribute("service.name", SERVICE_NAME)
            span.set_attribute("call.target", target)
            span.set_attribute("call.retry_count", attempt)
            try:
                # simulate this service's own base latency for the hop
                await asyncio.sleep(edge["base_latency_ms"] / 1000)
                resp = await client.get(_service_url(target), timeout=timeout_s)
                if resp.status_code >= 500:
                    last_status = "error"
                    span.set_attribute("call.status", "error")
                    span.set_attribute("call.http_status", resp.status_code)
                else:
                    last_status = "ok"
                    span.set_attribute("call.status", "ok")
                    span.set_attribute("call.http_status", resp.status_code)
                    return last_status
            except (httpx.TimeoutException, httpx.ConnectError):
                last_status = "timeout"
                span.set_attribute("call.status", "timeout")
        retry_count_used = attempt + 1
    return last_status


@app.get("/work")
async def work():
    # apply this service's own fault state first
    mode = fault_state["mode"]
    severity = fault_state["severity"]

    if mode == "down":
        # probabilistically fail outright, proportional to severity
        if random.random() < severity:
            return _err_response()
    elif mode == "slow":
        await asyncio.sleep(0.05 + severity * 1.5)  # extra induced latency

    # call all downstream dependencies concurrently
    statuses = []
    if CALLS:
        async with httpx.AsyncClient() as client:
            statuses = await asyncio.gather(*[_call_downstream(client, e) for e in CALLS])

    downstream_bad = sum(1 for s in statuses if s != "ok")
    if downstream_bad and downstream_bad >= max(1, len(statuses) // 2):
        # if half or more of our deps are unhealthy, we degrade too
        # (this is what lets failures cascade upward through the graph)
        return _err_response(reason="downstream_degraded")

    return {"service": SERVICE_NAME, "ok": True, "downstream": statuses}


def _err_response(reason="self_fault"):
    from fastapi import Response
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"service": SERVICE_NAME, "ok": False, "reason": reason})
