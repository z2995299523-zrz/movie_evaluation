"""Offline account, preflight, and one-way migration commands.

Run with ``python -m web.manage``. These commands never modify the desktop source.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import time
import uuid
from pathlib import Path

from app import DATA_FILE_NAME, DataValidationError, ReviewStore, normalize_reviews
from web.storage import ArchiveDatabase
from web.backup import create_backup, restore_backup, verify_backup
from web.images import validate_historical_image


def preflight(source: Path) -> dict:
    source = source.resolve()
    data_file = source / DATA_FILE_NAME
    raw_bytes = data_file.read_bytes()
    raw = json.loads(raw_bytes.decode("utf-8-sig"))
    if not isinstance(raw, list):
        raise DataValidationError("源数据必须是影评数组")
    normalized = normalize_reviews(raw)
    if normalized != raw:
        differences = [index for index, (before, after) in enumerate(zip(raw, normalized), 1) if before != after]
        raise DataValidationError(f"源数据会被修整；涉及记录序号：{differences[:20]}")
    store = ReviewStore(source)
    media = {}
    for review in raw:
        image = review.get("image")
        if image and image["path"] not in media:
            path = store.verify_image(image)
            validate_historical_image(path, expected_mime=image["mime"])
            media[image["path"]] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
    return {"source": str(source), "jsonSha256": hashlib.sha256(raw_bytes).hexdigest(),
            "recordCount": len(raw), "referencedMediaCount": len(media), "media": media}


def migrate(source: Path, target: Path) -> dict:
    report = preflight(source)
    raw = json.loads((source / DATA_FILE_NAME).read_text(encoding="utf-8-sig"))
    target = target.resolve()
    db_path = target / "db" / "app.sqlite3"
    if db_path.exists():
        raise DataValidationError("目标数据库已存在；迁移只允许导入全新的空目录")
    if target.exists() and any(target.iterdir()):
        raise DataValidationError("目标目录非空；请使用隔离的新目录")
    target.mkdir(parents=True, exist_ok=True)
    dest_store = ReviewStore(target)
    source_store = ReviewStore(source)
    for review in raw:
        image = review.get("image")
        if image:
            path = source_store.verify_image(image)
            with path.open("rb") as stream:
                installed = dest_store.import_image_stream(stream, image["size"], image["name"])
            if installed != image:
                raise DataValidationError("剧照迁移校验不一致")
    db = ArchiveDatabase(db_path)
    db.replace_archive(raw, 0)
    snapshot = db.list_reviews()
    restored = [item["review"] for item in snapshot["reviews"]]
    if restored != raw:
        raise DataValidationError("迁移后业务字段不一致")
    report["target"] = str(target)
    report["archiveRevision"] = snapshot["archiveRevision"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="影评网站离线管理")
    parser.add_argument("--data-dir", type=Path, help="网站数据目录")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("init-account", "reset-password"):
        action = actions.add_parser(name)
        action.add_argument("--username", required=True)
    action = actions.add_parser("preflight")
    action.add_argument("source", type=Path)
    action = actions.add_parser("migrate")
    action.add_argument("source", type=Path)
    action.add_argument("target", type=Path)
    action = actions.add_parser("backup")
    action.add_argument("destination", type=Path)
    action = actions.add_parser("backup-daily")
    action.add_argument("destination_dir", type=Path)
    action = actions.add_parser("verify-backup")
    action.add_argument("archive", type=Path)
    action = actions.add_parser("restore-backup")
    action.add_argument("archive", type=Path)
    action.add_argument("target", type=Path)
    actions.add_parser("upgrade-db", help="备份并迁移旧网站数据库，检查三范式表结构")
    args = parser.parse_args()
    if args.action in ("init-account", "reset-password"):
        if not args.data_dir:
            parser.error("账户操作必须指定 --data-dir")
        password = getpass.getpass("新密码：")
        confirmation = getpass.getpass("再次输入：")
        if password != confirmation:
            parser.error("两次密码不同")
        ArchiveDatabase(args.data_dir / "db" / "app.sqlite3").set_account(
            args.username, password, reset=args.action == "reset-password")
        print("账户已设置；旧会话已失效")
    elif args.action == "upgrade-db":
        if not args.data_dir:
            parser.error("数据库升级必须指定 --data-dir")
        database = ArchiveDatabase(args.data_dir / "db" / "app.sqlite3")
        with database.connect() as connection:
            report = {"schemaVersion": connection.execute("PRAGMA user_version").fetchone()[0],
                      "recordCount": connection.execute("SELECT COUNT(*) FROM reviews WHERE deleted_at IS NULL").fetchone()[0],
                      "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
                      "foreignKeyViolations": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
                      "migrationBackup": str(database.migration_backup) if database.migration_backup else None}
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.action == "preflight":
        print(json.dumps(preflight(args.source), ensure_ascii=False, indent=2))
    elif args.action == "migrate":
        print(json.dumps(migrate(args.source, args.target), ensure_ascii=False, indent=2))
    elif args.action == "backup":
        if not args.data_dir:
            parser.error("备份必须指定 --data-dir")
        print(json.dumps(create_backup(args.data_dir, args.destination), ensure_ascii=False, indent=2))
    elif args.action == "backup-daily":
        if not args.data_dir:
            parser.error("备份必须指定 --data-dir")
        destination = args.destination_dir / f"movie-review-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}.zip"
        result = create_backup(args.data_dir, destination)
        verify_backup(destination)
        print(json.dumps({"path": str(destination), "recordCount": result["recordCount"],
                          "mediaCount": len(result["mediaSha256"]), "verified": True}, ensure_ascii=False))
    elif args.action == "verify-backup":
        print(json.dumps(verify_backup(args.archive), ensure_ascii=False, indent=2))
    elif args.action == "restore-backup":
        print(json.dumps(restore_backup(args.archive, args.target), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
