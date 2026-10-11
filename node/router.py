"""Endpoints internos del nodo: /health, /store y /retrieve/{filename}.

Solo deben ser consumidos por el Coordinador (Host A) a través de Tailscale.
"""
import hashlib
import os
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

# --- Configuración del nodo (se define por variables de entorno) -------------
NODE_ID = os.getenv("NODE_ID", "node1")
STORAGE_DIR = Path(
    os.getenv("STORAGE_DIR", Path(__file__).resolve().parent.parent / "storage")
).resolve()
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

CHUNK_SIZE = 1024 * 1024  # 1 MB: evita cargar archivos enteros en RAM

router = APIRouter()


def safe_path(filename: Optional[str]) -> Path:
    """Valida el nombre y devuelve una ruta segura dentro de STORAGE_DIR
    (evita path traversal como ../../etc/passwd)."""
    if not filename or filename != Path(filename).name or "\\" in filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")
    if filename.startswith("."):
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")
    path = (STORAGE_DIR / filename).resolve()
    if path.parent != STORAGE_DIR:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")
    return path


@router.get("/health")
async def health():
    """Usado por el Coordinador en GET /status y para detectar caídas."""
    files = sum(1 for p in STORAGE_DIR.iterdir() if p.is_file() and not p.name.startswith("."))
    return {"status": "ok", "node_id": NODE_ID, "files_stored": files}


@router.post("/store", status_code=201)
async def store(
    file: UploadFile = File(...),
    filename: Optional[str] = Form(None),
):
    """Recibe los bytes (multipart/form-data) y los persiste en ./storage/.

    Escritura atómica: se escribe a un temporal y luego se renombra, así
    nunca queda un archivo a medias si la conexión se corta.
    """
    name = filename or file.filename
    dest = safe_path(name)
    tmp = dest.with_name(f".{dest.name}.{uuid4().hex}.tmp")

    sha256 = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := await file.read(CHUNK_SIZE):
                out.write(chunk)
                sha256.update(chunk)
                size += len(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, dest)  # atómico
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Error al guardar: {exc}")

    return {
        "status": "stored",
        "node_id": NODE_ID,
        "filename": dest.name,
        "size_bytes": size,
        "sha256": sha256.hexdigest(),
    }


@router.get("/retrieve/{filename}")
async def retrieve(filename: str):
    """Devuelve los bytes del archivo almacenado localmente."""
    path = safe_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en este nodo")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)