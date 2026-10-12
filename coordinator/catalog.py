"""
Gestor del Catálogo de Metadatos del Coordinador.
Persiste la información de los archivos y el índice del Round-Robin en catalog.json.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Directorio base y archivo de catálogo en la raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_FILE = BASE_DIR / "catalog.json"


class CatalogManager:
    def __init__(self, filepath: Path = CATALOG_FILE):
        self.filepath = filepath
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Crea el archivo catalog.json con estructura inicial si no existe."""
        if not self.filepath.exists():
            self._write({"round_robin_index": 0, "files": {}})

    def _read(self) -> Dict[str, Any]:
        """Lee el catálogo desde el disco con manejo de excepciones."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"round_robin_index": 0, "files": {}}

    def _write(self, data: Dict[str, Any]) -> None:
        """Escribe los datos actualizados en el catálogo."""
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_round_robin_index(self) -> int:
        """Obtiene el turno actual para asignación de nodos."""
        data = self._read()
        return data.get("round_robin_index", 0)

    def set_round_robin_index(self, index: int) -> None:
        """Actualiza el valor del turno Round-Robin."""
        data = self._read()
        data["round_robin_index"] = index
        self._write(data)

    def add_file(
        self,
        filename: str,
        size_bytes: int,
        sha256: str,
        primary_node: str,
        replica_node: str,
    ) -> None:
        """Registra un archivo confirmado con sus dos ubicaciones físicas."""
        data = self._read()
        data["files"][filename] = {
            "filename": filename,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "primary_node": primary_node,
            "replica_node": replica_node,
            "status": "COMMITTED",
            "created_at": datetime.now().isoformat(),
        }
        self._write(data)

    def get_file(self, filename: str) -> Optional[Dict[str, Any]]:
        """Busca y retorna los metadatos de un archivo por su nombre lógico."""
        data = self._read()
        return data.get("files", {}).get(filename)

    def list_files(self) -> Dict[str, Any]:
        """Retorna todos los archivos registrados en el catálogo."""
        data = self._read()
        return data.get("files", {})