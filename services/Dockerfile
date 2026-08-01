FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx opentelemetry-api opentelemetry-sdk pydantic

COPY services/ /app/services/
COPY data/topology.json /app/data/topology.json

WORKDIR /app/services
ENV SERVICE_HOST=docker
ENV PYTHONUNBUFFERED=1

# SERVICE_NAME and PORT are injected per-container by docker-compose
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
