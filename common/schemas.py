"""
Modelos de datos Pydantic compartidos para la API.
"""
from typing import Dict, List, Optional
from pydantic import BaseModel


# --- Respuestas Internas del Nodo ---
class HealthResponse(BaseModel):
    status: str
    node_id: str
    files_stored: int


class StoreResponse(BaseModel):
    status: str
    node_id: str
    filename: str
    size_bytes: int
    sha256: str


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
    size_bytes: int
    sha256: str
    primary_node: str
    replica_node: str
    status: str
    created_at: str


class FileListResponse(BaseModel):
    files: Dict[str, FileInfo]


class NodeStatus(BaseModel):
    node_id: str
    name: str
    url: str
    status: str
    details: Optional[Dict] = None


class NetworkStatusResponse(BaseModel):
    nodes: List[NodeStatus]