"""Almacenamiento de objetos inmutables y eliminación idempotente."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from common.config import BASE_DIR, MAX_FILE_SIZE
from common.files import CHUNK_SIZE, file_lock, finish_thread, hash_file, validate_filename

NODE_ID = os.getenv("NODE_ID", "node1")
NODE_PORT = int(os.getenv("NODE_PORT", "8001"))
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", str(BASE_DIR / "storage"))).resolve()


class NodeStorage:
    def __init__(self, directory: Path, node_id: str):
        self.directory, self.node_id = Path(directory).resolve(), node_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.locks = self.directory / ".locks"
        self.deleted = self.directory / ".deleted"
        self.deleted.mkdir(exist_ok=True)
        with file_lock(self.locks / "identity"):
            identity_file = self.directory / ".node.json"
            if identity_file.exists():
                identity = json.loads(identity_file.read_text(encoding="utf-8"))
                if identity["node_id"] != node_id:
                    raise RuntimeError("STORAGE_DIR pertenece a otro NODE_ID; usa un directorio distinto")
                self.storage_id = identity["storage_id"]
            else:
                self.storage_id = uuid4().hex
                temp = self.directory / ".identity.tmp"
                with temp.open("w", encoding="utf-8") as handle:
                    json.dump({"node_id": node_id, "storage_id": self.storage_id}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, identity_file)
        # Un proceso muerto libera el bloqueo. Nunca borrar un temporal activo.
        for temp in self.directory.glob(".pending-*.tmp"):
            try:
                with file_lock(self.locks / temp.stem, timeout=0):
                    temp.unlink(missing_ok=True)
            except TimeoutError:
                pass

    def path(self, filename):
        validate_filename(filename)
        candidate = self.directory / filename
        if candidate.is_symlink() or candidate.resolve().parent != self.directory:
            raise HTTPException(400, "Ruta de almacenamiento no autorizada")
        return candidate

    @staticmethod
    def token(filename):
        return hashlib.sha256(filename.encode("utf-8")).hexdigest()

    def tombstone(self, filename):
        return self.deleted / self.token(filename)

    def health(self):
        try:
            with tempfile.TemporaryFile(dir=self.directory) as probe:
                probe.write(b"ok")
                probe.flush()
                os.fsync(probe.fileno())
            free = shutil.disk_usage(self.directory).free
            count = sum(1 for path in self.directory.iterdir() if path.is_file() and not path.name.startswith("."))
        except OSError as exc:
            raise HTTPException(503, "El almacenamiento no está disponible para escritura") from exc
        return {"status": "ok", "node_id": self.node_id, "storage_id": self.storage_id,
                "files_stored": count, "writable": True, "free_bytes": free}

    def store(self, source, filename):
        destination = self.path(filename)
        temp = self.directory / f".pending-{uuid4().hex}.tmp"
        digest, size = hashlib.sha256(), 0
        with file_lock(self.locks / temp.stem):
            try:
                source.seek(0)
                with temp.open("wb") as handle:
                    while chunk := source.read(CHUNK_SIZE):
                        size += len(chunk)
                        if size > MAX_FILE_SIZE:
                            raise HTTPException(413, "El archivo excede MAX_FILE_SIZE")
                        handle.write(chunk)
                        digest.update(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                sha256 = digest.hexdigest()
                with file_lock(self.locks / self.token(filename)):
                    if self.tombstone(filename).exists():
                        raise HTTPException(410, "La operación de almacenamiento fue cancelada")
                    if destination.exists():
                        if not destination.is_file() or hash_file(destination) != (size, sha256):
                            raise HTTPException(409, "El objeto ya existe con otro contenido")
                    else:
                        os.replace(temp, destination)
                return {"status": "stored", "node_id": self.node_id, "storage_id": self.storage_id,
                        "filename": filename, "size_bytes": size, "sha256": sha256}
            except OSError as exc:
                raise HTTPException(503, "Error escribiendo en el almacenamiento del nodo") from exc
            finally:
                temp.unlink(missing_ok=True)

    def retrieve(self, filename):
        path = self.path(filename)
        if self.tombstone(filename).exists() or not path.is_file():
            raise HTTPException(404, "El objeto no existe en este nodo")
        return path

    def delete(self, filename):
        path = self.path(filename)
        with file_lock(self.locks / self.token(filename)):
            # La marca impide que una escritura retrasada resucite un objeto cancelado.
            with self.tombstone(filename).open("ab") as marker:
                marker.flush()
                os.fsync(marker.fileno())
            path.unlink(missing_ok=True)


def create_router(storage: NodeStorage):
    router = APIRouter()

    @router.get("/health")
    async def health():
        return await finish_thread(storage.health)

    @router.post("/store", status_code=201)
    async def store(file: UploadFile = File(...), filename: Optional[str] = Form(None)):
        try:
            return await finish_thread(storage.store, file.file, filename or file.filename or "")
        finally:
            await file.close()

    @router.get("/retrieve/{filename}")
    async def retrieve(filename: str):
        path = await finish_thread(storage.retrieve, filename)
        return FileResponse(path, media_type="application/octet-stream", filename=filename)

    @router.delete("/objects/{filename}", status_code=204)
    async def delete(filename: str):
        await finish_thread(storage.delete, filename)
        return Response(status_code=204)

    return router
