"""Consistent SQLite and referenced-media server backup and isolated restore."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from app import DataValidationError, ReviewStore
from web.images import validate_image_stream
from web import MAX_ARCHIVE_IMAGE_PIXELS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def create_backup(data_dir: Path, destination: Path) -> dict:
    data_dir = Path(data_dir).resolve()
    destination = Path(destination).resolve()
    db_path = data_dir / "db" / "app.sqlite3"
    if not db_path.is_file():
        raise DataValidationError("网站数据库不存在")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        copy_path = Path(temporary) / "app.sqlite3"
        with closing(sqlite3.connect(db_path)) as live, closing(sqlite3.connect(copy_path)) as snapshot:
            live.backup(snapshot)
        media_store = ReviewStore(data_dir)
        with closing(sqlite3.connect(copy_path)) as snapshot:
            snapshot.row_factory = sqlite3.Row
            schema_version = snapshot.execute("PRAGMA user_version").fetchone()[0]
            if schema_version == 2:
                record_count = snapshot.execute("SELECT COUNT(*) FROM reviews WHERE deleted_at IS NULL").fetchone()[0]
                referenced = {row["reference"]: {"path": row["reference"], "name": row["original_name"], "mime": row["mime"], "size": row["size"]}
                              for row in snapshot.execute("SELECT i.reference,i.original_name,a.mime,a.size FROM review_images i JOIN media_assets a ON a.reference=i.reference")}
                for asset in snapshot.execute("SELECT DISTINCT a.* FROM media_assets a JOIN user_uploads u ON u.reference=a.reference"):
                    referenced.setdefault(asset["reference"], {"path": asset["reference"], "name": asset["reference"].split("/")[-1],
                                                               "mime": asset["mime"], "size": asset["size"]})
                archive_revisions = {str(row["user_id"]): row["revision"] for row in snapshot.execute("SELECT * FROM archive_state")}
                archive_revision = archive_revisions.get("1", 0)
            else:
                rows = snapshot.execute("SELECT payload FROM reviews ORDER BY rowid").fetchall()
                reviews = [json.loads(row[0]) for row in rows]
                record_count = len(reviews)
                referenced = {review["image"]["path"]: review["image"] for review in reviews if review.get("image")}
                archive_revision = snapshot.execute("SELECT value FROM meta WHERE key='archive_revision'").fetchone()[0]
                archive_revisions = {"1": archive_revision}
        paths = {}
        for reference, image in referenced.items():
            paths[reference] = media_store.verify_image(image)
        manifest = {"format": "movie-review-server-backup", "version": 1,
                    "recordCount": record_count, "archiveRevision": archive_revision,
                    "archiveRevisions": archive_revisions, "schemaVersion": schema_version,
                    "databaseSha256": _sha256(copy_path),
                    "mediaSha256": {reference: _sha256(path) for reference, path in paths.items()}}
        descriptor, name = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
        os.close(descriptor)
        temporary_zip = Path(name)
        try:
            with zipfile.ZipFile(temporary_zip, "w", allowZip64=True) as archive:
                archive.write(copy_path, "db/app.sqlite3", compress_type=zipfile.ZIP_DEFLATED)
                for reference, path in sorted(paths.items()):
                    archive.write(path, reference, compress_type=zipfile.ZIP_STORED)
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2),
                                 compress_type=zipfile.ZIP_DEFLATED)
            os.replace(temporary_zip, destination)
        finally:
            temporary_zip.unlink(missing_ok=True)
    return manifest


def verify_backup(archive_path: Path) -> dict:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        if len(infos) != len(names) or len(infos) > 100_002 or sum(info.file_size for info in infos) > 5 * 1024**3:
            raise DataValidationError("服务器备份文件数量或大小异常")
        if "manifest.json" not in names or "db/app.sqlite3" not in names:
            raise DataValidationError("服务器备份缺少数据库或清单")
        if archive.getinfo("manifest.json").file_size > 1024 * 1024:
            raise DataValidationError("服务器备份清单过大")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("format") != "movie-review-server-backup" or manifest.get("version") != 1:
            raise DataValidationError("服务器备份格式不支持")
        db_digest = manifest.get("databaseSha256")
        if not isinstance(manifest.get("mediaSha256"), dict) or not isinstance(db_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", db_digest):
            raise DataValidationError("服务器备份清单无效")
        if any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in manifest["mediaSha256"].values()):
            raise DataValidationError("服务器备份媒体哈希无效")
        media_store = ReviewStore(Path("."))
        for reference in manifest["mediaSha256"]:
            # Every manifest key must be a standard content-addressed media path.
            media_store.resolve_image_path(reference)
            if Path(reference).stem != manifest["mediaSha256"][reference]:
                raise DataValidationError("服务器备份媒体名称与哈希不一致")
        expected = {"manifest.json", "db/app.sqlite3", *manifest["mediaSha256"].keys()}
        if names != expected:
            raise DataValidationError("服务器备份文件清单不一致")
        for name, digest in {"db/app.sqlite3": manifest["databaseSha256"], **manifest["mediaSha256"]}.items():
            if name.startswith("media/"):
                ReviewStore(Path(".")).resolve_image_path(name)
            actual = hashlib.sha256()
            with archive.open(name) as stream:
                while block := stream.read(1024 * 1024):
                    actual.update(block)
            if actual.hexdigest() != digest:
                raise DataValidationError(f"备份哈希不一致：{name}")
            if name != "db/app.sqlite3":
                with archive.open(name) as stream:
                    expected_mime = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[Path(name).suffix]
                    validate_image_stream(stream, max_pixels=MAX_ARCHIVE_IMAGE_PIXELS, expected_mime=expected_mime)
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "check.sqlite3"
            with archive.open("db/app.sqlite3") as source, db_path.open("wb") as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
            with closing(sqlite3.connect(db_path)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise DataValidationError("备份数据库完整性检查失败")
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise DataValidationError("备份数据库外键检查失败")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                count = db.execute("SELECT COUNT(*) FROM reviews" + (" WHERE deleted_at IS NULL" if version == 2 else "")).fetchone()[0]
                if count != manifest["recordCount"]:
                    raise DataValidationError("备份影评数量不一致")
                if version == 2:
                    references = {row[0] for row in db.execute("SELECT reference FROM review_images UNION SELECT reference FROM user_uploads")}
                else:
                    references = {image["path"] for row in db.execute("SELECT payload FROM reviews")
                                  if (image := json.loads(row[0]).get("image"))}
                if references != set(manifest["mediaSha256"]):
                    raise DataValidationError("备份媒体清单与数据库引用不一致")
    return manifest


def restore_backup(archive_path: Path, target: Path) -> dict:
    manifest = verify_backup(archive_path)
    requested_target = Path(target).absolute()
    for component in (requested_target, *requested_target.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise DataValidationError("恢复目标不能包含符号链接")
    target = requested_target.resolve()
    if target.exists() and any(target.iterdir()):
        raise DataValidationError("恢复目标必须是空目录")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for name in ("db/app.sqlite3", *manifest["mediaSha256"].keys()):
            destination = target / name
            if not destination.resolve().is_relative_to(target) or destination.is_symlink():
                raise DataValidationError("恢复文件路径越过目标目录")
            destination.parent.mkdir(parents=True, exist_ok=True)
            for component in (destination.parent, *destination.parent.parents):
                if component == target:
                    break
                if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
                    raise DataValidationError("恢复文件路径不能包含符号链接")
            with archive.open(name) as source, destination.open("xb") as output:
                while block := source.read(1024 * 1024):
                    output.write(block)
    with closing(sqlite3.connect(target / "db" / "app.sqlite3")) as db:
        with db:
            db.execute("DELETE FROM sessions")
    return manifest
