"""Diagnóstico aislado: no modifica el código ejecutable ni los datos del proyecto.

Ejecutar desde la raíz: python docs/diagnostico-ui/reproducir.py
Reproduce el commit histórico a942ed8, no el árbol de trabajo corregido.
Guarda resultados junto a este archivo. Requiere Git y las dependencias del proyecto.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
RESULTS = {}


def run(label, root, env, args):
    result = subprocess.run(
        [sys.executable, *args], cwd=root, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    (OUT / (label + ".txt")).write_text(result.stdout + result.stderr, encoding="utf-8")
    RESULTS[label] = {"returncode": result.returncode, "command": args}
    print(label, "exit=", result.returncode, flush=True)
    print((result.stdout + result.stderr)[-2200:], flush=True)


CHECKS = r'''
import json, time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from common.files import sha256
from coordinator import main as coordinator, services
from coordinator.catalog import CatalogManager
from node.main import create_app as create_node

nodes = [{"id": f"node{i}", "name": f"Nodo {i}", "url": f"http://node{i}"} for i in (1,2,3)]
root = Path("checks-data")
root.mkdir()
with ExitStack() as stack:
    clients = {n["id"]: stack.enter_context(TestClient(create_node(root / n["id"], n["id"]))) for n in nodes}
    manager = CatalogManager(root / "catalog.json")
    record = {"filename": "audit.txt", "storage_key": "audit-key.txt", "size_bytes": 4,
              "sha256": sha256(b"data"), "primary_node": "node1", "replica_node": "node2",
              "created_at": "2026-10-15T00:00:00Z", "status": "COMMITTED"}
    manager.add_file(record, 3)
    stack.enter_context(patch.object(services, "DEFAULT_NODES", nodes))
    def route(request):
        return clients[request.url.host].request(request.method, request.url.path,
                    content=request.read(), headers=dict(request.headers))
    stack.enter_context(patch.object(services, "client_factory", lambda **kw: httpx.Client(transport=httpx.MockTransport(route), **kw)))
    app = coordinator.create_app(manager)
    client = stack.enter_context(TestClient(app))
    output = {}
    output["inventory_before_first_probe"] = client.get("/nodes/files").json()
    output["status"] = client.get("/status").json()
    output["inventory_without_physical_files"] = client.get("/nodes/files").json()
    output["download_without_physical_files"] = client.get("/download/audit.txt").status_code
    output["public_file_record"] = client.get("/files").json()["files"]["audit.txt"]
    output["node_inventory_endpoint"] = clients["node1"].get("/files").status_code
    output["ui"] = {"status": client.get("/ui").status_code, "contains_title": "Mini Drive Distribuido" in client.get("/ui").text}
    for nid in ("node1", "node2"):
        clients[nid].post("/store", files={"file": ("audit-key.txt", b"data")})
    def partial_delete(url, **kw):
        request = httpx.Request("DELETE", url)
        if request.url.host == "node2":
            raise httpx.ConnectError("simulated node2 unavailable", request=request)
        return clients[request.url.host].delete(request.url.path)
    with patch.object(coordinator.httpx, "delete", partial_delete):
        response = client.delete("/files/audit.txt")
    output["partial_delete"] = {"status": response.status_code, "body": response.json(),
        "node1_exists": (root / "node1/audit-key.txt").exists(),
        "node2_exists": (root / "node2/audit-key.txt").exists(),
        "still_in_catalog": manager.get_file("audit.txt") is not None,
        "inventory": client.get("/nodes/files").json()}
    with patch.object(coordinator.httpx, "delete", lambda url, **kw: clients[httpx.URL(url).host].delete(httpx.URL(url).path)):
        output["delete_retry"] = client.delete("/files/audit.txt").status_code
    manager.add_file(record, 3)
    for nid in ("node1", "node2"):
        clients[nid].post("/store", files={"file": ("audit-key.txt", b"data")})
    with patch.object(coordinator.httpx, "delete", lambda *a, **kw: httpx.Response(200, json={"unexpected": True})):
        response = client.delete("/files/audit.txt")
    output["delete_invalid_ack"] = {"status": response.status_code,
        "still_in_catalog": manager.get_file("audit.txt") is not None,
        "node1_exists": (root / "node1/audit-key.txt").exists(),
        "node2_exists": (root / "node2/audit-key.txt").exists()}
    def slow_health(node):
        time.sleep(0.3)
        return True, {"status": "ok", "node_id": node["id"], "storage_id": node["id"]}
    with patch.object(services, "check_node_health", slow_health), patch.object(coordinator, "PROBE_TIMEOUT", 0.04):
        started = time.monotonic()
        response = client.get("/status?refresh=true")
        output["probe_timeout"] = {"elapsed_seconds": round(time.monotonic()-started, 3),
            "timeout_per_future": 0.04, "body": response.json()}
        time.sleep(0.35)
    print(json.dumps(output, ensure_ascii=False, indent=2))
'''


def main():
    baseline = subprocess.check_output(["git", "show", "db66fd4:coordinator/services.py"], cwd=BASE)
    RESULTS["commit"] = subprocess.check_output(["git", "rev-parse", "a942ed8"], cwd=BASE, text=True).strip()
    RESULTS["working_tree_status"] = subprocess.check_output(["git", "status", "--short"], cwd=BASE, text=True)
    RESULTS["ui_file_present"] = (BASE / "coordinator/static/index.html").is_file()
    RESULTS["python"] = sys.version
    with tempfile.TemporaryDirectory(prefix="mini-drive-diagnostic-") as temporary:
        root = Path(temporary)
        paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "a942ed8", "--",
            "common", "coordinator", "node", "scripts", "tests"], cwd=BASE, text=True).splitlines()
        for path in paths:
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(subprocess.check_output(["git", "show", "a942ed8:" + path], cwd=BASE))
        env = dict(os.environ, CATALOG_FILE=str(root / "catalog.json"), STORAGE_DIR=str(root / "bootstrap"),
                   NODE_ID="node1", PROBE_INTERVAL="0", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                   COORDINATOR_URL="http://127.0.0.1:1")
        env.pop("PYTHONPATH", None)
        run("01-original-import", root, env, ["-c", "import coordinator.main"])
        run("02-original-tests", root, env, ["-m", "unittest", "discover", "-s", "tests", "-v"])
        run("03-original-connectivity", root, env, ["scripts/test_connectivity.py"])
        run("04-original-only-exception-shim", root, env, ["-c", '''
from coordinator import services
services.NodeError = type("NodeError", (Exception,), {})
from coordinator.main import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    for path, method in (("/status", "get"), ("/upload", "post")):
        try:
            kwargs = {"files": {"file": ("audit.txt", b"data")}} if method == "post" else {}
            print(path, getattr(client, method)(path, **kwargs).status_code)
        except Exception as exc:
            print(path, type(exc).__name__, str(exc))
'''])
        # Única sustitución experimental: el transporte anterior, solo en la copia temporal.
        (root / "coordinator/services.py").write_bytes(baseline)
        run("05-compatible-transport-tests", root, env, ["-m", "unittest", "discover", "-s", "tests", "-v"])
        run("06-compatible-transport-http", root, env, ["tests/smoke_http.py"])
        (root / "diagnostic_checks.py").write_text(CHECKS, encoding="utf-8")
        run("07-compatible-transport-targeted", root, env, ["diagnostic_checks.py"])
        # Segunda intervención temporal para completar el recorrido HTTP que bloquea el contrato.
        schema = root / "common/schemas.py"
        schema.write_text(schema.read_text(encoding="utf-8").replace(
            "class FileInfo(BaseModel):\n    filename: str",
            "class FileInfo(BaseModel):\n    filename: str\n    storage_key: str"), encoding="utf-8")
        run("09-compatible-transport-and-schema-http", root, env, ["tests/smoke_http.py"])
    (OUT / "resultados.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
