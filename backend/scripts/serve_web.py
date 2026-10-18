"""Serve an Expo web export and proxy /api to a local API (same origin, so no CORS).

    python -I backend/scripts/serve_web.py <export-dir> <port> <api-origin>

Local verification only. Nothing is cached, rewritten or logged beyond the access line."""

from __future__ import annotations

import http.server
import mimetypes
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2])
API = sys.argv[3].rstrip("/")


class Handler(http.server.BaseHTTPRequestHandler):
    def _proxy(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "content-length"}}
        request = urllib.request.Request(f"{API}{self.path}", data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status, payload, reply = response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            status, payload, reply = error.code, error.read(), error.headers
        self.send_response(status)
        for key, value in reply.items():
            if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _static(self):
        path = self.path.split("?")[0].lstrip("/")
        target = (ROOT / path).resolve()
        if not str(target).startswith(str(ROOT)) or not target.is_file():
            target = ROOT / "index.html"                     # single-page fallback
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._proxy() if self.path.startswith("/api/") else self._static()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = lambda self: self._proxy() if self.path.startswith("/api/") else self.send_response(200) or self.end_headers()

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
