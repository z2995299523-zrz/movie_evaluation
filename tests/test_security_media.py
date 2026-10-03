"""Boundary tests for backup paths and the Web media validation pipeline."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from app import BACKUP_FORMAT, BACKUP_FORMAT_VERSION, DATA_FILE_NAME, DataValidationError, normalize_reviews
from web.backup import create_backup, restore_backup, verify_backup
from web.images import validate_image_stream
from web.storage import ArchiveDatabase

# Importing the ASGI module must never initialize a real archive during tests.
with tempfile.TemporaryDirectory() as module_directory:
    with patch.dict(os.environ, {"MOVIE_REVIEW_DATA_DIR": module_directory}):
        from web.main import create_app
from fastapi.testclient import TestClient


def image_bytes(size=(2, 2), fmt="PNG"):
    stream = io.BytesIO()
    Image.new("L", size).save(stream, format=fmt)
    return stream.getvalue()


def portable_archive(content, *, extension="png", mime="image/png"):
    image = {"path": f"media/{hashlib.sha256(content).hexdigest()}.{extension}",
             "name": "still." + extension, "mime": mime, "size": len(content)}
    review = normalize_reviews([{"id": "media_test", "title": "测试电影", "director": "",
                                "date": "2026-10-03", "rating": 0, "tags": [], "comment": "",
                                "image": image}])[0]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({"format": BACKUP_FORMAT,
                        "version": BACKUP_FORMAT_VERSION, "recordCount": 1}))
        archive.writestr(DATA_FILE_NAME, json.dumps([review], ensure_ascii=False))
        archive.writestr(image["path"], content)
    return output.getvalue(), image


class MediaSecurityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = create_app(self.root / "web", secure_cookie=False)
        self.db = self.app.state.database
        self.db.set_account("auditor", "isolated-security-password")
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        login = self.client.post("/api/auth/login", json={"username": "auditor",
                                                        "password": "isolated-security-password"})
        self.headers = {"X-CSRF-Token": login.json()["csrfToken"]}

    def test_normal_import_preserves_original_bytes_and_thumbnail_works(self):
        content = image_bytes()
        backup, image = portable_archive(content)
        preview = self.client.post("/api/imports/preview", files={"file": ("archive.zip", backup)}, headers=self.headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        confirmed = self.client.post("/api/imports/confirm", json={"importToken": preview.json()["importToken"]}, headers=self.headers)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        filename = image["path"].split("/")[-1]
        self.assertEqual(self.client.get("/api/media/" + filename).content, content)
        self.assertEqual(self.client.get("/api/media/" + filename + "/thumbnail").status_code, 200)

    def test_oversized_image_has_the_same_upload_and_import_limit(self):
        content = image_bytes((6500, 6500))
        uploaded = self.client.post("/api/media", files={"file": ("still.png", content, "image/png")}, headers=self.headers)
        self.assertEqual(uploaded.status_code, 422, uploaded.text)
        backup, _ = portable_archive(content)
        preview = self.client.post("/api/imports/preview", files={"file": ("archive.zip", backup)}, headers=self.headers)
        self.assertEqual(preview.status_code, 422, preview.text)
        self.assertEqual(self.client.get("/api/reviews").json()["reviews"], [])
        self.assertFalse((self.root / "web/media").exists())

    def test_invalid_image_data_is_rejected_before_import_changes(self):
        backup, _ = portable_archive(b"\xff\xd8\xff" + b"not an image", extension="jpg", mime="image/jpeg")
        response = self.client.post("/api/imports/preview", files={"file": ("archive.zip", backup)}, headers=self.headers)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/reviews").json()["reviews"], [])
        self.assertEqual(list((self.root / "web/imports").iterdir()), [])

    def test_existing_invalid_media_returns_controlled_error_and_releases_processor(self):
        content = b"\xff\xd8\xff" + b"old invalid data"
        _, image = portable_archive(content, extension="jpg", mime="image/jpeg")
        destination = self.root / "web" / image["path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(content)
        self.db.register_upload(1, image)
        filename = image["path"].split("/")[-1]
        self.assertEqual(self.client.get("/api/media/" + filename + "/thumbnail").status_code, 422)
        valid = self.client.post("/api/media", files={"file": ("normal.png", image_bytes(), "image/png")}, headers=self.headers)
        self.assertEqual(valid.status_code, 200, valid.text)

    def test_decoder_rejects_truncated_content_and_preserves_stream_position(self):
        content = image_bytes(fmt="JPEG")
        stream = io.BytesIO(content)
        self.assertEqual(validate_image_stream(stream), "image/jpeg")
        self.assertEqual(stream.tell(), 0)
        with self.assertRaises(DataValidationError):
            validate_image_stream(io.BytesIO(content[:-20]))

    def test_historical_pixel_budget_remains_available(self):
        stream = io.BytesIO(image_bytes((11, 10)))
        with self.assertRaises(DataValidationError):
            validate_image_stream(stream, max_pixels=100)
        self.assertEqual(validate_image_stream(stream, max_pixels=200), "image/png")


class BackupPathSecurityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        database = ArchiveDatabase(self.root / "source/db/app.sqlite3")
        database.set_account("auditor", "isolated-security-password")
        self.good_backup = self.root / "good.zip"
        create_backup(self.root / "source", self.good_backup)

    def test_normal_empty_backup_still_restores(self):
        target = self.root / "restored"
        restored = restore_backup(self.good_backup, target)
        self.assertEqual(restored["recordCount"], 0)
        self.assertTrue((target / "db/app.sqlite3").is_file())

    def test_manifest_media_keys_must_all_be_standard_media_references(self):
        with zipfile.ZipFile(self.good_backup) as source:
            originals = {name: source.read(name) for name in source.namelist()}
        for name in ("../outside.png", "/absolute.png", "data/image.png", "media/not-a-hash.png", "media/../../outside.png"):
            with self.subTest(name=name):
                manifest = json.loads(originals["manifest.json"])
                manifest["mediaSha256"] = {name: hashlib.sha256(b"boundary fixture").hexdigest()}
                candidate = self.root / "candidate.zip"
                with zipfile.ZipFile(candidate, "w") as archive:
                    archive.writestr("db/app.sqlite3", originals["db/app.sqlite3"])
                    archive.writestr("manifest.json", json.dumps(manifest))
                    archive.writestr(name, b"boundary fixture")
                with self.assertRaises(DataValidationError):
                    verify_backup(candidate)
                target = self.root / "rejected-restore"
                with self.assertRaises(DataValidationError):
                    restore_backup(candidate, target)
                self.assertFalse(target.exists())

    def test_restore_refuses_a_target_that_already_has_files(self):
        target = self.root / "nonempty"
        target.mkdir()
        marker = target / "existing.txt"
        marker.write_text("preserve me")
        with self.assertRaises(DataValidationError):
            restore_backup(self.good_backup, target)
        self.assertEqual(marker.read_text(), "preserve me")

    def test_media_names_and_formats_must_match_their_content(self):
        content = image_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with zipfile.ZipFile(self.good_backup) as source:
            original_db = source.read("db/app.sqlite3")
            original_manifest = json.loads(source.read("manifest.json"))
        for reference, expected_error in (("media/" + "a" * 64 + ".png", "名称与哈希"),
                                          ("media/" + digest + ".jpg", "声明类型")):
            with self.subTest(reference=reference):
                manifest = dict(original_manifest, mediaSha256={reference: digest})
                candidate = self.root / "format-boundary.zip"
                with zipfile.ZipFile(candidate, "w") as archive:
                    archive.writestr("manifest.json", json.dumps(manifest))
                    archive.writestr("db/app.sqlite3", original_db)
                    archive.writestr(reference, content)
                with self.assertRaisesRegex(DataValidationError, expected_error):
                    verify_backup(candidate)
