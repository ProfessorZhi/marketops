#!/usr/bin/env python3
"""Serve the local-runtime fixtures on 127.0.0.1.

The point is determinism: a fetch reader's score should not move because a
real site redesigned its homepage overnight. Every fixture is generated here,
so the bytes are stable and the gold labels can be checked against them.

Routes
------
  /                      index of what this server exposes
  /static/about.html     plain HTML with contacts a reader must keep
  /js/team.html          content injected by JavaScript only
  /lazy/list.html        list that fills in after a timer
  /page/1 .. /page/3     pagination with next/prev links
  /redirect/1            -> /redirect/2 -> /static/about.html
  /cookie/set            sets a cookie, redirects to /cookie/check
  /cookie/check          only renders the payload if the cookie is present
  /shell/spa.html        empty <div id="root">; a reader must not call this success
  /pdf/sample.pdf        a tiny text PDF generated on the fly
  /pdf/link/<name>       HTML that links to a PDF served on :18082
  /long/article.html     a long article; truncating readers lose its closing lines
  /static/team.html      several people with titles and addresses in plain HTML
  /org/subsidiary.html   states the parent/subsidiary relationship a reader must keep
  /meta/refresh.html     a <meta http-equiv="refresh"> hop, which is not an HTTP 302
"""

from __future__ import annotations

import http.server
import re
import socketserver
import sys
import urllib.parse

HOST = "127.0.0.1"
DEFAULT_PORT = 18080

ABOUT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>About Northwind Ltd</title></head>
<body>
  <header><nav><a href="/static/about.html">About</a> <a href="/js/team.html">Team</a></nav></header>
  <main>
    <h1>About Northwind Ltd</h1>
    <p>Northwind Ltd is a fictional company used only by the MarketOps benchmark.
       It assembles marine fittings in Rotterdam and has done so since 1987.
       Its registered office is 14 Harbour Road, Rotterdam.</p>
    <h2>Contact</h2>
    <p>Email: <a href="mailto:info@northwind.example">info@northwind.example</a></p>
    <p>Phone: +31 10 555 0123</p>
    <p>Press: <a href="mailto:press@northwind.example">press@northwind.example</a></p>
  </main>
  <footer><p>&copy; 2026 Northwind Ltd</p></footer>
</body></html>
"""

TEAM_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Northwind Team</title></head>
<body>
  <h1>Our team</h1>
  <div id="people">Loading…</div>
  <script>
    setTimeout(function () {
      document.getElementById('people').innerHTML =
        '<ul>' +
        '<li><strong>Marta Visser</strong> — Managing Director, <a href="mailto:m.visser@northwind.example">m.visser@northwind.example</a></li>' +
        '<li><strong>Pieter de Boer</strong> — Operations Manager, <a href="mailto:p.deboer@northwind.example">p.deboer@northwind.example</a></li>' +
        '</ul>';
    }, 300);
  </script>
</body></html>
"""

LAZY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Distributors</title></head>
<body>
  <h1>Distributors</h1>
  <ul id="rows"><li>Loading…</li></ul>
  <script>
    var data = [
      ['Hansa Marine', 'Hamburg', 'hansa@example.org'],
      ['Adriatic Supply', 'Split', 'adriatic@example.org'],
      ['Baltic Parts', 'Gdansk', 'baltic@example.org']
    ];
    setTimeout(function () {
      var ul = document.getElementById('rows');
      ul.innerHTML = '';
      data.forEach(function (r) {
        var li = document.createElement('li');
        li.textContent = r[0] + ' — ' + r[1] + ' — ' + r[2];
        ul.appendChild(li);
      });
    }, 500);
  </script>
</body></html>
"""

SPA_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Northwind</title></head>
<body>
  <div id="root"></div>
  <noscript>You need to enable JavaScript to run this app.</noscript>
  <script src="/shell/app.js"></script>
</body></html>
"""

EMPTY_JS = "/* placeholder: this fixture deliberately never fills #root */\n"


def pdf_bytes() -> bytes:
    """A minimal one-page PDF with selectable text, no external libraries."""
    text = "Northwind Ltd quality manual. Page one. Contact info@northwind.example."
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects)+1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


LONG_PARAGRAPH = (
    "Northwind Marine Fittings Ltd has supplied deck hardware to shipyards around "
    "the North Sea since 1987. Each casting is poured in the Rotterdam foundry, "
    "machined on site, pressure-tested at sixteen bar and photographed before it "
    "leaves the yard. The company keeps its inspection records for seven years."
)
LONG_TAIL = (
    "<h2>How to reach us</h2>"
    "<p>Write to <a href=\"mailto:long@northwind.example\">long@northwind.example</a> "
    "or call +31 10 555 0199. The office is open on weekdays from 08:00 to 17:00 CET.</p>"
)


def long_html() -> str:
    """A page whose payload sits at the end, not the start.

    A reader that truncates by character budget or by DOM depth returns a
    non-empty string for this page and still misses the contact block, which is
    the only part downstream extraction needs.
    """
    body = "".join(
        f"<h2>Section {i}</h2><p>{LONG_PARAGRAPH}</p>" for i in range(1, 121)
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Northwind — long technical note</title></head><body>"
        "<h1>Northwind long technical note</h1>"
        f"<main>{body}{LONG_TAIL}</main>"
        "<footer><p>&copy; 2026 Northwind Ltd</p></footer></body></html>"
    )


TEAM_STATIC_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Northwind — management</title></head>
<body>
  <h1>Management</h1>
  <p>The people below are the current management of Northwind Marine Fittings Ltd.</p>
  <table>
    <tr><th>Name</th><th>Role</th><th>Email</th><th>Phone</th></tr>
    <tr><td>Marta Visser</td><td>Managing Director</td>
        <td><a href="mailto:m.visser@northwind.example">m.visser@northwind.example</a></td>
        <td>+31 10 555 0124</td></tr>
    <tr><td>Pieter de Boer</td><td>Operations Manager</td>
        <td><a href="mailto:p.deboer@northwind.example">p.deboer@northwind.example</a></td>
        <td>+31 10 555 0125</td></tr>
    <tr><td>Sanne Bakker</td><td>Purchasing Lead</td>
        <td><a href="mailto:s.bakker@northwind.example">s.bakker@northwind.example</a></td>
        <td>+31 10 555 0126</td></tr>
  </table>
  <p>General enquiries: <a href="mailto:info@northwind.example">info@northwind.example</a></p>
</body></html>
"""

ORG_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Northwind Baltic — about</title></head>
<body>
  <h1>Northwind Baltic OÜ</h1>
  <p>Northwind Baltic OÜ is a wholly owned subsidiary of Northwind Marine Fittings Ltd,
     registered in Tallinn. It handles distribution in the Baltic states.</p>
  <p>The subsidiary does not manufacture. All production stays in Rotterdam.</p>
  <p>Contact: <a href="mailto:baltic@northwind.example">baltic@northwind.example</a>,
     +372 5555 0140.</p>
  <p>Group website: northwind.example</p>
</body></html>
"""

META_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Northwind — moved</title>
<meta http-equiv="refresh" content="0; url=/static/about.html">
</head>
<body><p>This page has moved. If nothing happens, follow
<a href="/static/about.html">this link</a>.</p></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "MarketOpsFixtures/1.0"

    def log_message(self, fmt, *args):  # keep the smoke output readable
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, extra: dict | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = urllib.parse.urlparse(self.path).path

        if path == "/":
            body = (
                "<h1>MarketOps fixtures</h1><ul>"
                + "".join(f'<li><a href="{p}">{p}</a></li>' for p in (
                    "/static/about.html", "/js/team.html", "/lazy/list.html",
                    "/page/1", "/redirect/1", "/cookie/set", "/shell/spa.html",
                    "/pdf/sample.pdf", "/long/article.html", "/static/team.html",
                    "/org/subsidiary.html", "/meta/refresh.html"))
                + "</ul>"
            ).encode()
            return self._send(body)

        if path == "/static/about.html":
            return self._send(ABOUT_HTML.encode())
        if path == "/js/team.html":
            return self._send(TEAM_HTML.encode())
        if path == "/lazy/list.html":
            return self._send(LAZY_HTML.encode())
        if path == "/shell/spa.html":
            return self._send(SPA_HTML.encode())
        if path == "/shell/app.js":
            return self._send(EMPTY_JS.encode(), ctype="application/javascript")
        if path == "/long/article.html":
            return self._send(long_html().encode())
        if path == "/static/team.html":
            return self._send(TEAM_STATIC_HTML.encode())
        if path == "/org/subsidiary.html":
            return self._send(ORG_HTML.encode())
        if path == "/meta/refresh.html":
            return self._send(META_HTML.encode())

        if path.startswith("/page/"):
            try:
                n = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self._send(b"bad page", status=400)
            if n < 1 or n > 3:
                return self._send(b"not found", status=404)
            rows = "".join(
                f"<li>Member {i} — member{i}@northwind.example</li>"
                for i in range((n - 1) * 10 + 1, n * 10 + 1)
            )
            nav = []
            if n > 1:
                nav.append(f'<a rel="prev" href="/page/{n-1}">Previous</a>')
            if n < 3:
                nav.append(f'<a rel="next" href="/page/{n+1}">Next</a>')
            body = (
                f"<h1>Members page {n} of 3</h1><ul>{rows}</ul>"
                f"<nav>{' '.join(nav)}</nav>"
            ).encode()
            return self._send(body)

        if path == "/redirect/1":
            return self._redirect("/redirect/2")
        if path == "/redirect/2":
            return self._redirect("/static/about.html")

        if path == "/cookie/set":
            return self._redirect(
                "/cookie/check",
                {"Set-Cookie": "mo_consent=yes; Path=/; Max-Age=3600"},
            )
        if path == "/cookie/check":
            if "mo_consent=yes" in (self.headers.get("Cookie") or ""):
                return self._send(
                    b"<h1>Consent accepted</h1><p>Cookie-gated content: "
                    b"sales@northwind.example</p>"
                )
            return self._send(
                b"<h1>Cookie required</h1><p>This page only renders after consent.</p>",
                status=403,
            )

        if path == "/pdf/sample.pdf":
            return self._send(pdf_bytes(), ctype="application/pdf")

        if path.startswith("/pdf/link/"):
            # A page that points at a PDF instead of being one. This is the
            # realistic path: a browser renders the page, the PDF itself is
            # fetched over HTTP. It exists so the pipeline can be measured
            # instead of assuming the browser can carry the bytes.
            name = path.rsplit("/", 1)[1]
            if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
                return self._send(b"bad name", status=400)
            body = (
                "<html><head><title>Document index</title></head><body>"
                "<h1>Public document index</h1>"
                "<p>Annual report available for download.</p>"
                f'<p><a href="http://127.0.0.1:18082/{name}">{name}</a></p>'
                "</body></html>"
            ).encode()
            return self._send(body)

        return self._send(b"not found", status=404)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    with Server((HOST, port), Handler) as httpd:
        print(f"fixtures on http://{HOST}:{port}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
