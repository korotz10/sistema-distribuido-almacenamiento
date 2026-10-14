"""Servicio único del laboratorio: subir, listar y descargar con réplica."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from threading import Lock
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn

from common.config import COORDINATOR_HOST, COORDINATOR_PORT
from common.files import read_upload, sha256, validate_filename
from common.schemas import FileListResponse, UploadResponse
from coordinator.catalog import CatalogError, CatalogManager, FileExists
from coordinator import services

logger = logging.getLogger(__name__)


def create_app(manager=None):
    @asynccontextmanager
    async def lifespan(app):
        services.get_nodes()
        if app.state.catalog is None:
            app.state.catalog = CatalogManager()
        yield

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
                key = uuid4().hex
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
                    raise HTTPException(503, "No se confirmaron dos copias; el archivo no se publicó")
                record = {"filename": name, "storage_key": key, "size_bytes": len(content),
                          "sha256": sha256(content), "primary_node": confirmed[0],
                          "replica_node": confirmed[1], "status": "COMMITTED",
                          "created_at": datetime.now(timezone.utc).isoformat()}
                catalog.add_file(record, len(nodes))
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
            return Response(content, media_type="application/octet-stream", headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
                "X-Storage-Node": node_id,
            })
        raise HTTPException(503, "Ninguna de las dos copias está disponible y es válida")

    @app.get("/status")
    def status():
        result = []
        for node in services.get_nodes():
            ready, details = services.check_node_health(node)
            result.append({**node, "status": "UP" if ready else "DOWN", "details": details})
        return {"nodes": result}

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=COORDINATOR_HOST, port=COORDINATOR_PORT)
