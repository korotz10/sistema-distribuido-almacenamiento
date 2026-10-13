"""Validación y escritura local de archivos para el laboratorio."""
import hashlib
import os
from pathlib import Path
import tempfile

from fastapi import HTTPException

from common.config import MAX_FILE_SIZE


def validate_filename(filename: str) -> str:
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³")
    if (not filename or filename.startswith(".") or filename.endswith((".", " "))
            or len(filename.encode("utf-8")) > 240
            or any(ord(char) < 32 or ord(char) == 127 or char in '<>:"/\\|?*' for char in filename)
            or filename.split(".", 1)[0].upper() in reserved):
        raise HTTPException(400, "Nombre de archivo no válido")
    return filename


def read_upload(source):
    """La demostración utiliza archivos pequeños, limitados a MAX_FILE_SIZE."""
    source.seek(0)
    content = source.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, "El archivo excede MAX_FILE_SIZE")
    return content


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def atomic_write(destination: Path, content: bytes):
    """Escribir en el mismo directorio y sustituir solo al terminar."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".pending-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
