"""
Endpoints internos del nodo: /health, /store, /retrieve/{filename}
Atiende las solicitudes enviadas únicamente por el Coordinador a través de Tailscale.
"""
import hashlib
import os
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

# 1. Configuración del Nodo desde variables de entorno
NODE_ID = os.getenv("NODE_ID", "node1")
NODE_PORT = int(os.getenv("NODE_PORT", "8001"))

# Definir y crear carpeta de almacenamiento local ./storage/
BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", BASE_DIR / "storage")).resolve()
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# Tamaño de bloque para lectura fluida en RAM (1 MB)
CHUNK_SIZE = 1024 * 1024

router = APIRouter()


def safe_path(filename: str) -> Path:
    """
    Valida el nombre del archivo y retorna una ruta segura dentro de ./storage/
    Previene ataques de tipo Path Traversal (ej. ../../archivo).
    """
    if not filename or filename != Path(filename).name or "\\" in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo no válido")
    if filename.startswith("."):
        raise HTTPException(status_code=400, detail="Nombre de archivo reservado o no válido")

    destination_path = (STORAGE_DIR / filename).resolve()

    # Verificar que el archivo final quede estrictamente dentro de STORAGE_DIR
    if destination_path.parent != STORAGE_DIR:
        raise HTTPException(status_code=400, detail="Ruta de almacenamiento no autorizada")

    return destination_path


@router.get("/health", tags=["Monitoreo"])
async def health():
    """
    Endpoint de diagnóstico usado por el Coordinador para verificar si el nodo está vivo.
    """
    files_count = sum(1 for p in STORAGE_DIR.iterdir() if p.is_file() and not p.name.startswith("."))
    return {
        "status": "ok",
        "node_id": NODE_ID,
        "files_stored": files_count,
    }


@router.post("/store", status_code=201, tags=["Almacenamiento"])
async def store(
    file: UploadFile = File(...),
    filename: Optional[str] = Form(None),
):
    """
    Recibe los bytes de un archivo enviando por el Coordinador y los guarda en ./storage/
    Utiliza escritura atómica con archivo temporal (.tmp).
    """
    target_name = filename or file.filename
    dest_path = safe_path(target_name)
    temp_path = dest_path.with_name(f".{dest_path.name}.{uuid4().hex}.tmp")

    sha256 = hashlib.sha256()
    size_bytes = 0

    try:
        with open(temp_path, "wb") as out_file:
            while chunk := await file.read(CHUNK_SIZE):
                out_file.write(chunk)
                sha256.update(chunk)
                size_bytes += len(chunk)

            out_file.flush()
            os.fsync(out_file.fileno())

        # Renombrado atómico: del temporal al archivo final
        os.replace(temp_path, dest_path)

    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Error escribiendo en disco local: {str(exc)}")

    return {
        "status": "stored",
        "node_id": NODE_ID,
        "filename": dest_path.name,
        "size_bytes": size_bytes,
        "sha256": sha256.hexdigest(),
    }


@router.get("/retrieve/{filename}", tags=["Almacenamiento"])
async def retrieve(filename: str):
    """
    Retorna el flujo de bytes del archivo guardado en el disco del nodo.
    """
    target_path = safe_path(filename)

    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="El archivo no existe en este nodo")

    return FileResponse(
        path=target_path,
        media_type="application/octet-stream",
        filename=target_path.name,
    )