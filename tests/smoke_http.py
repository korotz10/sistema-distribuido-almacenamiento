"""Prueba opcional con cuatro procesos Uvicorn y HTTP sobre 127.0.0.1."""
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def main():
    processes, logs = {}, []
    with tempfile.TemporaryDirectory(prefix="mini-drive-http-") as temporary:
        root = Path(temporary)
        for package in ("common", "coordinator", "node", "scripts"):
            shutil.copytree(BASE_DIR / package, root / package, ignore=shutil.ignore_patterns("__pycache__"))
        ports = []
        while len(ports) < 4:
            port = free_port()
            if port not in ports:
                ports.append(port)
        env = dict(os.environ,
                   CATALOG_FILE=str(root / "catalog.json"), PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=os.pathsep.join(str(Path(item).resolve()) for item in sys.path if item))
        env.pop("CLIENT_API_KEY", None)
        env.pop("NODE_API_KEY", None)
        for index in (1, 2, 3):
            env[f"NODE{index}_URL"] = f"http://127.0.0.1:{ports[index]}"
        env["COORDINATOR_URL"] = f"http://127.0.0.1:{ports[0]}"

        def connectivity(expected_code):
            result = subprocess.run(
                [sys.executable, "scripts/test_connectivity.py"], cwd=root, env=env,
                capture_output=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            assert result.returncode == expected_code, (result.stdout, result.stderr)

        def start(name, module, port, extras=None):
            log = (root / f"{name}.log").open("wb")
            logs.append(log)
            prefix = "NODE" if module == "node.main" else "COORDINATOR"
            process_env = dict(env, **(extras or {}))
            process_env.update({f"{prefix}_HOST": "127.0.0.1", f"{prefix}_PORT": str(port)})
            process = subprocess.Popen(
                [sys.executable, "-m", module],
                cwd=root, env=process_env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes[name] = process
            return process

        def stop(name):
            process = processes[name]
            if process.poll() is None:
                if os.name == "nt":
                    # El lanzador del venv puede crear otro proceso Python.
                    # Cerrar el árbol evita dejar servidores o logs abiertos.
                    result = subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode and process.poll() is None:
                        raise RuntimeError(f"No se pudo detener {name}: {result.stderr!r}")
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

        def ready(client, name, port):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if processes[name].poll() is not None:
                    raise AssertionError(f"{name} terminó prematuramente")
                try:
                    if client.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            raise AssertionError(f"{name} no arrancó")

        try:
            with httpx.Client(timeout=20, trust_env=False) as client:
                for index in (1, 2, 3):
                    start(f"node{index}", "node.main", ports[index],
                          {"NODE_ID": f"node{index}", "STORAGE_DIR": str(root / f"storage{index}")})
                for index in (1, 2, 3):
                    ready(client, f"node{index}", ports[index])
                start("coordinator", "coordinator.main", ports[0])
                ready(client, "coordinator", ports[0])
                connectivity(0)
                base = f"http://127.0.0.1:{ports[0]}"
                data = b"distributed-storage-test\n" * 140000  # > 3 MiB, supera el umbral de spool
                filename = "reporte-😀#100%.bin"
                uploaded = client.post(base + "/upload", files={"file": (filename, data)})
                assert uploaded.status_code == 201, uploaded.text
                assert uploaded.json()["sha256"] == hashlib.sha256(data).hexdigest()
                url = base + "/download/" + quote(filename, safe="")
                downloaded = client.get(url)
                assert downloaded.status_code == 200 and downloaded.content == data
                assert downloaded.headers["X-Storage-Node"] == uploaded.json()["locations"]["primary"]
                record = client.get(base + "/files").json()["files"][filename]
                for node_id in (record["primary_node"], record["replica_node"]):
                    physical = root / f"storage{node_id[-1]}" / record["storage_key"]
                    assert physical.read_bytes() == data, "No existe una copia física correcta"
                duplicate = client.post(base + "/upload", files={"file": (filename, b"replacement")})
                assert duplicate.status_code == 409
                primary = uploaded.json()["locations"]["primary"]
                stop(primary)
                connectivity(1)
                states = client.get(base + "/status").json()["nodes"]
                assert next(node for node in states if node["id"] == primary)["status"] == "DOWN"
                fallback = client.get(url)
                assert fallback.content == data
                assert fallback.headers["X-Storage-Node"] == record["replica_node"]
                replacement = client.post(base + "/upload", files={"file": ("second.txt", b"second")})
                assert replacement.status_code == 201, replacement.text
                # La guía pide estudiar la caída del coordinador con los nodos activos.
                primary_index = int(primary[-1])
                start(primary, "node.main", ports[primary_index],
                      {"NODE_ID": primary, "STORAGE_DIR": str(root / f"storage{primary_index}")})
                ready(client, primary, ports[primary_index])
                stop("coordinator")
                try:
                    client.get(base + "/files")
                except httpx.TransportError:
                    pass
                else:
                    raise AssertionError("El coordinador detenido todavía atiende peticiones")
                # El nodo conserva el objeto, aunque el servicio único esté caído.
                replica_port = ports[int(record["replica_node"][-1])]
                direct = client.get(f"http://127.0.0.1:{replica_port}/retrieve/" + record["storage_key"])
                assert direct.content == data
                for index in (1, 2, 3):
                    assert client.get(f"http://127.0.0.1:{ports[index]}/health").status_code == 200
                start("coordinator-restarted", "coordinator.main", ports[0])
                ready(client, "coordinator-restarted", ports[0])
                assert client.get(url).content == data
                assert client.get(base + "/files").status_code == 200
                connectivity(0)
                print("HTTP local: copias físicas, Unicode, hash, réplica tras caída, fallo del coordinador y reinicio con JSON OK")
                print("La entrega requiere repetir la demostración en cuatro equipos independientes.")
        finally:
            for name in list(processes):
                stop(name)
            for log in logs:
                log.close()


if __name__ == "__main__":
    main()
