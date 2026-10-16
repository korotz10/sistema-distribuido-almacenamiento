"""
Modelos de datos Pydantic compartidos para la API.
"""
from typing import Dict
from pydantic import BaseModel, Field


# --- Respuestas Públicas del Coordinador ---
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
    storage_ids: Dict[str, str] = Field(default_factory=dict)


class FileListResponse(BaseModel):
    files: Dict[str, FileInfo]
