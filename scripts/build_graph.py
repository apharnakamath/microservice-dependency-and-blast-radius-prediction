"""
Parses data/traces.jsonl into a call graph (nodes = services, edges = calls
weighted by volume) and computes structural features per node:
  - betweenness centrality
  - in-degree / out-degree
  - is_articulation_point (single point of failure if removed)
  - clustering coefficient
  - avg call latency on outgoing edges (weight)

This graph is used both to visualize the topology and as the source of the
"graph features" the ML model in Phase 4 will train on.

Swapping to Neo4j later: everything here is computed on a networkx.DiGraph
built from the same edge list -- you'd instead MERGE (:Service) nodes and
[:CALLS] relationships from the same trace rows, then either run
GDS betweenness/articulation-point algorithms server-side or keep computing
them in Python via neo4j-driver + networkx on the fetched edge list.

Usage:
    python scripts/build_graph.py
Writes: data/graph_features.json, data/graph.gexf (Gephi-importable)
"""
import json
from collections import defaultdict
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).parent.parent
TRACES_PATH = ROOT / "data" / "traces.jsonl"
TOPOLOGY_PATH = ROOT / "data" / "topology.json"
OUT_FEATURES = ROOT / "data" / "graph_features.json"
OUT_GEXF = ROOT / "data" / "graph.gexf"


def load_traces():
    rows = []
    with open(TRACES_PATH) as f:
        for line in f:
            r = json.loads(line)
            if r.get("target"):
                rows.append(r)
    return rows


def build_graph(rows):
    """Build a weighted directed graph: edge weight = call volume,
    edge attrs also carry avg latency and error rate observed in the trace
    window, which matter later as ML features (Phase 4)."""
    G = nx.DiGraph()
    edge_stats = defaultdict(lambda: {"count": 0, "errors": 0, "latency_sum": 0.0})

    for r in rows:
        key = (r["service"], r["target"])
        s = edge_stats[key]
        s["count"] += 1
        s["latency_sum"] += r["latency_ms"]
        if r["status"] != "ok":
            s["errors"] += 1

    for (src, dst), s in edge_stats.items():
        G.add_edge(
            src, dst,
            weight=s["count"],
            avg_latency_ms=round(s["latency_sum"] / s["count"], 2),
            error_rate=round(s["errors"] / s["count"], 3),
        )

    # make sure every known service is a node even if it had no traffic
    topo = json.loads(TOPOLOGY_PATH.read_text())
    for name in topo["services"]:
        if name not in G:
            G.add_node(name)

    return G


def compute_features(G: nx.DiGraph):
    betweenness = nx.betweenness_centrality(G, weight="weight")
    clustering = nx.clustering(G.to_undirected())

    # articulation points are defined on undirected graphs: a node whose
    # removal disconnects the graph. This is exactly our "single point of
    # failure" signal from the README.
    undirected = G.to_undirected()
    artic_points = set(nx.articulation_points(undirected)) if undirected.number_of_nodes() > 0 else set()

    features = {}
    for node in G.nodes():
        features[node] = {
            "in_degree": G.in_degree(node),
            "out_degree": G.out_degree(node),
            "betweenness_centrality": round(betweenness.get(node, 0.0), 5),
            "clustering_coefficient": round(clustering.get(node, 0.0), 5),
            "is_articulation_point": node in artic_points,
        }
    return features


def main():
    rows = load_traces()
    if not rows:
        raise SystemExit("No traces found -- run scripts/run_baseline.py first.")

    G = build_graph(rows)
    features = compute_features(G)

    OUT_FEATURES.write_text(json.dumps(features, indent=2))
    nx.write_gexf(G, OUT_GEXF)  # importable straight into Gephi, which you already know

    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    spofs = [n for n, f in features.items() if f["is_articulation_point"]]
    print(f"Articulation points (SPOFs) found: {spofs}")
    top_between = sorted(features.items(), key=lambda kv: -kv[1]["betweenness_centrality"])[:5]
    print("Top 5 by betweenness centrality:")
    for n, f in top_between:
        print(f"  {n}: {f['betweenness_centrality']}")
    print(f"Written: {OUT_FEATURES}, {OUT_GEXF}")


if __name__ == "__main__":
    main()
