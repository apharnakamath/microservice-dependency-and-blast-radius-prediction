"""
Generates a realistic microservice dependency topology.

Design goals (so it's actually useful for Phase 2/3 later):
- A handful of "hub" services many others depend on (high in-degree)
- Several "leaf" services with no downstream calls
- At least one deliberate articulation point / single-point-of-failure
- Mixed fan-out (some services call 1 thing, some call 3-4)
- Random but bounded call latency + a retry/timeout config per edge,
  since the README calls these out as ML features later.

Output: data/topology.json
{
  "services": {
    "svc-auth": {
      "port": 9000,
      "role": "hub" | "leaf" | "mid",
      "calls": [
        {"target": "svc-db-users", "timeout_ms": 300, "retries": 1, "base_latency_ms": 15}
      ]
    },
    ...
  }
}
"""
import json
import random
from pathlib import Path

random.seed(42)  # reproducible topology

N_SERVICES = 18
BASE_PORT = 9000
OUT_PATH = Path(__file__).parent.parent / "data" / "topology.json"


def build_topology():
    names = [f"svc-{i:02d}" for i in range(N_SERVICES)]

    # Layered structure: layer 0 = "front door" services, layer 1 = mid/business
    # logic (this is where our hubs live), layer 2 = leaf/data services.
    layer0 = names[:4]          # e.g. gateway-ish services, low in-degree
    layer1 = names[4:10]        # hubs: many layer0/other layer1 services call these
    layer2 = names[10:]         # leaves: data stores, no outgoing calls

    services = {n: {"port": BASE_PORT + i, "calls": []} for i, n in enumerate(names)}

    # layer0 -> layer1 (each layer0 service calls 1-3 layer1 services)
    for svc in layer0:
        targets = random.sample(layer1, k=random.randint(1, 3))
        for t in targets:
            services[svc]["calls"].append(_make_edge(t))

    # guarantee every hub is reachable from at least one entrypoint --
    # random sampling above can otherwise strand a hub with no inbound
    # callers, which would make it invisible to real traffic/traces
    reached = {c["target"] for svc in layer0 for c in services[svc]["calls"]}
    for hub in layer1:
        if hub not in reached:
            caller = random.choice(layer0)
            services[caller]["calls"].append(_make_edge(hub))


    # layer1 -> layer1 (some cross-calls, sparse, to add real graph structure)
    for svc in layer1:
        others = [s for s in layer1 if s != svc]
        if random.random() < 0.4:
            t = random.choice(others)
            services[svc]["calls"].append(_make_edge(t))

    # layer1 -> layer2 (each hub calls 1-4 leaf/data services)
    for svc in layer1:
        targets = random.sample(layer2, k=random.randint(1, 4))
        for t in targets:
            services[svc]["calls"].append(_make_edge(t))

    # Deliberately force a single point of failure: pick one layer2 service
    # and make EVERY layer1 hub depend on it (e.g. a shared "svc-cache" or
    # "svc-config" style dependency). This becomes our articulation point.
    spof = layer2[0]
    for svc in layer1:
        if not any(c["target"] == spof for c in services[svc]["calls"]):
            services[svc]["calls"].append(_make_edge(spof))

    # Assign roles for readability/labels (not used by the service logic itself)
    roles = {}
    for s in layer0:
        roles[s] = "entrypoint"
    for s in layer1:
        roles[s] = "hub"
    for s in layer2:
        roles[s] = "leaf"
    roles[spof] = "spof-leaf"

    for s in services:
        services[s]["role"] = roles[s]

    return {"services": services, "entrypoints": layer0, "spof": spof}


def _make_edge(target):
    return {
        "target": target,
        # timeouts kept generous relative to base_latency so that, under
        # normal conditions, failures come from injected faults (Phase 3)
        # rather than scheduler noise from running many processes on one core
        "timeout_ms": random.choice([800, 1200, 1500, 2000]),
        "retries": random.choice([0, 1, 1, 2]),   # weighted toward 1 retry
        "base_latency_ms": random.randint(5, 40),
    }



if __name__ == "__main__":
    topo = build_topology()
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(topo, indent=2))
    n_edges = sum(len(s["calls"]) for s in topo["services"].values())
    print(f"Generated {len(topo['services'])} services, {n_edges} edges")
    print(f"Entrypoints: {topo['entrypoints']}")
    print(f"Forced SPOF (articulation point target): {topo['spof']}")
    print(f"Written to {OUT_PATH}")
