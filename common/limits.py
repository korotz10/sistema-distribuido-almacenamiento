"""Límites de cuerpo, tiempo de lectura y transferencias concurrentes."""
import asyncio

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from common.config import MAX_CONCURRENT_TRANSFERS, MAX_FILE_SIZE, TIMEOUT_READ


class RequestLimitsMiddleware:
    def __init__(self, app):
        self.app = app
        self.transfers = asyncio.Semaphore(MAX_CONCURRENT_TRANSFERS)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        headers = dict(scope.get("headers", []))
        maximum = MAX_FILE_SIZE + 1024 * 1024
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Content-Length no válido"}, 400)(scope, receive, send)
        if length < 0 or length > maximum:
            return await JSONResponse({"detail": "Cuerpo HTTP demasiado grande"}, 413)(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            try:
                message = await asyncio.wait_for(receive(), TIMEOUT_READ)
            except asyncio.TimeoutError as exc:
                raise HTTPException(408, "Se agotó la espera de datos de la petición") from exc
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > maximum:
                    raise HTTPException(413, "Cuerpo HTTP demasiado grande")
            return message

        if path in ("/upload", "/store") or path.startswith(("/download/", "/retrieve/", "/repair/")):
            async with self.transfers:
                await self.app(scope, limited_receive, send)
        else:
            await self.app(scope, limited_receive, send)
