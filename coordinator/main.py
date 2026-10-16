"""Servicio único del laboratorio: subir, listar y descargar con réplica."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import logging
import math
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
from coordinator.monitor import NodeMonitor
from coordinator import services

logger = logging.getLogger(__name__)

# Máximo de espera por la respuesta de salud de un nodo. Un nodo que no contesta en este
# tiempo se marca DOWN sin hacer esperar a los demás ni a la interfaz.
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT", "2"))
INVENTORY_TIMEOUT = float(os.getenv("INVENTORY_TIMEOUT", "5"))
if any(not math.isfinite(v) or v <= 0 for v in (PROBE_TIMEOUT, INVENTORY_TIMEOUT)):
    raise ValueError("Los tiempos máximos deben ser positivos y finitos")


def make_storage_key(name):
    """Clave interna del objeto en los nodos: aleatoria, pero conserva la extensión
    original (foto.png -> 3f9a....png) para que el archivo guardado siga siendo una imagen."""
    key = uuid4().hex
    suffix = Path(name).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        try:
            return validate_filename(key + suffix)
        except HTTPException:
            pass  # si el nombre no es válido para el nodo, se usa la clave sin extensión
    return key


def create_app(manager=None):
    # --- Monitoreo para la interfaz web (bitácora y estado de conexión de los nodos) ---
    events = deque(maxlen=200)
    events_lock = Lock()
    inventory_task = None

    def now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def log_event(kind, message, **extra):
        with events_lock:
            events.appendleft({"time": now(), "type": kind, "message": message, **extra})

    monitor = NodeMonitor(PROBE_TIMEOUT, log_event)

    @asynccontextmanager
    async def lifespan(app):
        services.get_nodes()
        if app.state.catalog is None:
            app.state.catalog = CatalogManager()
        try:
            yield
        finally:
            await monitor.close()
            if inventory_task and not inventory_task.done():
                inventory_task.cancel()
                with suppress(asyncio.CancelledError):
                    await inventory_task

    app = FastAPI(title="Mini Drive Distribuido - Laboratorio", lifespan=lifespan)
    app.state.catalog = manager
    app.state.monitor = monitor
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
                confirmed, storage_ids, identities = [], set(), {}
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
                    identities[node["id"]] = details["storage_id"]
                    if len(confirmed) == 2:
                        break
                if len(confirmed) < 2:
                    log_event("error", f"'{name}' no se publicó: solo se confirmaron {len(confirmed)} de 2 copias", file=name)
                    raise HTTPException(503, "No se confirmaron dos copias; el archivo no se publicó")
                record = {"filename": name, "storage_key": key, "size_bytes": len(content),
                          "sha256": sha256(content), "primary_node": confirmed[0],
                          "replica_node": confirmed[1], "status": "COMMITTED",
                          "storage_ids": identities,
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

    @app.delete("/files/{filename}")
    def delete_file(filename: str):
        """Persiste la eliminación pendiente y permite reintentar después de cualquier fallo."""
        validate_filename(filename)
        with upload_lock:  # no se mezcla con una subida del mismo nombre
            catalog = app.state.catalog
            record = catalog.get_file(filename)
            if not record:
                raise HTTPException(404, "Archivo no encontrado en el catálogo")
            nodes = {node["id"]: node for node in services.get_nodes()}
            record = catalog.begin_delete(filename)
            # Reintentar ambas copias es seguro: borrar un objeto ausente también confirma.
            deleted, failed = [], []
            for node_id in (record["primary_node"], record["replica_node"]):
                node = nodes.get(node_id)
                if node is None:
                    failed.append(f"{node_id} (no está configurado)")
                    continue
                try:
                    services.delete_file_from_node(node, record)
                except services.NodeError as exc:
                    failed.append(str(exc))
                    continue
                deleted.append(node_id)
            if failed:
                log_event("error", f"No se eliminó '{filename}' del todo: falló {', '.join(failed)}", file=filename)
                return JSONResponse(status_code=503, content={
                    "detail": "Eliminación pendiente: pueden haberse borrado copias. Reintenta para completarla. " + "; ".join(failed),
                    "status": "DELETING", "deleted_from": deleted,
                    "pending_nodes": [n for n in (record["primary_node"], record["replica_node"]) if n not in deleted],
                })
            catalog.delete_file(filename)
            log_event("delete", f"'{filename}' eliminado de {record['primary_node']} y {record['replica_node']}", file=filename)
            return {"message": "Archivo eliminado de los dos nodos", "filename": filename, "deleted_from": deleted}

    @app.get("/download/{filename}")
    def download(filename: str):
        validate_filename(filename)
        record = app.state.catalog.get_file(filename)
        if not record:
            raise HTTPException(404, "Archivo no encontrado en el catálogo")
        if record["status"] == "DELETING":
            raise HTTPException(409, "El archivo tiene una eliminación pendiente; reintenta eliminarlo")
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
                log_event("failover", f"FAILOVER: copia principal no disponible o inválida, '{filename}' servido por la réplica {node_id}",
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
    async def status():
        return {"nodes": await monitor.snapshot()}

    # --- Endpoints de apoyo a la interfaz web ---
    @app.get("/events")
    def get_events():
        with events_lock:
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

    async def read_inventory():
        nodes = services.get_nodes()
        checks = await asyncio.gather(*(services.list_node_files(n, INVENTORY_TIMEOUT) for n in nodes))
        files = await asyncio.to_thread(app.state.catalog.list_files)
        names = {f["storage_key"]: f["filename"] for f in files.values()}
        checked_at = now()
        return {"nodes": {n["id"]: {**check, "checked_at": checked_at,
                    "files": [{**item, "filename": names.get(item["storage_key"], item["storage_key"])}
                              for item in check["files"]]} for n, check in zip(nodes, checks)}}

    @app.get("/nodes/files")
    async def nodes_files():
        nonlocal inventory_task
        if inventory_task is None or inventory_task.done():
            inventory_task = asyncio.create_task(read_inventory())
        return await asyncio.shield(inventory_task)

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
