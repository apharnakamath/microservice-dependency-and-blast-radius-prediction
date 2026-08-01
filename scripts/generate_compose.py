"""
Generates docker-compose.yml from data/topology.json so the 18 microservice
blocks (and any future N) never have to be hand-written or hand-edited when
the topology changes.

Usage:
    python scripts/generate_compose.py
Writes: docker-compose.yml (project root)
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
TOPOLOGY_PATH = ROOT / "data" / "topology.json"
OUT_PATH = ROOT / "docker-compose.yml"


def main():
    topo = json.loads(TOPOLOGY_PATH.read_text())
    services = topo["services"]

    lines = ["services:"]

    for name, cfg in services.items():
        port = cfg["port"]
        lines += [
            f"  {name}:",
            f"    build:",
            f"      context: .",
            f"      dockerfile: services/Dockerfile",
            f"    environment:",
            f"      - SERVICE_NAME={name}",
            f"      - PORT={port}",
            f"    networks: [blast-radius]",
            f"    restart: unless-stopped",
            "",
        ]

    # API depends on every service being up (it doesn't call them directly,
    # but the model/predictions are only meaningful once the mesh is live)
    dep_list = "\n".join(f"      - {n}" for n in services)
    lines += [
        "  api:",
        "    build:",
        "      context: .",
        "      dockerfile: api/Dockerfile",
        "    ports:",
        '      - "8080:8080"',
        "    environment:",
        "      - API_KEY=${API_KEY:-devkey}",
        "    depends_on:",
        dep_list,
        "    networks: [blast-radius]",
        "    restart: unless-stopped",
        "",
        "  dashboard:",
        "    build:",
        "      context: .",
        "      dockerfile: dashboard/Dockerfile",
        "    ports:",
        '      - "8000:80"',
        "    depends_on:",
        "      - api",
        "    networks: [blast-radius]",
        "    restart: unless-stopped",
        "",
        "networks:",
        "  blast-radius:",
        "    driver: bridge",
        "",
    ]

    OUT_PATH.write_text("\n".join(lines))
    print(f"Wrote {OUT_PATH} ({len(services)} service containers + api + dashboard)")


if __name__ == "__main__":
    main()
