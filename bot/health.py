"""Мини HTTP-сервер для хостингов (Render и др.).

Render Web Service требует, чтобы приложение слушало порт ($PORT).
Без PORT env (локальный запуск) сервер не поднимается.
"""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        await reader.readline()  # строка запроса
        while True:
            header = await reader.readline()
            if header in (b"\r\n", b"\n", b""):
                break
        body = b'{"status":"ok"}'
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start_health_server() -> asyncio.AbstractServer | None:
    """Поднять /health на порту PORT (для Render). None — если PORT не задан."""
    port = os.getenv("PORT")
    if not port:
        return None
    try:
        server = await asyncio.start_server(_handle, "0.0.0.0", int(port))
        logger.info("health-сервер на порту %s (для хостинга)", port)
        return server
    except Exception as ex:
        logger.warning("health-сервер не поднялся: %s", ex)
        return None
