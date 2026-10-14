"""
Точка входа на Render: HTTP-сервер для health-check + Telegram-бот в одном процессе.

Render — web-сервис, ему нужен порт, который слушает HTTP. Бот крутит polling
в этом же процессе, чтобы не поднимать второй контейнер.

    python entrypoint.py
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bot

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

PAGE = (
    "MITINI running. Telegram bot is polling. "
    "See /health for status, /start in Telegram for commands."
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.encode()
            ctype = "text/plain; charset=utf-8"
        elif self.path.startswith("/health"):
            body = b'{"status":"ok"}'
            ctype = "application/json"
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class Server(ThreadingHTTPServer):
    """Render рвёт соединение на health-check — не логируем это как ошибку."""

    daemon_threads = True
    allow_reuse_address = True


def main():
    from voice_clone import mem_mb, status

    st = status()
    print(f"[boot] свободно {mem_mb()} МБ, синтез: {st['backend']}, "
          f"режим {st['mode']}, образец: {'есть' if st['ref'] else 'нет (пришли /ref)'}")
    srv = Server((HOST, PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"health: http://{HOST}:{PORT}/health")
    bot.main()


if __name__ == "__main__":
    main()
