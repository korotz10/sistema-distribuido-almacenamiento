"""
Punto de Entrada del Coordinador (Host A - Santiago).
Ofrece la interfaz unificada REST (Puerto 8000) para el usuario.
"""
import asyncio
import hashlib
from collections import deque
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

# Importaciones desde common
from common.config import COORDINATOR_HOST, COORDINATOR_PORT
from common.schemas import (
    FileListResponse,
    NetworkStatusResponse,
    UploadResponse,
)
from coordinator.catalog import CatalogManager
from coordinator.services import (
    check_node_health,
    get_nodes,
    list_node_files,
    retrieve_file_from_node,
    select_nodes_round_robin,
    store_file_in_node,
)

app = FastAPI(
    title="Mini Drive Distribuido - Coordinador",
    description="Servidor Central de Almacenamiento Distribuido (Host A)",
    version="1.0.0",
)

catalog = CatalogManager()

# Bitácora en memoria de las últimas operaciones (la consume la interfaz web)
events: deque = deque(maxlen=200)


def log_event(kind: str, message: str, **extra) -> None:
    events.appendleft({"time": datetime.now().isoformat(timespec="seconds"), "type": kind, "message": message, **extra})


@app.get("/", tags=["Información"])
async def root():
    return {
        "service": "Mini Drive Distribuido - Coordinador",
        "role": "Coordinator (Host A)",
        "status": "online",
        "configured_nodes": len(get_nodes()),
    }


@app.post("/upload", status_code=201, response_model=UploadResponse, tags=["Operaciones Cliente"])
async def upload_file(file: UploadFile = File(...)):
    """
    Subida transparente: Selecciona 2 nodos por Round-Robin,
    almacena el archivo en ambos (Replicación Síncrona) y actualiza el catálogo.
    """
    content = await file.read()
    filename = file.filename

    if not filename:
        raise HTTPException(status_code=400, detail="El archivo debe tener un nombre válido")

    # 1. Seleccionar Nodo Principal y Réplica
    primary, replica = select_nodes_round_robin()

    print(f"📤 Procesando '{filename}' -> Principal: {primary['id']}, Réplica: {replica['id']}")

    # 2. Guardar en el Nodo Principal
    res_primary = await store_file_in_node(primary["url"], filename, content)
    if not res_primary:
        log_event("error", f"Falló '{filename}': nodo principal {primary['id']} no respondió", file=filename)
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo almacenar el archivo en el nodo principal ({primary['id']}).",
        )

    # 3. Guardar en el Nodo Réplica
    res_replica = await store_file_in_node(replica["url"], filename, content)
    if not res_replica:
        log_event("error", f"'{filename}' quedó solo en {primary['id']}: falló la réplica en {replica['id']}", file=filename)
        raise HTTPException(
            status_code=503,
            detail=f"Se guardó en el principal ({primary['id']}) pero falló la réplica en ({replica['id']}).",
        )

    # 4. Calcular Hash SHA-256 local
    file_hash = hashlib.sha256(content).hexdigest()

    # 5. Registrar en el Catálogo
    catalog.add_file(
        filename=filename,
        size_bytes=len(content),
        sha256=file_hash,
        primary_node=primary["id"],
        replica_node=replica["id"],
    )

    log_event(
        "upload",
        f"'{filename}' guardado en {primary['id']} (principal) y {replica['id']} (réplica)",
        file=filename, primary=primary["id"], replica=replica["id"],
    )

    return {
        "message": "Archivo subido y replicado exitosamente",
        "filename": filename,
        "size_bytes": len(content),
        "sha256": file_hash,
        "locations": {
            "primary": primary["id"],
            "replica": replica["id"],
        },
        "status": "COMMITTED",
    }


@app.get("/download/{filename}", tags=["Operaciones Cliente"])
async def download_file(filename: str):
    """
    Descarga transparente: Busca el archivo en el catálogo e intenta obtenerlo del
    Nodo Principal. Si este falla (caído o timeout), ejecuta FAILOVER a la réplica.
    """
    file_info = catalog.get_file(filename)
    if not file_info:
        raise HTTPException(status_code=404, detail="Archivo no encontrado en el catálogo.")

    nodes_map = {n["id"]: n["url"] for n in get_nodes()}
    primary_id = file_info["primary_node"]
    replica_id = file_info["replica_node"]

    def _response(content: bytes, served_by: str, failover: bool) -> Response:
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Served-By": served_by,
                "X-Failover": "true" if failover else "false",
                "Access-Control-Expose-Headers": "X-Served-By, X-Failover, Content-Disposition",
            },
        )

    # 1. Intentar descargar desde el Nodo Principal
    if primary_id in nodes_map:
        content = await retrieve_file_from_node(nodes_map[primary_id], filename)
        if content is not None:
            log_event("download", f"'{filename}' servido por {primary_id} (principal)", file=filename, served_by=primary_id)
            return _response(content, primary_id, False)

    # 2. FAILOVER TRANSPARENTE: Si el principal falla, intentar desde la Réplica
    print(f"⚠️ Principal '{primary_id}' inaccesible. Aplicando FAILOVER a Réplica '{replica_id}'...")
    if replica_id in nodes_map:
        content = await retrieve_file_from_node(nodes_map[replica_id], filename)
        if content is not None:
            log_event(
                "failover",
                f"FAILOVER: '{primary_id}' no respondió, '{filename}' servido por la réplica {replica_id}",
                file=filename, served_by=replica_id, failed=primary_id,
            )
            return _response(content, replica_id, True)

    log_event("error", f"'{filename}' no disponible en {primary_id} ni en {replica_id}", file=filename)
    raise HTTPException(
        status_code=503,
        detail="El archivo no está disponible en ninguno de los nodos registrados.",
    )


@app.get("/files", response_model=FileListResponse, tags=["Diagnóstico"])
async def list_files():
    """Retorna el catálogo completo con nombres y ubicaciones físicas de las copias."""
    return {"files": catalog.list_files()}


@app.get("/status", response_model=NetworkStatusResponse, tags=["Diagnóstico"])
async def health_status():
    """Diagnóstico de red: Sondea la disponibilidad de los 3 Nodos de almacenamiento."""
    status_results = []
    for node in get_nodes():
        is_alive, details = await check_node_health(node["url"])
        status_results.append({
            "node_id": node["id"],
            "name": node["name"],
            "url": node["url"],
            "status": "HEALTHY" if is_alive else "DOWN",
            "details": details,
        })
    return {"nodes": status_results}


@app.get("/events", tags=["Diagnóstico"])
async def get_events():
    """Últimas operaciones del coordinador (subidas, descargas, failovers, errores)."""
    return {"events": list(events)}


@app.get("/nodes/files", tags=["Diagnóstico"])
async def nodes_files():
    """Contenido REAL de cada nodo (lo que hay en su ./storage/), para contrastar con el catálogo."""
    nodes = get_nodes()
    results = await asyncio.gather(*(list_node_files(n["url"]) for n in nodes))
    return {
        "nodes": {
            n["id"]: {"reachable": files is not None, "files": files or []}
            for n, files in zip(nodes, results)
        }
    }


# --- Interfaz web (http://<coordinador>:8000/ui) ---
UI_DIR = Path(__file__).resolve().parent / "static"


@app.get("/ui", include_in_schema=False)
async def ui_redirect():
    return RedirectResponse("/ui/")


app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")


if __name__ == "__main__":
    print(f"🧠 Iniciando Coordinador en {COORDINATOR_HOST}:{COORDINATOR_PORT}...")
    uvicorn.run("coordinator.main:app", host=COORDINATOR_HOST, port=COORDINATOR_PORT, reload=False)