"""Pruebas de los requisitos y de las protecciones básicas; todo el disco es temporal."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

_bootstrap = tempfile.TemporaryDirectory(prefix="mini-drive-tests-")
os.environ["STORAGE_DIR"] = str(Path(_bootstrap.name) / "bootstrap")
os.environ["NODE_ID"] = "node1"
os.environ.pop("CLIENT_API_KEY", None)
os.environ.pop("NODE_API_KEY", None)
unittest.addModuleCleanup(_bootstrap.cleanup)

import httpx
from fastapi.testclient import TestClient

from common import files as file_utils
from coordinator import main as coordinator, services
from coordinator.catalog import CatalogError, CatalogManager
from node.main import create_app as create_node
from scripts.migrate_catalog import migrate

NODES = [{"id": f"node{i}", "name": f"Nodo {i}", "url": f"http://node{i}"} for i in (1, 2, 3)]


class ClusterTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="mini-drive-cluster-")))
        self.catalog = CatalogManager(self.root / "catalog.json")
        self.apps = {n["id"]: create_node(self.root / n["id"], n["id"]) for n in NODES}
        self.clients = {key: self.stack.enter_context(TestClient(app)) for key, app in self.apps.items()}
        self.down, self.store_down, self.bad_ack, self.lost_ack = set(), set(), set(), set()

        def route(request):
            self.assertNotIn("authorization", request.headers)
            node_id, path = request.url.host, request.url.path
            if node_id in self.down or (path == "/store" and node_id in self.store_down):
                raise httpx.ConnectError("Nodo detenido", request=request)
            response = self.clients[node_id].request(request.method, path,
                        content=request.read(), headers=dict(request.headers))
            if path == "/store" and response.status_code == 201:
                if node_id in self.lost_ack:
                    raise httpx.ReadTimeout("Respuesta perdida", request=request)
                if node_id in self.bad_ack:
                    return httpx.Response(201, json={"sha256": "incorrect"})
            return response

        def client_factory(**kwargs):
            return httpx.Client(transport=httpx.MockTransport(route), **kwargs)

        self.stack.enter_context(patch.object(services, "DEFAULT_NODES", NODES))
        self.stack.enter_context(patch.object(services, "client_factory", client_factory))
        self.app = coordinator.create_app(self.catalog)
        self.client = self.stack.enter_context(TestClient(self.app))

    def upload(self, name="document.txt", content=b"original"):
        return self.client.post("/upload", files={"file": (name, content)})

    def download(self, name="document.txt"):
        return self.client.get("/download/" + quote(name, safe=""))

    def copies(self, name="document.txt"):
        record = self.catalog.get_file(name)
        return [(self.root / node / record["storage_key"]).read_bytes()
                for node in (record["primary_node"], record["replica_node"])]

    def test_distribution_and_real_replicas(self):
        expected = [("node1", "node2"), ("node2", "node3"), ("node3", "node1")]
        for index, (primary, replica) in enumerate(expected):
            name = f"file{index}.txt"
            response = self.upload(name)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["locations"], {"primary": primary, "replica": replica})
            self.assertEqual(self.copies(name), [b"original"] * 2)

    def test_primary_failure_uses_replica_and_identifies_it(self):
        self.upload()
        self.down.add("node1")
        response = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"original")
        self.assertEqual(response.headers["X-Storage-Node"], "node2")

    def test_replica_failure_preserves_primary_download(self):
        self.upload()
        self.down.add("node2")
        self.assertEqual(self.download().headers["X-Storage-Node"], "node1")

    def test_both_copies_down(self):
        self.upload()
        self.down.update(("node1", "node2"))
        self.assertEqual(self.download().status_code, 503)

    def test_corruption_uses_replica(self):
        self.upload()
        record = self.catalog.get_file("document.txt")
        (self.root / "node1" / record["storage_key"]).write_bytes(b"INVALID!")
        response = self.download()
        self.assertEqual(response.content, b"original")
        self.assertEqual(response.headers["X-Storage-Node"], "node2")
        (self.root / "node2" / record["storage_key"]).write_bytes(b"INVALID!")
        self.assertEqual(self.download().status_code, 503)

    def test_alternative_node_when_a_node_is_down(self):
        self.down.add("node1")
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["locations"], {"primary": "node2", "replica": "node3"})

    def test_failure_after_health_uses_alternative(self):
        self.store_down.add("node1")
        self.assertEqual(self.upload().json()["locations"], {"primary": "node2", "replica": "node3"})

    def test_one_copy_does_not_publish_and_retry_uses_new_key(self):
        self.store_down.update(("node2", "node3"))
        self.assertEqual(self.upload().status_code, 503)
        self.assertEqual(self.catalog.list_files(), {})
        old_keys = {p.name for p in (self.root / "node1").iterdir() if not p.name.startswith(".")}
        self.assertEqual(len(old_keys), 1)
        self.store_down.clear()
        self.assertEqual(self.upload(content=b"retry").status_code, 201)
        self.assertNotIn(self.catalog.get_file("document.txt")["storage_key"], old_keys)
        self.assertEqual(self.copies(), [b"retry"] * 2)

    def test_bad_ack_is_not_counted_as_a_copy(self):
        self.bad_ack.update(("node1", "node2", "node3"))
        self.assertEqual(self.upload().status_code, 503)
        self.assertEqual(self.catalog.list_files(), {})

    def test_lost_ack_can_use_other_two_nodes(self):
        self.lost_ack.add("node1")
        self.assertEqual(self.upload().json()["locations"], {"primary": "node2", "replica": "node3"})

    def test_duplicate_name_preserves_original(self):
        self.assertEqual(self.upload().status_code, 201)
        before = self.catalog.get_file("document.txt")
        self.assertEqual(self.upload(content=b"replacement").status_code, 409)
        self.assertEqual(self.catalog.get_file("document.txt"), before)
        self.assertEqual(self.copies(), [b"original"] * 2)

    def test_concurrent_duplicate_has_only_one_success(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda content: self.upload(content=content), [b"first", b"second"]))
        self.assertEqual(sorted(r.status_code for r in responses), [201, 409])
        expected = b"first" if responses[0].status_code == 201 else b"second"
        self.assertEqual(self.copies(), [expected] * 2)

    def test_concurrent_different_names_are_kept(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(lambda i: self.upload(f"{i}.txt"), range(3)))
        self.assertTrue(all(r.status_code == 201 for r in responses))
        self.assertEqual(len(self.catalog.list_files()), 3)

    def test_catalog_survives_restart(self):
        self.upload()
        reopened = CatalogManager(self.catalog.filepath)
        with TestClient(coordinator.create_app(reopened)) as client:
            response = client.get("/download/document.txt")
            self.assertEqual(response.content, b"original")

    def test_unicode_and_url_characters(self):
        for name in ("report-😀.txt", "a#b.txt", "100%.txt", "espacio á.txt"):
            with self.subTest(name=name):
                self.assertEqual(self.upload(name).status_code, 201)
                response = self.download(name)
                self.assertEqual(response.content, b"original")
                self.assertIn("filename*=UTF-8''", response.headers["content-disposition"])

    def test_invalid_names(self):
        for name in ("../outside", ".env", "CON.txt", "a:b.txt", "a?b.txt", "trailing."):
            with self.subTest(name=name):
                self.assertEqual(self.upload(name).status_code, 400)
        self.assertEqual(self.catalog.list_files(), {})

    def test_empty_file(self):
        self.assertEqual(self.upload(content=b"").status_code, 201)
        self.assertEqual(self.download().content, b"")

    def test_size_limit_on_coordinator_and_node(self):
        with patch.object(file_utils, "MAX_FILE_SIZE", 3):
            self.assertEqual(self.upload(content=b"four").status_code, 413)
            self.assertEqual(self.clients["node1"].post("/store", files={"file": ("key", b"four")}).status_code, 413)
        self.assertEqual(self.catalog.list_files(), {})

    def test_node_object_cannot_be_replaced(self):
        for content, status in ((b"one", 201), (b"one", 201), (b"two", 409)):
            self.assertEqual(self.clients["node1"].post("/store", files={"file": ("key", content)}).status_code, status)

    def test_no_cross_process_control_folders(self):
        self.upload()
        for node in ("node1", "node2", "node3"):
            self.assertFalse((self.root / node / ".locks").exists())
            self.assertFalse((self.root / node / ".deleted").exists())

    def test_wrong_identity_is_rejected(self):
        self.apps["node1"].state.storage.node_id = "wrong"
        self.assertEqual(self.upload().json()["locations"], {"primary": "node2", "replica": "node3"})

    def test_same_storage_is_not_counted_twice(self):
        identity = self.apps["node1"].state.storage.storage_id
        for app in self.apps.values():
            app.state.storage.storage_id = identity
        self.assertEqual(self.upload().status_code, 503)
        self.assertEqual(self.catalog.list_files(), {})

    def test_status_and_coordinator_health_are_separate(self):
        self.down.update(n["id"] for n in NODES)
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertTrue(all(n["status"] == "DOWN" for n in self.client.get("/status").json()["nodes"]))

    def test_missing_file_and_missing_form(self):
        self.assertEqual(self.download("missing").status_code, 404)
        self.assertEqual(self.client.post("/upload").status_code, 422)

    def test_api_without_keys_or_extra_endpoints(self):
        self.assertEqual(self.upload().status_code, 201)
        self.assertNotIn("securitySchemes", self.app.openapi().get("components", {}))
        self.assertEqual(set(self.app.openapi()["paths"]), {"/upload", "/download/{filename}", "/files", "/status", "/health"})

    def test_corrupt_catalog_is_not_reset(self):
        self.catalog.filepath.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.client.get("/files").status_code, 503)
        self.assertEqual(self.catalog.filepath.read_text(), "{broken")

    def test_failed_catalog_write_does_not_lose_previous_entries(self):
        self.upload()
        before = self.catalog.filepath.read_bytes()
        with patch.object(file_utils.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(CatalogError):
                self.catalog.add_file({**self.catalog.get_file("document.txt"), "filename": "second.txt"}, 3)
        self.assertEqual(self.catalog.filepath.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".pending-*")))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mini-drive-catalog-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def record(self):
        return {"filename": "legacy.txt", "storage_key": "original-key", "size_bytes": 4,
                "sha256": hashlib.sha256(b"data").hexdigest(), "primary_node": "node1",
                "replica_node": "node2", "created_at": "2026-10-12", "status": "COMMITTED"}

    def test_new_catalog_initializes_without_template(self):
        manager = CatalogManager(self.root / "catalog.json")
        self.assertEqual(manager.list_files(), {})
        self.assertEqual(manager.candidates(NODES), NODES)

    def test_corrupt_json_blocks_initialization(self):
        path = self.root / "catalog.json"
        path.write_text("broken", encoding="utf-8")
        with self.assertRaises(CatalogError):
            CatalogManager(path)
        self.assertEqual(path.read_text(), "broken")

    def test_legacy_json_keeps_physical_filename(self):
        record = self.record()
        record.pop("storage_key")
        path = self.root / "catalog.json"
        path.write_text(json.dumps({"round_robin_index": 1, "files": {"legacy.txt": record}}))
        manager = CatalogManager(path)
        self.assertEqual(manager.get_file("legacy.txt")["storage_key"], "legacy.txt")
        self.assertEqual(manager.candidates(NODES)[0]["id"], "node2")

    def test_existing_sqlite_requires_explicit_migration(self):
        (self.root / "catalog.sqlite3").write_bytes(b"not overwritten")
        with self.assertRaises(CatalogError):
            CatalogManager(self.root / "catalog.json")
        self.assertFalse((self.root / "catalog.json").exists())

    def test_sqlite_export_preserves_mapping_and_source(self):
        source, destination = self.root / "old.sqlite3", self.root / "catalog.json"
        record = self.record()
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.execute("CREATE TABLE files (filename, storage_key, size_bytes, sha256, primary_node, replica_node, created_at)")
            connection.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(record[k] for k in
                               ("filename", "storage_key", "size_bytes", "sha256", "primary_node", "replica_node", "created_at")))
            connection.execute("CREATE TABLE metadata (key, value)")
            connection.execute("INSERT INTO metadata VALUES ('round_robin_index', '2')")
        before = source.read_bytes()
        self.assertEqual(migrate(source, destination), 1)
        manager = CatalogManager(destination)
        self.assertEqual(manager.get_file("legacy.txt"), record)
        self.assertEqual(manager.candidates(NODES)[0]["id"], "node3")
        self.assertEqual(source.read_bytes(), before)
        with self.assertRaises(FileExistsError):
            migrate(source, destination)

    def test_storage_directory_keeps_its_identity(self):
        create_node(self.root / "storage", "node1")
        with self.assertRaises(RuntimeError):
            create_node(self.root / "storage", "node2")

    def test_duplicate_urls_rejected(self):
        nodes = [*NODES[:2], dict(NODES[2], url=NODES[0]["url"])]
        with patch.object(services, "DEFAULT_NODES", nodes):
            with self.assertRaises(services.NodeError):
                services.get_nodes()


if __name__ == "__main__":
    unittest.main()
