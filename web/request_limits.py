"""Enforce request budgets before JSON or multipart parsing, including streams."""
from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

MAX_JSON_BODY_BYTES = 50 * 1024 * 1024
MAX_ACCOUNT_BODY_BYTES = 64 * 1024


class RequestBodyLimits:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == "/api/media":
            limit = 51 * 1024 * 1024  # 50 MB original plus multipart headers.
        elif path == "/api/imports/preview":
            limit = 201 * 1024 * 1024
        elif path == "/api/exports/graph":
            limit = 10_000_000
        elif path.startswith(("/api/auth/", "/api/users")):
            limit = MAX_ACCOUNT_BODY_BYTES
        else:
            limit = MAX_JSON_BODY_BYTES
        lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        if lengths:
            try:
                length = int(lengths[0])
                if len(lengths) != 1 or length < 0:
                    raise ValueError()
            except (ValueError, TypeError):
                return await JSONResponse({"detail": "请求长度无效"}, status_code=400)(scope, receive, send)
            if length > limit:
                return await JSONResponse({"detail": "请求内容过大"}, status_code=413)(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "请求内容过大")
            return message

        await self.app(scope, limited_receive, send)
