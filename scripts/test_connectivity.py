"""
Script de diagnóstico y prueba de conectividad de red.
Sondea el Coordinador local y todos los Nodos de almacenamiento en Tailscale.
"""
import asyncio
import sys
from pathlib import Path

# Ajustar el path para ejecutar el script directamente desde cualquier ubicación
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import httpx
from common.config import COORDINATOR_PORT, DEFAULT_NODES, TIMEOUT_CONNECT


async def test_node(node: dict) -> bool:
    """Envía una petición GET /health a un nodo de almacenamiento."""
    health_url = f"{node['url']}/health"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_CONNECT) as client:
            response = await client.get(health_url)
            if response.status_code == 200:
                data = response.json()
                files = data.get("files_stored", 0)
                print(
                    f"🟢 [{node['id']}] {node['name']} ({node['url']}) "
                    f"-> ONLINE | Archivos en disco: {files}"
                )
                return True
            else:
                print(
                    f"🟡 [{node['id']}] {node['name']} ({node['url']}) "
                    f"-> HTTP Error {response.status_code}"
                )
                return False
    except Exception as exc:
        print(
            f"🔴 [{node['id']}] {node['name']} ({node['url']}) "
            f"-> INACCESIBLE ({type(exc).__name__})"
        )
        return False


async def test_coordinator() -> bool:
    """Verifica si el Coordinador local está ejecutándose."""
    url = f"http://localhost:{COORDINATOR_PORT}/status"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_CONNECT) as client:
            response = await client.get(url)
            if response.status_code == 200:
                print(f"🧠 [Coordinador] Local (Puerto {COORDINATOR_PORT}) -> ONLINE")
                return True
            else:
                print(f"🟡 [Coordinador] Local (Puerto {COORDINATOR_PORT}) -> HTTP Error {response.status_code}")
                return False
    except Exception:
        print(f"🔴 [Coordinador] Local (Puerto {COORDINATOR_PORT}) -> NO EJECUTANDO")
        return False


async def main():
    print("=" * 70)
    print("🔍 VERIFICACIÓN DE CONECTIVIDAD RED DISTRIBUIDA (TAILSCALE)")
    print("=" * 70)

    print("\n1. Verificando Estado del Coordinador (Host A):")
    await test_coordinator()

    print("\n2. Verificando Nodos de Almacenamiento Remotos:")
    tasks = [test_node(node) for node in DEFAULT_NODES]
    results = await asyncio.gather(*tasks)

    print("\n" + "=" * 70)
    online_count = sum(1 for r in results if r)
    total_nodes = len(DEFAULT_NODES)
    print(f"📊 RESUMEN RED: {online_count}/{total_nodes} Nodos de Almacenamiento respondiendo.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())