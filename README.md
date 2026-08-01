# Microservice Dependency Graph & Blast-Radius Prediction 

## Overview
- `services/` : generic OpenTelemetry-instrumented FastAPI service (one process is one microservice, driven by `data/topology.json`)
- `scripts/generate_topology.py` : builds the 18-service dependency graph (hubs, leaves, a forced SPOF)
- `scripts/run_baseline.py` : starts all services, generates healthy traffic, writes `data/traces.jsonl`
- `scripts/build_graph.py` : parses traces into a NetworkX graph, computes centrality/articulation points/clustering → `data/graph_features.json`, `data/graph.gexf` (open directly in Gephi)
- `scripts/fault_injector.py` / `run_campaign.py` : injects faults, measures cascade, writes `data/failure_dataset.csv`
- `scripts/build_ml_dataset.py` : joins failure data ans graph features in `data/ml_dataset.csv`
- `scripts/train_model.py` : trains XGBoost classifier (will it be impacted?) + RandomForest regressor (how severely?), evaluates against the naive "any direct dependency is equally likely" baseline - `data/model.joblib`, `data/model_metrics.json`
- `api/main.py` : FastAPI service exposing `/graph`, `/predict/{service}`, `/metrics`
- `dashboard/index.html` : standalone incident console: force-directed dependency graph, click-to-fail simulation, cascade replay ordered by predicted blast radius

## Quick start
```bash
pip install fastapi uvicorn httpx opentelemetry-api opentelemetry-sdk pandas networkx scikit-learn xgboost joblib

python scripts/generate_topology.py
python scripts/run_baseline.py --duration 25 --rps 4
python scripts/build_graph.py

# full sweep, all 18 services x 4 fault variations
python scripts/run_campaign.py
   
# or run a quick subset:
python scripts/run_campaign.py --targets svc-10 svc-05 --variations 2

python scripts/build_ml_dataset.py
python scripts/train_model.py
```

### Run from the project root (two terminals)
```bash
uvicorn api.main:app --host 127.0.0.1 --port 8080 --reload
python3 -m http.server 8000 --directory dashboard
# then open http://127.0.0.1:8000
```

### Run it with Docker
```bash
python scripts/generate_compose.py     # regenerate compose file if topology changed
docker compose build
docker compose up
# API:       http://localhost:8080  (send header: X-API-Key: devkey)
# Dashboard: http://localhost:8000
```

