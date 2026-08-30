from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import posixpath
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any


APP_NAME = "MovieReview"
DATA_FILE_NAME = "movie-reviews.json"
MAX_IMPORT_BYTES = 50 * 1024 * 1024
MAX_REVIEWS = 100_000
MAX_REWATCHES_PER_REVIEW = 10_000
MAX_BACKUPS = 20
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_IMAGE_CHUNK_BYTES = 1024 * 1024
MAX_BACKUP_UNCOMPRESSED_BYTES = 5 * 1024 * 1024 * 1024
MAX_BACKUP_ENTRIES = MAX_REVIEWS + 2
BACKUP_FORMAT = "movie-review-backup"
BACKUP_FORMAT_VERSION = 1
IMAGE_PATH_RE = re.compile(r"media/([0-9a-f]{64})\.(jpg|png|webp)")
REWATCH_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$")
IMAGE_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class DataValidationError(ValueError):
    """Raised when a review JSON document does not match the supported schema."""


def _clean_text(value: Any, field: str, max_length: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise DataValidationError(f"{field} 必须是文本")
    value = value.strip()
    if required and not value:
        raise DataValidationError(f"{field} 不能为空")
    if len(value) > max_length:
        raise DataValidationError(f"{field} 不能超过 {max_length} 个字符")
    return value


def _clean_timestamp(value: Any, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    timestamp = int(value)
    return timestamp if 0 <= timestamp <= 32_503_680_000_000 else fallback


def _detect_image_type(header: bytes) -> tuple[str, str] | None:
    if len(header) >= 3 and header.startswith(b"\xff\xd8\xff"):
        return "jpg", IMAGE_TYPES["jpg"]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", IMAGE_TYPES["png"]
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp", IMAGE_TYPES["webp"]
    return None


def _stream_sha256(stream: Any) -> str:
    """Hash an open binary stream on every supported Python 3.10+ version."""
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def _normalize_image(value: Any, index: int) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DataValidationError(f"第 {index} 条的剧照信息格式不正确")

    path = value.get("path")
    if not isinstance(path, str) or IMAGE_PATH_RE.fullmatch(path) is None:
        raise DataValidationError(f"第 {index} 条的剧照路径不安全")
    match = IMAGE_PATH_RE.fullmatch(path)
    assert match is not None
    extension = match.group(2)

    name = _clean_text(value.get("name"), f"第 {index} 条的剧照文件名", 255, required=True)
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise DataValidationError(f"第 {index} 条的剧照文件名不安全")

    mime = value.get("mime")
    if mime != IMAGE_TYPES[extension]:
        raise DataValidationError(f"第 {index} 条的剧照类型与路径不一致")

    size = value.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_IMAGE_BYTES:
        raise DataValidationError(f"第 {index} 条的剧照大小不正确")

    return {"path": path, "name": name, "mime": mime, "size": size}


def _normalize_rewatches(value: Any, index: int, now_ms: int) -> list[dict[str, Any]]:
    """Validate repeat-viewing notes while preserving old review files without them."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_REWATCHES_PER_REVIEW:
        raise DataValidationError(f"第 {index} 条的重复观看记录格式不正确")

    normalized: list[dict[str, Any]] = []
    for rewatch_index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise DataValidationError(f"第 {index} 条的第 {rewatch_index} 次观看记录不是对象")

        raw_watched_at = item.get("watchedAt")
        if not isinstance(raw_watched_at, str) or REWATCH_TIMESTAMP_RE.fullmatch(raw_watched_at) is None:
            raise DataValidationError(f"第 {index} 条的第 {rewatch_index} 次观影时间格式不正确")
        try:
            watched_at = datetime.fromisoformat(raw_watched_at)
        except ValueError as exc:
            raise DataValidationError(f"第 {index} 条的第 {rewatch_index} 次观影时间格式不正确") from exc
        if watched_at.tzinfo is not None:
            raise DataValidationError(f"第 {index} 条的第 {rewatch_index} 次观影时间格式不正确")

        feeling = _clean_text(item.get("feeling"), f"第 {index} 条的第 {rewatch_index} 次观影感受", 100_000)
        created_at = _clean_timestamp(item.get("createdAt"), now_ms)
        normalized_item = {
            "watchedAt": watched_at.replace(second=0, microsecond=0).isoformat(timespec="minutes"),
            "feeling": feeling,
            "createdAt": created_at,
        }
        raw_rating = item.get("rating")
        if raw_rating is not None:
            if isinstance(raw_rating, bool) or not isinstance(raw_rating, (int, float)) or int(raw_rating) != raw_rating:
                raise DataValidationError(f"第 {index} 条的第 {rewatch_index} 次评分必须是 0 到 5 的整数")
            rating = int(raw_rating)
            if not 0 <= rating <= 5:
                raise DataValidationError(f"第 {index} 条的第 {rewatch_index} 次评分必须是 0 到 5 的整数")
            normalized_item["rating"] = rating
        normalized.append(normalized_item)
    return normalized


def _normalize_personal_context(value: Any, index: int) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DataValidationError(f"第 {index} 条的电影与我的关系格式不正确")
    limits = {"location": 200, "companions": 300, "mood": 200, "lifeStage": 300, "impact": 5_000, "favoriteScene": 5_000, "favoriteQuote": 2_000, "recommendTo": 500}
    normalized = {key: _clean_text(value.get(key), f"第 {index} 条的{key}", limit) for key, limit in limits.items()}
    watch_again = value.get("watchAgain")
    if watch_again is not None and not isinstance(watch_again, bool):
        raise DataValidationError(f"第 {index} 条的再次重看意愿格式不正确")
    if isinstance(watch_again, bool):
        normalized["watchAgain"] = watch_again
    return normalized if any(item not in ("", None) for item in normalized.values()) else None


def normalize_reviews(value: Any) -> list[dict[str, Any]]:
    """Validate and normalize imported or UI-provided review records."""
    if not isinstance(value, list):
        raise DataValidationError("影评数据的最外层必须是数组")
    if len(value) > MAX_REVIEWS:
        raise DataValidationError(f"影评数量不能超过 {MAX_REVIEWS} 条")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    now_ms = int(time.time() * 1000)

    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise DataValidationError(f"第 {index} 条影评不是对象")

        title = _clean_text(item.get("title"), f"第 {index} 条的电影名称", 300, required=True)
        director = _clean_text(item.get("director"), f"第 {index} 条的导演", 200)
        comment = _clean_text(item.get("comment"), f"第 {index} 条的评论", 100_000)

        raw_date = item.get("date")
        if not isinstance(raw_date, str):
            raise DataValidationError(f"第 {index} 条的观影日期格式不正确")
        try:
            watched_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise DataValidationError(f"第 {index} 条的观影日期格式不正确") from exc

        rating = item.get("rating", 0)
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or int(rating) != rating:
            raise DataValidationError(f"第 {index} 条的评分必须是 0 到 5 的整数")
        rating = int(rating)
        if not 0 <= rating <= 5:
            raise DataValidationError(f"第 {index} 条的评分必须是 0 到 5 的整数")

        raw_tags = item.get("tags", [])
        if raw_tags is None:
            raw_tags = []
        if not isinstance(raw_tags, list) or len(raw_tags) > 50:
            raise DataValidationError(f"第 {index} 条的标签格式不正确")
        tags: list[str] = []
        for raw_tag in raw_tags:
            tag = _clean_text(raw_tag, f"第 {index} 条的标签", 100)
            if tag and tag not in tags:
                tags.append(tag)

        raw_categories = item.get("categories", [])
        if raw_categories is None:
            raw_categories = []
        if not isinstance(raw_categories, list) or len(raw_categories) > 8:
            raise DataValidationError(f"第 {index} 条的电影分类格式不正确")
        categories: list[str] = []
        for raw_category in raw_categories:
            category = _clean_text(raw_category, f"第 {index} 条的电影分类", 40)
            if category and category not in categories:
                categories.append(category)

        review_id = item.get("id")
        if not isinstance(review_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", review_id):
            review_id = uuid.uuid4().hex
        while review_id in seen_ids:
            review_id = uuid.uuid4().hex
        seen_ids.add(review_id)

        created_at = _clean_timestamp(item.get("createdAt"), now_ms)
        updated_at = _clean_timestamp(item.get("updatedAt"), created_at)
        review = {
            "id": review_id,
            "title": title,
            "director": director,
            "date": watched_date.isoformat(),
            "rating": rating,
            "tags": tags,
            "comment": comment,
            "createdAt": created_at,
            "updatedAt": updated_at,
        }
        if categories:
            review["categories"] = categories
        personal_context = _normalize_personal_context(item.get("personalContext"), index)
        if personal_context:
            review["personalContext"] = personal_context
        rewatched = _normalize_rewatches(item.get("rewatches"), index, now_ms)
        if rewatched:
            review["rewatches"] = rewatched
        image = _normalize_image(item.get("image"), index)
        if image is not None:
            review["image"] = image
        normalized.append(review)

    return normalized


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def atomic_write(path: Path, content: bytes) -> None:
    """Replace a file atomically by writing a sibling temporary file first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _validate_zip_entry_name(name: str) -> None:
    if not name or "\\" in name or name.startswith("/"):
        raise DataValidationError("备份包包含不安全的文件路径")
    normalized = posixpath.normpath(name)
    if normalized != name or normalized.startswith("../") or normalized == ".." or ":" in name:
        raise DataValidationError("备份包包含路径穿越或绝对路径")


def _dropped_image_path(event: Any) -> Path:
    """Return the single native file path carried by a pywebview drop event."""
    unsupported = "暂不支持从此程序拖入图片，请先保存到本地再选择"
    if not isinstance(event, dict):
        raise DataValidationError(unsupported)
    transfer = event.get("dataTransfer")
    files = transfer.get("files") if isinstance(transfer, dict) else None
    if not isinstance(files, list) or not files:
        raise DataValidationError(unsupported)
    if len(files) != 1:
        raise DataValidationError("每条影评只能上传一张主剧照")
    file_info = files[0]
    path_value = file_info.get("pywebviewFullPath") if isinstance(file_info, dict) else None
    if not isinstance(path_value, str) or not path_value.strip():
        raise DataValidationError(unsupported)
    source = Path(path_value)
    if not source.is_absolute():
        raise DataValidationError(unsupported)
    return source


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            app_data = os.environ.get("APPDATA")
            root = Path(app_data) if app_data else Path.home() / ".config"
            path = root / APP_NAME / "settings.json"
        self.path = path

    def load_data_directory(self) -> Path | None:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        value = document.get("dataDirectory") if isinstance(document, dict) else None
        return Path(value) if isinstance(value, str) and value else None

    def save_data_directory(self, directory: Path) -> None:
        payload = {"dataDirectory": str(directory.resolve()), "updatedAt": datetime.now().isoformat(timespec="seconds")}
        atomic_write(self.path, _json_bytes(payload))


class ReviewStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.data_path = self.directory / DATA_FILE_NAME
        self.backup_directory = self.directory / "backups"
        self.media_directory = self.directory / "media"
        self._lock = threading.RLock()

    def resolve_image_path(self, reference: str) -> Path:
        if not isinstance(reference, str) or IMAGE_PATH_RE.fullmatch(reference) is None:
            raise DataValidationError("剧照路径不安全")
        candidate = (self.directory / Path(reference)).resolve()
        media_root = self.media_directory.resolve()
        try:
            candidate.relative_to(media_root)
        except ValueError as exc:
            raise DataValidationError("剧照路径越过了媒体目录") from exc
        return candidate

    def import_image(self, source: Path, original_name: str | None = None) -> dict[str, Any]:
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise DataValidationError(f"无法读取剧照文件：{exc}") from exc
        if size == 0:
            raise DataValidationError("剧照文件为空")
        if size > MAX_IMAGE_BYTES:
            raise DataValidationError("剧照不能超过 50 MB")

        try:
            with source.open("rb") as stream:
                detected = _detect_image_type(stream.read(16))
                suffix = source.suffix.lower()
                allowed_suffixes = {"jpg": {".jpg", ".jpeg"}, "png": {".png"}, "webp": {".webp"}}
                if detected is None:
                    raise DataValidationError("仅支持 JPG、PNG 和 WebP 剧照")
                if suffix not in allowed_suffixes[detected[0]]:
                    raise DataValidationError("剧照扩展名与实际文件内容不一致")
                stream.seek(0)
                return self.import_image_stream(stream, size, original_name or source.name)
        except OSError as exc:
            raise DataValidationError(f"无法读取剧照文件：{exc}") from exc

    def import_dropped_image(self, source: Path) -> dict[str, Any]:
        """Import a real local path obtained from pywebview's native drop event."""
        try:
            if source.is_dir():
                raise DataValidationError("请拖入一张图片文件，不支持文件夹")
            if source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise DataValidationError("仅支持 JPG、PNG 和 WebP 剧照")
            return self.import_image(source)
        except DataValidationError as exc:
            if str(exc).startswith("无法读取剧照文件"):
                raise DataValidationError("无法读取拖入的剧照文件") from exc
            raise
        except OSError as exc:
            raise DataValidationError("无法读取拖入的剧照文件") from exc

    def import_image_stream(self, stream: Any, expected_size: int, original_name: str) -> dict[str, Any]:
        if not 0 < expected_size <= MAX_IMAGE_BYTES:
            raise DataValidationError("剧照必须大于 0 字节且不能超过 50 MB")
        safe_name = _clean_text(original_name, "剧照文件名", 255, required=True)
        if "/" in safe_name or "\\" in safe_name or safe_name in {".", ".."}:
            raise DataValidationError("剧照文件名不安全")

        self.media_directory.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=".image-", suffix=".tmp", dir=self.media_directory)
        temp_path = Path(temp_name)
        digest = hashlib.sha256()
        total = 0
        header = b""
        try:
            with os.fdopen(descriptor, "wb") as target:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise DataValidationError("剧照读取结果不是二进制数据")
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise DataValidationError("剧照不能超过 50 MB")
                    if len(header) < 16:
                        header += chunk[: 16 - len(header)]
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())

            if total != expected_size:
                raise DataValidationError("剧照大小在读取过程中发生变化")
            detected = _detect_image_type(header)
            if detected is None:
                raise DataValidationError("仅支持 JPG、PNG 和 WebP 剧照")
            extension, mime = detected
            reference = f"media/{digest.hexdigest()}.{extension}"
            destination = self.resolve_image_path(reference)
            if destination.exists():
                with destination.open("rb") as existing_stream:
                    existing_digest = _stream_sha256(existing_stream)
                if destination.stat().st_size != total or existing_digest != digest.hexdigest():
                    raise DataValidationError("媒体目录中存在哈希相同但大小异常的文件")
                temp_path.unlink(missing_ok=True)
            else:
                os.replace(temp_path, destination)
            return {"path": reference, "name": safe_name, "mime": mime, "size": total}
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def verify_image(self, image: dict[str, Any]) -> Path:
        normalized = _normalize_image(image, 1)
        assert normalized is not None
        path = self.resolve_image_path(normalized["path"])
        try:
            stat_size = path.stat().st_size
            with path.open("rb") as stream:
                header = stream.read(16)
                detected = _detect_image_type(header)
                stream.seek(0)
                digest = _stream_sha256(stream)
        except OSError as exc:
            raise DataValidationError(f"剧照文件缺失或无法读取：{normalized['name']}") from exc
        if stat_size != normalized["size"] or stat_size > MAX_IMAGE_BYTES:
            raise DataValidationError(f"剧照大小与记录不一致：{normalized['name']}")
        match = IMAGE_PATH_RE.fullmatch(normalized["path"])
        assert match is not None
        if detected is None or detected[1] != normalized["mime"] or digest != match.group(1):
            raise DataValidationError(f"剧照内容与记录不一致：{normalized['name']}")
        return path

    def get_image_info(self, reference: str) -> dict[str, Any]:
        path = self.resolve_image_path(reference)
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                detected = _detect_image_type(stream.read(16))
        except OSError as exc:
            raise DataValidationError("剧照文件缺失或无法读取") from exc
        if not 0 < size <= MAX_IMAGE_BYTES or detected is None:
            raise DataValidationError("剧照文件无效")
        match = IMAGE_PATH_RE.fullmatch(reference)
        assert match is not None
        if detected[0] != match.group(2):
            raise DataValidationError("剧照内容与路径类型不一致")
        return {"path": reference, "size": size, "mime": detected[1], "chunkSize": MAX_IMAGE_CHUNK_BYTES}

    def read_image_chunk(self, reference: str, offset: int, length: int) -> dict[str, Any]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise DataValidationError("剧照分块偏移量无效")
        if isinstance(length, bool) or not isinstance(length, int) or not 0 < length <= MAX_IMAGE_CHUNK_BYTES:
            raise DataValidationError("剧照分块长度无效")
        info = self.get_image_info(reference)
        if offset > info["size"]:
            raise DataValidationError("剧照分块偏移量超出文件范围")
        path = self.resolve_image_path(reference)
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read(min(length, info["size"] - offset))
        return {
            "path": reference,
            "offset": offset,
            "nextOffset": offset + len(chunk),
            "size": info["size"],
            "data": base64.b64encode(chunk).decode("ascii"),
            "done": offset + len(chunk) >= info["size"],
        }

    def prepare(self) -> None:
        if not self.directory.exists() or not self.directory.is_dir():
            raise OSError("数据目录不存在，请重新选择")
        descriptor, temp_name = tempfile.mkstemp(prefix=".movie-review-write-test-", dir=self.directory)
        os.close(descriptor)
        Path(temp_name).unlink(missing_ok=True)
        if not self.data_path.exists():
            atomic_write(self.data_path, _json_bytes([]))

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            self.prepare()
            if self.data_path.stat().st_size > MAX_IMPORT_BYTES:
                raise DataValidationError("数据文件超过 50 MB，已拒绝加载")
            try:
                value = json.loads(self.data_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                raise DataValidationError(f"数据文件不是有效的 JSON（第 {exc.lineno} 行）") from exc
            return normalize_reviews(value)

    def save(self, reviews: Any) -> list[dict[str, Any]]:
        normalized = normalize_reviews(reviews)
        content = _json_bytes(normalized)
        with self._lock:
            self.prepare()
            current = self.data_path.read_bytes()
            if current == content:
                return normalized
            self._backup_current()
            atomic_write(self.data_path, content)
            self._prune_backups()
        return normalized

    def export_backup(self, destination: Path, reviews: Any) -> list[dict[str, Any]]:
        normalized = normalize_reviews(reviews)
        referenced_images: dict[str, Path] = {}
        for review in normalized:
            image = review.get("image")
            if image is not None and image["path"] not in referenced_images:
                referenced_images[image["path"]] = self.verify_image(image)

        manifest = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_FORMAT_VERSION,
            "exportedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "recordCount": len(normalized),
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        os.close(descriptor)
        temp_path = Path(temp_name)
        try:
            with zipfile.ZipFile(temp_path, "w", allowZip64=True) as archive:
                archive.writestr(
                    "manifest.json",
                    _json_bytes(manifest),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                )
                archive.writestr(
                    DATA_FILE_NAME,
                    _json_bytes(normalized),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                )
                for reference, path in sorted(referenced_images.items()):
                    archive.write(path, reference, compress_type=zipfile.ZIP_STORED)
            os.replace(temp_path, destination)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return normalized

    def prepare_json_import(self, source: Path) -> dict[str, Any]:
        try:
            if source.stat().st_size > MAX_IMPORT_BYTES:
                raise DataValidationError("JSON 文件超过 50 MB")
            value = json.loads(source.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise DataValidationError(f"JSON 格式错误（第 {exc.lineno} 行）") from exc
        except OSError as exc:
            raise DataValidationError(f"无法读取导入文件：{exc}") from exc
        reviews = normalize_reviews(value)
        image_sources: dict[str, Path] = {}
        for review in reviews:
            image = review.get("image")
            if image is None or image["path"] in image_sources:
                continue
            candidate = (source.parent / Path(image["path"])).resolve()
            try:
                candidate.relative_to((source.parent / "media").resolve())
            except ValueError as exc:
                raise DataValidationError("导入 JSON 的剧照路径越过了同级 media 目录") from exc
            self._verify_external_image(candidate, image)
            image_sources[image["path"]] = candidate
        return {"kind": "json", "source": str(source.resolve()), "reviews": reviews, "imageSources": image_sources}

    def prepare_zip_import(self, source: Path) -> dict[str, Any]:
        try:
            archive = zipfile.ZipFile(source, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise DataValidationError("所选文件不是有效的 ZIP 完整备份") from exc

        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_BACKUP_ENTRIES:
                raise DataValidationError("备份包文件数量过多")
            names: set[str] = set()
            total_size = 0
            info_by_name: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                _validate_zip_entry_name(info.filename)
                if info.filename in names:
                    raise DataValidationError("备份包包含重复文件名")
                names.add(info.filename)
                info_by_name[info.filename] = info
                if info.flag_bits & 0x1:
                    raise DataValidationError("不支持加密 ZIP 备份")
                if info.is_dir():
                    raise DataValidationError("备份包不应包含空目录项")
                total_size += info.file_size
                if total_size > MAX_BACKUP_UNCOMPRESSED_BYTES:
                    raise DataValidationError("备份包解压后超过 5 GB")
                if info.filename.startswith("media/") and info.file_size > MAX_IMAGE_BYTES:
                    raise DataValidationError("备份包中存在超过 50 MB 的剧照")

            required = {"manifest.json", DATA_FILE_NAME}
            if not required.issubset(names):
                raise DataValidationError("备份包缺少 manifest.json 或 movie-reviews.json")
            if info_by_name["manifest.json"].file_size > 1024 * 1024:
                raise DataValidationError("备份清单异常过大")
            if info_by_name[DATA_FILE_NAME].file_size > MAX_IMPORT_BYTES:
                raise DataValidationError("备份中的影评 JSON 超过 50 MB")

            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
                raw_reviews = json.loads(archive.read(DATA_FILE_NAME).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DataValidationError("备份清单或影评 JSON 格式无效") from exc
            version = manifest.get("version") if isinstance(manifest, dict) else None
            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != BACKUP_FORMAT
                or isinstance(version, bool)
                or version != BACKUP_FORMAT_VERSION
            ):
                raise DataValidationError("不支持此备份格式或版本")

            reviews = normalize_reviews(raw_reviews)
            record_count = manifest.get("recordCount")
            if isinstance(record_count, bool) or not isinstance(record_count, int) or record_count != len(reviews):
                raise DataValidationError("备份清单中的记录数量不一致")

            referenced: dict[str, dict[str, Any]] = {}
            for review in reviews:
                image = review.get("image")
                if image is not None:
                    referenced[image["path"]] = image
            media_entries = {name for name in names if name.startswith("media/")}
            unexpected = names - required - media_entries
            if unexpected:
                raise DataValidationError("备份包包含不支持的额外文件")
            if media_entries != set(referenced):
                raise DataValidationError("备份中的剧照与影评引用不一致")

            for reference, image in referenced.items():
                info = info_by_name[reference]
                if info.file_size != image["size"]:
                    raise DataValidationError(f"备份中的剧照大小不一致：{image['name']}")
                with archive.open(info, "r") as stream:
                    self._verify_stream_image(stream, info.file_size, image)

        return {"kind": "zip", "source": str(source.resolve()), "reviews": reviews}

    def apply_import(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        source = Path(plan["source"])
        if plan["kind"] == "json":
            fresh = self.prepare_json_import(source)
            reviews = fresh["reviews"]
            for review in reviews:
                image = review.get("image")
                if image is None:
                    continue
                installed = self.import_image(fresh["imageSources"][image["path"]], image["name"])
                if installed != image:
                    raise DataValidationError(f"导入后的剧照校验不一致：{image['name']}")
        elif plan["kind"] == "zip":
            fresh = self.prepare_zip_import(source)
            reviews = fresh["reviews"]
            image_by_path = {review["image"]["path"]: review["image"] for review in reviews if review.get("image")}
            with zipfile.ZipFile(source, "r") as archive:
                for reference, image in image_by_path.items():
                    with archive.open(reference, "r") as stream:
                        installed = self.import_image_stream(stream, image["size"], image["name"])
                    if installed != image:
                        raise DataValidationError(f"导入后的剧照校验不一致：{image['name']}")
        else:
            raise DataValidationError("未知的导入计划")
        return self.save(reviews)

    def _verify_external_image(self, path: Path, image: dict[str, Any]) -> None:
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                self._verify_stream_image(stream, size, image)
        except OSError as exc:
            raise DataValidationError(f"导入数据缺少剧照：{image['name']}") from exc

    def _verify_stream_image(self, stream: Any, size: int, image: dict[str, Any]) -> None:
        if size != image["size"] or not 0 < size <= MAX_IMAGE_BYTES:
            raise DataValidationError(f"剧照大小与记录不一致：{image['name']}")
        digest = hashlib.sha256()
        total = 0
        header = b""
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise DataValidationError(f"剧照超过 50 MB：{image['name']}")
            if len(header) < 16:
                header += chunk[: 16 - len(header)]
            digest.update(chunk)
        match = IMAGE_PATH_RE.fullmatch(image["path"])
        assert match is not None
        detected = _detect_image_type(header)
        if total != size or detected is None or detected[1] != image["mime"] or digest.hexdigest() != match.group(1):
            raise DataValidationError(f"剧照内容与记录不一致：{image['name']}")

    def _backup_current(self) -> None:
        if not self.data_path.exists() or self.data_path.stat().st_size == 0:
            return
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = self.backup_directory / f"movie-reviews-{stamp}.json"
        shutil.copy2(self.data_path, backup_path)

    def _prune_backups(self) -> None:
        try:
            backups = sorted(self.backup_directory.glob("movie-reviews-*.json"), key=lambda item: item.stat().st_mtime)
            for old_backup in backups[:-MAX_BACKUPS]:
                old_backup.unlink(missing_ok=True)
        except OSError:
            # A successful primary save must not be reported as failed only because
            # an old backup could not be cleaned up.
            pass


class DesktopApi:
    def __init__(self, settings: SettingsStore | None = None) -> None:
        # Keep implementation objects private. pywebview recursively inspects
        # public attributes of js_api objects; exposing a Window here makes it
        # crawl the native WinForms/WebView2 object graph and can freeze startup.
        self._settings = settings or SettingsStore()
        self._store: ReviewStore | None = None
        self._window: Any = None
        self._pending_imports: dict[str, dict[str, Any]] = {}
        self._image_drop_lock = threading.Lock()
        self._image_drop_generation = 0
        self._image_drop_binding: Any = None
        configured = self._settings.load_data_directory()
        if configured is not None:
            self._store = ReviewStore(configured)

    def _state(self, *, message: str = "", error: str = "") -> dict[str, Any]:
        if self._store is None:
            return {
                "ok": False,
                "needsSetup": True,
                "reviews": [],
                "dataDirectory": "",
                "dataFile": "",
                "message": message or "请选择影评数据的保存目录",
                "error": error,
            }
        try:
            reviews = self._store.load()
            return {
                "ok": True,
                "needsSetup": False,
                "reviews": reviews,
                "dataDirectory": str(self._store.directory),
                "dataFile": str(self._store.data_path),
                "message": message,
                "error": "",
            }
        except (OSError, DataValidationError) as exc:
            return {
                "ok": False,
                "needsSetup": False,
                "reviews": [],
                "dataDirectory": str(self._store.directory),
                "dataFile": str(self._store.data_path),
                "message": message,
                "error": str(exc),
            }

    def get_state(self) -> dict[str, Any]:
        return self._state()

    def choose_data_directory(self) -> dict[str, Any]:
        if self._window is None:
            return self._state(error="窗口尚未就绪")
        import webview

        initial_directory = str(self._store.directory) if self._store else ""
        selected = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=initial_directory)
        if not selected:
            return self._state(message="已取消选择")
        candidate = ReviewStore(Path(selected[0]))
        try:
            candidate.prepare()
            self._settings.save_data_directory(candidate.directory)
            self._store = candidate
            return self._state(message="数据目录已设置")
        except (OSError, DataValidationError) as exc:
            return self._state(error=f"无法使用所选目录：{exc}")

    def save_reviews(self, reviews: Any) -> dict[str, Any]:
        if self._store is None:
            return self._state(error="尚未选择数据目录")
        try:
            normalized = self._store.save(reviews)
            state = self._state(message="数据已保存")
            state["reviews"] = normalized
            return state
        except (OSError, DataValidationError) as exc:
            return self._state(error=f"保存失败：{exc}")

    def select_review_image(self) -> dict[str, Any]:
        if self._window is None or self._store is None:
            return {"ok": False, "cancelled": False, "error": "窗口或数据目录尚未就绪"}
        import webview

        selected = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=str(self._store.directory),
            allow_multiple=False,
            file_types=("剧照图片 (*.jpg;*.jpeg;*.png;*.webp)",),
        )
        if not selected:
            return {"ok": False, "cancelled": True, "error": ""}
        try:
            image = self._store.import_image(Path(selected[0]))
            return {"ok": True, "cancelled": False, "image": image, "error": ""}
        except (OSError, DataValidationError) as exc:
            return {"ok": False, "cancelled": False, "error": f"剧照导入失败：{exc}"}

    def _begin_image_drop(self) -> int:
        with self._image_drop_lock:
            self._image_drop_generation += 1
            return self._image_drop_generation

    def _is_current_image_drop(self, request_id: int) -> bool:
        with self._image_drop_lock:
            return request_id == self._image_drop_generation

    def get_review_image_info(self, reference: str) -> dict[str, Any]:
        if self._store is None:
            return {"ok": False, "error": "尚未选择数据目录"}
        try:
            return {"ok": True, **self._store.get_image_info(reference), "error": ""}
        except (OSError, DataValidationError) as exc:
            return {"ok": False, "error": str(exc)}

    def get_review_image_chunk(self, reference: str, offset: int, length: int) -> dict[str, Any]:
        if self._store is None:
            return {"ok": False, "error": "尚未选择数据目录"}
        try:
            return {"ok": True, **self._store.read_image_chunk(reference, offset, length), "error": ""}
        except (OSError, DataValidationError) as exc:
            return {"ok": False, "error": str(exc)}

    def select_import_file(self) -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "cancelled": False, "error": "窗口尚未就绪"}
        import webview

        initial_directory = str(self._store.directory) if self._store else ""
        selected = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=initial_directory,
            allow_multiple=False,
            file_types=("影评完整备份 (*.zip)", "JSON 数据文件 (*.json)"),
        )
        if not selected:
            return {"ok": False, "cancelled": True, "error": ""}
        source = Path(selected[0])
        try:
            if source.suffix.lower() == ".zip":
                plan = self._store.prepare_zip_import(source) if self._store else None
            elif source.suffix.lower() == ".json":
                plan = self._store.prepare_json_import(source) if self._store else None
            else:
                raise DataValidationError("仅支持 .zip 完整备份或 .json 数据文件")
            if plan is None:
                raise DataValidationError("尚未选择数据目录")
            token = uuid.uuid4().hex
            self._pending_imports = {token: plan}
            return {
                "ok": True,
                "cancelled": False,
                "reviews": plan["reviews"],
                "source": str(source),
                "importToken": token,
                "format": plan["kind"],
                "error": "",
            }
        except (OSError, DataValidationError) as exc:
            return {"ok": False, "cancelled": False, "error": f"导入文件无效：{exc}"}

    def apply_import(self, token: str) -> dict[str, Any]:
        if self._store is None:
            return self._state(error="尚未选择数据目录")
        plan = self._pending_imports.get(token)
        if plan is None:
            return self._state(error="导入请求已失效，请重新选择备份文件")
        try:
            normalized = self._store.apply_import(plan)
            self._pending_imports.clear()
            state = self._state(message=f"成功导入 {len(normalized)} 条影评")
            state["reviews"] = normalized
            return state
        except (OSError, DataValidationError, zipfile.BadZipFile) as exc:
            return self._state(error=f"导入失败：{exc}")

    def export_backup(self, reviews: Any) -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "cancelled": False, "error": "窗口尚未就绪"}
        import webview

        try:
            normalized = normalize_reviews(reviews)
        except DataValidationError as exc:
            return {"ok": False, "cancelled": False, "error": f"无法导出：{exc}"}
        initial_directory = str(self._store.directory) if self._store else ""
        filename = f"movie-reviews-backup-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.zip"
        selected = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=initial_directory,
            save_filename=filename,
            file_types=("影评完整备份 (*.zip)",),
        )
        if not selected:
            return {"ok": False, "cancelled": True, "error": ""}
        destination = Path(selected[0])
        if destination.suffix.lower() != ".zip":
            destination = destination.with_suffix(".zip")
        try:
            if self._store is None:
                raise DataValidationError("尚未选择数据目录")
            self._store.export_backup(destination, normalized)
            return {"ok": True, "cancelled": False, "path": str(destination), "error": ""}
        except (OSError, DataValidationError, zipfile.BadZipFile) as exc:
            return {"ok": False, "cancelled": False, "error": f"导出失败：{exc}"}

    def export_annual_report(self, filename: str, content: str) -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "cancelled": False, "error": "窗口尚未就绪"}
        if not isinstance(filename, str) or not re.fullmatch(r"[\w\-\u4e00-\u9fff]{1,80}\.html", filename):
            return {"ok": False, "cancelled": False, "error": "报告文件名无效"}
        if not isinstance(content, str) or not 0 < len(content) <= 10_000_000:
            return {"ok": False, "cancelled": False, "error": "报告内容无效或过大"}
        import webview
        selected = self._window.create_file_dialog(webview.FileDialog.SAVE, directory=str(self._store.directory) if self._store else "", save_filename=filename, file_types=("HTML 年度报告 (*.html)",))
        if not selected:
            return {"ok": False, "cancelled": True, "error": ""}
        destination = Path(selected[0]).with_suffix(".html")
        try:
            atomic_write(destination, content.encode("utf-8"))
            return {"ok": True, "cancelled": False, "path": str(destination), "error": ""}
        except OSError as exc:
            return {"ok": False, "cancelled": False, "error": f"报告导出失败：{exc}"}

    def export_graph_image(self, data_url: str) -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "cancelled": False, "error": "窗口尚未就绪"}
        prefix = "data:image/png;base64,"
        if not isinstance(data_url, str) or not data_url.startswith(prefix) or len(data_url) > 70_000_000:
            return {"ok": False, "cancelled": False, "error": "图谱图片数据无效或过大"}
        try:
            content = base64.b64decode(data_url[len(prefix):], validate=True)
        except (ValueError, binascii.Error):
            return {"ok": False, "cancelled": False, "error": "图谱图片编码无效"}
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            return {"ok": False, "cancelled": False, "error": "图谱图片不是有效 PNG"}
        import webview
        selected = self._window.create_file_dialog(webview.FileDialog.SAVE, directory=str(self._store.directory) if self._store else "", save_filename=f"电影知识图谱-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png", file_types=("PNG 图片 (*.png)",))
        if not selected:
            return {"ok": False, "cancelled": True, "error": ""}
        destination = Path(selected[0]).with_suffix(".png")
        try:
            atomic_write(destination, content)
            return {"ok": True, "cancelled": False, "path": str(destination), "error": ""}
        except OSError as exc:
            return {"ok": False, "cancelled": False, "error": f"图谱导出失败：{exc}"}

    def open_data_directory(self) -> dict[str, Any]:
        if self._store is None:
            return {"ok": False, "error": "尚未选择数据目录"}
        try:
            os.startfile(self._store.directory)  # type: ignore[attr-defined]
            return {"ok": True, "error": ""}
        except OSError as exc:
            return {"ok": False, "error": f"无法打开目录：{exc}"}

    def exit_app(self) -> None:
        if self._window is not None:
            self._window.destroy()


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / name


def expose_desktop_api(window: Any, api: DesktopApi) -> None:
    """Expose only the intentional bridge surface; never expose the API object graph."""
    window.expose(
        api.get_state,
        api.choose_data_directory,
        api.save_reviews,
        api.select_review_image,
        api.get_review_image_info,
        api.get_review_image_chunk,
        api.select_import_file,
        api.apply_import,
        api.export_backup,
        api.export_annual_report,
        api.export_graph_image,
        api.open_data_directory,
        api.exit_app,
    )


def bind_review_image_drop(window: Any, api: DesktopApi) -> None:
    """Bind the native drop payload without exposing arbitrary path reads to JS."""
    from webview.dom import DOMEventHandler

    picker = window.dom.get_element("#imagePicker")
    if picker is None:
        return

    def send(script: str) -> None:
        try:
            window.evaluate_js(script)
        except Exception:
            # The window may have closed while a background image copy finishes.
            pass

    def finish(request_id: int, result: dict[str, Any]) -> None:
        if not api._is_current_image_drop(request_id):
            return
        payload = json.dumps(result, ensure_ascii=False)
        send(f"window.handleNativeImageDropResult({request_id}, {payload})")

    def on_drop(event: Any) -> None:
        request_id = api._begin_image_drop()
        send(f"window.beginNativeImageDrop({request_id})")
        try:
            source = _dropped_image_path(event)
        except DataValidationError as exc:
            finish(request_id, {"ok": False, "error": str(exc)})
            return

        def import_in_background() -> None:
            if api._store is None:
                finish(request_id, {"ok": False, "error": "尚未选择数据目录"})
                return
            try:
                image = api._store.import_dropped_image(source)
                finish(request_id, {"ok": True, "image": image, "error": ""})
            except DataValidationError as exc:
                finish(request_id, {"ok": False, "error": str(exc)})
            except OSError:
                finish(request_id, {"ok": False, "error": "无法读取拖入的剧照文件"})

        threading.Thread(target=import_in_background, name="review-image-drop", daemon=True).start()

    handler = DOMEventHandler(on_drop, True, True)
    picker.events.drop += handler
    # Retain the DOM wrapper and handler for the lifetime of the API object.
    api._image_drop_binding = (picker, handler)


def main() -> None:
    import webview

    html = resource_path("index.html").read_text(encoding="utf-8")
    api = DesktopApi()
    window = webview.create_window(
        "我的影评记录",
        html=html,
        width=1120,
        height=780,
        min_size=(760, 560),
        background_color="#0f0f13",
        text_select=True,
    )
    api._window = window
    # Explicit exposure prevents pywebview from recursively scanning internal
    # settings, storage and native window objects.
    expose_desktop_api(window, api)
    webview.start(bind_review_image_drop, (window, api), gui="edgechromium", debug=False, private_mode=True)


if __name__ == "__main__":
    main()
