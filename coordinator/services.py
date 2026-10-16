"""Comunicación HTTP sencilla: solicitar, comprobar y usar la respuesta."""
import asyncio
from urllib.parse import quote

import httpx

from common.config import DEFAULT_NODES, TIMEOUT_CONNECT, TIMEOUT_READ
from common.files import sha256

# Las pruebas sustituyen el transporte; las peticiones y comprobaciones son las mismas.
client_factory = httpx.Client
async_client_factory = httpx.AsyncClient


class NodeError(Exception):
    pass


def get_nodes():
    nodes = [dict(node, url=node["url"].rstrip("/")) for node in DEFAULT_NODES]
    if (len(nodes) != 3 or len({n["id"] for n in nodes}) != 3
            or len({str(httpx.URL(n["url"])) for n in nodes}) != 3):
        raise NodeError("Configura tres nodos con identificadores y URL distintos")
    for node in nodes:
        url = httpx.URL(node["url"])
        if (url.scheme not in ("http", "https") or not url.host or url.userinfo or url.query or url.fragment
                or url.path not in ("", "/")):
            raise NodeError("URL de nodo incorrecta")
    return nodes


def node_client():
    return client_factory(timeout=httpx.Timeout(TIMEOUT_READ, connect=TIMEOUT_CONNECT),
                          headers={"Accept-Encoding": "identity"}, trust_env=False)


def valid_identity(data, node):
    return (isinstance(data, dict) and data.get("node_id") == node["id"]
            and isinstance(data.get("storage_id"), str) and bool(data["storage_id"]))


async def read_node_json(node, path, timeout):
    async def request():
        async with async_client_factory(timeout=timeout, trust_env=False,
                                       headers={"Accept-Encoding": "identity"}) as client:
            response = await client.get(node["url"] + path)
            response.raise_for_status()
            return response.json()
    # Límite total, además de los tiempos de conexión/lectura del cliente.
    return await asyncio.wait_for(request(), timeout=timeout)


async def probe_node(node, timeout):
    try:
        data = await read_node_json(node, "/health", timeout)
        valid = valid_identity(data, node) and data.get("status") == "ok"
        return valid, data if valid else {"error": "La identidad o el estado del nodo no coincide"}
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
        return False, {"error": str(exc) or type(exc).__name__}


def check_node_health(node):
    try:
        with node_client() as client:
            response = client.get(node["url"] + "/health")
            response.raise_for_status()
            data = response.json()
        valid = (isinstance(data, dict) and data.get("status") == "ok"
                 and data.get("node_id") == node["id"]
                 and isinstance(data.get("storage_id"), str) and bool(data["storage_id"]))
        return valid, data if valid else {"error": "La identidad no coincide"}
    except (httpx.HTTPError, ValueError) as exc:
        return False, {"error": str(exc) or type(exc).__name__}


def store_file_in_node(node, key, content, storage_id):
    try:
        with node_client() as client:
            response = client.post(node["url"] + "/store", files={"file": (key, content)})
        expected = {"status": "stored", "node_id": node["id"], "storage_id": storage_id,
                    "filename": key, "size_bytes": len(content), "sha256": sha256(content)}
        if response.status_code != 201 or response.json() != expected:
            raise NodeError(f"{node['id']}: no confirmó una copia correcta")
    except (httpx.HTTPError, ValueError) as exc:
        raise NodeError(f"{node['id']}: error al enviar el archivo") from exc


def retrieve_file_from_node(node, record):
    try:
        with node_client() as client:
            url = node["url"] + "/retrieve/" + quote(record["storage_key"], safe="")
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    content.extend(chunk)
                    if len(content) > record["size_bytes"]:
                        raise NodeError(f"{node['id']}: tamaño incorrecto")
        if len(content) != record["size_bytes"] or sha256(content) != record["sha256"]:
            raise NodeError(f"{node['id']}: contenido incorrecto")
        return bytes(content)
    except httpx.HTTPError as exc:
        raise NodeError(f"{node['id']}: no se pudo recuperar el archivo") from exc


def delete_file_from_node(node, record):
    ready, identity = check_node_health(node)
    expected_storage = record.get("storage_ids", {}).get(node["id"])
    if not ready or (expected_storage and identity["storage_id"] != expected_storage):
        raise NodeError(f"{node['id']}: no responde el almacenamiento esperado")
    try:
        with node_client() as client:
            response = client.delete(node["url"] + "/store/" + quote(record["storage_key"], safe=""))
            response.raise_for_status()
            data = response.json()
        if (response.status_code != 200 or not valid_identity(data, node)
                or data["storage_id"] != identity["storage_id"]
                or data.get("filename") != record["storage_key"]
                or data.get("status") not in ("deleted", "absent")):
            raise NodeError(f"{node['id']}: confirmación de eliminación incorrecta")
    except (httpx.HTTPError, ValueError) as exc:
        raise NodeError(f"{node['id']}: no se confirmó la eliminación") from exc


async def list_node_files(node, timeout):
    try:
        data = await read_node_json(node, "/files", timeout)
        if not valid_identity(data, node) or not isinstance(data.get("files"), list):
            raise ValueError("Identidad o inventario incorrectos")
        keys = set()
        for item in data["files"]:
            if (not isinstance(item, dict) or not isinstance(item.get("storage_key"), str)
                    or not item["storage_key"] or item["storage_key"] in keys
                    or type(item.get("size_bytes")) is not int or item["size_bytes"] < 0
                    or not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in item["sha256"])):
                raise ValueError("Inventario incorrecto")
            keys.add(item["storage_key"])
        return {"reachable": True, "source": "disk", "storage_id": data["storage_id"], "files": data["files"]}
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
        return {"reachable": False, "source": "disk", "files": [], "error": str(exc) or type(exc).__name__}
