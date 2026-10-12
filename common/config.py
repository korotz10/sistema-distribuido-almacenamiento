"""
Configuración global centralizada para el Sistema Distribuido.
"""
import os
from pathlib import Path

# Directorio raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent

# Configuración del Coordinador (Host A - Santiago)
COORDINATOR_HOST = os.getenv("COORDINATOR_HOST", "0.0.0.0")
COORDINATOR_PORT = int(os.getenv("COORDINATOR_PORT", "8000"))

# Lista oficial de Nodos de Almacenamiento
# Si cambian las IPs en Tailscale, solo las actualizas aquí o por variables de entorno
DEFAULT_NODES = [
    {
        "id": "node1",
        "name": "Nodo 1 (Ivanna)",
        "url": os.getenv("NODE1_URL", "http://100.121.209.117:8001"),
    },
    {
        "id": "node2",
        "name": "Nodo 2 (Dayana)",
        "url": os.getenv("NODE2_URL", "http://100.90.207.18:8002"),
    },
    {
        "id": "node3",
        "name": "Nodo 3 (Victor)",
        "url": os.getenv("NODE3_URL", "http://100.98.240.121:8003"),
    },
]

# Tiempos de espera en segundos para peticiones HTTPX
TIMEOUT_CONNECT = 3.0
TIMEOUT_READ = 10.0

# Límites por proceso y persistencia del coordinador.
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(100 * 1024 * 1024)))
MAX_CONCURRENT_TRANSFERS = int(os.getenv("MAX_CONCURRENT_TRANSFERS", "4"))
TRANSFER_TIMEOUT = float(os.getenv("TRANSFER_TIMEOUT", "120"))
PENDING_TTL = max(float(os.getenv("PENDING_TTL", "600")), TRANSFER_TIMEOUT * 2)
GARBAGE_GRACE = max(float(os.getenv("GARBAGE_GRACE", "300")), TRANSFER_TIMEOUT * 2)
MAINTENANCE_INTERVAL = float(os.getenv("MAINTENANCE_INTERVAL", "30"))
CATALOG_DB = Path(os.getenv("CATALOG_DB", str(BASE_DIR / "catalog.sqlite3")))

if min(MAX_FILE_SIZE, MAX_CONCURRENT_TRANSFERS, TRANSFER_TIMEOUT, MAINTENANCE_INTERVAL) <= 0:
    raise ValueError("Los límites y tiempos de espera deben ser positivos")
