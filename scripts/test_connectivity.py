"""Consultar el coordinador y cada nodo. Salida 0 si todos responden, 1 si alguno falla."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from common.config import COORDINATOR_PORT, TIMEOUT_CONNECT
from coordinator.services import check_node_health, get_nodes


def main():
    base = os.getenv("COORDINATOR_URL", f"http://127.0.0.1:{COORDINATOR_PORT}").rstrip("/")
    try:
        with httpx.Client(timeout=TIMEOUT_CONNECT, trust_env=False) as client:
            response = client.get(base + "/health")
            data = response.json()
            coordinator_ok = (response.status_code == 200 and isinstance(data, dict)
                              and data.get("service") == "coordinator" and data.get("status") == "ok")
    except (httpx.HTTPError, ValueError):
        coordinator_ok = False
    print(f"Coordinador {base}: {'UP' if coordinator_ok else 'DOWN'}")
    results = []
    for node in get_nodes():
        ready, details = check_node_health(node)
        results.append(ready)
        print(f"{node['id']} {node['url']}: {'UP' if ready else 'DOWN'} | {details}")
    print(f"Nodos disponibles: {sum(results)}/{len(results)}")
    print("Esto comprueba HTTP. La evidencia de cuatro equipos distintos se documenta por separado.")
    return 0 if coordinator_ok and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
