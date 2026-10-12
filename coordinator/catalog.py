"""Catálogo SQLite transaccional, reservas de subida y limpieza durable."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from common.config import BASE_DIR, CATALOG_DB, GARBAGE_GRACE, PENDING_TTL


class CatalogError(Exception):
    pass


class UploadConflict(CatalogError):
    pass


class CatalogManager:
    def __init__(self, filepath: Path = CATALOG_DB, legacy_file: Path = None):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_file = Path(legacy_file) if legacy_file else (
            BASE_DIR / "catalog.json" if self.filepath == CATALOG_DB else self.filepath.with_suffix(".json"))
        try:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS files (
                        filename TEXT PRIMARY KEY, storage_key TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0), sha256 TEXT NOT NULL,
                        primary_node TEXT NOT NULL, replica_node TEXT NOT NULL,
                        created_at TEXT NOT NULL, CHECK(primary_node != replica_node));
                    CREATE TABLE IF NOT EXISTS uploads (
                        operation_id TEXT PRIMARY KEY, filename TEXT NOT NULL UNIQUE,
                        storage_key TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, nodes TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS garbage (
                        storage_key TEXT NOT NULL, node_id TEXT NOT NULL, not_before REAL NOT NULL,
                        PRIMARY KEY(storage_key, node_id));
                """)
            with self._transaction() as connection:
                if not connection.execute("SELECT 1 FROM metadata WHERE key='initialized'").fetchone():
                    self._import_legacy(connection)
                    connection.execute("INSERT INTO metadata VALUES ('initialized', '1')")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CatalogError("No se puede migrar el catálogo; el JSON original se conserva") from exc

    @contextmanager
    def _connection(self):
        connection = None
        try:
            connection = sqlite3.connect(self.filepath, timeout=15)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        except sqlite3.Error as exc:
            raise CatalogError("Error de lectura o escritura del catálogo") from exc
        finally:
            if connection:
                connection.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _import_legacy(self, connection):
        data = {"round_robin_index": 0, "files": {}}
        if self.legacy_file.exists():
            data = json.loads(self.legacy_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
                raise ValueError("Estructura del catálogo JSON no válida")
        index = data.get("round_robin_index", 0)
        if type(index) is not int or index < 0:
            raise ValueError("Índice round-robin no válido")
        connection.execute("INSERT INTO metadata VALUES ('round_robin_index', ?)", (str(index),))
        for name, entry in data["files"].items():
            if (entry["filename"] != name or entry["status"] != "COMMITTED"
                    or type(entry["size_bytes"]) is not int or entry["size_bytes"] < 0
                    or not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in entry["sha256"])):
                raise ValueError("Metadatos JSON no válidos")
            connection.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (name, name, entry["size_bytes"], entry["sha256"],
                                entry["primary_node"], entry["replica_node"], entry["created_at"]))

    @staticmethod
    def _info(row):
        return {**dict(row), "status": "COMMITTED"} if row else None

    def get_file(self, filename):
        with self._connection() as connection:
            return self._info(connection.execute("SELECT * FROM files WHERE filename=?", (filename,)).fetchone())

    def ping(self):
        with self._connection() as connection:
            connection.execute("SELECT value FROM metadata WHERE key='initialized'").fetchone()

    def list_files(self):
        with self._connection() as connection:
            return {row["filename"]: self._info(row) for row in connection.execute("SELECT * FROM files ORDER BY filename")}

    def reserve_upload(self, filename, nodes):
        if len(nodes) < 2:
            raise CatalogError("Se necesitan al menos dos nodos")
        operation_id, storage_key = uuid4().hex, uuid4().hex
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM uploads WHERE filename=?", (filename,)).fetchone():
                raise UploadConflict("Ya hay una subida en curso con este nombre")
            index = int(connection.execute("SELECT value FROM metadata WHERE key='round_robin_index'").fetchone()[0])
            connection.execute("UPDATE metadata SET value=? WHERE key='round_robin_index'", (str((index + 1) % len(nodes)),))
            connection.execute("INSERT INTO uploads VALUES (?, ?, ?, ?, ?)",
                               (operation_id, filename, storage_key, time.time(), json.dumps([node["id"] for node in nodes])))
        offset = index % len(nodes)
        return {"operation_id": operation_id, "storage_key": storage_key, "nodes": nodes[offset:] + nodes[:offset]}

    @staticmethod
    def _queue(connection, storage_key, nodes, not_before):
        for node in nodes:
            connection.execute("INSERT INTO garbage VALUES (?, ?, ?) ON CONFLICT(storage_key, node_id) "
                               "DO UPDATE SET not_before=MIN(not_before, excluded.not_before)",
                               (storage_key, node, not_before))

    def commit_upload(self, operation_id, size_bytes, sha256, primary_node, replica_node):
        with self._transaction() as connection:
            pending = connection.execute("SELECT * FROM uploads WHERE operation_id=?", (operation_id,)).fetchone()
            if not pending or pending["created_at"] + PENDING_TTL < time.time():
                raise UploadConflict("La reserva de subida expiró")
            if primary_node == replica_node or not {primary_node, replica_node} <= set(json.loads(pending["nodes"])):
                raise CatalogError("Ubicaciones de réplica no válidas")
            old = connection.execute("SELECT * FROM files WHERE filename=?", (pending["filename"],)).fetchone()
            if old:
                self._queue(connection, old["storage_key"], [old["primary_node"], old["replica_node"]], time.time() + GARBAGE_GRACE)
            connection.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(filename) DO UPDATE SET "
                               "storage_key=excluded.storage_key, size_bytes=excluded.size_bytes, sha256=excluded.sha256, "
                               "primary_node=excluded.primary_node, replica_node=excluded.replica_node, created_at=excluded.created_at",
                               (pending["filename"], pending["storage_key"], size_bytes, sha256, primary_node, replica_node,
                                datetime.now(timezone.utc).isoformat()))
            unused = set(json.loads(pending["nodes"])) - {primary_node, replica_node}
            self._queue(connection, pending["storage_key"], unused, time.time())
            connection.execute("DELETE FROM uploads WHERE operation_id=?", (operation_id,))

    def abort_upload(self, operation_id):
        with self._transaction() as connection:
            pending = connection.execute("SELECT * FROM uploads WHERE operation_id=?", (operation_id,)).fetchone()
            if pending:
                self._queue(connection, pending["storage_key"], json.loads(pending["nodes"]), time.time())
                connection.execute("DELETE FROM uploads WHERE operation_id=?", (operation_id,))

    def expire_uploads(self):
        with self._transaction() as connection:
            rows = connection.execute("SELECT * FROM uploads WHERE created_at < ?", (time.time() - PENDING_TTL,)).fetchall()
            for row in rows:
                self._queue(connection, row["storage_key"], json.loads(row["nodes"]), time.time())
                connection.execute("DELETE FROM uploads WHERE operation_id=?", (row["operation_id"],))

    def garbage(self):
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM garbage WHERE not_before <= ? ORDER BY not_before LIMIT 100", (time.time(),))]

    def acknowledge_cleanup(self, storage_key, node_id):
        with self._transaction() as connection:
            connection.execute("DELETE FROM garbage WHERE storage_key=? AND node_id=?", (storage_key, node_id))

    def retry_cleanup_later(self, storage_key, node_id):
        # Llevar el fallo al final de la cola: un nodo caído no bloquea otros lotes.
        with self._transaction() as connection:
            connection.execute("UPDATE garbage SET not_before=? WHERE storage_key=? AND node_id=?",
                               (time.time(), storage_key, node_id))

    def backup(self, destination):
        destination = Path(destination)
        if destination.resolve() == self.filepath.resolve():
            raise ValueError("El respaldo debe tener una ruta distinta al catálogo")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
