"""Verify browser policy and request limits with isolated HTTP clients."""
import os
import re
import io
import json
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory() as module_directory:
    with patch.dict(os.environ, {"MOVIE_REVIEW_DATA_DIR": module_directory}):
        from web.main import create_app
from fastapi.testclient import TestClient
from app import BACKUP_FORMAT, BACKUP_FORMAT_VERSION, DATA_FILE_NAME, ReviewStore


class NetworkSecurityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = create_app(Path(temporary.name), secure_cookie=False)
        self.app.state.database.set_account("auditor", "isolated-security-password")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def login(self):
        response = self.client.post("/api/auth/login", json={"username": "auditor", "password": "isolated-security-password"})
        self.assertEqual(response.status_code, 200)
        return {"X-CSRF-Token": response.json()["csrfToken"]}

    def test_page_nonce_matches_policy_and_changes_per_request(self):
        first = self.client.get("/")
        nonce = re.search(r'<script nonce="([A-Za-z0-9_-]+)">', first.text).group(1)
        policy = first.headers["content-security-policy"]
        self.assertIn("'nonce-" + nonce + "'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("connect-src 'self'", policy)
        second = self.client.get("/")
        self.assertNotEqual(first.headers["content-security-policy"], second.headers["content-security-policy"])
        self.assertEqual(first.headers["x-frame-options"], "DENY")
        self.assertEqual(first.headers["cache-control"], "no-store")

    def test_unauthenticated_media_write_is_rejected_before_parser(self):
        response = self.client.post("/api/media", content=b"unparsed input", headers={"Content-Type": "multipart/form-data"})
        self.assertEqual(response.status_code, 401, response.text)
        self.assertIn("content-security-policy", response.headers)

    def test_valid_login_and_profile_write_remain_available(self):
        headers = self.login()
        response = self.client.put("/api/auth/profile", json={"displayName": "正常资料"}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["displayName"], "正常资料")

    def test_declared_oversized_login_is_rejected_without_authentication(self):
        response = self.client.post("/api/auth/login", content=b"{}", headers={"Content-Length": str(65 * 1024)})
        self.assertEqual(response.status_code, 413, response.text)

    def test_streamed_account_body_cannot_bypass_length_limit(self):
        headers = self.login()
        headers["Content-Type"] = "application/json"
        def body():
            yield b'{"displayName":"'
            yield b"x" * (40 * 1024)
            yield b"x" * (40 * 1024)
            yield b'"}'
        response = self.client.put("/api/auth/profile", content=body(), headers=headers)
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["displayName"], "auditor")

    def test_inflight_previews_reserve_capacity_before_background_processing(self):
        headers = self.login()
        for _ in range(19):
            response = self.client.post("/api/imports/preview", files={"file": ("archive.json", b"[]")}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": BACKUP_FORMAT, "version": BACKUP_FORMAT_VERSION, "recordCount": 0}))
            archive.writestr(DATA_FILE_NAME, "[]")
        started, release = threading.Event(), threading.Event()
        original = ReviewStore.prepare_zip_import
        def paused_prepare(store, source):
            started.set()
            if not release.wait(10):
                raise RuntimeError("Test preparation timed out")
            return original(store, source)
        def preview():
            return self.client.post("/api/imports/preview", files={"file": ("archive.zip", content.getvalue())}, headers=headers)
        with patch.object(ReviewStore, "prepare_zip_import", paused_prepare), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(preview)
            try:
                self.assertTrue(started.wait(5))
                refused = pool.submit(preview).result(timeout=5)
                self.assertEqual(refused.status_code, 429, refused.text)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10).status_code, 200)
        self.assertEqual(len(list((self.app.state.database.path.parent.parent / "imports").iterdir())), 20)

    def test_import_confirmation_keeps_health_responsive_and_rejects_duplicate(self):
        headers = self.login()
        preview = self.client.post("/api/imports/preview", files={"file": ("archive.json", b"[]")}, headers=headers).json()
        started, release = threading.Event(), threading.Event()
        database = self.app.state.database
        original = database.replace_archive
        def paused_replace(*args, **kwargs):
            started.set()
            if not release.wait(10):
                raise RuntimeError("Test import timed out")
            return original(*args, **kwargs)
        with TestClient(self.app) as client, patch.object(database, "replace_archive", paused_replace), ThreadPoolExecutor(max_workers=2) as pool:
            client.cookies.update(self.client.cookies)
            body = {"importToken": preview["importToken"]}
            first = pool.submit(client.post, "/api/imports/confirm", json=body, headers=headers)
            try:
                self.assertTrue(started.wait(5))
                health = pool.submit(client.get, "/healthz").result(timeout=3)
                self.assertEqual(health.status_code, 200)
                duplicate = pool.submit(client.post, "/api/imports/confirm", json=body, headers=headers).result(timeout=3)
                self.assertEqual(duplicate.status_code, 409, duplicate.text)
            finally:
                release.set()
            completed = first.result(timeout=10)
            self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(database.list_reviews(1)["archiveRevision"], 1)

    def test_import_token_type_is_validated(self):
        headers = self.login()
        response = self.client.post("/api/imports/confirm", json={"importToken": []}, headers=headers)
        self.assertEqual(response.status_code, 410, response.text)
