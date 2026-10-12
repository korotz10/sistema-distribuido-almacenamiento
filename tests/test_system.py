"""Regresiones del diagnóstico: HTTP real de FastAPI sobre transporte ASGI, discos aislados."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

# Ninguna importación de las apps debe inicializar almacenamiento del proyecto.
_bootstrap = tempfile.TemporaryDirectory(prefix="mini-drive-tests-bootstrap-")
os.environ["STORAGE_DIR"] = str(Path(_bootstrap.name) / "default-node")
os.environ["NODE_ID"] = "node1"
os.environ["CLIENT_API_KEY"] = "client-test-" + "c" * 40
os.environ["NODE_API_KEY"] = "node-test-" + "n" * 40

import httpx

from common import files as file_utils, security
from coordinator.catalog import CatalogError, CatalogManager, UploadConflict
from coordinator import main as coordinator, services
from node.main import create_app as create_node
from node import router as node_router

NODES = [{"id": f"node{i}", "name": f"Nodo {i}", "url": f"http://node{i}"} for i in (1, 2, 3)]
CLIENT_HEADERS = {"Authorization": "Bearer " + os.environ["CLIENT_API_KEY"]}
NODE_HEADERS = {"Authorization": "Bearer " + os.environ["NODE_API_KEY"]}


class ClusterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mini-drive-regression-")
        self.root = Path(self.temp.name)
        self.catalog = CatalogManager(self.root / "catalog.sqlite3")
        self.apps = {node["id"]: create_node(self.root / node["id"], node["id"]) for node in NODES}
        self.storage = {node_id: app.state.storage for node_id, app in self.apps.items()}
        self.down, self.bad_ack, self.lost_ack = set(), set(), set()
        self.store_down = set()
        self.requests = []
        self.before = None
        owner = self

        class RouterTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                node_id, path = request.url.host, request.url.path
                owner.requests.append((node_id, request.method, path))
                await request.aread()
                if node_id in owner.down or (path == "/store" and node_id in owner.store_down):
                    raise httpx.ConnectError("simulated node failure", request=request)
                if owner.before:
                    await owner.before(request)
                response = await httpx.ASGITransport(app=owner.apps[node_id]).handle_async_request(request)
                if path == "/store" and response.status_code == 201:
                    if node_id in owner.lost_ack:
                        raise httpx.ReadTimeout("simulated acknowledgement loss", request=request)
                    if node_id in owner.bad_ack:
                        return httpx.Response(201, json={"status": "stored", "sha256": "incorrect"}, request=request)
                return response

        def client_factory(**kwargs):
            return httpx.AsyncClient(transport=RouterTransport(), **kwargs)

        self.node_patch = patch.object(services, "DEFAULT_NODES", NODES)
        self.client_patch = patch.object(services, "client_factory", client_factory)
        self.node_patch.start()
        self.client_patch.start()
        self.app = coordinator.create_app(self.catalog)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                       base_url="http://coordinator", headers=CLIENT_HEADERS)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.client_patch.stop()
        self.node_patch.stop()
        self.temp.cleanup()

    async def upload(self, filename="document.txt", content=b"original"):
        return await self.client.post("/upload", files={"file": (filename, content)})

    async def download(self, filename="document.txt"):
        return await self.client.get("/download/" + quote(filename, safe=""))

    def copies(self, record):
        return [(self.storage[node_id].directory / record["storage_key"]).read_bytes()
                for node_id in (record["primary_node"], record["replica_node"])]

    async def test_round_robin_and_two_copies(self):
        pairs = []
        for index in range(3):
            name = f"file{index}.txt"
            response = await self.upload(name, b"content")
            self.assertEqual(response.status_code, 201, response.text)
            pairs.append(response.json()["locations"])
            self.assertEqual(self.copies(self.catalog.get_file(name)), [b"content", b"content"])
        self.assertEqual(pairs, [{"primary": "node1", "replica": "node2"},
                                 {"primary": "node2", "replica": "node3"},
                                 {"primary": "node3", "replica": "node1"}])

    async def test_failed_overwrite_preserves_committed_version(self):
        self.assertEqual((await self.upload()).status_code, 201)
        old = self.catalog.get_file("document.txt")
        self.store_down.update(("node2", "node3"))
        response = await self.upload(content=b"new version")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.catalog.get_file("document.txt"), old)
        self.assertEqual(self.copies(old), [b"original", b"original"])
        self.assertEqual((await self.download()).content, b"original")

    async def test_successful_overwrite_publishes_new_key_and_defers_old_cleanup(self):
        await self.upload()
        old = self.catalog.get_file("document.txt")
        self.assertEqual((await self.upload(content=b"new")).status_code, 201)
        current = self.catalog.get_file("document.txt")
        self.assertNotEqual(old["storage_key"], current["storage_key"])
        self.assertEqual((await self.download()).content, b"new")
        await self.client.post("/maintenance")
        self.assertEqual(self.copies(old), [b"original", b"original"])
        with closing(sqlite3.connect(self.catalog.filepath)) as db, db:
            db.execute("UPDATE garbage SET not_before=0 WHERE storage_key=?", (old["storage_key"],))
        await self.client.post("/maintenance")
        for node_id in (old["primary_node"], old["replica_node"]):
            self.assertFalse((self.storage[node_id].directory / old["storage_key"]).exists())
        self.assertEqual(self.copies(current), [b"new", b"new"])

    async def test_concurrent_same_name_has_one_conflict(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def before(request):
            if request.url.path == "/store" and request.url.host == "node1":
                entered.set()
                await release.wait()
        self.before = before
        first = asyncio.create_task(self.upload(content=b"VERSION_A"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            second = await self.upload(content=b"VERSION_B")
            self.assertEqual(second.status_code, 409, second.text)
        finally:
            release.set()
        self.assertEqual((await first).status_code, 201)
        self.assertEqual(self.copies(self.catalog.get_file("document.txt")), [b"VERSION_A"] * 2)

    async def test_node_down_is_replaced_by_third_node(self):
        self.down.add("node1")
        response = await self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["locations"], {"primary": "node2", "replica": "node3"})

    async def test_store_failure_after_health_is_replaced(self):
        self.store_down.add("node1")
        response = await self.upload()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["locations"], {"primary": "node2", "replica": "node3"})

    async def test_failed_new_upload_is_tracked_and_cleaned(self):
        self.store_down.update(("node2", "node3"))
        self.assertEqual((await self.upload()).status_code, 503)
        self.assertIsNone(self.catalog.get_file("document.txt"))
        pending = self.catalog.garbage()
        self.assertEqual(len(pending), 3)
        key = pending[0]["storage_key"]
        self.assertTrue((self.storage["node1"].directory / key).exists())
        response = await self.client.post("/maintenance")
        self.assertEqual(response.json()["cleaned"], 3)
        self.assertFalse((self.storage["node1"].directory / key).exists())
        self.assertEqual(self.catalog.garbage(), [])

    async def test_cleanup_retries_when_node_returns(self):
        self.store_down.update(("node2", "node3"))
        await self.upload()
        self.down.add("node1")
        await self.client.post("/maintenance")
        self.assertEqual(len(self.catalog.garbage()), 1)
        self.down.clear()
        await self.client.post("/maintenance")
        self.assertEqual(self.catalog.garbage(), [])

    async def test_lost_ack_does_not_leave_an_untracked_copy(self):
        self.lost_ack.add("node1")
        self.assertEqual((await self.upload()).status_code, 201)
        record = self.catalog.get_file("document.txt")
        self.assertTrue((self.storage["node1"].directory / record["storage_key"]).exists())
        await self.client.post("/maintenance")
        self.assertFalse((self.storage["node1"].directory / record["storage_key"]).exists())
        self.assertEqual(self.copies(record), [b"original"] * 2)

    async def test_bad_ack_is_not_committed(self):
        self.bad_ack.update(("node1", "node2", "node3"))
        self.assertEqual((await self.upload()).status_code, 502)
        self.assertEqual(self.catalog.list_files(), {})

    async def test_corrupt_primary_uses_verified_replica(self):
        await self.upload()
        record = self.catalog.get_file("document.txt")
        (self.storage["node1"].directory / record["storage_key"]).write_bytes(b"CORRUPTED")
        response = await self.download()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"original")
        audit = await self.client.get("/integrity")
        self.assertEqual(audit.json()["files"]["document.txt"]["status"], "DEGRADED")
        self.assertEqual(audit.json()["files"]["document.txt"]["copies"][0]["status"], "CORRUPT")

    async def test_missing_both_copies_is_reported_by_integrity(self):
        await self.upload()
        record = self.catalog.get_file("document.txt")
        for node_id in ("node1", "node2"):
            (self.storage[node_id].directory / record["storage_key"]).unlink()
        response = await self.client.get("/status")
        self.assertEqual(response.json()["scope"], "NODE_READINESS")
        self.assertTrue(all(node["status"] == "READY" for node in response.json()["nodes"]))
        self.assertEqual((await self.client.get("/integrity")).json()["files"]["document.txt"]["status"], "UNAVAILABLE")
        self.assertEqual((await self.download()).status_code, 503)

    async def test_repair_restores_two_verified_copies(self):
        await self.upload()
        record = self.catalog.get_file("document.txt")
        (self.storage["node1"].directory / record["storage_key"]).write_bytes(b"corrupt")
        response = await self.client.post("/repair/document.txt")
        self.assertEqual(response.status_code, 200, response.text)
        repaired = self.catalog.get_file("document.txt")
        self.assertNotEqual(record["storage_key"], repaired["storage_key"])
        self.assertEqual(self.copies(repaired), [b"original"] * 2)
        self.assertEqual((await self.client.get("/integrity")).json()["files"]["document.txt"]["status"], "AVAILABLE")

    async def test_special_and_unicode_names_round_trip(self):
        for name in ("a#b.txt", "report-😀.txt", "100%.txt", "espacio y acento-á.txt"):
            with self.subTest(name=name):
                response = await self.upload(name, b"data")
                self.assertEqual(response.status_code, 201, response.text)
                downloaded = await self.download(name)
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(downloaded.content, b"data")
                self.assertIn("attachment;", downloaded.headers["content-disposition"])

    async def test_invalid_names_are_client_errors(self):
        for name in (".env", "../outside.txt", "a?b.txt", "CON.txt", "trailing.", "a:b.txt"):
            with self.subTest(name=name):
                response = await self.upload(name)
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.catalog.list_files(), {})

    async def test_anonymous_requests_are_rejected_before_parsing(self):
        response = await self.client.post("/upload", headers={"Authorization": ""}, content=b"invalid multipart")
        self.assertEqual(response.status_code, 401)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.apps["node1"]), base_url="http://node1") as client:
            for method, url in (("POST", "/store"), ("GET", "/retrieve/file.txt"), ("DELETE", "/objects/file.txt")):
                self.assertEqual((await client.request(method, url)).status_code, 401)
        self.assertEqual((await self.client.get("/health", headers={"Authorization": ""})).status_code, 200)

    async def test_unconfigured_auth_fails_closed(self):
        with patch.dict(os.environ, {"CLIENT_API_KEY": ""}):
            self.assertEqual((await self.client.get("/files")).status_code, 503)

    async def test_file_size_limit_and_body_limit(self):
        with patch.object(file_utils, "MAX_FILE_SIZE", 3):
            self.assertEqual((await self.upload(content=b"four")).status_code, 413)
        with patch.object(security, "MAX_FILE_SIZE", 3):
            response = await self.client.post("/upload", content=b"", headers={"Content-Length": str(1024 * 1024 + 4)})
            self.assertEqual(response.status_code, 413)

    async def test_chunked_body_limit(self):
        async def body():
            yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\n\r\n'
            for _ in range(18):
                yield b"x" * 65536
            yield b"\r\n--boundary--\r\n"
        with patch.object(security, "MAX_FILE_SIZE", 3):
            response = await self.client.post("/upload", content=body(), headers={"Content-Type": "multipart/form-data; boundary=boundary"})
        self.assertEqual(response.status_code, 413, response.text)

    async def test_stalled_upload_body_times_out(self):
        async def body():
            yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="slow.txt"\r\n\r\n'
            await asyncio.sleep(10)
        with patch.object(security, "TIMEOUT_READ", 0.02):
            response = await self.client.post("/upload", content=body(), headers={"Content-Type": "multipart/form-data; boundary=boundary"})
        self.assertEqual(response.status_code, 408)

    async def test_tombstone_prevents_late_store_after_cancellation(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.apps["node1"]),
                                    base_url="http://node1", headers=NODE_HEADERS) as client:
            key = "cancelled-object"
            self.assertEqual((await client.delete("/objects/" + key)).status_code, 204)
            response = await client.post("/store", files={"file": (key, b"late")})
            self.assertEqual(response.status_code, 410)
            self.assertFalse((self.storage["node1"].directory / key).exists())

    async def test_node_objects_cannot_be_overwritten(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.apps["node1"]),
                                    base_url="http://node1", headers=NODE_HEADERS) as client:
            for content, status in ((b"first", 201), (b"first", 201), (b"different", 409)):
                response = await client.post("/store", files={"file": ("key", content)})
                self.assertEqual(response.status_code, status)
            self.assertEqual((await client.get("/retrieve/key")).content, b"first")

    async def test_empty_file_round_trip(self):
        self.assertEqual((await self.upload(content=b"")).status_code, 201)
        response = await self.download()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    async def test_same_size_corruption_is_rejected_by_hash(self):
        await self.upload()
        record = self.catalog.get_file("document.txt")
        (self.storage["node1"].directory / record["storage_key"]).write_bytes(b"INVALID!")
        self.assertEqual((await self.download()).content, b"original")
        (self.storage["node2"].directory / record["storage_key"]).write_bytes(b"INVALID!")
        self.assertEqual((await self.download()).status_code, 503)

    async def test_cancellation_during_commit_does_not_delete_published_data(self):
        entered, release = threading.Event(), threading.Event()
        original = self.catalog.commit_upload

        def delayed_commit(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("La prueba no liberó el commit")
            return original(*args)

        with patch.object(self.catalog, "commit_upload", delayed_commit):
            task = asyncio.create_task(self.upload())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                task.cancel()
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        record = self.catalog.get_file("document.txt")
        self.assertIsNotNone(record)
        await self.client.post("/maintenance")
        self.assertEqual(self.copies(record), [b"original"] * 2)

    async def test_cancellation_during_replication_registers_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def before(request):
            if request.url.path == "/store":
                entered.set()
                await release.wait()
        self.before = before
        task = asyncio.create_task(self.upload())
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.catalog.get_file("document.txt"))
        self.assertEqual(len(self.catalog.garbage()), 3)

    async def test_wrong_node_identity_is_not_used(self):
        self.storage["node1"].node_id = "unexpected-node"
        response = await self.upload()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["locations"], {"primary": "node2", "replica": "node3"})

    async def test_probe_calls_are_concurrent(self):
        entered, release = set(), asyncio.Event()
        all_entered = asyncio.Event()

        async def before(request):
            if request.url.path == "/health":
                entered.add(request.url.host)
                if len(entered) == 3:
                    all_entered.set()
                await release.wait()
        self.before = before
        task = asyncio.create_task(self.client.get("/status"))
        try:
            await asyncio.wait_for(all_entered.wait(), 5)
        finally:
            release.set()
        self.assertEqual((await task).status_code, 200)

    async def test_timeout_preserves_previous_version(self):
        await self.upload()
        previous = self.catalog.get_file("document.txt")

        async def before(request):
            if request.url.path == "/store":
                await asyncio.sleep(10)
        self.before = before
        with patch.object(coordinator, "TRANSFER_TIMEOUT", 0.1):
            response = await self.upload(content=b"new")
        self.assertEqual(response.status_code, 504)
        self.assertEqual(self.catalog.get_file("document.txt"), previous)

    async def test_legacy_json_data_remains_downloadable(self):
        name = "legacy#á.txt"
        digest = hashlib.sha256(b"legacy").hexdigest()
        entry = {"filename": name, "size_bytes": 6, "sha256": digest, "primary_node": "node1",
                 "replica_node": "node2", "created_at": "2026-01-01T00:00:00", "status": "COMMITTED"}
        legacy = self.root / "legacy.json"
        legacy.write_text(json.dumps({"round_robin_index": 0, "files": {name: entry}}), encoding="utf-8")
        imported = CatalogManager(self.root / "import.sqlite3", legacy_file=legacy)
        for node_id in ("node1", "node2"):
            (self.storage[node_id].directory / name).write_bytes(b"legacy")
        self.app.state.catalog = imported
        self.assertEqual((await self.download(name)).content, b"legacy")
        self.assertEqual(imported.get_file(name)["storage_key"], name)

    async def test_corrupt_database_returns_503_without_reset(self):
        # Cerrar todas las conexiones (cada operación abre y cierra la suya).
        self.catalog.filepath.write_bytes(b"not a SQLite database")
        response = await self.client.get("/files")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.catalog.filepath.read_bytes(), b"not a SQLite database")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mini-drive-catalog-")
        self.root = Path(self.temp.name)
        self.catalog = CatalogManager(self.root / "catalog.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_parallel_connections_do_not_lose_updates(self):
        def write(index):
            manager = CatalogManager(self.catalog.filepath)
            operation = manager.reserve_upload(f"{index}.txt", NODES)
            manager.commit_upload(operation["operation_id"], 1, "a" * 64, "node1", "node2")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(24)))
        self.assertEqual(len(self.catalog.list_files()), 24)

    def test_reservation_excludes_other_managers(self):
        first = self.catalog.reserve_upload("same.txt", NODES)
        other = CatalogManager(self.catalog.filepath)
        with self.assertRaises(UploadConflict):
            other.reserve_upload("same.txt", NODES)
        self.catalog.abort_upload(first["operation_id"])
        other.reserve_upload("same.txt", NODES)

    def test_expired_reservation_is_recovered_and_cannot_commit(self):
        pending = self.catalog.reserve_upload("file.txt", NODES)
        with closing(sqlite3.connect(self.catalog.filepath)) as db, db:
            db.execute("UPDATE uploads SET created_at=0")
        restarted = CatalogManager(self.catalog.filepath)
        restarted.expire_uploads()
        with self.assertRaises(UploadConflict):
            self.catalog.commit_upload(pending["operation_id"], 1, "a" * 64, "node1", "node2")
        self.assertEqual(len(restarted.garbage()), 3)
        restarted.reserve_upload("file.txt", NODES)

    def test_invalid_legacy_json_is_preserved_and_blocks_migration(self):
        legacy = self.root / "broken.json"
        legacy.write_text("{truncated", encoding="utf-8")
        database = self.root / "new.sqlite3"
        for _ in range(2):
            with self.assertRaises(CatalogError):
                CatalogManager(database, legacy_file=legacy)
        self.assertEqual(legacy.read_text(encoding="utf-8"), "{truncated")
        legacy.write_text('{"round_robin_index": 0, "files": {}}', encoding="utf-8")
        self.assertEqual(CatalogManager(database, legacy_file=legacy).list_files(), {})

    def test_backup_can_be_opened_independently(self):
        pending = self.catalog.reserve_upload("file.txt", NODES)
        self.catalog.commit_upload(pending["operation_id"], 1, "a" * 64, "node1", "node2")
        backup = self.root / "backup.sqlite3"
        self.catalog.backup(backup)
        self.assertEqual(CatalogManager(backup).list_files(), self.catalog.list_files())

    def test_node_directory_cannot_be_shared_by_different_ids(self):
        directory = self.root / "storage"
        node_router.NodeStorage(directory, "node1")
        with self.assertRaises(RuntimeError):
            node_router.NodeStorage(directory, "node2")

    def test_abandoned_node_temporaries_are_removed_on_restart(self):
        directory = self.root / "storage"
        node_router.NodeStorage(directory, "node1")
        temporary = directory / ".pending-abandoned.tmp"
        temporary.write_bytes(b"partial data")
        node_router.NodeStorage(directory, "node1")
        self.assertFalse(temporary.exists())

    def test_active_node_temporaries_survive_other_worker_startup(self):
        directory = self.root / "storage"
        storage = node_router.NodeStorage(directory, "node1")
        temporary = directory / ".pending-active.tmp"
        with file_utils.file_lock(storage.locks / temporary.stem):
            temporary.write_bytes(b"active data")
            node_router.NodeStorage(directory, "node1")
            self.assertTrue(temporary.exists())

    def test_duplicate_node_urls_are_rejected(self):
        nodes = [dict(NODES[0]), dict(NODES[1], url=NODES[0]["url"])]
        with patch.object(services, "DEFAULT_NODES", nodes):
            with self.assertRaises(services.NodeError):
                services.get_nodes()

    def test_cleanup_failure_does_not_starve_later_entries(self):
        with self.catalog._transaction() as db:
            for index in range(101):
                db.execute("INSERT INTO garbage VALUES (?, ?, ?)", (str(index), "node1", index))
        batch = self.catalog.garbage()
        self.assertEqual(len(batch), 100)
        for item in batch:
            self.catalog.retry_cleanup_later(item["storage_key"], item["node_id"])
        self.assertEqual(self.catalog.garbage()[0]["storage_key"], "100")


if __name__ == "__main__":
    unittest.main()
