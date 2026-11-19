"""A local HTTP server serving the synthetic corpus and the awkward cases.

It listens on 127.0.0.1:18090 only. The existing fixture server on 18080 is
left untouched -- see ``benchmark/research-core/ENVIRONMENT_AUDIT.md``.

The routes here are the fetch contract in executable form: redirect chains,
a cookie gate, a meta refresh, JavaScript-rendered rows, pagination, a real
PDF, an empty SPA shell, a 404, binary garbage, a wrong ``lang`` attribute,
duplicated copy and a stale page. A fetcher's score is measured against what
this server actually serves, so the gold can be read from the bytes.

Standalone:
    python -m benchmark.research_core.mocks.web --port 18090
"""

from __future__ import annotations

import argparse
import http.server
import pathlib
import socketserver
import threading
import urllib.parse

REPO = pathlib.Path(__file__).resolve().parents[3]
CORPUS = REPO / "benchmark" / "datasets" / "research-core" / "corpus"
SITES = CORPUS / "sites"

#: A one-page PDF with a real text layer, written by hand so the fixture needs
#: no PDF library at serve time. pypdf (used by the tests) reads it back.
PDF_TEXT = ["Braunfeld Maschinenbau GmbH", "CNC Werkzeugmaschinen",
            "info@braunfeld-mb.example"]


def _pdf_bytes(lines: list[str]) -> bytes:
    content = "BT /F1 14 Tf 72 760 Td " + " ".join(
        f"({line}) Tj 0 -22 Td" for line in lines
    ) + " ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode(),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref_at}\n%%EOF\n").encode()
    return bytes(out)


CATALOGUE_PDF = _pdf_bytes(PDF_TEXT)


def _page(title: str, body: str, *, lang: str = "en", head: str = "") -> str:
    return ("<!doctype html>\n"
            f'<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
            f"<title>{title}</title>\n{head}</head>\n<body>\n{body}\n</body>\n</html>\n")


def _northwind_about_with_contact() -> str:
    about = (SITES / "northwind-marine" / "about.html").read_text(encoding="utf-8")
    return about.replace(
        "</main>",
        '<p>Email: <a href="mailto:info@northwindmarine.example">info@northwindmarine.example</a></p>\n</main>',
    )


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ResearchCoreFixtures/1.0"

    def log_message(self, *args):  # silence the per-request stderr line
        return

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str,
              headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str, status: int = 200, **kwargs) -> None:
        self._send(status, body.encode("utf-8"), "text/html; charset=utf-8", **kwargs)

    # -- routing ---------------------------------------------------------
    def do_GET(self):  # noqa: N802 - http.server API
        path = urllib.parse.urlsplit(self.path).path

        if path.startswith("/sites/"):
            return self._static(path[len("/sites/"):])
        if path.startswith("/dynamic/"):
            return self._dynamic(path)
        if path == "/healthz":
            return self._send(200, b"ok\n", "text/plain; charset=utf-8")
        return self._static(path.lstrip("/"))

    def _static(self, relative: str) -> None:
        target = (SITES / relative).resolve()
        if not str(target).startswith(str(SITES.resolve())) or not target.is_file():
            return self._html("<h1>Not found</h1>", 404)
        content_type = "text/html; charset=utf-8" if target.suffix == ".html" else "application/octet-stream"
        self._send(200, target.read_bytes(), content_type)

    def _dynamic(self, path: str) -> None:
        route = path[len("/dynamic/"):]

        if route == "redirect/1":
            self.send_response(302)
            self.send_header("Location", "/dynamic/redirect/2")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if route == "redirect/2":
            self.send_response(302)
            self.send_header("Location", "/northwind-marine/about.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if route == "cookie/set":
            self.send_response(302)
            self.send_header("Location", "/dynamic/cookie/check")
            self.send_header("Set-Cookie", "consent=yes; Path=/; Max-Age=600; HttpOnly")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if route == "cookie/check":
            cookie = self.headers.get("Cookie", "")
            if "consent=yes" not in cookie:
                return self._html("<h1>Consent required</h1>", 403)
            body = _page("Northwind Marine",
                         "<h1>Cookie accepted</h1>\n<p>Northwind Marine respects your choice.</p>\n"
                         '<p><a href="mailto:info@northwindmarine.example">info@northwindmarine.example</a></p>')
            return self._html(body)

        if route == "meta-refresh":
            body = _page(
                "Redirecting",
                "<p>Weiterleitung…</p>",
                head='<meta http-equiv="refresh" content="0;url=/sites/van-dijk-watersport/contact.html">\n',
            )
            return self._html(body)

        if route == "lazy":
            body = _page(
                "Laden",
                '<h1>Standorte</h1>\n<div id="rows">Wird geladen…</div>\n'
                "<script>\nsetTimeout(function () {\n"
                "  document.getElementById('rows').innerHTML = "
                "'<ul><li>Braunfeld — Esslingen</li><li>Braunfeld — Stuttgart</li></ul>';\n"
                "}, 300);\n</script>",
                lang="de",
            )
            return self._html(body)

        if route == "page/2":
            body = _page("Zuiderzee Techniek — pagina 2",
                         "<h1>Pagina 2</h1>\n<p>Zuiderzee Techniek — vestigingen en contactpersonen.</p>\n"
                         '<p><a href="mailto:info@zuiderzeetechniek.example">info@zuiderzeetechniek.example</a></p>',
                         lang="nl")
            return self._html(body)

        if route == "pdf-link":
            body = _page("Produktkatalog",
                         "<h1>Produktkatalog</h1>\n"
                         '<p><a href="/dynamic/catalogue.pdf">Produktkatalog als PDF herunterladen</a></p>',
                         lang="de")
            return self._html(body)
        if route == "catalogue.pdf":
            return self._send(200, CATALOGUE_PDF, "application/pdf")

        if route == "spa":
            return self._html(_page("App", '<div id="app">Loading</div>\n<script src="/app.js"></script>'))

        if route == "js-rendered":
            body = _page(
                "Kontakt",
                '<h1>Kontakt</h1>\n<div id="contact">Wird geladen…</div>\n'
                "<script>\nsetTimeout(function () {\n"
                "  document.getElementById('contact').innerHTML = "
                "'<p>Braunfeld Maschinenbau GmbH</p>"
                "<p><a href=\"mailto:info@braunfeld-mb.example\">info@braunfeld-mb.example</a></p>';\n"
                "}, 200);\n</script>",
                lang="de",
            )
            return self._html(body)

        if route == "not-found":
            return self._html("<h1>Gone</h1>\n<p>This resource is no longer available.</p>", 410)

        if route == "binary":
            return self._send(200, bytes(range(256)) * 4, "image/png")

        if route == "missing-page":
            return self._html("<h1>404 Not found</h1>", 404)

        if route == "garbage":
            return self._send(200, bytes(range(0, 32)) * 8, "text/html; charset=utf-8")

        if route == "wrong-language":
            body = _page("Kranich Software",
                         "<h1>Kranich Software</h1>\n<p>ERP-Systeme für den Mittelstand.</p>\n"
                         '<p><a href="mailto:info@kranich-sw.example">info@kranich-sw.example</a></p>',
                         lang="en")
            return self._html(body)

        if route == "duplicate":
            return self._html(_northwind_about_with_contact())

        if route == "stale":
            body = _page("Harborline Foods",
                         "<h1>Harborline Foods</h1>\n<p>Chilled and frozen produce distribution.</p>\n"
                         "<p>Last updated: 2019.</p>\n"
                         '<p><a href="mailto:sales@harborlinefoods.example">sales@harborlinefoods.example</a></p>')
            return self._html(body)

        return self._html("<h1>Not found</h1>", 404)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(host: str = "127.0.0.1", port: int = 18090) -> tuple[_Server, threading.Thread]:
    server = _Server((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18090)
    args = parser.parse_args()
    server, _ = start(args.host, args.port)
    print(f"serving {SITES} on http://{args.host}:{args.port}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
