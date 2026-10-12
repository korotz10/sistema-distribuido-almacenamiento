"""Comunicación con nodos y verificación de cada transferencia."""
import asyncio
import hashlib
from pathlib import Path
import tempfile
from urllib.parse import quote

import httpx

from common.config import DEFAULT_NODES, TIMEOUT_CONNECT, TIMEOUT_READ
from common.files import CHUNK_SIZE, finish_thread

# Punto de inyección usado por pruebas de integración, sin reemplazar la lógica HTTP.
client_factory = httpx.AsyncClient


class NodeError(Exception):
    def __init__(self, message, status_code=503):
        super().__init__(message)
        self.status_code = status_code


def get_nodes():
    nodes = [dict(node, url=node["url"].rstrip("/")) for node in DEFAULT_NODES]
    if (len(nodes) < 2 or len({node["id"] for node in nodes}) != len(nodes)
            or len({str(httpx.URL(node["url"])) for node in nodes}) != len(nodes)):
        raise NodeError("Configura al menos dos nodos con identificadores y URL distintos")
    for node in nodes:
        url = httpx.URL(node["url"])
        if url.scheme not in ("http", "https") or not url.host or url.username or url.password or url.query or url.fragment:
            raise NodeError("La URL de un nodo no es válida")
    return nodes


def node_client(timeout=None):
    return client_factory(
        timeout=timeout or httpx.Timeout(TIMEOUT_READ, connect=TIMEOUT_CONNECT),
        headers={"Accept-Encoding": "identity"},
        trust_env=False,
    )


async def check_node_health(node):
    try:
        async with node_client(TIMEOUT_CONNECT) as client:
            response = await client.get(node["url"] + "/health")
            if response.status_code != 200:
                return False, {"error": f"HTTP {response.status_code}"}
            details = response.json()
            valid = (isinstance(details, dict) and details.get("status") == "ok"
                     and details.get("node_id") == node["id"] and details.get("writable") is True
                     and isinstance(details.get("storage_id"), str) and bool(details["storage_id"])
                     and type(details.get("free_bytes")) is int and details["free_bytes"] >= 0)
            return valid, details if valid else {"error": "Identidad o almacenamiento del nodo no válido"}
    except (httpx.HTTPError, ValueError, NodeError) as exc:
        return False, {"error": str(exc) or type(exc).__name__}


async def store_file_in_node(node, storage_key, source: Path, size, sha256, storage_id):
    try:
        with source.open("rb") as handle:
            async with node_client() as client:
                response = await client.post(node["url"] + "/store",
                    files={"file": (storage_key, handle, "application/octet-stream")},
                    data={"filename": storage_key})
        if response.status_code != 201:
            raise NodeError(f"{node['id']}: HTTP {response.status_code}")
        ack = response.json()
        expected = {"status": "stored", "node_id": node["id"], "storage_id": storage_id,
                    "filename": storage_key, "size_bytes": size, "sha256": sha256}
        if not isinstance(ack, dict) or any(ack.get(key) != value for key, value in expected.items()):
            raise NodeError(f"{node['id']}: confirmación con metadatos incorrectos", 502)
        return ack
    except (httpx.HTTPError, ValueError, OSError) as exc:
        raise NodeError(f"{node['id']}: error durante la transferencia") from exc


async def retrieve_file_from_node(node, storage_key, size, sha256):
    """Verificar primero en un temporal; jamás enviar al cliente bytes sin verificar."""
    path = None
    try:
        async with node_client() as client:
            async with client.stream("GET", node["url"] + "/retrieve/" + quote(storage_key, safe="")) as response:
                if response.status_code != 200:
                    raise NodeError(f"{node['id']}: HTTP {response.status_code}")
                digest, received = hashlib.sha256(), 0
                with tempfile.NamedTemporaryFile(prefix="mini-drive-download-", delete=False) as handle:
                    path = Path(handle.name)
                    async for chunk in response.aiter_bytes(CHUNK_SIZE):
                        received += len(chunk)
                        if received > size:
                            raise NodeError(f"{node['id']}: tamaño de archivo incorrecto", 502)
                        digest.update(chunk)
                        await finish_thread(handle.write, chunk)
                if received != size or digest.hexdigest() != sha256:
                    raise NodeError(f"{node['id']}: integridad SHA-256 incorrecta", 502)
        return path
    except BaseException as exc:
        if path:
            path.unlink(missing_ok=True)
        if isinstance(exc, (httpx.HTTPError, OSError)):
            raise NodeError(f"{node['id']}: error durante la descarga") from exc
        raise


async def delete_file_in_node(node, storage_key):
    try:
        async with node_client() as client:
            response = await client.delete(node["url"] + "/objects/" + quote(storage_key, safe=""))
            return response.status_code == 204
    except (httpx.HTTPError, NodeError):
        return False


async def cleanup_pending(catalog):
    await finish_thread(catalog.expire_uploads)
    nodes = {node["id"]: node for node in get_nodes()}
    pending = await asyncio.to_thread(catalog.garbage)
    semaphore = asyncio.Semaphore(8)

    async def clean(item):
        node = nodes.get(item["node_id"])
        async with semaphore:
            if node and await delete_file_in_node(node, item["storage_key"]):
                await finish_thread(catalog.acknowledge_cleanup, item["storage_key"], item["node_id"])
                return 1
            await finish_thread(catalog.retry_cleanup_later, item["storage_key"], item["node_id"])
        return 0

    removed = sum(await asyncio.gather(*(clean(item) for item in pending)))
    return {"processed": len(pending), "cleaned": removed, "pending_in_batch": len(pending) - removed}
