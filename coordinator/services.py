"""
Servicios Distribuidos: Selección Round-Robin, Replicación Síncrona y Consultas HTTP a Nodos.
"""
from typing import Any, Dict, Optional, Tuple
import httpx

# Importamos la configuración centralizada desde common.config
from common.config import DEFAULT_NODES, TIMEOUT_CONNECT, TIMEOUT_READ
from coordinator.catalog import CatalogManager

catalog = CatalogManager()


def get_nodes() -> list[Dict[str, str]]:
    """Retorna la lista de nodos centralizada en common.config."""
    return DEFAULT_NODES


def select_nodes_round_robin() -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Selecciona el Nodo Principal por turno cíclico (Round-Robin)
    y asigna el siguiente nodo consecutivo como Réplica.
    """
    nodes = get_nodes()
    total_nodes = len(nodes)
    current_index = catalog.get_round_robin_index()

    primary_node = nodes[current_index % total_nodes]
    replica_node = nodes[(current_index + 1) % total_nodes]

    # Avanzar el turno para la siguiente subida
    catalog.set_round_robin_index((current_index + 1) % total_nodes)

    return primary_node, replica_node


async def check_node_health(node_url: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Sondea el endpoint /health de un nodo para diagnosticar su estado."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_CONNECT) as client:
            response = await client.get(f"{node_url}/health")
            if response.status_code == 200:
                return True, response.json()
    except Exception:
        pass
    return False, None


async def store_file_in_node(node_url: str, filename: str, content: bytes) -> Optional[Dict[str, Any]]:
    """
    Envía los bytes de un archivo al endpoint /store de un nodo mediante multipart/form-data.
    """
    try:
        files = {"file": (filename, content)}
        data = {"filename": filename}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_READ, connect=TIMEOUT_CONNECT)
        ) as client:
            response = await client.post(f"{node_url}/store", files=files, data=data)
            if response.status_code == 201:
                return response.json()
    except Exception as exc:
        print(f"❌ Error comunicando con nodo {node_url}: {exc}")
    return None


async def retrieve_file_from_node(node_url: str, filename: str) -> Optional[bytes]:
    """Solicita los bytes de un archivo al endpoint /retrieve/{filename} de un nodo."""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT_READ, connect=TIMEOUT_CONNECT)
        ) as client:
            response = await client.get(f"{node_url}/retrieve/{filename}")
            if response.status_code == 200:
                return response.content
    except Exception:
        pass
    return None

async def list_node_files(node_url: str) -> Optional[list]:
    """Pide al nodo la lista real de archivos que tiene en disco (None si el nodo no responde)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_CONNECT) as client:
            response = await client.get(f"{node_url}/files")
            if response.status_code == 200:
                return response.json().get("files", [])
    except Exception:
        pass
    return None
