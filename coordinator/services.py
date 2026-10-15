"""Comunicación HTTP sencilla: solicitar, comprobar y usar la respuesta."""
from urllib.parse import quote

import httpx

from common.config import DEFAULT_NODES, TIMEOUT_CONNECT, TIMEOUT_READ
from common.files import sha256

# Las pruebas sustituyen el transporte; las peticiones y comprobaciones son las mismas.
client_factory = httpx.Client


class NodeError(Exception):
    pass


def get_nodes():
    nodes = [dict(node, url=node["url"].rstrip("/")) for node in DEFAULT_NODES]
    if (len(nodes) != 3 or len({n["id"] for n in nodes}) != 3
            or len({str(httpx.URL(n["url"])) for n in nodes}) != 3):
        raise NodeError("Configura tres nodos con identificadores y URL distintos")
    for node in nodes:
        url = httpx.URL(node["url"])
        if url.scheme not in ("http", "https") or not url.host or url.userinfo or url.query or url.fragment:
            raise NodeError("URL de nodo incorrecta")
    return nodes


def node_client():
    return client_factory(timeout=httpx.Timeout(TIMEOUT_READ, connect=TIMEOUT_CONNECT),
                          headers={"Accept-Encoding": "identity"}, trust_env=False)


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
