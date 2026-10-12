"""Sondeo independiente del coordinador y los nodos; salida 0 si todos responden."""
import asyncio
import os
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import httpx

from common.config import COORDINATOR_PORT, TIMEOUT_CONNECT
from coordinator.services import check_node_health, get_nodes


async def test_coordinator():
    base = os.getenv("COORDINATOR_URL", f"http://127.0.0.1:{COORDINATOR_PORT}").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_CONNECT, trust_env=False) as client:
            response = await client.get(base + "/health")
            valid = response.status_code == 200 and response.json().get("service") == "coordinator"
            print(f"Coordinador {base}: {'READY' if valid else 'ERROR HTTP ' + str(response.status_code)}")
            return valid
    except (httpx.HTTPError, ValueError):
        print(f"Coordinador {base}: INACCESIBLE")
        return False


async def test_node(node):
    ready, details = await check_node_health(node)
    print(f"{node['id']} {node['url']}: {'READY' if ready else 'DOWN'} | {details}")
    return ready


async def main():
    nodes = get_nodes()
    results = await asyncio.gather(test_coordinator(), *(test_node(node) for node in nodes))
    print(f"Nodos disponibles: {sum(results[1:])}/{len(nodes)}. La integridad se consulta en /integrity.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
