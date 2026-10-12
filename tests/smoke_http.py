"""Prueba opcional con cuatro procesos Uvicorn y HTTP sobre 127.0.0.1."""
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

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
        for package in ("common", "coordinator", "node"):
            shutil.copytree(BASE_DIR / package, root / package, ignore=shutil.ignore_patterns("__pycache__"))
        (root / "catalog.json").write_text('{"round_robin_index": 0, "files": {}}', encoding="utf-8")
        ports = []
        while len(ports) < 4:
            port = free_port()
            if port not in ports:
                ports.append(port)
        node_key, client_key = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
        env = dict(os.environ, NODE_API_KEY=node_key, CLIENT_API_KEY=client_key,
                   CATALOG_DB=str(root / "catalog.sqlite3"), PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=os.pathsep.join(str(Path(item).resolve()) for item in sys.path if item))
        for index in (1, 2, 3):
            env[f"NODE{index}_URL"] = f"http://127.0.0.1:{ports[index]}"
        node_headers, client_headers = {"Authorization": "Bearer " + node_key}, {"Authorization": "Bearer " + client_key}

        def start(name, module, port, extras=None):
            log = (root / f"{name}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                cwd=root, env=dict(env, **(extras or {})), stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes[name] = process
            return process

        def stop(name):
            process = processes[name]
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

        def ready(client, name, port, headers=None):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if processes[name].poll() is not None:
                    raise AssertionError(f"{name} terminó prematuramente")
                try:
                    if client.get(f"http://127.0.0.1:{port}/health", headers=headers).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            raise AssertionError(f"{name} no arrancó")

        try:
            with httpx.Client(timeout=20, trust_env=False) as client:
                for index in (1, 2, 3):
                    start(f"node{index}", "node.main:app", ports[index],
                          {"NODE_ID": f"node{index}", "STORAGE_DIR": str(root / f"storage{index}")})
                for index in (1, 2, 3):
                    ready(client, f"node{index}", ports[index], node_headers)
                start("coordinator", "coordinator.main:app", ports[0])
                ready(client, "coordinator", ports[0])
                base = f"http://127.0.0.1:{ports[0]}"
                data = b"distributed-storage-test\n" * 140000  # > 3 MiB, supera el umbral de spool
                filename = "reporte-😀#100%.bin"
                uploaded = client.post(base + "/upload", headers=client_headers, files={"file": (filename, data)})
                assert uploaded.status_code == 201, uploaded.text
                assert uploaded.json()["sha256"] == hashlib.sha256(data).hexdigest()
                from urllib.parse import quote
                url = base + "/download/" + quote(filename, safe="")
                downloaded = client.get(url, headers=client_headers)
                assert downloaded.status_code == 200 and downloaded.content == data
                primary = uploaded.json()["locations"]["primary"]
                stop(primary)
                assert client.get(url, headers=client_headers).content == data
                replacement = client.post(base + "/upload", headers=client_headers, files={"file": ("second.txt", b"second")})
                assert replacement.status_code == 201, replacement.text
                stop("coordinator")
                start("coordinator-restarted", "coordinator.main:app", ports[0])
                ready(client, "coordinator-restarted", ports[0])
                assert client.get(url, headers=client_headers).content == data
                repaired = client.post(base + "/repair/" + quote(filename, safe=""), headers=client_headers)
                assert repaired.status_code == 200, repaired.text
                audit = client.get(base + "/integrity", headers=client_headers)
                assert all(item["status"] == "AVAILABLE" for item in audit.json()["files"].values())
                assert client.get(base + "/files").status_code == 401
                print("HTTP real: subida de >3 MiB, Unicode, hash, failover, nodo alternativo, reinicio, reparación y autenticación OK")
        finally:
            for name in list(processes):
                stop(name)
            for log in logs:
                log.close()


if __name__ == "__main__":
    main()
