-- Schema v2: normalized business content, including historical versions.
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
 display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','user')),
 is_active INTEGER NOT NULL CHECK(is_active IN (0,1)),
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_state (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
 revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS movies (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, director TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 public_id TEXT NOT NULL, current_version_id INTEGER, deleted_at INTEGER,
 UNIQUE(user_id,public_id),
 FOREIGN KEY(id,current_version_id) REFERENCES review_versions(review_id,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS reviews_owner ON reviews(user_id,deleted_at);
CREATE TABLE IF NOT EXISTS review_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE RESTRICT,
 revision INTEGER NOT NULL CHECK(revision > 0), movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE RESTRICT,
 watched_date TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 0 AND 5), comment TEXT NOT NULL,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, changed_at INTEGER NOT NULL,
 operation TEXT NOT NULL CHECK(operation IN ('create','update','delete','replace','migration')),
 UNIQUE(review_id,id)
);
CREATE INDEX IF NOT EXISTS versions_review ON review_versions(review_id);
CREATE TABLE IF NOT EXISTS terms (
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK(kind IN ('tag','category')),
 name TEXT NOT NULL, UNIQUE(kind,name)
);
CREATE TABLE IF NOT EXISTS review_terms (
 version_id INTEGER NOT NULL REFERENCES review_versions(id) ON DELETE CASCADE,
 term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE RESTRICT, position INTEGER NOT NULL CHECK(position >= 0),
 PRIMARY KEY(version_id,term_id)
);
CREATE TABLE IF NOT EXISTS rewatches (
 version_id INTEGER NOT NULL REFERENCES review_versions(id) ON DELETE CASCADE, position INTEGER NOT NULL CHECK(position >= 0),
 watched_at TEXT NOT NULL, feeling TEXT NOT NULL, created_at INTEGER NOT NULL,
 rating INTEGER CHECK(rating BETWEEN 0 AND 5), PRIMARY KEY(version_id,position)
);
CREATE TABLE IF NOT EXISTS personal_context (
 version_id INTEGER PRIMARY KEY REFERENCES review_versions(id) ON DELETE CASCADE,
 location TEXT NOT NULL, companions TEXT NOT NULL, mood TEXT NOT NULL, life_stage TEXT NOT NULL,
 impact TEXT NOT NULL, favorite_scene TEXT NOT NULL, favorite_quote TEXT NOT NULL, recommend_to TEXT NOT NULL,
 watch_again INTEGER CHECK(watch_again IN (0,1))
);
CREATE TABLE IF NOT EXISTS media_assets (
 reference TEXT PRIMARY KEY, mime TEXT NOT NULL CHECK(mime IN ('image/jpeg','image/png','image/webp')),
 size INTEGER NOT NULL CHECK(size > 0)
);
CREATE TABLE IF NOT EXISTS review_images (
 version_id INTEGER PRIMARY KEY REFERENCES review_versions(id) ON DELETE CASCADE,
 reference TEXT NOT NULL REFERENCES media_assets(reference) ON DELETE RESTRICT, original_name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS review_images_reference ON review_images(reference);
CREATE TABLE IF NOT EXISTS user_uploads (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 reference TEXT NOT NULL REFERENCES media_assets(reference) ON DELETE RESTRICT, uploaded_at INTEGER NOT NULL,
 PRIMARY KEY(user_id,reference)
);
CREATE TABLE IF NOT EXISTS idempotency (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT, request_key TEXT NOT NULL,
 review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE RESTRICT,
 payload_hash TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(user_id,request_key)
);
CREATE TABLE IF NOT EXISTS login_failures (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS failures_source ON login_failures(source,at);
