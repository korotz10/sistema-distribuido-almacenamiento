"""Servicio único del laboratorio: subir, listar y descargar con réplica."""
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
from threading import Lock
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

from common.config import COORDINATOR_HOST, COORDINATOR_PORT
from common.files import read_upload, sha256, validate_filename
from common.schemas import FileListResponse, UploadResponse
from coordinator.catalog import CatalogError, CatalogManager, FileExists
from coordinator import services

logger = logging.getLogger(__name__)

# Segundos entre sondeos automáticos de los nodos (0 = desactivado). Alimenta la interfaz web.
PROBE_INTERVAL = float(os.getenv("PROBE_INTERVAL", "3"))


def make_storage_key(name):
    """Clave interna del objeto en los nodos: aleatoria, pero conserva la extensión
    original (foto.png -> 3f9a....png) para que el archivo guardado siga siendo una imagen."""
    key = uuid4().hex
    suffix = Path(name).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        try:
            return validate_filename(key + suffix)
        except Exception:
            pass  # si el nombre no es válido para el nodo, se usa la clave sin extensión
    return key


def create_app(manager=None):
    # --- Monitoreo para la interfaz web (bitácora y estado de conexión de los nodos) ---
    events = deque(maxlen=200)
    node_state = {}
    state_lock = Lock()

    def now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def log_event(kind, message, **extra):
        events.appendleft({"time": now(), "type": kind, "message": message, **extra})

    def probe_nodes():
        """Sondea todos los nodos, actualiza cuándo se conectaron/cayeron y devuelve su estado."""
        nodes = services.get_nodes()
        with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
            checks = list(pool.map(services.check_node_health, nodes))
        out = []
        with state_lock:
            for node, (ready, details) in zip(nodes, checks):
                st = node_state.setdefault(node["id"], {"alive": None, "connected_since": None,
                                                        "last_seen": None, "down_since": None})
                if ready:
                    if st["alive"] is not True:
                        st["connected_since"], st["down_since"] = now(), None
                        log_event("node", f"{node['id']} conectado", node=node["id"])
                    st["last_seen"] = now()
                elif st["alive"] is not False:
                    st["down_since"] = now()
                    log_event("node", f"{node['id']} dejó de responder", node=node["id"])
                st["alive"] = bool(ready)
                out.append((node, ready, details, dict(st)))
        return out

    async def probe_loop():
        while True:
            try:
                await asyncio.to_thread(probe_nodes)
            except Exception as exc:
                logger.warning("Error sondeando nodos: %s", exc)
            await asyncio.sleep(PROBE_INTERVAL)

    @asynccontextmanager
    async def lifespan(app):
        services.get_nodes()
        if app.state.catalog is None:
            app.state.catalog = CatalogManager()
        task = asyncio.create_task(probe_loop()) if PROBE_INTERVAL > 0 else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Mini Drive Distribuido - Laboratorio", lifespan=lifespan)
    app.state.catalog = manager
    # Una subida a la vez: sencillo de explicar y suficiente para la demostración.
    upload_lock = Lock()

    @app.exception_handler(FileExists)
    def conflict(request: Request, exc: FileExists):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(CatalogError)
    def catalog_error(request: Request, exc: CatalogError):
        logger.error("Catálogo: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(services.NodeError)
    def node_error(request: Request, exc: services.NodeError):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.get("/health")
    def health():
        app.state.catalog.list_files()
        return {"status": "ok", "service": "coordinator"}

    @app.post("/upload", status_code=201, response_model=UploadResponse)
    def upload(file: UploadFile = File(...)):
        try:
            name = validate_filename(file.filename or "")
            with upload_lock:
                catalog = app.state.catalog
                if catalog.get_file(name):
                    raise FileExists("El nombre ya existe; utiliza otro nombre")
                content = read_upload(file.file)
                nodes = services.get_nodes()
                # Un intento fallido nunca reutiliza el objeto de otro intento.
                key = make_storage_key(name)
                confirmed, storage_ids = [], set()
                for node in catalog.candidates(nodes):
                    ready, details = services.check_node_health(node)
                    if not ready or details["storage_id"] in storage_ids:
                        continue
                    try:
                        services.store_file_in_node(node, key, content, details["storage_id"])
                    except services.NodeError as exc:
                        logger.warning("%s", exc)
                        continue
                    confirmed.append(node["id"])
                    storage_ids.add(details["storage_id"])
                    if len(confirmed) == 2:
                        break
                if len(confirmed) < 2:
                    log_event("error", f"'{name}' no se publicó: solo se confirmaron {len(confirmed)} de 2 copias", file=name)
                    raise HTTPException(503, "No se confirmaron dos copias; el archivo no se publicó")
                record = {"filename": name, "storage_key": key, "size_bytes": len(content),
                          "sha256": sha256(content), "primary_node": confirmed[0],
                          "replica_node": confirmed[1], "status": "COMMITTED",
                          "created_at": datetime.now(timezone.utc).isoformat()}
                catalog.add_file(record, len(nodes))
                log_event("upload", f"'{name}' guardado en {confirmed[0]} (principal) y {confirmed[1]} (réplica)",
                          file=name, primary=confirmed[0], replica=confirmed[1])
                return {"message": "Archivo guardado en dos nodos", "filename": name,
                        "size_bytes": record["size_bytes"], "sha256": record["sha256"],
                        "locations": {"primary": confirmed[0], "replica": confirmed[1]},
                        "status": "COMMITTED"}
        finally:
            file.file.close()

    @app.get("/files", response_model=FileListResponse)
    def files():
        return {"files": app.state.catalog.list_files()}

    @app.get("/download/{filename}")
    def download(filename: str):
        validate_filename(filename)
        record = app.state.catalog.get_file(filename)
        if not record:
            raise HTTPException(404, "Archivo no encontrado en el catálogo")
        nodes = {node["id"]: node for node in services.get_nodes()}
        for node_id in (record["primary_node"], record["replica_node"]):
            if node_id not in nodes:
                continue
            try:
                content = services.retrieve_file_from_node(nodes[node_id], record)
            except services.NodeError as exc:
                logger.warning("%s; se intentará la siguiente copia", exc)
                continue
            logger.info("Descarga de %s desde %s", filename, node_id)
            failover = node_id != record["primary_node"]
            if failover:
                log_event("failover", f"FAILOVER: {record['primary_node']} no respondió, '{filename}' servido por la réplica {node_id}",
                          file=filename, served_by=node_id, failed=record["primary_node"])
            else:
                log_event("download", f"'{filename}' servido por {node_id} (principal)", file=filename, served_by=node_id)
            return Response(content, media_type="application/octet-stream", headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
                "X-Storage-Node": node_id,
                "X-Served-By": node_id,
                "X-Failover": "true" if failover else "false",
                "Access-Control-Expose-Headers": "X-Served-By, X-Failover, X-Storage-Node, Content-Disposition",
            })
        log_event("error", f"'{filename}' no disponible en {record['primary_node']} ni en {record['replica_node']}", file=filename)
        raise HTTPException(503, "Ninguna de las dos copias está disponible y es válida")

    @app.get("/status")
    def status():
        result = []
        for node, ready, details, st in probe_nodes():
            result.append({**node, "node_id": node["id"], "status": "UP" if ready else "DOWN", "details": details,
                           "connected_since": st["connected_since"] if ready else None,
                           "last_seen": st["last_seen"],
                           "down_since": None if ready else st["down_since"]})
        return {"nodes": result}

    # --- Endpoints de apoyo a la interfaz web ---
    @app.get("/events")
    def get_events():
        return {"events": list(events)}

    @app.get("/catalog")
    def catalog_info():
        catalog = app.state.catalog
        files = catalog.list_files()
        info = {"total_files": len(files),
                "total_bytes": sum(f.get("size_bytes", 0) for f in files.values()),
                "next_primary": None, "next_replica": None}
        try:  # el orden de candidatos indica a quién le tocaría la próxima subida
            ids = [n["id"] for n in catalog.candidates(services.get_nodes())]
            info["next_primary"] = ids[0] if ids else None
            info["next_replica"] = ids[1] if len(ids) > 1 else None
        except Exception as exc:
            logger.debug("No se pudo calcular el próximo turno: %s", exc)
        return info

    @app.get("/nodes/files")
    def nodes_files():
        """Qué copia debe tener cada nodo según el catálogo (los nodos guardan por storage_key)."""
        files = app.state.catalog.list_files()
        with state_lock:
            alive = {k: v["alive"] is True for k, v in node_state.items()}
        result = {}
        for node in services.get_nodes():
            nid = node["id"]
            items = [{"filename": f["filename"], "size_bytes": f.get("size_bytes", 0)}
                     for f in files.values() if nid in (f["primary_node"], f["replica_node"])]
            result[nid] = {"reachable": alive.get(nid, False), "files": items, "source": "catalog"}
        return {"nodes": result}

    # --- Interfaz web: http://<coordinador>:8000/ui ---
    ui_dir = Path(__file__).resolve().parent / "static"
    if ui_dir.is_dir():
        @app.get("/ui", include_in_schema=False)
        def ui_redirect():
            return RedirectResponse("/ui/")

        app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=COORDINATOR_HOST, port=COORDINATOR_PORT)
