"""A local server for the frozen Public Evidence snapshots.

The capture script is the only thing that touches the network. After capture,
these bytes are served on 127.0.0.1:18091 so the fetch and evidence stages run
offline and reproduce exactly what was retrieved, including the SHA256.

Standalone:
    python -m benchmark.research_core.mocks.public_web --port 18091
"""

from __future__ import annotations

import argparse
import http.server
import pathlib
import socketserver
import threading
import urllib.parse

REPO = pathlib.Path(__file__).resolve().parents[3]
SNAPSHOTS = REPO / "benchmark" / "datasets" / "research-core" / "public-evidence" / "snapshots"


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ResearchCorePublic/1.0"

    def log_message(self, *args):  # silence the per-request stderr line
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = urllib.parse.urlsplit(self.path).path.lstrip("/")
        if path == "healthz":
            return self._send(200, b"ok\n", "text/plain; charset=utf-8")
        target = (SNAPSHOTS / path).resolve()
        if not str(target).startswith(str(SNAPSHOTS.resolve())) or not target.is_file():
            return self._send(404, b"<h1>Not found</h1>", "text/html; charset=utf-8")
        self._send(200, target.read_bytes(), "text/html; charset=utf-8")


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(host: str = "127.0.0.1", port: int = 18091) -> tuple[_Server, threading.Thread]:
    server = _Server((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18091)
    args = parser.parse_args()
    server, _ = start(args.host, args.port)
    print(f"serving {SNAPSHOTS} on http://{args.host}:{args.port}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
