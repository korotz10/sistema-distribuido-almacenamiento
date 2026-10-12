"""Validación portable, bloqueo entre procesos y transferencias con memoria acotada."""
import asyncio
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import time

from fastapi import HTTPException
from fastapi.responses import FileResponse

from common.config import MAX_FILE_SIZE

CHUNK_SIZE = 1024 * 1024


def validate_filename(filename: str) -> str:
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³")
    if (not filename or filename.startswith(".") or filename.endswith((".", " "))
            or len(filename.encode("utf-8")) > 240
            or any(ord(char) < 32 or ord(char) == 127 or char in '<>:"/\\|?*' for char in filename)
            or filename.split(".", 1)[0].upper() in reserved):
        raise HTTPException(400, "Nombre de archivo no válido")
    return filename


@contextmanager
def file_lock(path: Path, timeout: float = 15):
    """Bloqueo usado en un hilo; funciona entre procesos en Windows y Unix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("No se pudo adquirir el bloqueo de almacenamiento")
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def hash_file(path: Path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def spool_upload(source):
    """Copiar y calcular el hash en un hilo, sin leer el archivo completo en RAM."""
    path = None
    try:
        digest, size = hashlib.sha256(), 0
        with tempfile.NamedTemporaryFile(prefix="mini-drive-", delete=False) as target:
            path = Path(target.name)
            source.seek(0)
            while chunk := source.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, "El archivo excede MAX_FILE_SIZE")
                target.write(chunk)
                digest.update(chunk)
        return path, size, digest.hexdigest()
    except BaseException:
        if path:
            path.unlink(missing_ok=True)
        raise


async def finish_thread(function, *args):
    """No abandonar un hilo que todavía podría publicar datos o dejar temporales."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class TemporaryFileResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.to_thread(Path(self.path).unlink, missing_ok=True)
