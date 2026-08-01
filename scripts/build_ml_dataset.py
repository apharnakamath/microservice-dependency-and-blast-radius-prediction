"""
Joins data/failure_dataset.csv (labeled fault-injection trials) with
data/graph_features.json (structural features per node) and
data/topology.json (edge config) to build the feature table Phase 4
trains on.

Feature set per row (failed_service, affected_service) pair, matching the
README's spec: graph features of both nodes, distance between them,
direct-edge config (retries/timeout) if one exists, and the injected
fault parameters themselves (mode/severity) as "load"-type context.

Output: data/ml_dataset.csv
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
FAILURE_CSV = ROOT / "data" / "failure_dataset.csv"
FEATURES_JSON = ROOT / "data" / "graph_features.json"
TOPOLOGY_JSON = ROOT / "data" / "topology.json"
OUT_CSV = ROOT / "data" / "ml_dataset.csv"


def direct_edge_config(topology, src, dst):
    """If `src` directly calls `dst`, return its timeout/retries/base_latency.
    None if there's no direct edge (they're only connected transitively)."""
    for call in topology["services"][src]["calls"]:
        if call["target"] == dst:
            return call
    return None


def main():
    df = pd.read_csv(FAILURE_CSV)
    features = json.loads(FEATURES_JSON.read_text())
    topology = json.loads(TOPOLOGY_JSON.read_text())

    rows = []
    for _, r in df.iterrows():
        failed = r["failed_service"]
        affected = r["affected_service"]
        ff = features.get(failed, {})
        af = features.get(affected, {})
        edge = direct_edge_config(topology, affected, failed)  # affected calls failed?

        rows.append({
            **r.to_dict(),
            "failed_betweenness": ff.get("betweenness_centrality", 0.0),
            "failed_in_degree": ff.get("in_degree", 0),
            "failed_out_degree": ff.get("out_degree", 0),
            "failed_is_articulation_point": ff.get("is_articulation_point", False),
            "affected_betweenness": af.get("betweenness_centrality", 0.0),
            "affected_in_degree": af.get("in_degree", 0),
            "affected_out_degree": af.get("out_degree", 0),
            "has_direct_edge": edge is not None,
            "direct_edge_timeout_ms": edge["timeout_ms"] if edge else -1,
            "direct_edge_retries": edge["retries"] if edge else -1,
        })

    out = pd.DataFrame(rows)
    # mode as a simple binary flag; keep injected_severity as the continuous knob
    out["fault_mode_down"] = (out["failed_mode"] == "down").astype(int)
    out.to_csv(OUT_CSV, index=False)
    print(f"Wrote {len(out)} rows, {out.shape[1]} columns to {OUT_CSV}")
    print(out.dtypes)


if __name__ == "__main__":
    main()
