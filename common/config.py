"""
Configuración global centralizada para el Sistema Distribuido.
Contiene las IPs oficiales de Tailscale y los puertos asignados a cada integrante.
"""
import os
from pathlib import Path

# Directorio raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent

# Configuración del Coordinador (Host A - Santiago)
COORDINATOR_HOST = os.getenv("COORDINATOR_HOST", "0.0.0.0")
COORDINATOR_PORT = int(os.getenv("COORDINATOR_PORT", "8000"))

# Lista oficial de Nodos de Almacenamiento con sus IPs reales de Tailscale
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

# Tiempos de espera en segundos para peticiones HTTPX en red Mesh
TIMEOUT_CONNECT = 3.0
TIMEOUT_READ = 10.0