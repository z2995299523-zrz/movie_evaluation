"""Migration behavior without importing the web app or modifying its default database."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import DataValidationError
from web.storage import ArchiveDatabase


def record(**changes):
    value = {"id": "old_review", "title": "原始电影", "director": "导演", "date": "2026-09-30",
             "rating": 0, "tags": ["记忆", "经典"], "comment": "保留原文", "createdAt": 1700000000000,
             "updatedAt": 1700000000001, "categories": ["剧情", "悬疑"],
             "rewatches": [{"watchedAt": "2026-09-30T19:30", "feeling": "再看", "createdAt": 1700000000000, "rating": 0}],
             "personalContext": {"location": "", "companions": "", "mood": "", "lifeStage": "", "impact": "",
                                 "favoriteScene": "", "favoriteQuote": "", "recommendTo": "", "watchAgain": False}}
    value.update(changes)
    return value


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "db" / "app.sqlite3"
        self.path.parent.mkdir()

    def legacy(self, value):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE account(id INTEGER PRIMARY KEY CHECK(id=1),username TEXT,password_hash TEXT,changed_at INTEGER);
                CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,csrf_hash TEXT,expires_at INTEGER,created_at INTEGER);
                CREATE TABLE reviews(id TEXT PRIMARY KEY,payload TEXT,revision INTEGER,updated_at INTEGER);
                CREATE TABLE review_history(id INTEGER PRIMARY KEY,review_id TEXT,revision INTEGER,payload TEXT,operation TEXT,changed_at INTEGER);
                CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER);
                CREATE TABLE idempotency(request_key TEXT PRIMARY KEY,review_id TEXT,payload_hash TEXT,created_at INTEGER);
                CREATE TABLE uploads(reference TEXT PRIMARY KEY,uploaded_at INTEGER);
                CREATE TABLE login_failures(source TEXT,at INTEGER);
            """)
            hashed = ArchiveDatabase.password_hash("original-long-password")
            db.execute("INSERT INTO account VALUES(1,'原用户',?,123)", (hashed,))
            db.execute("INSERT INTO sessions VALUES('old-token','old-csrf',9999999999,1)")
            db.execute("INSERT INTO reviews VALUES(?,?,4,123)", (value["id"], json.dumps(value, ensure_ascii=False)))
            db.execute("INSERT INTO review_history VALUES(1,?,3,?,'update',122)", (value["id"], json.dumps(record(comment="旧版本"), ensure_ascii=False)))
            db.execute("INSERT INTO meta VALUES('archive_revision',7)")
        return hashed

    def test_legacy_migration_preserves_fields_history_password_and_revision(self):
        value = record()
        hashed = self.legacy(value)
        database = ArchiveDatabase(self.path)
        self.assertTrue(database.migration_backup.is_file())
        self.assertEqual(database.list_reviews(), {"reviews": [{"review": value, "revision": 4}], "archiveRevision": 7})
        token, _ = database.login("原用户", "original-long-password", "migration-test")
        self.assertEqual(database.session(token)["user_id"], 1)
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT password_hash FROM users WHERE id=1").fetchone()[0], hashed)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(database.read_snapshot(db, 1, include_history=True)[0]["review"]["comment"], "旧版本")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions WHERE token_hash='old-token'").fetchone()[0], 0)
        with closing(sqlite3.connect(database.migration_backup)) as db:
            self.assertEqual(json.loads(db.execute("SELECT payload FROM reviews").fetchone()[0]), value)
        # Reopening a v2 database must not migrate again or reset the archive.
        self.assertIsNone(ArchiveDatabase(self.path).migration_backup)
        self.assertEqual(ArchiveDatabase(self.path).list_reviews()["archiveRevision"], 7)

    def test_invalid_legacy_data_rolls_back_schema_and_preserves_backup(self):
        value = record(title="  不能静默裁剪  ")
        self.legacy(value)
        with self.assertRaises(DataValidationError):
            ArchiveDatabase(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(json.loads(db.execute("SELECT payload FROM reviews").fetchone()[0]), value)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM account").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone())
        backups = list((self.path.parent.parent / "backups").glob("pre-schema-v2-*.sqlite3"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
