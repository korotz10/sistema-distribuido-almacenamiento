"""Tres operaciones del nodo: salud, almacenamiento y recuperación."""
import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from common.files import atomic_write, read_upload, sha256, validate_filename


class NodeStorage:
    def __init__(self, directory: Path, node_id: str):
        self.directory = Path(directory).resolve()
        self.node_id = node_id
        self.lock = Lock()
        self.directory.mkdir(parents=True, exist_ok=True)
        identity_path = self.directory / ".node.json"
        if identity_path.exists():
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            if identity["node_id"] != node_id:
                raise RuntimeError("STORAGE_DIR pertenece a otro NODE_ID; usa un directorio distinto")
        else:
            identity = {"node_id": node_id, "storage_id": uuid4().hex}
            atomic_write(identity_path, json.dumps(identity).encode("utf-8"))
        self.storage_id = identity["storage_id"]

    def path(self, filename):
        validate_filename(filename)
        path = self.directory / filename
        if path.is_symlink() or path.resolve().parent != self.directory:
            raise HTTPException(400, "Ruta de almacenamiento no válida")
        return path

    def health(self):
        return {"status": "ok", "node_id": self.node_id, "storage_id": self.storage_id,
                "storage_dir": str(self.directory),
                "files_stored": sum(p.is_file() and not p.name.startswith(".") for p in self.directory.iterdir())}

    def store(self, filename, content):
        path = self.path(filename)
        with self.lock:
            if path.exists():
                if path.read_bytes() != content:
                    raise HTTPException(409, "El objeto ya existe con otro contenido")
            else:
                atomic_write(path, content)
        return {"status": "stored", "node_id": self.node_id, "storage_id": self.storage_id,
                "filename": filename, "size_bytes": len(content), "sha256": sha256(content)}

    def retrieve(self, filename):
        path = self.path(filename)
        if not path.is_file():
            raise HTTPException(404, "Archivo no encontrado en este nodo")
        return path

    def delete(self, filename):
        """Borra una copia. Es idempotente: si ya no está, responde 'absent' sin error."""
        if filename.startswith("."):
            raise HTTPException(400, "Nombre de archivo no válido")
        path = self.path(filename)
        with self.lock:
            existed = path.is_file()
            if existed:
                path.unlink()
        return {"status": "deleted" if existed else "absent", "node_id": self.node_id, "filename": filename}


def create_router(storage):
    router = APIRouter()

    @router.get("/health")
    def health():
        return storage.health()

    @router.post("/store", status_code=201)
    def store(file: UploadFile = File(...), filename: str | None = Form(None)):
        try:
            key = validate_filename(filename or file.filename or "")
            return storage.store(key, read_upload(file.file))
        finally:
            file.file.close()

    @router.get("/retrieve/{filename}")
    def retrieve(filename: str):
        return FileResponse(storage.retrieve(filename), filename=filename, media_type="application/octet-stream")

    @router.delete("/store/{filename}")
    def delete(filename: str):
        return storage.delete(filename)

    return router
