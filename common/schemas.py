"""Contratos de respuesta del catálogo y la publicación de archivos."""
from pydantic import BaseModel


class FileLocations(BaseModel):
    primary: str
    replica: str


class UploadResponse(BaseModel):
    message: str
    filename: str
    size_bytes: int
    sha256: str
    locations: FileLocations
    status: str


class FileInfo(BaseModel):
    filename: str
    storage_key: str
    size_bytes: int
    sha256: str
    primary_node: str
    replica_node: str
    status: str
    created_at: str


class FileListResponse(BaseModel):
    files: dict[str, FileInfo]
