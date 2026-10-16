"""Configuración del laboratorio: un coordinador y tres nodos HTTP."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
COORDINATOR_HOST = os.getenv("COORDINATOR_HOST", "100.116.61.8")
COORDINATOR_PORT = int(os.getenv("COORDINATOR_PORT", "8000"))
CATALOG_FILE = Path(os.getenv("CATALOG_FILE", str(BASE_DIR / "catalog.json")))
NODE_ID = os.getenv("NODE_ID", "node1")
NODE_PORT = int(os.getenv("NODE_PORT", "8001"))
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", str(BASE_DIR / "storage")))

# Direcciones del equipo de trabajo; se pueden sustituir desde el entorno.
DEFAULT_NODES = [
    {"id": "node1", "name": "Nodo 1 (Ivanna)",
     "url": os.getenv("NODE1_URL", "http://100.121.209.117:8001")},
    {"id": "node2", "name": "Nodo 2 (Dayana)",
     "url": os.getenv("NODE2_URL", "http://100.90.207.18:8002")},
    {"id": "node3", "name": "Nodo 3 (Victor)",
     "url": os.getenv("NODE3_URL", "http://100.98.240.121:8003")},
]

TIMEOUT_CONNECT = 3.0
TIMEOUT_READ = 10.0
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(10 * 1024 * 1024)))
if MAX_FILE_SIZE <= 0:
    raise ValueError("MAX_FILE_SIZE debe ser positivo")
