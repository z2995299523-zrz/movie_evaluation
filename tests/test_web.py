import base64
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from web.main import create_app
    from web.backup import create_backup, restore_backup, verify_backup
except ModuleNotFoundError:
    TestClient = None


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def review(review_id="movie_1", **changes):
    value = {"id": review_id, "title": "电影", "director": "导演", "date": "2026-09-29",
             "rating": 5, "tags": [], "comment": "观后感", "createdAt": 1700000000000,
             "updatedAt": 1700000000000,
             "rewatches": [{"watchedAt": "2026-09-30T19:30", "feeling": "重看", "createdAt": 1700000000000, "rating": 4}],
             "personalContext": {"location": "影院", "companions": "朋友", "mood": "期待", "lifeStage": "毕业",
                                 "impact": "影响", "favoriteScene": "结尾", "favoriteQuote": "台词",
                                 "recommendTo": "家人", "watchAgain": True}}
    value.update(changes)
    return value


@unittest.skipIf(TestClient is None, "Web dependencies are not installed in this environment")
class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.app = create_app(self.path, secure_cookie=False)
        self.app.state.database.set_account("me", "long-password-for-tests")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def login(self):
        response = self.client.post("/api/auth/login", json={"username": "me", "password": "long-password-for-tests"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrfToken"]

    def headers(self, csrf):
        return {"X-CSRF-Token": csrf, "Origin": "http://testserver"}

    def add_user(self, csrf, username="second", role="user"):
        result = self.client.post("/api/users", json={"username": username, "displayName": "第二位用户",
                                                     "password": "second-long-password", "role": role}, headers=self.headers(csrf))
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()["user"]

    def user_client(self, username="second", password="second-long-password"):
        client = TestClient(self.app)
        self.addCleanup(client.close)
        result = client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(result.status_code, 200, result.text)
        return client, result.json()["csrfToken"]

    def test_admin_user_management_and_session_revocation(self):
        csrf = self.login()
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["role"], "admin")
        user = self.add_user(csrf)
        other, other_csrf = self.user_client()
        self.assertEqual(other.get("/api/users").status_code, 403)
        self.assertEqual(other.post("/api/users", json={"username": "intruder", "displayName": "侵入者",
                                                      "password": "long-password-for-tests"}, headers=self.headers(other_csrf)).status_code, 403)
        self.assertEqual(self.client.post("/api/users", json={"username": "second", "displayName": "重复",
                                                             "password": "long-password-for-tests"}, headers=self.headers(csrf)).status_code, 409)
        listed = self.client.get("/api/users").json()["users"]
        self.assertEqual(len(listed), 2)
        self.assertFalse(any("password" in key for value in listed for key in value))
        body = {"username": "second", "displayName": "已停用", "role": "user", "isActive": False}
        self.assertEqual(self.client.put(f"/api/users/{user['id']}", json=body, headers=self.headers(csrf)).status_code, 200)
        self.assertEqual(other.get("/api/reviews").status_code, 401)
        self.assertEqual(other.post("/api/auth/login", json={"username": "second", "password": "second-long-password"}).status_code, 401)
        body["isActive"] = True
        self.client.put(f"/api/users/{user['id']}", json=body, headers=self.headers(csrf))
        other, _ = self.user_client()
        self.assertEqual(self.client.post(f"/api/users/{user['id']}/password", json={"password": "reset-long-password"}, headers=self.headers(csrf)).status_code, 200)
        self.assertEqual(other.get("/api/reviews").status_code, 401)
        self.user_client(password="reset-long-password")
        self.assertEqual(self.client.put("/api/users/1", json={"username": "me", "displayName": "本人", "role": "user", "isActive": True}, headers=self.headers(csrf)).status_code, 409)
        self.assertEqual(self.client.put("/api/users/1", json={"username": "me", "displayName": "本人", "role": "admin", "isActive": False}, headers=self.headers(csrf)).status_code, 409)

    def test_own_profile_updates_only_display_name_and_preserves_sessions_and_archive(self):
        self.assertEqual(self.client.put('/api/auth/profile', json={'displayName': '未登录'}).status_code, 401)
        csrf = self.login()
        user = self.add_user(csrf)
        other, other_csrf = self.user_client()
        second_device, _ = self.user_client()
        before_admin = self.client.get('/api/auth/me').json()['user']
        before_archive = other.get('/api/reviews').json()
        self.assertEqual(other.put('/api/auth/profile', json={'displayName': '资料'}).status_code, 403)
        for body in ({'displayName': '   '}, {'displayName': '名称\n'}, {'displayName': '长' * 81},
                     {'displayName': '本人', 'role': 'admin'}, {'displayName': '本人', 'id': 1},
                     {'displayName': '本人', 'username': 'changed'}):
            self.assertEqual(other.put('/api/auth/profile', json=body, headers=self.headers(other_csrf)).status_code, 422)
        result = other.put('/api/auth/profile', json={'displayName': '  我的电影档案  '}, headers=self.headers(other_csrf))
        self.assertEqual(result.status_code, 200, result.text)
        updated = result.json()['user']
        self.assertEqual(updated['displayName'], '我的电影档案')
        for key in ('id', 'username', 'role', 'isActive', 'createdAt'):
            self.assertEqual(updated[key], user[key])
        self.assertEqual(second_device.get('/api/auth/me').json()['user']['displayName'], '我的电影档案')
        self.assertEqual(other.get('/api/reviews').json(), before_archive)
        self.assertEqual(self.client.get('/api/auth/me').json()['user'], before_admin)
        self.assertEqual(other.get('/api/users').status_code, 403)
        self.assertNotIn('password', str(updated.keys()).lower())

    def test_account_styles_are_served_and_included_in_web_page(self):
        page = self.client.get('/')
        self.assertIn('href="/web/account.css"', page.text)
        styles = self.client.get('/web/account.css')
        self.assertEqual(styles.status_code, 200)
        self.assertTrue(styles.headers['content-type'].startswith('text/css'))
        self.assertIn('.web-account-trigger', styles.text)

    def test_own_password_requires_current_password_and_revokes_all_sessions(self):
        csrf = self.login()
        second_device, _ = self.user_client("me", "long-password-for-tests")
        response = self.client.post("/api/auth/password", json={"currentPassword": "wrong", "password": "updated-long-password"}, headers=self.headers(csrf))
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/auth/password", json={"currentPassword": "long-password-for-tests", "password": "updated-long-password"}, headers=self.headers(csrf))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(second_device.get("/api/reviews").status_code, 401)
        self.assertEqual(self.client.get("/api/reviews").status_code, 401)
        self.user_client("me", "updated-long-password")

    def test_users_cannot_read_modify_export_or_replace_each_others_archive(self):
        csrf = self.login()
        self.add_user(csrf)
        other, other_csrf = self.user_client()
        key = uuid.uuid4().hex
        first = self.client.post("/api/reviews", json={"review": review("private"), "requestKey": key}, headers=self.headers(csrf))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(other.get("/api/reviews").json()["reviews"], [])
        self.assertEqual(other.get("/api/reviews/private").status_code, 404)
        self.assertEqual(other.put("/api/reviews/private", json={"review": review("private"), "revision": 1}, headers=self.headers(other_csrf)).status_code, 409)
        self.assertEqual(other.delete("/api/reviews/private?revision=1", headers=self.headers(other_csrf)).status_code, 409)
        # Public review IDs and retry keys are scoped by user, not globally unique.
        result = other.post("/api/reviews", json={"review": review("private", title="另一个档案"), "requestKey": key}, headers=self.headers(other_csrf))
        self.assertEqual(result.status_code, 201, result.text)
        before = self.client.get("/api/reviews").json()
        result = other.put("/api/archive/reviews", json={"reviews": [], "archiveRevision": 1}, headers=self.headers(other_csrf))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get("/api/reviews").json(), before)
        with zipfile.ZipFile(io.BytesIO(other.get("/api/exports/backup").content)) as archive:
            self.assertEqual(json.loads(archive.read("movie-reviews.json")), [])
        forged = review("forged", user_id=1)
        self.assertEqual(other.post("/api/reviews", json={"review": forged, "requestKey": uuid.uuid4().hex}, headers=self.headers(other_csrf)).status_code, 422)

    def test_media_and_import_preview_are_owned_by_current_user(self):
        csrf = self.login()
        self.add_user(csrf)
        other, other_csrf = self.user_client()
        uploaded = self.client.post("/api/media", files={"file": ("private.png", PNG, "image/png")}, headers=self.headers(csrf)).json()["image"]
        filename = uploaded["path"].split("/")[-1]
        self.assertEqual(other.get("/api/media/" + filename).status_code, 404)
        self.assertEqual(other.get("/api/media/" + filename + "/thumbnail").status_code, 404)
        self.assertEqual(other.post("/api/reviews", json={"review": review(image=uploaded), "requestKey": uuid.uuid4().hex}, headers=self.headers(other_csrf)).status_code, 404)
        self.client.post("/api/reviews", json={"review": review(image=uploaded), "requestKey": uuid.uuid4().hex}, headers=self.headers(csrf))
        backup = self.client.get("/api/exports/backup").content
        preview = self.client.post("/api/imports/preview", files={"file": ("backup.zip", backup, "application/zip")}, headers=self.headers(csrf)).json()
        self.assertEqual(other.post("/api/imports/confirm", json={"importToken": preview["importToken"]}, headers=self.headers(other_csrf)).status_code, 410)
        # Intentionally importing a supplied backup installs media into that user's archive.
        preview = other.post("/api/imports/preview", files={"file": ("backup.zip", backup, "application/zip")}, headers=self.headers(other_csrf)).json()
        result = other.post("/api/imports/confirm", json={"importToken": preview["importToken"]}, headers=self.headers(other_csrf))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(other.get("/api/media/" + filename).content, PNG)
        self.assertEqual(len(self.client.get("/api/reviews").json()["reviews"]), 1)

    def test_structured_versions_and_backup_keep_deleted_historical_media(self):
        csrf = self.login()
        image = self.client.post("/api/media", files={"file": ("original.png", PNG, "image/png")}, headers=self.headers(csrf)).json()["image"]
        original = review(image=image, tags=["记忆", "成长"], categories=["剧情", "经典"])
        self.client.post("/api/reviews", json={"review": original, "requestKey": uuid.uuid4().hex}, headers=self.headers(csrf))
        changed = review(title="修订的名称", rating=0)
        self.client.put("/api/reviews/movie_1", json={"review": changed, "revision": 1}, headers=self.headers(csrf))
        self.client.delete("/api/reviews/movie_1?revision=2", headers=self.headers(csrf))
        database = self.app.state.database
        with database.connect() as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertFalse(any(row["name"] == "payload" for row in db.execute("PRAGMA table_info(review_versions)")))
            history = database.read_snapshot(db, 1, include_history=True)
            self.assertEqual(history[0]["review"], original)
            self.assertEqual(history[1]["review"], changed)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO archive_state(user_id,revision) VALUES(99999,0)")
        archive = self.path / "historical.zip"
        self.assertEqual(create_backup(self.path, archive)["recordCount"], 0)
        restored = self.path / "historical-restore"
        restore_backup(archive, restored)
        self.assertEqual((restored / image["path"]).read_bytes(), PNG)

    def test_server_backup_restores_all_users_but_no_sessions(self):
        csrf = self.login()
        user = self.add_user(csrf)
        other, other_csrf = self.user_client()
        self.client.post("/api/reviews", json={"review": review(title="管理员档案"), "requestKey": uuid.uuid4().hex}, headers=self.headers(csrf))
        other.post("/api/reviews", json={"review": review(title="用户档案"), "requestKey": uuid.uuid4().hex}, headers=self.headers(other_csrf))
        # Unattached uploads must survive server restoration as well.
        image = other.post("/api/media", files={"file": ("pending.png", PNG, "image/png")}, headers=self.headers(other_csrf)).json()["image"]
        archive = self.path / "all-users.zip"
        self.assertEqual(create_backup(self.path, archive)["recordCount"], 2)
        restored = self.path / "all-users-restored"
        restore_backup(archive, restored)
        from web.storage import ArchiveDatabase
        database = ArchiveDatabase(restored / "db" / "app.sqlite3")
        self.assertEqual(database.list_reviews(1)["reviews"][0]["review"]["title"], "管理员档案")
        self.assertEqual(database.list_reviews(user["id"])["reviews"][0]["review"]["title"], "用户档案")
        self.assertTrue(database.can_access_image(user["id"], image["path"]))
        self.assertEqual((restored / image["path"]).read_bytes(), PNG)
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_auth_csrf_logout_and_password_reset(self):
        self.assertEqual(self.client.get("/api/reviews").status_code, 401)
        self.assertEqual(self.client.get("/api/exports/backup").status_code, 401)
        self.assertEqual(self.client.get("/api/media/" + "a" * 64 + ".png").status_code, 401)
        csrf = self.login()
        self.assertEqual(self.client.post("/api/reviews", json={"review": review(), "requestKey": uuid.uuid4().hex}).status_code, 403)
        created = self.client.post("/api/reviews", json={"review": review(), "requestKey": uuid.uuid4().hex}, headers=self.headers(csrf))
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers(csrf)).status_code, 200)
        self.assertEqual(self.client.get("/api/reviews").status_code, 401)
        csrf = self.login()
        self.app.state.database.set_account("me", "another-long-password", reset=True)
        self.assertEqual(self.client.get("/api/reviews").status_code, 401)

    def test_revision_conflict_and_idempotent_create(self):
        csrf = self.login()
        key = uuid.uuid4().hex
        body = {"review": review(), "requestKey": key}
        first = self.client.post("/api/reviews", json=body, headers=self.headers(csrf))
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post("/api/reviews", json=body, headers=self.headers(csrf))
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(len(self.client.get("/api/reviews").json()["reviews"]), 1)
        changed = review(title="改后")
        saved = self.client.put("/api/reviews/movie_1", json={"review": changed, "revision": 1}, headers=self.headers(csrf))
        self.assertEqual(saved.status_code, 200, saved.text)
        stale = self.client.put("/api/reviews/movie_1", json={"review": review(title="旧页面"), "revision": 1}, headers=self.headers(csrf))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get("/api/reviews/movie_1").json()["review"]["title"], "改后")
        self.assertEqual(self.client.delete("/api/reviews/movie_1?revision=1", headers=self.headers(csrf)).status_code, 409)

    def test_archive_replace_is_atomic_on_stale_revision(self):
        csrf = self.login()
        self.client.post("/api/reviews", json={"review": review(), "requestKey": uuid.uuid4().hex}, headers=self.headers(csrf))
        before = self.client.get("/api/reviews").json()
        self.client.post("/api/reviews", json={"review": review("movie_2"), "requestKey": uuid.uuid4().hex}, headers=self.headers(csrf))
        response = self.client.put("/api/archive/reviews", json={"reviews": [review(title="覆盖")],
                                                                 "archiveRevision": before["archiveRevision"]}, headers=self.headers(csrf))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.client.get("/api/reviews").json()["reviews"]), 2)

    def test_login_throttle_persists_and_bad_zip_does_not_replace_archive(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/api/auth/login", json={"username": "me", "password": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "me", "password": "long-password-for-tests"}).status_code, 429)
        with self.app.state.database.connect() as db:
            db.execute("DELETE FROM login_failures")
        csrf = self.login()
        self.client.post("/api/reviews", json={"review": review(), "requestKey": uuid.uuid4().hex}, headers=self.headers(csrf))
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("../escape", "bad")
            archive.writestr("manifest.json", "{}")
            archive.writestr("movie-reviews.json", "[]")
        response = self.client.post("/api/imports/preview", files={"file": ("bad.zip", content.getvalue(), "application/zip")},
                                    headers=self.headers(csrf))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.client.get("/api/reviews").json()["reviews"]), 1)

    def test_upload_private_media_and_backup_round_trip(self):
        csrf = self.login()
        upload = self.client.post("/api/media", files={"file": ("still.png", PNG, "image/png")}, headers=self.headers(csrf))
        self.assertEqual(upload.status_code, 200, upload.text)
        image = upload.json()["image"]
        filename = image["path"].split("/")[-1]
        self.assertEqual(self.client.get("/api/media/" + filename).content, PNG)
        created = self.client.post("/api/reviews", json={"review": review(image=image), "requestKey": uuid.uuid4().hex},
                                   headers=self.headers(csrf))
        self.assertEqual(created.status_code, 201, created.text)
        backup = self.client.get("/api/exports/backup")
        self.assertEqual(backup.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
            self.assertEqual(archive.read(image["path"]), PNG)
            self.assertEqual(json.loads(archive.read("movie-reviews.json"))[0], review(image=image))
        preview = self.client.post("/api/imports/preview", files={"file": ("archive.zip", backup.content, "application/zip")},
                                   headers=self.headers(csrf))
        self.assertEqual(preview.status_code, 200, preview.text)
        token = preview.json()["importToken"]
        applied = self.client.post("/api/imports/confirm", json={"importToken": token}, headers=self.headers(csrf))
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["reviews"][0]["review"], review(image=image))

    def test_server_backup_verifies_and_restores_in_isolation(self):
        csrf = self.login()
        self.client.post("/api/reviews", json={"review": review(), "requestKey": uuid.uuid4().hex}, headers=self.headers(csrf))
        archive = self.path / "server-backup.zip"
        manifest = create_backup(self.path, archive)
        self.assertEqual(manifest["recordCount"], 1)
        self.assertEqual(verify_backup(archive)["recordCount"], 1)
        restored = self.path / "restored"
        restore_backup(archive, restored)
        from web.storage import ArchiveDatabase
        self.assertEqual(ArchiveDatabase(restored / "db" / "app.sqlite3").list_reviews()["reviews"][0]["review"], review())
        with zipfile.ZipFile(archive) as source:
            files = {name: source.read(name) for name in source.namelist()}
        files["db/app.sqlite3"] = b"broken"
        broken = self.path / "broken.zip"
        with zipfile.ZipFile(broken, "w") as dest:
            for name, content in files.items():
                dest.writestr(name, content)
        with self.assertRaises(Exception):
            verify_backup(broken)


if __name__ == "__main__":
    unittest.main()
