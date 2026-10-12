"""API pública con publicación atómica de versiones y descargas verificadas."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
import uvicorn

from common.config import (
    COORDINATOR_HOST, COORDINATOR_PORT, MAINTENANCE_INTERVAL,
    MAX_CONCURRENT_TRANSFERS, TRANSFER_TIMEOUT,
)
from common.files import TemporaryFileResponse, finish_thread, spool_upload, validate_filename
from common.schemas import FileListResponse, UploadResponse
from common.security import AccessMiddleware, validate_key
from coordinator.catalog import CatalogError, CatalogManager, UploadConflict
from coordinator import services

logger = logging.getLogger(__name__)


async def replicate(operation, path, size, sha256):
    nodes = operation["nodes"]
    states = await asyncio.gather(*(services.check_node_health(node) for node in nodes))
    available, storage_ids = [], set()
    for node, (ready, details) in zip(nodes, states):
        if ready and details["storage_id"] not in storage_ids and details.get("free_bytes", 0) >= size:
            available.append((node, details["storage_id"]))
            storage_ids.add(details["storage_id"])
    if len(available) < 2:
        raise HTTPException(503, "Se necesitan dos almacenamientos disponibles e independientes")

    async def put(candidate):
        node, storage_id = candidate
        await services.store_file_in_node(node, operation["storage_key"], path, size, sha256, storage_id)
        return node

    outcomes = await asyncio.gather(*(put(candidate) for candidate in available[:2]), return_exceptions=True)
    confirmed, errors = [], []
    for outcome in outcomes:
        if isinstance(outcome, services.NodeError):
            errors.append(outcome)
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            confirmed.append(outcome)
    for candidate in available[2:]:
        if len(confirmed) == 2:
            break
        try:
            confirmed.append(await put(candidate))
        except services.NodeError as exc:
            errors.append(exc)
    if len(confirmed) != 2:
        code = 502 if any(error.status_code == 502 for error in errors) else 503
        raise HTTPException(code, "No se pudieron verificar dos copias; la versión anterior se conserva")
    return confirmed


def create_app(manager=None):
    async def get_catalog():
        if app.state.catalog is None:
            async with app.state.catalog_lock:
                if app.state.catalog is None:
                    app.state.catalog = await asyncio.to_thread(CatalogManager)
        return app.state.catalog

    async def maintain():
        while True:
            try:
                await services.cleanup_pending(await get_catalog())
            except Exception:
                logger.exception("No se pudo completar el mantenimiento; se reintentará")
            await asyncio.sleep(MAINTENANCE_INTERVAL)

    @asynccontextmanager
    async def lifespan(app):
        validate_key("CLIENT_API_KEY")
        validate_key("NODE_API_KEY")
        services.get_nodes()
        await get_catalog()
        maintenance = asyncio.create_task(maintain())
        try:
            yield
        finally:
            maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance

    app = FastAPI(title="Mini Drive Distribuido - Coordinador", version="2.0.0",
                  lifespan=lifespan, dependencies=[Depends(HTTPBearer(auto_error=False))])
    app.state.catalog = manager
    app.state.catalog_lock = asyncio.Lock()
    app.state.audit_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSFERS)
    app.add_middleware(AccessMiddleware, key_variable="CLIENT_API_KEY", public_health=True)

    @app.exception_handler(UploadConflict)
    async def conflict_handler(request: Request, exc: UploadConflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(CatalogError)
    async def catalog_error_handler(request: Request, exc: CatalogError):
        logger.error("Catálogo no disponible: %s", exc)
        return JSONResponse({"detail": "Catálogo no disponible; no se restableció ni se borraron sus datos"}, 503)

    @app.exception_handler(services.NodeError)
    async def node_error_handler(request: Request, exc: services.NodeError):
        return JSONResponse({"detail": str(exc)}, exc.status_code)

    @app.get("/health")
    async def health():
        catalog = await get_catalog()
        await asyncio.to_thread(catalog.ping)
        return {"status": "ok", "service": "coordinator"}

    @app.get("/")
    async def root():
        return {"service": "Mini Drive Distribuido", "role": "Coordinator", "version": "2.0.0",
                "configured_nodes": len(services.get_nodes())}

    @app.post("/upload", status_code=201, response_model=UploadResponse)
    async def upload_file(file: UploadFile = File(...)):
        path, operation, catalog = None, None, None
        try:
            filename = validate_filename(file.filename or "")
            catalog = await get_catalog()
            # La reserva precede al trabajo de disco para rechazar colisiones inmediatamente.
            operation = await finish_thread(catalog.reserve_upload, filename, services.get_nodes())
            spool_task = asyncio.create_task(asyncio.to_thread(spool_upload, file.file))
            try:
                path, size, sha256 = await asyncio.shield(spool_task)
            except asyncio.CancelledError:
                path, _, _ = await spool_task
                raise
            try:
                confirmed = await asyncio.wait_for(replicate(operation, path, size, sha256), TRANSFER_TIMEOUT)
            except asyncio.TimeoutError as exc:
                raise HTTPException(504, "Se agotó el plazo de replicación; la versión anterior se conserva") from exc
            primary, replica = confirmed
            # finish_thread evita abandonar un commit que ya está ejecutándose.
            await finish_thread(catalog.commit_upload, operation["operation_id"], size, sha256, primary["id"], replica["id"])
            return {"message": "Versión guardada y verificada en dos nodos", "filename": filename,
                    "size_bytes": size, "sha256": sha256,
                    "locations": {"primary": primary["id"], "replica": replica["id"]}, "status": "COMMITTED"}
        finally:
            try:
                if operation and catalog:
                    # Si se confirmó, la reserva ya no existe y este método no hace cambios.
                    await finish_thread(catalog.abort_upload, operation["operation_id"])
            finally:
                try:
                    if path:
                        await asyncio.to_thread(path.unlink, missing_ok=True)
                finally:
                    await file.close()

    async def fetch(record):
        nodes = {node["id"]: node for node in services.get_nodes()}
        for node_id in (record["primary_node"], record["replica_node"]):
            node = nodes.get(node_id)
            if node:
                try:
                    return await services.retrieve_file_from_node(
                        node, record["storage_key"], record["size_bytes"], record["sha256"])
                except services.NodeError as exc:
                    logger.warning("Copia rechazada en %s: %s", node_id, exc)
        raise HTTPException(503, "No hay una copia disponible con tamaño y SHA-256 correctos")

    @app.get("/download/{filename}")
    async def download_file(filename: str):
        validate_filename(filename)
        catalog = await get_catalog()
        record = await asyncio.to_thread(catalog.get_file, filename)
        if not record:
            raise HTTPException(404, "Archivo no encontrado en el catálogo")
        try:
            path = await asyncio.wait_for(fetch(record), TRANSFER_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "Se agotó el plazo de descarga") from exc
        # FileResponse genera filename* UTF-8 y transmite el temporal ya verificado.
        return TemporaryFileResponse(path, media_type="application/octet-stream", filename=filename)

    @app.get("/files", response_model=FileListResponse)
    async def list_files():
        catalog = await get_catalog()
        return {"files": await asyncio.to_thread(catalog.list_files)}

    @app.get("/status")
    async def health_status():
        nodes = services.get_nodes()
        states = await asyncio.gather(*(services.check_node_health(node) for node in nodes))
        return {"scope": "NODE_READINESS", "integrity_endpoint": "/integrity", "nodes": [
            {"node_id": node["id"], "name": node["name"], "url": node["url"],
             "status": "READY" if ready else "DOWN", "details": details}
            for node, (ready, details) in zip(nodes, states)]}

    @app.get("/integrity")
    async def integrity():
        catalog = await get_catalog()
        records = await asyncio.to_thread(catalog.list_files)
        nodes = {node["id"]: node for node in services.get_nodes()}

        async def check_copy(record, node_id):
            async with app.state.audit_semaphore:
                node = nodes.get(node_id)
                if not node:
                    return {"node_id": node_id, "status": "UNAVAILABLE"}
                path = None
                try:
                    path = await asyncio.wait_for(services.retrieve_file_from_node(
                        node, record["storage_key"], record["size_bytes"], record["sha256"]), TRANSFER_TIMEOUT)
                    return {"node_id": node_id, "status": "VERIFIED"}
                except services.NodeError as exc:
                    return {"node_id": node_id, "status": "CORRUPT" if exc.status_code == 502 else "UNAVAILABLE"}
                except asyncio.TimeoutError:
                    return {"node_id": node_id, "status": "UNAVAILABLE"}
                finally:
                    if path:
                        path.unlink(missing_ok=True)

        results = {}
        for filename in records:
            record = await asyncio.to_thread(catalog.get_file, filename)
            copies = await asyncio.gather(*(check_copy(record, node_id) for node_id in (
                record["primary_node"], record["replica_node"])))
            verified = sum(copy["status"] == "VERIFIED" for copy in copies)
            results[filename] = {"status": ("UNAVAILABLE", "DEGRADED", "AVAILABLE")[verified],
                                 "storage_key": record["storage_key"], "copies": copies}
        return {"checked_at": datetime.now(timezone.utc).isoformat(), "files": results}

    @app.post("/maintenance")
    async def maintenance():
        return await services.cleanup_pending(await get_catalog())

    @app.post("/repair/{filename}", response_model=UploadResponse)
    async def repair(filename: str):
        validate_filename(filename)
        catalog = await get_catalog()
        operation = await finish_thread(catalog.reserve_upload, filename, services.get_nodes())
        path = None
        try:
            # Reservar antes de leer evita que la reparación restaure una versión obsoleta.
            record = await asyncio.to_thread(catalog.get_file, filename)
            if not record:
                raise HTTPException(404, "Archivo no encontrado en el catálogo")
            path = await asyncio.wait_for(fetch(record), TRANSFER_TIMEOUT)
            confirmed = await asyncio.wait_for(replicate(
                operation, path, record["size_bytes"], record["sha256"]), TRANSFER_TIMEOUT)
            primary, replica = confirmed
            await finish_thread(catalog.commit_upload, operation["operation_id"], record["size_bytes"],
                                record["sha256"], primary["id"], replica["id"])
            return {"message": "Redundancia restaurada con dos copias verificadas", "filename": filename,
                    "size_bytes": record["size_bytes"], "sha256": record["sha256"],
                    "locations": {"primary": primary["id"], "replica": replica["id"]}, "status": "COMMITTED"}
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "Se agotó el plazo de reparación") from exc
        finally:
            try:
                await finish_thread(catalog.abort_upload, operation["operation_id"])
            finally:
                if path:
                    path.unlink(missing_ok=True)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("coordinator.main:app", host=COORDINATOR_HOST, port=COORDINATOR_PORT, reload=False)
