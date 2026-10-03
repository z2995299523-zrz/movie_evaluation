"""Security regressions for validation before bounded automatic backups."""
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

# web.main creates a module-level app. Keep that initialization isolated too.
_module_data = tempfile.TemporaryDirectory(prefix="movie-security-auth-module-")
try:
    with patch.dict(os.environ, {"MOVIE_REVIEW_DATA_DIR": _module_data.name}):
        from fastapi.testclient import TestClient
        from web.main import create_app
        from web.backup import restore_backup, verify_backup
except ModuleNotFoundError:
    TestClient = None


def review(identifier):
    return {"id": identifier, "title": "Security test", "director": "Test",
            "date": "2026-10-02", "rating": 5, "tags": [], "comment": "Test",
            "createdAt": 1700000000000, "updatedAt": 1700000000000}


@unittest.skipIf(TestClient is None, "Web dependencies are not installed in this environment")
class AutomaticBackupSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="movie-security-auth-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.app = create_app(self.path, secure_cookie=False)
        self.database = self.app.state.database
        self.database.set_account("admin", "test-admin-long-password")
        self.user = self.database.create_user(1, "user", "User", "test-user-long-password")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        logged = self.client.post("/api/auth/login", json={"username": "user", "password": "test-user-long-password"})
        self.assertEqual(logged.status_code, 200)
        self.headers = {"Origin": "http://testserver", "X-CSRF-Token": logged.json()["csrfToken"]}
        self.backup_dir = self.path / "backups" / "automatic-v1"

    def create(self, identifier, user_id=None):
        return self.database.create_review(review(identifier), uuid.uuid4().hex,
                                           self.user["id"] if user_id is None else user_id)

    def backups(self):
        return list(self.backup_dir.glob("*.zip"))

    def age_backups(self):
        for path in self.backups():
            os.utime(path, (1, 1))

    def test_missing_foreign_stale_and_invalid_payloads_do_not_back_up(self):
        self.create("foreign", user_id=1)
        self.create("own")
        for identifier, revision in (("missing", 1), ("foreign", 1), ("own", 999)):
            for _ in range(3):
                response = self.client.delete(f"/api/reviews/{identifier}?revision={revision}", headers=self.headers)
                self.assertEqual(response.status_code, 409)
        stale = self.client.put("/api/archive/reviews", json={"reviews": [], "archiveRevision": 999}, headers=self.headers)
        self.assertEqual(stale.status_code, 409)
        duplicate = self.client.put("/api/archive/reviews", json={"reviews": [review("duplicate"), review("duplicate")],
                                                                  "archiveRevision": 1}, headers=self.headers)
        self.assertEqual(duplicate.status_code, 422)
        self.assertEqual(self.backups(), [])
        self.assertEqual(len(self.database.list_reviews(self.user["id"])["reviews"]), 1)

    def test_successful_delete_retains_a_verified_pre_change_restore_point(self):
        self.create("own")
        self.create("foreign", user_id=1)
        result = self.client.delete("/api/reviews/own?revision=1", headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.backups()), 1)
        manifest = verify_backup(self.backups()[0])
        self.assertEqual(manifest["recordCount"], 2)
        target = self.path / "restored"
        restore_backup(self.backups()[0], target)
        from web.storage import ArchiveDatabase
        restored = ArchiveDatabase(target / "db" / "app.sqlite3")
        self.assertEqual(restored.list_reviews(self.user["id"])["reviews"][0]["review"]["id"], "own")
        self.assertEqual(restored.list_reviews(1)["reviews"][0]["review"]["id"], "foreign")
        self.assertEqual(self.database.list_reviews(self.user["id"])["reviews"], [])

    def test_valid_operations_are_throttled_across_app_restart(self):
        self.create("first")
        self.create("second")
        self.assertEqual(self.client.delete("/api/reviews/first?revision=1", headers=self.headers).status_code, 200)
        restarted = create_app(self.path, secure_cookie=False)
        with TestClient(restarted) as other:
            other.cookies.update(self.client.cookies)
            result = other.delete("/api/reviews/second?revision=1", headers=self.headers)
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.headers.get("retry-after"), "10")
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(len(self.database.list_reviews(self.user["id"])["reviews"]), 1)
        self.age_backups()
        result = self.client.delete("/api/reviews/second?revision=1", headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.backups()), 2)

    def test_file_budget_blocks_mutation_and_preserves_existing_backups(self):
        self.create("first")
        self.create("second")
        self.assertEqual(self.client.delete("/api/reviews/first?revision=1", headers=self.headers).status_code, 200)
        self.age_backups()
        manual = self.path / "backups" / "user-history.zip"
        manual.write_bytes(b"Existing user history must remain unchanged")
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in [manual, *self.backups()]}
        with patch.dict(os.environ, {"MOVIE_REVIEW_AUTO_BACKUP_MAX_FILES": "1"}):
            limited = create_app(self.path, secure_cookie=False)
        with TestClient(limited) as client:
            client.cookies.update(self.client.cookies)
            result = client.delete("/api/reviews/second?revision=1", headers=self.headers)
        self.assertEqual(result.status_code, 507)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(len(self.database.list_reviews(self.user["id"])["reviews"]), 1)
        for path, digest in hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_byte_budget_and_backup_failure_leave_archive_unchanged(self):
        self.create("own")
        with patch.dict(os.environ, {"MOVIE_REVIEW_AUTO_BACKUP_MAX_BYTES": "1024"}):
            limited = create_app(self.path, secure_cookie=False)
        with TestClient(limited) as client:
            client.cookies.update(self.client.cookies)
            result = client.delete("/api/reviews/own?revision=1", headers=self.headers)
        self.assertEqual(result.status_code, 507)
        self.assertEqual(self.backups(), [])
        with patch("web.main.create_backup", side_effect=OSError("Simulated storage failure")):
            result = self.client.delete("/api/reviews/own?revision=1", headers=self.headers)
        self.assertEqual(result.status_code, 507)
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.database.list_reviews(self.user["id"])["reviews"][0]["review"]["id"], "own")

    def test_concurrent_same_revision_produces_one_backup_and_one_change(self):
        self.create("own")
        def delete(_):
            with TestClient(self.app) as client:
                client.cookies.update(self.client.cookies)
                return client.delete("/api/reviews/own?revision=1", headers=self.headers).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(delete, range(2)))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(verify_backup(self.backups()[0])["recordCount"], 1)

    def test_stale_import_confirm_does_not_create_a_backup(self):
        preview = self.client.post("/api/imports/preview", files={"file": ("archive.json", json.dumps([]).encode(), "application/json")},
                                   headers=self.headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.create("after-preview")
        confirmed = self.client.post("/api/imports/confirm", json={"importToken": preview.json()["importToken"]}, headers=self.headers)
        self.assertEqual(confirmed.status_code, 409)
        self.assertEqual(self.backups(), [])
        self.assertEqual(len(self.database.list_reviews(self.user["id"])["reviews"]), 1)


if __name__ == "__main__":
    unittest.main()
