"""Authenticated browser API. Run behind a local-only Uvicorn and HTTPS proxy."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import uuid
import zipfile
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from app import (BACKUP_FORMAT, BACKUP_FORMAT_VERSION, DATA_FILE_NAME, DataValidationError,
                 MAX_IMAGE_BYTES, ReviewStore, _json_bytes)
from web.storage import ArchiveDatabase, ConflictError
from web.backup import create_backup
from web import MAX_ARCHIVE_IMAGE_PIXELS


ROOT = Path(__file__).resolve().parents[1]
COOKIE_NAME = "movie_review_session"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = MAX_ARCHIVE_IMAGE_PIXELS


class ApiBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(ApiBody):
    username: str = Field(max_length=80)
    password: str = Field(max_length=1024)


class UserCreateBody(ApiBody):
    username: str
    displayName: str
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["admin", "user"] = "user"


class UserUpdateBody(ApiBody):
    username: str
    displayName: str
    role: Literal["admin", "user"]
    isActive: bool = Field(strict=True)


class PasswordBody(ApiBody):
    password: str = Field(min_length=12, max_length=1024)


class ChangePasswordBody(PasswordBody):
    currentPassword: str = Field(max_length=1024)


class ProfileBody(ApiBody):
    displayName: str = Field(min_length=1, max_length=80)


class CreateBody(ApiBody):
    review: dict
    requestKey: str


class UpdateBody(ApiBody):
    review: dict
    revision: int = Field(strict=True, ge=1)


class ReplaceBody(ApiBody):
    reviews: list[dict]
    archiveRevision: int = Field(strict=True, ge=0)


def create_app(data_dir: Path | None = None, *, secure_cookie: bool = True) -> FastAPI:
    default_dir = ROOT / ".runtime-web" if sys.platform == "win32" else Path("/var/lib/movie-review")
    data_dir = Path(data_dir or os.environ.get("MOVIE_REVIEW_DATA_DIR", default_dir)).resolve()
    db = ArchiveDatabase(data_dir / "db" / "app.sqlite3")
    media_store = ReviewStore(data_dir)
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    application.state.database = db
    pending_imports: dict[str, dict] = {}
    image_processing = threading.BoundedSemaphore(1)

    def backup_before_bulk(label: str) -> None:
        backup_dir = data_dir / "backups"
        destination = backup_dir / f"{label}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}.zip"
        create_backup(data_dir, destination)

    @application.middleware("http")
    async def private_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @application.exception_handler(DataValidationError)
    async def validation_error(_: Request, exc: DataValidationError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @application.exception_handler(ConflictError)
    async def conflict_error(_: Request, exc: ConflictError):
        return JSONResponse({"detail": str(exc), "code": "conflict"}, status_code=409)

    @application.exception_handler(PermissionError)
    async def permission_error(_: Request, exc: PermissionError):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @application.exception_handler(LookupError)
    async def missing_resource(_: Request, exc: LookupError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    def session(request: Request):
        token = request.cookies.get(COOKIE_NAME)
        found = db.session(token)
        if found is None:
            raise HTTPException(401, "请先登录")
        return found

    def write_session(request: Request):
        found = session(request)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                raise HTTPException(403, "请求来源不匹配")
        elif request.headers.get("sec-fetch-site") not in (None, "same-origin"):
            raise HTTPException(403, "请求来源不匹配")
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not hmac.compare_digest(hashlib.sha256(csrf.encode()).hexdigest(), found["csrf_hash"]):
            raise HTTPException(403, "安全令牌无效，请刷新页面")
        return found

    def admin_session(request: Request):
        found = session(request)
        if found["role"] != "admin":
            raise HTTPException(403, "仅管理员可以管理用户")
        return found

    def admin_write_session(request: Request):
        found = write_session(request)
        if found["role"] != "admin":
            raise HTTPException(403, "仅管理员可以管理用户")
        return found

    @application.get("/healthz")
    def healthz():
        return {"ok": True}

    @application.get("/readyz")
    def readyz():
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True}

    @application.get("/")
    def index():
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        html = html.replace("</head>", '<link rel="stylesheet" href="/web/account.css">\n</head>', 1)
        html = html.replace("<script>", '<script src="/web/client.js"></script>\n<script>', 1)
        return HTMLResponse(html)

    @application.get("/web/client.js")
    def client_script():
        return FileResponse(ROOT / "web" / "client.js", media_type="application/javascript")

    @application.get("/web/account.css")
    def account_styles():
        return FileResponse(ROOT / "web" / "account.css", media_type="text/css")

    @application.post("/api/auth/login")
    def login(body: Credentials, request: Request):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                raise HTTPException(403, "请求来源不匹配")
        source = request.client.host if request.client else "unknown"
        try:
            token, csrf = db.login(body.username, body.password, source)
        except PermissionError as exc:
            raise HTTPException(429 if "尝试过多" in str(exc) else 401, str(exc)) from exc
        response = JSONResponse({"ok": True, "csrfToken": csrf, "user": db.session_user(db.session(token))})
        response.set_cookie(COOKIE_NAME, token, max_age=7 * 86400, httponly=True,
                            secure=secure_cookie, samesite="strict", path="/")
        return response

    @application.get("/api/auth/me")
    def me(request: Request, found=Depends(session)):
        return {"ok": True, "csrfToken": db.csrf_for_session(request.cookies[COOKIE_NAME]), "user": db.session_user(found)}

    @application.put("/api/auth/profile")
    def update_profile(body: ProfileBody, found=Depends(write_session)):
        return {"user": db.update_profile(found["user_id"], body.displayName)}

    @application.post("/api/auth/password")
    def change_password(body: ChangePasswordBody, found=Depends(write_session)):
        db.change_password(found["user_id"], body.currentPassword, body.password)
        response = JSONResponse({"ok": True, "reauthenticate": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @application.get("/api/users")
    def list_users(found=Depends(admin_session)):
        return {"users": db.list_users(found["user_id"])}

    @application.post("/api/users", status_code=201)
    def create_user(body: UserCreateBody, found=Depends(admin_write_session)):
        return {"user": db.create_user(found["user_id"], body.username, body.displayName, body.password, body.role)}

    @application.put("/api/users/{user_id}")
    def update_user(user_id: int, body: UserUpdateBody, found=Depends(admin_write_session)):
        return {"user": db.update_user(found["user_id"], user_id, body.username, body.displayName, body.role, body.isActive)}

    @application.post("/api/users/{user_id}/password")
    def reset_password(user_id: int, body: PasswordBody, found=Depends(admin_write_session)):
        db.reset_user_password(found["user_id"], user_id, body.password)
        return {"ok": True}

    @application.post("/api/auth/logout")
    def logout(request: Request, _: object = Depends(write_session)):
        db.logout(request.cookies[COOKIE_NAME])
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @application.get("/api/reviews")
    def list_reviews(found=Depends(session)):
        return db.list_reviews(found["user_id"])

    @application.get("/api/reviews/{review_id}")
    def get_review(review_id: str, found=Depends(session)):
        for item in db.list_reviews(found["user_id"])["reviews"]:
            if item["review"]["id"] == review_id:
                return item
        raise HTTPException(404, "影评不存在")

    def check_image(review: dict, user_id: int):
        image = review.get("image")
        if image:
            normalized = db.validate_review(review)["image"]
            if not db.can_access_image(user_id, normalized["path"]):
                raise HTTPException(404, "剧照不存在，请先上传")
            media_store.verify_image(normalized)

    @application.post("/api/reviews", status_code=201)
    def create_review(body: CreateBody, found=Depends(write_session)):
        check_image(body.review, found["user_id"])
        return db.create_review(body.review, body.requestKey, found["user_id"])

    @application.put("/api/reviews/{review_id}")
    def update_review(review_id: str, body: UpdateBody, found=Depends(write_session)):
        check_image(body.review, found["user_id"])
        return db.update_review(review_id, body.review, body.revision, found["user_id"])

    @application.delete("/api/reviews/{review_id}")
    def delete_review(review_id: str, revision: int, found=Depends(write_session)):
        backup_before_bulk("pre-delete")
        db.delete_review(review_id, revision, found["user_id"])
        return {"ok": True}

    @application.get("/api/archive/version")
    def archive_version(found=Depends(session)):
        return {"archiveRevision": db.list_reviews(found["user_id"])["archiveRevision"]}

    @application.put("/api/archive/reviews")
    def replace_archive(body: ReplaceBody, found=Depends(write_session)):
        for review in body.reviews:
            check_image(review, found["user_id"])
        backup_before_bulk("pre-bulk")
        return db.replace_archive(body.reviews, body.archiveRevision, found["user_id"])

    def image_path(filename: str, user_id: int) -> tuple[str, Path]:
        reference = "media/" + filename
        if not db.can_access_image(user_id, reference):
            raise HTTPException(404, "剧照不存在")
        try:
            path = media_store.resolve_image_path(reference)
        except DataValidationError as exc:
            raise HTTPException(404, "剧照不存在") from exc
        if not path.is_file():
            raise HTTPException(404, "剧照不存在")
        return reference, path

    @application.get("/api/media/{filename}")
    def get_image(filename: str, found=Depends(session)):
        reference, path = image_path(filename, found["user_id"])
        mime = media_store.get_image_info(reference)["mime"]
        return FileResponse(path, media_type=mime)

    @application.get("/api/media/{filename}/thumbnail")
    def get_thumbnail(filename: str, found=Depends(session)):
        reference, path = image_path(filename, found["user_id"])
        thumb = data_dir / "thumbnails" / (filename + ".jpg")
        if not thumb.is_file():
            if not image_processing.acquire(timeout=10):
                raise HTTPException(503, "图片处理繁忙，请重试")
            try:
                if not thumb.is_file():
                    thumb.parent.mkdir(parents=True, exist_ok=True)
                    with Image.open(path) as image:
                        image.thumbnail((480, 480))
                        rgb = image.convert("RGB")
                        descriptor, temp_name = tempfile.mkstemp(dir=thumb.parent, suffix=".tmp")
                        os.close(descriptor)
                        try:
                            rgb.save(temp_name, format="JPEG", quality=82)
                            os.replace(temp_name, thumb)
                        finally:
                            Path(temp_name).unlink(missing_ok=True)
            finally:
                image_processing.release()
        return FileResponse(thumb, media_type="image/jpeg")

    @application.post("/api/media")
    def upload_image(file: UploadFile = File(...), found=Depends(write_session)):
        if not file.filename or len(file.filename) > 255 or "/" in file.filename or "\\" in file.filename:
            raise DataValidationError("剧照文件名无效")
        if Path(file.filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise DataValidationError("仅支持 JPG、PNG 和 WebP 剧照；HEIC 暂不支持")
        stream = file.file
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        if not 0 < size <= MAX_IMAGE_BYTES:
            raise DataValidationError("剧照必须大于 0 字节且不能超过 50 MB")
        if not image_processing.acquire(timeout=10):
            raise HTTPException(503, "图片处理繁忙，请重试")
        try:
            stream.seek(0)
            try:
                with Image.open(stream) as decoded:
                    if decoded.width * decoded.height > 40_000_000:
                        raise DataValidationError("剧照像素过大")
                    if decoded.format not in {"JPEG", "PNG", "WEBP"}:
                        raise DataValidationError("剧照格式无效")
                    detected_mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[decoded.format]
                    decoded.verify()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise DataValidationError("剧照无法解码或格式无效") from exc
            if detected_mime != file.content_type and file.content_type not in (None, "application/octet-stream"):
                raise DataValidationError("剧照声明类型与实际内容不一致")
            stream.seek(0)
            image = media_store.import_image_stream(stream, size, file.filename)
        finally:
            image_processing.release()
        db.register_upload(found["user_id"], image)
        return {"ok": True, "image": image}

    @application.get("/api/exports/backup")
    def export_backup(found=Depends(session)):
        snapshot = db.list_reviews(found["user_id"])
        reviews = [item["review"] for item in snapshot["reviews"]]
        manifest = {"format": BACKUP_FORMAT, "version": BACKUP_FORMAT_VERSION,
                    "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "recordCount": len(reviews)}
        export_dir = data_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=export_dir, suffix=".zip")
        os.close(descriptor)
        output = Path(name)
        try:
            with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
                archive.writestr("manifest.json", _json_bytes(manifest), compress_type=zipfile.ZIP_DEFLATED)
                archive.writestr(DATA_FILE_NAME, _json_bytes(reviews), compress_type=zipfile.ZIP_DEFLATED)
                references = {item["image"]["path"]: item["image"] for item in reviews if item.get("image")}
                for reference, image in sorted(references.items()):
                    path = media_store.verify_image(image)
                    archive.write(path, reference, compress_type=zipfile.ZIP_STORED)
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return FileResponse(output, media_type="application/zip", filename="movie-reviews-backup.zip",
                            background=BackgroundTask(output.unlink, missing_ok=True))

    @application.post("/api/exports/graph")
    async def export_graph(request: Request, _: object = Depends(write_session)):
        if int(request.headers.get("content-length", "0")) > 10_000_000:
            raise HTTPException(413, "关系图数据过大")
        try:
            graph = await request.json()
            if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
                raise ValueError()
            payload = json.dumps(graph, ensure_ascii=False, allow_nan=False)
            if len(payload.encode("utf-8")) > 10_000_000:
                raise ValueError()
        except (ValueError, TypeError, json.JSONDecodeError):
            raise DataValidationError("关系图数据无效或过大")
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        html = (ROOT / "graph_export.html").read_text(encoding="utf-8").replace("__GRAPH_DATA__", payload)
        return Response(html, media_type="text/html; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="movie-graph.html"'})

    @application.post("/api/imports/preview")
    async def preview_import(request: Request, file: UploadFile = File(...), found=Depends(write_session)):
        for old_token, old in list(pending_imports.items()):
            if old["expires"] < time.time():
                old["source"].unlink(missing_ok=True)
                pending_imports.pop(old_token, None)
        if len(pending_imports) >= 20:
            raise HTTPException(429, "待确认导入过多，请稍后再试")
        if not file.filename or Path(file.filename).suffix.lower() not in {".json", ".zip"}:
            raise DataValidationError("仅支持 JSON 或 ZIP 备份")
        suffix = Path(file.filename).suffix.lower()
        imports_dir = data_dir / "imports"
        imports_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=imports_dir, suffix=suffix)
        total = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as target:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise DataValidationError("备份不能超过 200 MB")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            source = Path(temp_name)
            if suffix == ".zip":
                plan = media_store.prepare_zip_import(source)
                with zipfile.ZipFile(source) as archive:
                    raw_reviews = json.loads(archive.read(DATA_FILE_NAME).decode("utf-8-sig"))
            else:
                raw_reviews = json.loads(source.read_text(encoding="utf-8-sig"))
                if any(item.get("image") for item in raw_reviews if isinstance(item, dict)):
                    raise DataValidationError("含剧照的 JSON 请使用完整 ZIP 备份")
                from app import normalize_reviews
                plan = {"kind": "json", "reviews": normalize_reviews(raw_reviews)}
            if raw_reviews != plan["reviews"]:
                raise DataValidationError("备份包含会被自动修整的字段，请先用迁移预检处理差异")
            token = uuid.uuid4().hex
            pending_imports[token] = {"source": source, "sha256": digest.hexdigest(),
                                      "plan": plan, "session": db._hash(request.cookies[COOKIE_NAME]),
                                      "archiveRevision": db.list_reviews(found["user_id"])["archiveRevision"],
                                      "userId": found["user_id"],
                                      "expires": time.time() + 1800}
            return {"ok": True, "recordCount": len(plan["reviews"]), "format": plan["kind"],
                    "importToken": token, "archiveRevision": pending_imports[token]["archiveRevision"]}
        except (OSError, ValueError, zipfile.BadZipFile, UnicodeError) as exc:
            Path(temp_name).unlink(missing_ok=True)
            raise DataValidationError(f"导入文件无效：{exc}") from exc
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    @application.post("/api/imports/confirm")
    async def confirm_import(request: Request, found=Depends(write_session)):
        body = await request.json()
        token = body.get("importToken") if isinstance(body, dict) else None
        item = pending_imports.get(token)
        if not item or item["expires"] < time.time() or item["session"] != db._hash(request.cookies[COOKIE_NAME]) or item["userId"] != found["user_id"]:
            raise HTTPException(410, "导入预览已失效，请重新选择备份")
        source = item["source"]
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise DataValidationError("备份文件在预览后发生变化")
        plan = item["plan"]
        if plan["kind"] == "zip":
            fresh = media_store.prepare_zip_import(source)
            if fresh["reviews"] != plan["reviews"]:
                raise DataValidationError("备份内容在预览后发生变化")
            with zipfile.ZipFile(source) as archive:
                images = {review["image"]["path"]: review["image"] for review in plan["reviews"] if review.get("image")}
                for reference, image in images.items():
                    with archive.open(reference) as stream:
                        installed = media_store.import_image_stream(stream, image["size"], image["name"])
                    if installed != image:
                        raise DataValidationError("导入媒体与预览不一致")
                    db.register_upload(found["user_id"], installed)
        backup_before_bulk("pre-import")
        result = db.replace_archive(plan["reviews"], item["archiveRevision"], found["user_id"])
        pending_imports.pop(token, None)
        source.unlink(missing_ok=True)
        return result

    return application


app = create_app()
