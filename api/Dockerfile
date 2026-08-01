FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn joblib networkx pandas scikit-learn xgboost

COPY api/ /app/api/
COPY data/topology.json /app/data/topology.json
COPY data/graph_features.json /app/data/graph_features.json
COPY data/model.joblib /app/data/model.joblib

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
