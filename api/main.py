"""
Serves:
  GET  /graph                     -> full topology + structural features, for the dashboard to render
  GET  /predict/{service}         -> ranked blast-radius prediction if `service` fails
  GET  /health

Run: uvicorn api.main:app --port 8080 --reload   (from the project root)
"""
import json
import os
from pathlib import Path

import joblib
import networkx as nx
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader

ROOT = Path(__file__).parent.parent
API_KEY = os.environ.get("API_KEY", "devkey")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key header")
    return key
TOPOLOGY = json.loads((ROOT / "data" / "topology.json").read_text())
FEATURES = json.loads((ROOT / "data" / "graph_features.json").read_text())
MODEL_BUNDLE = joblib.load(ROOT / "data" / "model.joblib")
CLF = MODEL_BUNDLE["classifier"]
REG = MODEL_BUNDLE["regressor"]
FEATURE_COLS = MODEL_BUNDLE["feature_cols"]

SERVICES = TOPOLOGY["services"]

app = FastAPI(title="Blast Radius Predictor")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def build_call_graph():
    G = nx.DiGraph()
    G.add_nodes_from(SERVICES)
    for src, cfg in SERVICES.items():
        for c in cfg["calls"]:
            G.add_edge(src, c["target"])
    return G


CALL_GRAPH = build_call_graph()


def graph_distance(affected: str, failed: str):
    try:
        return nx.shortest_path_length(CALL_GRAPH, source=affected, target=failed)
    except nx.NetworkXNoPath:
        return None


def direct_edge_config(src: str, dst: str):
    for call in SERVICES[src]["calls"]:
        if call["target"] == dst:
            return call
    return None


def build_features(failed: str, affected: str, mode: str, severity: float) -> dict:
    ff = FEATURES.get(failed, {})
    af = FEATURES.get(affected, {})
    dist = graph_distance(affected, failed)
    edge = direct_edge_config(affected, failed)
    return {
        "graph_distance": dist if dist is not None else 99,
        "injected_severity": severity,
        "fault_mode_down": 1 if mode == "down" else 0,
        "failed_betweenness": ff.get("betweenness_centrality", 0.0),
        "failed_in_degree": ff.get("in_degree", 0),
        "failed_out_degree": ff.get("out_degree", 0),
        "failed_is_articulation_point": int(ff.get("is_articulation_point", False)),
        "affected_betweenness": af.get("betweenness_centrality", 0.0),
        "affected_in_degree": af.get("in_degree", 0),
        "affected_out_degree": af.get("out_degree", 0),
        "has_direct_edge": int(edge is not None),
        "direct_edge_timeout_ms": edge["timeout_ms"] if edge else -1,
        "direct_edge_retries": edge["retries"] if edge else -1,
    }, dist


@app.get("/health")
def health():
    return {"ok": True, "services": len(SERVICES)}


@app.get("/metrics", dependencies=[Security(require_api_key)])
def metrics():
    path = ROOT / "data" / "model_metrics.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="run scripts/train_model.py first")
    return json.loads(path.read_text())



@app.get("/graph", dependencies=[Security(require_api_key)])
def graph():
    nodes = [
        {
            "id": name,
            "role": cfg.get("role", "unknown"),
            **FEATURES.get(name, {}),
        }
        for name, cfg in SERVICES.items()
    ]
    edges = [
        {"source": src, "target": c["target"], "timeout_ms": c["timeout_ms"], "retries": c["retries"]}
        for src, cfg in SERVICES.items() for c in cfg["calls"]
    ]
    return {"nodes": nodes, "edges": edges, "entrypoints": TOPOLOGY.get("entrypoints", [])}


@app.get("/predict/{service}", dependencies=[Security(require_api_key)])
def predict(service: str, mode: str = Query("down", pattern="^(down|slow)$"),
            severity: float = Query(1.0, ge=0.0, le=1.0)):
    if service not in SERVICES:
        raise HTTPException(status_code=404, detail=f"unknown service '{service}'")

    rows = []
    row_meta = []
    for other in SERVICES:
        if other == service:
            continue
        feats, dist = build_features(service, other, mode, severity)
        rows.append(feats)
        row_meta.append((other, dist))

    X = pd.DataFrame(rows)[FEATURE_COLS]
    proba = CLF.predict_proba(X)[:, 1]
    sev_pred = REG.predict(X)

    predictions = []
    for (other, dist), p, s in zip(row_meta, proba, sev_pred):
        predictions.append({
            "service": other,
            "graph_distance": dist,
            "predicted_probability": round(float(p), 4),
            "predicted_severity": round(max(0.0, float(s)), 4),
        })

    predictions.sort(key=lambda r: -r["predicted_probability"])

    return {
        "failed_service": service,
        "fault_mode": mode,
        "injected_severity": severity,
        "blast_radius": predictions,
        "predicted_impacted_count": sum(1 for p in predictions if p["predicted_probability"] >= 0.5),
    }
