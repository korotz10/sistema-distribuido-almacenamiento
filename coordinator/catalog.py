"""Catálogo JSON. Un coordinador por catálogo; bloqueo local entre sus peticiones."""
import json
from pathlib import Path
from threading import RLock

from common.config import CATALOG_FILE
from common.files import atomic_write


class CatalogError(Exception):
    pass


class FileExists(CatalogError):
    pass


class CatalogManager:
    def __init__(self, filepath=CATALOG_FILE):
        self.filepath = Path(filepath)
        self.lock = RLock()
        if not self.filepath.exists():
            if self.filepath.with_suffix(".sqlite3").exists():
                raise CatalogError("Existe un catálogo SQLite: exporta sus datos con scripts/migrate_catalog.py")
            self._write({"round_robin_index": 0, "files": {}})
        self._read()

    @staticmethod
    def validate(data):
        if (not isinstance(data, dict) or not isinstance(data.get("files"), dict)
                or type(data.get("round_robin_index")) is not int or data["round_robin_index"] < 0):
            raise ValueError("Estructura de catálogo incorrecta")
        for name, record in data["files"].items():
            if (not isinstance(record, dict) or record.get("filename") != name
                    or record.get("status") != "COMMITTED"
                    or type(record.get("size_bytes")) is not int or record["size_bytes"] < 0
                    or not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in record["sha256"])
                    or not isinstance(record.get("primary_node"), str) or not record["primary_node"]
                    or not isinstance(record.get("replica_node"), str) or not record["replica_node"]
                    or record["primary_node"] == record["replica_node"]
                    or not isinstance(record.get("created_at"), str)):
                raise ValueError("Metadatos de archivo incorrectos")
            # Compatibilidad con el JSON original, que guardaba por nombre.
            record.setdefault("storage_key", name)
            if not isinstance(record["storage_key"], str) or not record["storage_key"]:
                raise ValueError("Identificador físico incorrecto")
        return data

    def _read(self):
        try:
            return self.validate(json.loads(self.filepath.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise CatalogError("No se puede leer el catálogo; el archivo original se conserva") from exc

    def _write(self, data):
        try:
            content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            atomic_write(self.filepath, content)
        except OSError as exc:
            raise CatalogError("No se pudo guardar el catálogo") from exc

    def list_files(self):
        with self.lock:
            return self._read()["files"]

    def get_file(self, filename):
        return self.list_files().get(filename)

    def candidates(self, nodes):
        with self.lock:
            index = self._read()["round_robin_index"] % len(nodes)
            return nodes[index:] + nodes[:index]

    def delete_file(self, filename):
        """Quita un archivo del catálogo y devuelve su registro (None si no existía)."""
        with self.lock:
            data = self._read()
            record = data["files"].pop(filename, None)
            if record is not None:
                self._write(data)
            return record

    def add_file(self, record, node_count):
        with self.lock:
            data = self._read()
            if record["filename"] in data["files"]:
                raise FileExists("El nombre ya existe; utiliza otro nombre para el nuevo archivo")
            data["files"][record["filename"]] = record
            data["round_robin_index"] = (data["round_robin_index"] + 1) % node_count
            self._write(data)
