#!/usr/bin/env python3
"""`rig web`: a read-only board in the browser (design: docs/journals/261020-phase-4-design.md).

One page and a few JSON endpoints, standard library only, 127.0.0.1 only. It reads through the local API's read
methods and shows the views the server already built. It cannot change anything: the proxy is an allowlist of read
methods and every non-GET request is refused. Access needs a random token (carried in a cookie after the first
visit), the Host header must be the bound address (DNS rebinding), and cross-site requests are refused.
"""
from __future__ import annotations

import argparse
import hmac
import http.server
import json
import re
import secrets
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import web_assets  # noqa: E402

COOKIE = "rig_web"
JOB_ID = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
ASSETS = {"/": ("text/html; charset=utf-8", web_assets.INDEX_HTML),
          "/app.js": ("text/javascript; charset=utf-8", web_assets.APP_JS),
          "/app.css": ("text/css; charset=utf-8", web_assets.APP_CSS)}


class BadParams(ValueError):
    pass


def _int(query, name, default, low, high):
    raw = query.get(name, [str(default)])[0]
    if not re.fullmatch(r"\d{1,7}", raw) or not low <= int(raw) <= high:
        raise BadParams(f"{name} must be a number from {low} to {high}")
    return int(raw)


def _job_id(query, required):
    raw = query.get("id", [""])[0]
    if raw and not JOB_ID.fullmatch(raw):
        raise BadParams("id must be a job id")
    if required and not raw:
        raise BadParams("id is required")
    return raw


def _chat_session(query):
    raw = query.get("session", [""])[0]
    if not re.fullmatch(r"c-[0-9a-f]{8}", raw):
        raise BadParams("session must look like c-1a2b3c4d")
    return raw


def _boolean(query, name):
    raw = query.get(name, ["false"])[0]
    if raw not in ("true", "false"):
        raise BadParams(f"{name} must be true or false")
    return raw == "true"


# The only methods reachable from the browser. Each entry validates its own query string, so nothing the page
# sends is passed on unchecked, and none of them mutates anything.
READ_METHODS = {
    "api.ping": lambda q: {},
    "board.snapshot": lambda q: {"rows": _int(q, "rows", 100, 0, 500), "start": _int(q, "start", 0, 0, 1_000_000)},
    "workspace.status": lambda q: {"deep": _boolean(q, "deep")},
    "fleet.status": lambda q: {},
    "chat.status": lambda q: {},
    "chat.transcript": lambda q: {"session": _chat_session(q), "tail": _int(q, "tail", 200, 1, 5000)},
    "job.evidence": lambda q: {"id": _job_id(q, True)},
    "log.follow": lambda q: {"id": _job_id(q, False), "lines": _int(q, "lines", 60, 1, 500)},
}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "RigWeb"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # the access log would echo tokens and job ids
        pass

    # -- plumbing ---------------------------------------------------------
    def _send(self, status, body: bytes, content_type="application/json", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, value, extra=()):
        self._send(status, json.dumps(value).encode(), extra=extra)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"}

    def _cookie_ok(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and hmac.compare_digest(value.encode(), self.server.token.encode()):
                return True
        return False

    def _refuse_method(self):
        self._json(405, {"message": "this board is read-only"}, extra=[("Allow", "GET, HEAD")])

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse_method

    # -- GET ----------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._json(421, {"message": "unexpected Host header"})
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in {"same-origin", "none"}:
            return self._json(403, {"message": "cross-site requests are refused"})
        parts = urlsplit(self.path)
        query = parse_qs(parts.query, keep_blank_values=True)
        if parts.path == "/" and "token" in query:  # first visit: trade the token for a cookie, drop it from the URL
            if hmac.compare_digest(query["token"][0].encode(), self.server.token.encode()):
                return self._send(302, b"", "text/plain", extra=[
                    ("Location", "/"), ("Set-Cookie", f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/")])
            return self._json(401, {"message": "wrong token"})
        if not self._cookie_ok():
            return self._send(401, b"Open the URL that rig web printed (it carries the access token).\n", "text/plain; charset=utf-8")
        if parts.path in ASSETS:
            kind, body = ASSETS[parts.path]
            return self._send(200, body.encode(), kind)
        if parts.path.startswith("/api/"):
            return self._api(parts.path[5:], query)
        self._json(404, {"message": "not found"})

    def _api(self, method, query):
        build = READ_METHODS.get(method)
        if build is None:
            return self._json(404, {"message": f"no such read method: {method}"})
        try:
            params = build(query)
        except BadParams as error:
            return self._json(400, {"message": str(error)})
        import local_api
        try:
            reply = local_api.call(self.server.repo, method, params, timeout=self.server.timeout_for(method))
        except (ConnectionError, OSError, ValueError) as error:
            return self._json(502, {"message": f"the Rig API did not answer: {error}"})
        self._json(200, reply)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, repo, port=0, token=None):
        self.repo = Path(repo).resolve()
        self.token = token or secrets.token_urlsafe(32)
        self.last_request = time.monotonic()
        super().__init__(("127.0.0.1", port), Handler)  # loopback only; there is no host option on purpose
        self.port = self.server_address[1]

    @staticmethod
    def timeout_for(method):
        return 25.0 if method in ("workspace.status", "fleet.status", "job.evidence") else 8.0

    def url(self):
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def process_request(self, request, client_address):
        self.last_request = time.monotonic()
        super().process_request(request, client_address)


def serve(repo, port=0, idle=0.0, open_browser=False, out=sys.stdout) -> int:
    import local_api
    try:
        local_api.ensure(repo)
    except OSError:
        pass
    server = Server(repo, port)
    print(f"rig web: {server.url()}", file=out, flush=True)
    print("rig web: read-only, on 127.0.0.1 only; Ctrl-C stops it", file=out, flush=True)
    if open_browser:
        webbrowser.open(server.url())
    if idle > 0:
        def watch():
            while True:
                time.sleep(min(idle, 5))
                if time.monotonic() - server.last_request > idle:
                    server.shutdown()
                    return
        threading.Thread(target=watch, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig web", description="A read-only board in the browser (127.0.0.1 only)")
    parser.add_argument("--repo", default="")
    parser.add_argument("--port", type=int, default=0, help="port to listen on (default: a free one)")
    parser.add_argument("--idle", type=float, default=0.0, help="exit after this many seconds without a request")
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    args = parser.parse_args(argv)
    try:
        import jobs
        repo = jobs.repo_root(args.repo or None)
        import harness
        harness.assert_project_enabled(repo)
        return serve(repo, args.port, args.idle, args.open)
    except (OSError, ValueError, SystemExit) as error:
        print(f"rig web: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
