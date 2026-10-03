"""Behavioral SQL-injection boundaries for the authenticated SQLite API."""
import copy
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote


# Importing web.main initializes an app; keep that side effect isolated.
_module_data = tempfile.TemporaryDirectory(prefix="movie-security-sql-module-")
try:
    with patch.dict(os.environ, {"MOVIE_REVIEW_DATA_DIR": _module_data.name}):
        from fastapi.testclient import TestClient
        from web.main import create_app
except ModuleNotFoundError:
    TestClient = None


def review(identifier="sql_text"):
    return {"id": identifier, "title": "'; DROP TABLE reviews; --", "director": "' UNION SELECT username FROM users --",
            "date": "2026-10-03", "rating": 5, "tags": ["x'); DELETE FROM users; --", "' OR 1=1 --"],
            "categories": ["'; DROP TABLE terms; --"], "comment": "quote ' and \";\nUPDATE users SET role='admin'; --",
            "createdAt": 1700000000000, "updatedAt": 1700000000000,
            "rewatches": [{"watchedAt": "2026-10-03T12:30", "feeling": "'; DELETE FROM sessions; --",
                            "createdAt": 1700000000000, "rating": 4}],
            "personalContext": {"location": "' OR 1=1 --", "companions": "'; DROP TABLE users; --",
                                "mood": "' UNION SELECT password_hash FROM users --", "lifeStage": "SQL-like text",
                                "impact": "'; UPDATE users SET is_active=0; --", "favoriteScene": "'; DELETE FROM movies; --",
                                "favoriteQuote": "'; DROP TABLE review_versions; --", "recommendTo": "' OR 1=1 --", "watchAgain": True}}


@unittest.skipIf(TestClient is None, "Web dependencies are not installed in this environment")
class SqlInjectionBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="movie-security-sql-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.app = create_app(self.path, secure_cookie=False)
        self.database = self.app.state.database
        self.database.set_account("admin", "sql-test-admin-password")
        self.user = self.database.create_user(1, "reader", "Reader", "sql-test-reader-password")
        self.admin = TestClient(self.app)
        self.client = TestClient(self.app)
        self.addCleanup(self.admin.close)
        self.addCleanup(self.client.close)
        self.admin_headers = self.login(self.admin, "admin", "sql-test-admin-password")
        self.headers = self.login(self.client, "reader", "sql-test-reader-password")
        with self.database.connect() as connection:
            self.tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def login(self, client, username, password):
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrfToken"]}

    def assert_database_intact(self):
        with self.database.connect() as connection:
            self.assertEqual({row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}, self.tables)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            user = connection.execute("SELECT role,is_active FROM users WHERE id=?", (self.user["id"],)).fetchone()
            self.assertEqual((user["role"], user["is_active"]), ("user", 1))

    def test_sql_like_credentials_cannot_bypass_authentication(self):
        with TestClient(self.app) as anonymous:
            credentials = [
                {"username": "admin' OR 1=1 --", "password": "wrong-password"},
                {"username": "admin'; DELETE FROM users; --", "password": "wrong-password"},
                {"username": "admin", "password": "' OR 1=1 --"},
                {"username": "' UNION SELECT * FROM users --", "password": "wrong-password"},
            ]
            for body in credentials:
                response = anonymous.post("/api/auth/login", json=body)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("movie_review_session", response.cookies)
                self.assertEqual(anonymous.get("/api/auth/me").status_code, 401)
            self.login(anonymous, "admin", "sql-test-admin-password")
        self.assert_database_intact()

    def test_sql_like_username_is_a_literal_account_with_its_own_role(self):
        username = "' OR 1=1 --"
        created = self.admin.post("/api/users", json={"username": username, "displayName": "'; DROP TABLE users; --",
                                                     "password": "literal-account-password", "role": "user"}, headers=self.admin_headers)
        self.assertEqual(created.status_code, 201, created.text)
        uid = created.json()["user"]["id"]
        with TestClient(self.app) as literal:
            self.assertEqual(literal.post("/api/auth/login", json={"username": username, "password": "wrong-password"}).status_code, 401)
            headers = self.login(literal, username, "literal-account-password")
            current = literal.get("/api/auth/me").json()["user"]
            self.assertEqual((current["id"], current["username"], current["role"]), (uid, username, "user"))
            self.assertEqual(literal.get("/api/users").status_code, 403)
            display_name = "'; UPDATE users SET role='admin'; --"
            updated = literal.put("/api/auth/profile", json={"displayName": display_name}, headers=headers)
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["user"]["displayName"], display_name)
            self.assertEqual(literal.get("/api/auth/me").json()["user"]["role"], "user")
        self.assert_database_intact()

    def test_sql_like_review_and_request_key_round_trip_as_data(self):
        value = review()
        key = "request-key-'; DELETE FROM reviews; --"
        created = self.client.post("/api/reviews", json={"review": value, "requestKey": key}, headers=self.headers)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["review"], value)
        replay = self.client.post("/api/reviews", json={"review": value, "requestKey": key}, headers=self.headers)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json(), created.json())
        self.assertEqual(self.client.get("/api/reviews/sql_text").json()["review"], value)
        self.assertEqual(self.admin.get("/api/reviews").json()["reviews"], [])
        updated_value = copy.deepcopy(value)
        updated_value["title"] = "'; UPDATE users SET role='admin'; --"
        updated_value["comment"] += "\nSELECT sqlite_version();"
        changed = self.client.put("/api/reviews/sql_text", json={"review": updated_value, "revision": 1}, headers=self.headers)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.client.get("/api/reviews/sql_text").json()["review"], updated_value)
        self.assert_database_intact()
        deleted = self.client.delete("/api/reviews/sql_text?revision=2", headers=self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get("/api/reviews").json()["reviews"], [])
        self.assert_database_intact()

    def test_sql_like_paths_and_typed_ids_do_not_expand_owner_scope(self):
        private = review("admin_private")
        self.database.create_review(private, uuid.uuid4().hex, 1)
        injection = quote("admin_private' OR 1=1 --", safe="")
        self.assertEqual(self.client.get(f"/api/reviews/{injection}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/reviews/{injection}?revision=1", headers=self.headers).status_code, 409)
        self.assertEqual(self.client.get("/api/reviews/admin_private").status_code, 404)
        self.assertEqual(self.client.delete("/api/reviews/admin_private?revision=1", headers=self.headers).status_code, 409)
        self.assertEqual(self.client.delete("/api/reviews/admin_private?revision=1%20OR%201=1", headers=self.headers).status_code, 422)
        wrong_identifier = self.admin.post("/api/users/1%20OR%201=1/password", json={"password": "another-long-password"}, headers=self.admin_headers)
        self.assertEqual(wrong_identifier.status_code, 422)
        self.assertEqual(self.admin.get("/api/reviews/admin_private").json()["review"], private)
        self.assertEqual(list((self.path / "backups").rglob("*.zip")), [])
        self.assert_database_intact()

    def test_json_import_preserves_sql_like_text_without_executing_it(self):
        private = review("admin_private")
        self.database.create_review(private, uuid.uuid4().hex, 1)
        imported = [review("imported_sql")]
        preview = self.client.post("/api/imports/preview", files={"file": ("archive.json", json.dumps(imported).encode(), "application/json")},
                                   headers=self.headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        confirmed = self.client.post("/api/imports/confirm", json={"importToken": preview.json()["importToken"]}, headers=self.headers)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(self.client.get("/api/reviews/imported_sql").json()["review"], imported[0])
        self.assertEqual(self.admin.get("/api/reviews/admin_private").json()["review"], private)
        self.assert_database_intact()


if __name__ == "__main__":
    unittest.main()
