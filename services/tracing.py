"""
Lightweight OpenTelemetry setup shared by every service.

We use the real OTel SDK (TracerProvider, spans, attributes) so this is
genuine distributed tracing instrumentation -- but export spans as JSON
lines to a local file instead of standing up a Jaeger/Collector container.
That file is exactly the "traces" Phase 2 parses into a graph. If you want
to point this at a real Jaeger/OTel Collector later, you only change the
exporter in this one file.
"""
import json
import os
import time
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor

TRACE_LOG_PATH = Path(__file__).parent.parent / "data" / "traces.jsonl"


class JsonLinesExporter(SpanExporter):
    """Appends each finished span as one JSON line: service, target, latency,
    success/failure, timestamp -- exactly what Phase 2 needs to build the graph."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(exist_ok=True)

    def export(self, spans):
        with open(self.path, "a") as f:
            for span in spans:
                attrs = dict(span.attributes or {})
                record = {
                    "trace_id": format(span.context.trace_id, "032x"),
                    "span_id": format(span.context.span_id, "016x"),
                    "name": span.name,
                    "service": attrs.get("service.name"),
                    "target": attrs.get("call.target"),
                    "start_ns": span.start_time,
                    "end_ns": span.end_time,
                    "latency_ms": (span.end_time - span.start_time) / 1e6,
                    "status": attrs.get("call.status", "ok"),
                    "http_status": attrs.get("call.http_status"),
                    "retry_count": attrs.get("call.retry_count", 0),
                    "wall_time": time.time(),
                }
                f.write(json.dumps(record) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def get_tracer(service_name: str):
    resource = Resource(attributes={"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(JsonLinesExporter(TRACE_LOG_PATH)))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
