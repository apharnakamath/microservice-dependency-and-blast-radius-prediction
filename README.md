# Blast Radius - Microservice Dependency Graph & Incident Prediction

Predicts what happens next when a microservice fails: which other services get hit, how badly, and how fast. Trained on real cascading-failure data from a live 18-service mesh, not synthetic labels.

**Live demo:** https://microservice-dependency-and-blast-radius.onrender.com

---

## What it does

- Simulates an 18-service microservice architecture (entrypoints, hubs, leaves, one deliberate single point of failure)
- Computes graph-structural features per service: betweenness centrality, in/out-degree, articulation-point status
- Runs controlled fault-injection experiments (kill/slow each service, multiple severities) and measures the real cascade
- Trains an XGBoost classifier and RandomForest regressor to predict blast radius, benchmarked against a naive baseline
- Serves live predictions via a FastAPI backend, visualized in a D3.js incident-console dashboard

## Results

| | Naive baseline | Trained model |
|---|---|---|
| F1 | 0.55 | **0.83** |
| Improvement | — | **+0.28 F1** |

`graph_distance` alone accounts for ~74% of feature importance and structural position in the call graph does most of the predictive work.

**Key finding:** the deliberately-planted single point of failure did *not* register as a graph-theoretic articulation point (it's a leaf, so removing it doesn't disconnect the graph) yet it had ~5x the empirical blast radius of comparable services. Structural centrality alone would have missed it; the fault-injection data caught it.

---

## How it works

- **18 services** = 18 real FastAPI processes (one file, `services/app.py`, driven by `SERVICE_NAME` env var + `topology.json` config), calling each other over real HTTP
- **"Down/slow"** = not killing processes, each service holds an in-memory fault flag, toggled via `POST /admin/fault`. When "down," it deliberately returns 503s at a set probability; when "slow," it adds latency
- **Cascades are real, not scripted** : every service checks its own downstream call results; if enough dependencies fail, it fails too and passes that failure upward. Nobody hardcodes which services affect which it emerges from real HTTP calls
- **The fault injector** flips one service's flag, polls all 17 others, and logs (failed service, affected service, severity, time-to-impact) as one labeled training row
- **The API doesn't touch the live mesh at request time** - it only reads the pre-trained model + static graph features, so it's cheap to run in production without the 18-container mesh behind it

---

## Run it locally

```bash
pip install fastapi uvicorn httpx opentelemetry-api opentelemetry-sdk pandas networkx scikit-learn xgboost joblib

# Phase 1-2: build topology, generate traffic, build dependency graph
python scripts/generate_topology.py
python scripts/run_baseline.py --duration 25 --rps 4
python scripts/build_graph.py

# Phase 3: fault-injection campaign -> labeled cascade data
python scripts/run_campaign.py

# Phase 4: build ML dataset + train model
python scripts/build_ml_dataset.py
python scripts/train_model.py
```

## Run the API + dashboard locally

```bash
# terminal 1
uvicorn api.main:app --host 127.0.0.1 --port 8080 --reload

# terminal 2
python3 -m http.server 8000 --directory dashboard
```

Open `http://127.0.0.1:8000`. Test the API directly:
```bash
curl -H "X-API-Key: devkey" http://127.0.0.1:8080/predict/svc-10   #replace the devkey
```

## Run it with Docker

```bash
python scripts/generate_compose.py
docker compose build
docker compose up
# API: http://localhost:8080  |  Dashboard: http://localhost:8000
```


CI (`.github/workflows/ci.yml`) builds all images and smoke-tests `/health`, `/predict`, and auth enforcement on every push.

---
