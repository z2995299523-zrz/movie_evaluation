"""Normalized SQLite archive, user management, and atomic v1 migration."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from app import DataValidationError, MAX_REVIEWS, ReviewStore, normalize_reviews

SCHEMA_VERSION = 2
CONTEXT_FIELDS = {
    "location": "location", "companions": "companions", "mood": "mood",
    "lifeStage": "life_stage", "impact": "impact", "favoriteScene": "favorite_scene",
    "favoriteQuote": "favorite_quote", "recommendTo": "recommend_to",
}


class ConflictError(Exception):
    pass


class ArchiveDatabase:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migration_backup: Path | None = None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise DataValidationError("数据库版本比程序新，请使用对应版本的程序")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            legacy = "reviews" in tables and any(r["name"] == "payload" for r in db.execute("PRAGMA table_info(reviews)"))
            if legacy:
                self._migrate_legacy(db, tables)
            elif version == 0:
                if tables - {"sqlite_sequence"}:
                    raise DataValidationError("数据库包含无法识别的表，拒绝自动修改")
                self._create_schema(db)
                self._ensure_owner(db)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise DataValidationError("数据库版本不支持自动迁移")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _create_schema(db):
        # executescript commits an open transaction, so execute DDL separately.
        for statement in Path(__file__).with_name("schema.sql").read_text(encoding="utf-8").split(";"):
            if statement.strip():
                db.execute(statement)

    @staticmethod
    def _ensure_owner(db):
        now = int(time.time())
        db.execute("INSERT OR IGNORE INTO users(id,username,display_name,password_hash,role,is_active,created_at,updated_at) "
                   "VALUES(1,'__uninitialized_owner__','档案所有者','','admin',0,?,?)", (now, now))
        db.execute("INSERT OR IGNORE INTO archive_state(user_id,revision) VALUES(1,0)")

    def _migrate_legacy(self, db, tables):
        backup_dir = self.path.parent.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"pre-schema-v2-{time.time_ns()}-{secrets.token_hex(4)}.sqlite3"
        # BEGIN IMMEDIATE blocks writers while another connection backs up v1.
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DataValidationError("迁移前数据库备份校验失败")
        self.migration_backup = backup
        names = ("account", "sessions", "reviews", "idempotency", "review_history", "meta", "uploads", "login_failures")
        if tables - set(names) - {"sqlite_sequence"}:
            raise DataValidationError("旧数据库存在未知表，已保留备份并停止迁移")
        for name in names:
            if name in tables:
                db.execute(f"ALTER TABLE {name} RENAME TO legacy_{name}")
        self._create_schema(db)
        self._ensure_owner(db)
        if "account" in tables:
            account = db.execute("SELECT * FROM legacy_account WHERE id=1").fetchone()
            if account:
                db.execute("UPDATE users SET username=?,display_name=?,password_hash=?,is_active=1,created_at=?,updated_at=? WHERE id=1",
                           (account["username"], account["username"], account["password_hash"], account["changed_at"], account["changed_at"]))
        if "meta" in tables:
            revision = db.execute("SELECT value FROM legacy_meta WHERE key='archive_revision'").fetchone()
            if revision:
                db.execute("UPDATE archive_state SET revision=? WHERE user_id=1", (revision[0],))
        current = db.execute("SELECT * FROM legacy_reviews ORDER BY rowid").fetchall()
        history = db.execute("SELECT * FROM legacy_review_history ORDER BY id").fetchall() if "review_history" in tables else []
        identities = {}
        for public_id in [r["id"] for r in current] + [r["review_id"] for r in history]:
            if public_id not in identities:
                identities[public_id] = db.execute("INSERT INTO reviews(user_id,public_id,deleted_at) VALUES(1,?,?)",
                                                   (public_id, int(time.time()))).lastrowid
        for row in history:
            value = self._legacy_review(row["payload"], row["review_id"])
            vid = self._write_version(db, identities[row["review_id"]], value, row["revision"], row["operation"], row["changed_at"])
            db.execute("UPDATE reviews SET current_version_id=? WHERE id=?", (vid, identities[row["review_id"]]))
        for row in current:
            value = self._legacy_review(row["payload"], row["id"])
            vid = self._write_version(db, identities[row["id"]], value, row["revision"], "migration", row["updated_at"])
            db.execute("UPDATE reviews SET current_version_id=?,deleted_at=NULL WHERE id=?", (vid, identities[row["id"]]))
        if "uploads" in tables:
            store = ReviewStore(self.path.parent.parent)
            for row in db.execute("SELECT * FROM legacy_uploads").fetchall():
                path = store.resolve_image_path(row["reference"])
                if path.is_file():
                    info = store.get_image_info(row["reference"])
                    self._register_asset(db, {"path": row["reference"], "mime": info["mime"], "size": info["size"]})
                    db.execute("INSERT OR IGNORE INTO user_uploads(user_id,reference,uploaded_at) VALUES(1,?,?)", (row["reference"], row["uploaded_at"]))
        if "idempotency" in tables:
            for row in db.execute("SELECT * FROM legacy_idempotency").fetchall():
                if row["review_id"] in identities:
                    db.execute("INSERT INTO idempotency(user_id,request_key,review_id,payload_hash,created_at) VALUES(1,?,?,?,?)",
                               (row["request_key"], identities[row["review_id"]], row["payload_hash"], row["created_at"]))
        if "login_failures" in tables:
            db.execute("INSERT INTO login_failures(source,at) SELECT source,at FROM legacy_login_failures")
        # v1 sessions did not record a user, so require a fresh login.
        for name in names:
            if name in tables:
                db.execute(f"DROP TABLE legacy_{name}")
        if db.execute("PRAGMA foreign_key_check").fetchall() or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DataValidationError("迁移后数据库约束或完整性检查失败")
        actual = [i["review"] for i in self.read_snapshot(db, 1)]
        if actual != [json.loads(r["payload"]) for r in current]:
            raise DataValidationError("迁移后影评字段不一致，已回滚")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @classmethod
    def _legacy_review(cls, payload, public_id):
        raw = json.loads(payload)
        value = cls.validate_review(raw)
        if raw != value or value["id"] != public_id:
            raise DataValidationError("旧影评需要修整或 ID 不一致，拒绝有损迁移")
        return value

    @staticmethod
    def password_hash(password):
        if not 12 <= len(password) <= 1024:
            raise DataValidationError("密码需要 12 到 1024 个字符")
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
        return "pbkdf2_sha256$600000$" + salt.hex() + "$" + digest.hex()

    @staticmethod
    def verify_password(password, stored):
        try:
            algorithm, count, salt, expected = stored.split("$")
            if algorithm != "pbkdf2_sha256" or len(password) > 1024:
                return False
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(count))
            return hmac.compare_digest(actual, bytes.fromhex(expected))
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _validate_user(username, display_name, role):
        if not 1 <= len(username) <= 80 or username != username.strip() or not username.isprintable():
            raise DataValidationError("账户名需要 1 到 80 个可显示字符，首尾不能有空格")
        if not 1 <= len(display_name.strip()) <= 80 or not display_name.isprintable():
            raise DataValidationError("显示名称需要 1 到 80 个可显示字符")
        if role not in ("admin", "user"):
            raise DataValidationError("用户角色无效")

    @staticmethod
    def public_user(row):
        return {"id": row["id"], "username": row["username"], "displayName": row["display_name"],
                "role": row["role"], "isActive": bool(row["is_active"]),
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    @staticmethod
    def _require_admin(db, actor_id):
        if not db.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND is_active=1 AND password_hash<>''", (actor_id,)).fetchone():
            raise PermissionError("仅管理员可以管理用户")

    def set_account(self, username, password, *, reset=False):
        """Offline bootstrap/reset, compatible with existing deployment commands."""
        self._validate_user(username, username, "admin")
        hashed = self.password_hash(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if reset:
                user = db.execute("SELECT id FROM users WHERE username=? AND password_hash<>''", (username,)).fetchone()
                if not user:
                    raise DataValidationError("账户不存在")
                user_id = user["id"]
            else:
                if db.execute("SELECT 1 FROM users WHERE password_hash<>''").fetchone():
                    raise DataValidationError("账户已初始化，请通过用户管理新增用户")
                user_id = 1
            db.execute("UPDATE users SET username=?,display_name=?,password_hash=?,is_active=1,updated_at=? WHERE id=?",
                       (username, username, hashed, int(time.time()), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def list_users(self, actor_id):
        with self.connect() as db:
            self._require_admin(db, actor_id)
            return [self.public_user(r) for r in db.execute("SELECT * FROM users WHERE password_hash<>'' ORDER BY id")]

    def create_user(self, actor_id, username, display_name, password, role="user"):
        self._validate_user(username, display_name, role)
        hashed = self.password_hash(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_admin(db, actor_id)
            if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ConflictError("账户名已存在")
            now = int(time.time())
            uid = db.execute("INSERT INTO users(username,display_name,password_hash,role,is_active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                             (username, display_name.strip(), hashed, role, now, now)).lastrowid
            db.execute("INSERT INTO archive_state(user_id,revision) VALUES(?,0)", (uid,))
            return self.public_user(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

    def update_user(self, actor_id, user_id, username, display_name, role, is_active):
        self._validate_user(username, display_name, role)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_admin(db, actor_id)
            old = db.execute("SELECT * FROM users WHERE id=? AND password_hash<>''", (user_id,)).fetchone()
            if not old:
                raise LookupError("用户不存在")
            if old["role"] == "admin" and old["is_active"] and (role != "admin" or not is_active):
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1 AND password_hash<>''").fetchone()[0] <= 1:
                    raise ConflictError("必须保留至少一位启用的管理员")
            if db.execute("SELECT 1 FROM users WHERE username=? AND id<>?", (username, user_id)).fetchone():
                raise ConflictError("账户名已存在")
            db.execute("UPDATE users SET username=?,display_name=?,role=?,is_active=?,updated_at=? WHERE id=?",
                       (username, display_name.strip(), role, int(is_active), int(time.time()), user_id))
            if old["username"] != username or old["role"] != role or bool(old["is_active"]) != is_active:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return self.public_user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def update_profile(self, user_id, display_name):
        """Update only the authenticated user's display name; retain sessions."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE id=? AND is_active=1 AND password_hash<>''", (user_id,)).fetchone()
            if not user:
                raise PermissionError("账户已失效，请重新登录")
            self._validate_user(user["username"], display_name, user["role"])
            db.execute("UPDATE users SET display_name=?,updated_at=? WHERE id=?",
                       (display_name.strip(), int(time.time()), user_id))
            return self.public_user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def reset_user_password(self, actor_id, user_id, password):
        hashed = self.password_hash(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_admin(db, actor_id)
            if db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=? AND password_hash<>''",
                          (hashed, int(time.time()), user_id)).rowcount != 1:
                raise LookupError("用户不存在")
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def change_password(self, user_id, current, password):
        hashed = self.password_hash(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT password_hash FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone()
            if not row or not self.verify_password(current, row["password_hash"]):
                raise PermissionError("当前密码错误")
            db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (hashed, int(time.time()), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def login(self, username, password, source):
        now = int(time.time())
        source_hash = self._hash(source)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_failures WHERE at<?", (now - 900,))
            failures = db.execute("SELECT COUNT(*) FROM login_failures WHERE source=?", (source_hash,)).fetchone()[0]
            if failures >= 5:
                error = "尝试过多，请 15 分钟后再试"
            else:
                account = db.execute("SELECT * FROM users WHERE username=? AND is_active=1", (username,)).fetchone()
                stored = account["password_hash"] if account else "pbkdf2_sha256$600000$" + "00" * 32 + "$" + "00" * 32
                valid = self.verify_password(password, stored)
                if not account or not valid:
                    db.execute("INSERT INTO login_failures(source,at) VALUES(?,?)", (source_hash, now))
                    error = "账户或密码错误"
                else:
                    db.execute("DELETE FROM login_failures WHERE source=?", (source_hash,))
                    token = secrets.token_urlsafe(48)
                    csrf = self.csrf_for_session(token)
                    db.execute("DELETE FROM sessions WHERE expires_at<?", (now,))
                    db.execute("INSERT INTO sessions(token_hash,user_id,csrf_hash,expires_at,created_at) VALUES(?,?,?,?,?)",
                               (self._hash(token), account["id"], self._hash(csrf), now + 7 * 86400, now))
                    error = ""
        if error:
            raise PermissionError(error)
        return token, csrf

    @staticmethod
    def _hash(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def session(self, token):
        if not token:
            return None
        with self.connect() as db:
            return db.execute("SELECT s.*,u.username,u.display_name,u.role,u.is_active,u.created_at AS user_created_at,u.updated_at AS user_updated_at "
                              "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1",
                              (self._hash(token), int(time.time()))).fetchone()

    @staticmethod
    def session_user(row):
        return {"id": row["user_id"], "username": row["username"], "displayName": row["display_name"],
                "role": row["role"], "isActive": True, "createdAt": row["user_created_at"], "updatedAt": row["user_updated_at"]}

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (self._hash(token),))

    @staticmethod
    def csrf_for_session(token):
        return hmac.new(token.encode(), b"movie-review-csrf-v1", hashlib.sha256).hexdigest()

    @staticmethod
    def validate_review(value):
        if not isinstance(value, dict):
            raise DataValidationError("影评不是对象")
        allowed = {"id", "title", "director", "date", "rating", "tags", "comment",
                   "createdAt", "updatedAt", "categories", "rewatches", "personalContext", "image"}
        if set(value) - allowed:
            raise DataValidationError("影评包含不支持的字段")
        normalized = normalize_reviews([value])[0]
        if value.get("id") != normalized["id"]:
            raise DataValidationError("影评 ID 无效")
        return normalized

    @staticmethod
    def _register_asset(db, image):
        old = db.execute("SELECT mime,size FROM media_assets WHERE reference=?", (image["path"],)).fetchone()
        if old and (old["mime"] != image["mime"] or old["size"] != image["size"]):
            raise DataValidationError("同一剧照的类型或大小不一致")
        db.execute("INSERT OR IGNORE INTO media_assets(reference,mime,size) VALUES(?,?,?)", (image["path"], image["mime"], image["size"]))

    def register_upload(self, user_id, image):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._register_upload(db, user_id, image)

    def _register_upload(self, db, user_id, image):
        self._register_asset(db, image)
        db.execute("INSERT INTO user_uploads(user_id,reference,uploaded_at) VALUES(?,?,?) "
                   "ON CONFLICT(user_id,reference) DO UPDATE SET uploaded_at=excluded.uploaded_at",
                   (user_id, image["path"], int(time.time())))

    def can_access_image(self, user_id, reference):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM user_uploads WHERE user_id=? AND reference=?", (user_id, reference)).fetchone()
                        or db.execute("SELECT 1 FROM reviews r JOIN review_images i ON i.version_id=r.current_version_id "
                                      "WHERE r.user_id=? AND r.deleted_at IS NULL AND i.reference=?", (user_id, reference)).fetchone())

    def _write_version(self, db, internal_id, value, revision, operation, changed_at=None):
        movie = db.execute("SELECT v.movie_id,m.title,m.director FROM reviews r JOIN review_versions v ON v.id=r.current_version_id "
                           "JOIN movies m ON m.id=v.movie_id WHERE r.id=?", (internal_id,)).fetchone()
        if movie and movie["title"] == value["title"] and movie["director"] == value["director"]:
            movie_id = movie["movie_id"]
        else:
            movie_id = db.execute("INSERT INTO movies(title,director) VALUES(?,?)", (value["title"], value["director"])).lastrowid
        vid = db.execute("INSERT INTO review_versions(review_id,revision,movie_id,watched_date,rating,comment,created_at,updated_at,changed_at,operation) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)", (internal_id, revision, movie_id, value["date"], value["rating"], value["comment"],
                         value["createdAt"], value["updatedAt"], changed_at if changed_at is not None else int(time.time()), operation)).lastrowid
        for kind, field in [("tag", "tags"), ("category", "categories")]:
            for position, name in enumerate(value.get(field, [])):
                db.execute("INSERT OR IGNORE INTO terms(kind,name) VALUES(?,?)", (kind, name))
                term_id = db.execute("SELECT id FROM terms WHERE kind=? AND name=?", (kind, name)).fetchone()[0]
                db.execute("INSERT INTO review_terms(version_id,term_id,position) VALUES(?,?,?)", (vid, term_id, position))
        for position, event in enumerate(value.get("rewatches", [])):
            db.execute("INSERT INTO rewatches(version_id,position,watched_at,feeling,created_at,rating) VALUES(?,?,?,?,?,?)",
                       (vid, position, event["watchedAt"], event["feeling"], event["createdAt"], event.get("rating")))
        context = value.get("personalContext")
        if context:
            db.execute("INSERT INTO personal_context(version_id,location,companions,mood,life_stage,impact,favorite_scene,favorite_quote,recommend_to,watch_again) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (vid, *(context[key] for key in CONTEXT_FIELDS), context.get("watchAgain")))
        if value.get("image"):
            self._register_asset(db, value["image"])
            db.execute("INSERT INTO review_images(version_id,reference,original_name) VALUES(?,?,?)", (vid, value["image"]["path"], value["image"]["name"]))
        return vid

    @staticmethod
    def _decode_versions(db, rows):
        decoded = {}
        for row in rows:
            decoded[row["version_id"]] = {"review": {"id": row["public_id"], "title": row["title"], "director": row["director"],
                "date": row["watched_date"], "rating": row["rating"], "tags": [], "comment": row["comment"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}, "revision": row["revision"]}
        ids = list(decoded)
        # Chunk IN clauses for builds with a 999-variable limit.
        for offset in range(0, len(ids), 500):
            chunk = ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            for term in db.execute(f"SELECT l.version_id,l.position,t.kind,t.name FROM review_terms l JOIN terms t ON t.id=l.term_id "
                                   f"WHERE l.version_id IN ({placeholders}) ORDER BY l.position", chunk):
                field = "tags" if term["kind"] == "tag" else "categories"
                decoded[term["version_id"]]["review"].setdefault(field, []).append(term["name"])
            for event in db.execute(f"SELECT * FROM rewatches WHERE version_id IN ({placeholders}) ORDER BY position", chunk):
                value = {"watchedAt": event["watched_at"], "feeling": event["feeling"], "createdAt": event["created_at"]}
                if event["rating"] is not None:
                    value["rating"] = event["rating"]
                decoded[event["version_id"]]["review"].setdefault("rewatches", []).append(value)
            for context in db.execute(f"SELECT * FROM personal_context WHERE version_id IN ({placeholders})", chunk):
                value = {key: context[column] for key, column in CONTEXT_FIELDS.items()}
                if context["watch_again"] is not None:
                    value["watchAgain"] = bool(context["watch_again"])
                decoded[context["version_id"]]["review"]["personalContext"] = value
            for image in db.execute(f"SELECT i.version_id,i.reference,i.original_name,a.mime,a.size FROM review_images i "
                                    f"JOIN media_assets a ON a.reference=i.reference WHERE i.version_id IN ({placeholders})", chunk):
                decoded[image["version_id"]]["review"]["image"] = {"path": image["reference"], "name": image["original_name"], "mime": image["mime"], "size": image["size"]}
        return list(decoded.values())

    @classmethod
    def read_snapshot(cls, db, user_id=None, *, include_history=False):
        join = "v.review_id=r.id" if include_history else "v.id=r.current_version_id"
        where = "1=1" if include_history else "r.deleted_at IS NULL"
        if user_id is not None:
            where += " AND r.user_id=?"
        rows = db.execute(f"SELECT r.public_id,v.id AS version_id,v.*,m.title,m.director FROM reviews r "
                          f"JOIN review_versions v ON {join} JOIN movies m ON m.id=v.movie_id WHERE {where} ORDER BY r.id,v.id",
                          () if user_id is None else (user_id,)).fetchall()
        return cls._decode_versions(db, rows)

    def list_reviews(self, user_id=1):
        with self.connect() as db:
            db.execute("BEGIN")
            records = self.read_snapshot(db, user_id)
            version = db.execute("SELECT revision FROM archive_state WHERE user_id=?", (user_id,)).fetchone()
            if version is None:
                raise LookupError("用户不存在")
        return {"reviews": records, "archiveRevision": version[0]}

    @staticmethod
    def _bump(db, user_id):
        db.execute("UPDATE archive_state SET revision=revision+1 WHERE user_id=?", (user_id,))
        return db.execute("SELECT revision FROM archive_state WHERE user_id=?", (user_id,)).fetchone()[0]

    @staticmethod
    def _find(db, public_id, user_id):
        return db.execute("SELECT r.*,v.revision FROM reviews r LEFT JOIN review_versions v ON v.id=r.current_version_id "
                          "WHERE r.user_id=? AND r.public_id=?", (user_id, public_id)).fetchone()

    def create_review(self, value, key, user_id=1):
        review = self.validate_review(value)
        if not 16 <= len(key) <= 128:
            raise DataValidationError("创建请求缺少有效的幂等标识")
        digest = self._hash(json.dumps(review, ensure_ascii=False, separators=(",", ":")))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM idempotency WHERE created_at<?", (int(time.time()) - 30 * 86400,))
            existing = db.execute("SELECT * FROM idempotency WHERE user_id=? AND request_key=?", (user_id, key)).fetchone()
            old = self._find(db, review["id"], user_id)
            if existing:
                if not old or existing["review_id"] != old["id"] or existing["payload_hash"] != digest:
                    raise ConflictError("此创建请求已用于另一条影评")
                if old["deleted_at"] is not None:
                    raise ConflictError("这条影评已被删除")
                return next(i for i in self.read_snapshot(db, user_id) if i["review"]["id"] == review["id"])
            if old and old["deleted_at"] is None:
                raise ConflictError("影评 ID 已存在")
            if db.execute("SELECT COUNT(*) FROM reviews WHERE user_id=? AND deleted_at IS NULL", (user_id,)).fetchone()[0] >= MAX_REVIEWS:
                raise DataValidationError("影评数量已达到上限")
            internal_id = old["id"] if old else db.execute("INSERT INTO reviews(user_id,public_id) VALUES(?,?)", (user_id, review["id"])).lastrowid
            revision = (old["revision"] or 0) + 1 if old else 1
            vid = self._write_version(db, internal_id, review, revision, "create")
            db.execute("UPDATE reviews SET current_version_id=?,deleted_at=NULL WHERE id=?", (vid, internal_id))
            db.execute("INSERT INTO idempotency(user_id,request_key,review_id,payload_hash,created_at) VALUES(?,?,?,?,?)",
                       (user_id, key, internal_id, digest, int(time.time())))
            self._bump(db, user_id)
        return {"review": review, "revision": revision}

    def update_review(self, review_id, value, revision, user_id=1):
        review = self.validate_review(value)
        if review["id"] != review_id:
            raise DataValidationError("影评 ID 不一致")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._find(db, review_id, user_id)
            if not old or old["deleted_at"] is not None or old["revision"] != revision:
                raise ConflictError("其他设备已修改或删除这条影评，请查看最新内容后重新编辑")
            vid = self._write_version(db, old["id"], review, revision + 1, "update")
            db.execute("UPDATE reviews SET current_version_id=? WHERE id=?", (vid, old["id"]))
            self._bump(db, user_id)
        return {"review": review, "revision": revision + 1}

    def delete_review(self, review_id, revision, user_id=1, *, before_change: Callable[[], None] | None = None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._find(db, review_id, user_id)
            if not old or old["deleted_at"] is not None or old["revision"] != revision:
                raise ConflictError("其他设备已修改或删除这条影评")
            value = next(i["review"] for i in self.read_snapshot(db, user_id) if i["review"]["id"] == review_id)
            if before_change is not None:
                # No writes precede this callback. The reserved lock protects the
                # validated state while another connection takes its backup.
                before_change()
            vid = self._write_version(db, old["id"], value, revision + 1, "delete")
            db.execute("UPDATE reviews SET current_version_id=?,deleted_at=? WHERE id=?", (vid, int(time.time()), old["id"]))
            self._bump(db, user_id)

    def validate_archive_revision(self, archive_revision, user_id):
        with self.connect() as db:
            self._require_archive_revision(db, archive_revision, user_id)

    @staticmethod
    def _require_archive_revision(db, archive_revision, user_id):
        current = db.execute("SELECT revision FROM archive_state WHERE user_id=?", (user_id,)).fetchone()
        if not current or current[0] != archive_revision:
            raise ConflictError("档案已在其他设备改变，请刷新后重试批量操作")

    def replace_archive(self, values, archive_revision, user_id=1, *,
                        before_change: Callable[[], None] | None = None, uploaded_images=()):
        if not isinstance(values, list) or len(values) > MAX_REVIEWS:
            raise DataValidationError("影评数据格式无效或数量超出上限")
        reviews = [self.validate_review(value) for value in values]
        if len({v["id"] for v in reviews}) != len(reviews):
            raise DataValidationError("存在重复的影评 ID")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_archive_revision(db, archive_revision, user_id)
            if before_change is not None:
                before_change()
            for image in uploaded_images:
                self._register_upload(db, user_id, image)
            incoming = {i["id"] for i in reviews}
            now = int(time.time())
            for old in self.read_snapshot(db, user_id):
                if old["review"]["id"] not in incoming:
                    identity = self._find(db, old["review"]["id"], user_id)
                    vid = self._write_version(db, identity["id"], old["review"], old["revision"] + 1, "delete")
                    db.execute("UPDATE reviews SET current_version_id=?,deleted_at=? WHERE id=?", (vid, now, identity["id"]))
            db.execute("DELETE FROM idempotency WHERE user_id=?", (user_id,))
            result = []
            for review in reviews:
                old = self._find(db, review["id"], user_id)
                internal_id = old["id"] if old else db.execute("INSERT INTO reviews(user_id,public_id) VALUES(?,?)", (user_id, review["id"])).lastrowid
                revision = (old["revision"] or 0) + 1 if old else 1
                vid = self._write_version(db, internal_id, review, revision, "replace")
                db.execute("UPDATE reviews SET current_version_id=?,deleted_at=NULL WHERE id=?", (vid, internal_id))
                result.append({"review": review, "revision": revision})
            new_version = self._bump(db, user_id)
        return {"reviews": result, "archiveRevision": new_version}
