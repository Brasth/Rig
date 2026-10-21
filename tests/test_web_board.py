#!/usr/bin/env python3
"""`rig web`: the read-only board. Security of the server, the shipped assets, a real API behind it, and a real browser."""
import glob
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import local_api  # noqa: E402
import web_assets  # noqa: E402
import web_board  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "dc"


def make_repo(root: Path, extra: str = "") -> Path:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".rig").mkdir(exist_ok=True)
    (root / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n\n[workers]\nclaude = true\n" + extra)
    return root


class Served(unittest.TestCase):
    """A web server in a thread whose upstream is replaced, so only the web layer is under test."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.td.name))
        self.calls = []
        self.reply = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

        def fake_call(repo, method, params=None, timeout=10.0):
            self.calls.append((method, params))
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

        self.patch = mock.patch.object(local_api, "call", fake_call)
        self.patch.start()
        self.server = web_board.Server(self.repo, token="tok" * 11)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = {"Cookie": f"rig_web={self.server.token}"}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.patch.stop()
        self.td.cleanup()

    def get(self, path, headers=None, method="GET", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.server.port}")
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body


class Access(Served):
    def test_it_listens_on_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_no_cookie_means_no_page_and_no_data(self):
        for path in ("/", "/app.js", "/api/board.snapshot"):
            response, body = self.get(path)
            self.assertEqual(response.status, 401, path)
            self.assertNotIn(b"Rig board", body)
        self.assertEqual(self.calls, [], "the API is not touched without access")

    def test_the_token_is_traded_for_an_httponly_strict_cookie_and_dropped_from_the_url(self):
        response, _ = self.get(f"/?token={self.server.token}")
        self.assertEqual((response.status, response.getheader("Location")), (302, "/"))
        cookie = response.getheader("Set-Cookie")
        self.assertIn(f"rig_web={self.server.token}", cookie)
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)

    def test_a_wrong_token_or_cookie_is_refused(self):
        self.assertEqual(self.get("/?token=nope")[0].status, 401)
        self.assertEqual(self.get("/", {"Cookie": "rig_web=nope"})[0].status, 401)
        self.assertEqual(self.get("/", {"Cookie": f"other=1; rig_web={self.server.token}"})[0].status, 200)

    def test_a_foreign_host_header_is_refused_even_with_the_cookie(self):
        for host in ("evil.example", f"evil.example:{self.server.port}", "127.0.0.1", f"127.0.0.1.evil.example:{self.server.port}"):
            response, _ = self.get("/", self.cookie, host=host)
            self.assertEqual(response.status, 421, host)
        self.assertEqual(self.get("/", self.cookie, host=f"localhost:{self.server.port}")[0].status, 200)

    def test_cross_site_requests_are_refused(self):
        for site in ("cross-site", "same-site"):
            self.assertEqual(self.get("/api/api.ping", {**self.cookie, "Sec-Fetch-Site": site})[0].status, 403, site)
        for site in ("same-origin", "none"):
            self.assertEqual(self.get("/api/api.ping", {**self.cookie, "Sec-Fetch-Site": site})[0].status, 200, site)


class ReadOnly(Served):
    def test_every_write_verb_is_refused(self):
        for verb in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            response, _ = self.get("/api/job.cancel", self.cookie, method=verb)
            self.assertEqual(response.status, 405, verb)
            self.assertEqual(response.getheader("Allow"), "GET, HEAD")
        self.assertEqual(self.calls, [])

    def test_no_mutating_api_method_is_reachable_by_any_name(self):
        mutating = [n for n, (_, m, _) in local_api.METHODS.items() if m] + [n for n, (m, _) in local_api.LOCAL_METHODS.items() if m]
        self.assertIn("job.cancel", mutating)
        self.assertIn("env.recover", mutating)
        for name in mutating:
            response, _ = self.get(f"/api/{name}?id=x&confirm_id=y", self.cookie)
            self.assertEqual(response.status, 404, name)
        self.assertEqual(self.calls, [], "none of them reached the API")

    def test_the_allowlist_holds_only_read_methods_that_exist(self):
        readonly = {n for n, (_, m, _) in local_api.METHODS.items() if not m} | {n for n, (m, _) in local_api.LOCAL_METHODS.items() if not m}
        self.assertLessEqual(set(web_board.READ_METHODS), readonly)

    def test_each_read_method_is_forwarded_with_validated_parameters(self):
        cases = {"/api/api.ping": ("api.ping", {}),
                 "/api/board.snapshot?rows=50": ("board.snapshot", {"rows": 50, "start": 0}),
                 "/api/workspace.status?deep=true": ("workspace.status", {"deep": True}),
                 "/api/fleet.status": ("fleet.status", {}),
                 "/api/job.evidence?id=job-7f2": ("job.evidence", {"id": "job-7f2"}),
                 "/api/log.follow?id=job-7f2&lines=20": ("log.follow", {"id": "job-7f2", "lines": 20}),
                 "/api/log.follow": ("log.follow", {"id": "", "lines": 60})}
        for path, expected in cases.items():
            self.calls.clear()
            response, body = self.get(path, self.cookie)
            self.assertEqual(response.status, 200, path)
            self.assertEqual(self.calls, [expected], path)
            self.assertEqual(json.loads(body), self.reply)

    def test_bad_parameters_never_reach_the_api(self):
        for path in ("/api/board.snapshot?rows=-1", "/api/board.snapshot?rows=9999", "/api/board.snapshot?rows=abc",
                     "/api/workspace.status?deep=maybe", "/api/job.evidence", "/api/job.evidence?id=../etc/passwd",
                     "/api/job.evidence?id=" + "a" * 201, "/api/log.follow?lines=0", "/api/log.follow?id=a%20b"):
            response, body = self.get(path, self.cookie)
            self.assertEqual(response.status, 400, path)
            self.assertIn("message", json.loads(body))
        self.assertEqual(self.calls, [])

    def test_unknown_paths_and_traversal_attempts(self):
        for path in ("/nope", "/api/", "/api/nope", "/../etc/passwd", "/app.js/../../etc/passwd", "//etc/passwd"):
            self.assertIn(self.get(path, self.cookie)[0].status, (404, 400), path)

    def test_an_unreachable_api_is_a_502_with_a_reason(self):
        self.reply = ConnectionError("Rig API is not running")
        response, body = self.get("/api/api.ping", self.cookie)
        self.assertEqual(response.status, 502)
        self.assertIn("did not answer", json.loads(body)["message"])


class Headers(Served):
    def test_security_headers_on_every_kind_of_response(self):
        for path, headers in (("/", self.cookie), ("/app.js", self.cookie), ("/api/api.ping", self.cookie), ("/", {}), ("/nope", self.cookie)):
            response, _ = self.get(path, headers)
            csp = response.getheader("Content-Security-Policy")
            self.assertIn("default-src 'none'", csp, path)
            self.assertIn("script-src 'self'", csp)
            self.assertNotIn("unsafe-inline", csp)
            self.assertNotIn("unsafe-eval", csp)
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"), "no CORS headers, ever")

    def test_content_types_and_head(self):
        self.assertTrue(self.get("/", self.cookie)[0].getheader("Content-Type").startswith("text/html"))
        self.assertTrue(self.get("/app.js", self.cookie)[0].getheader("Content-Type").startswith("text/javascript"))
        self.assertTrue(self.get("/app.css", self.cookie)[0].getheader("Content-Type").startswith("text/css"))
        response, body = self.get("/", self.cookie, method="HEAD")
        self.assertEqual((response.status, body), (200, b""))


class Assets(unittest.TestCase):
    def test_no_html_injection_or_dynamic_code_and_no_external_loads(self):
        for name, text in (("index", web_assets.INDEX_HTML), ("js", web_assets.APP_JS), ("css", web_assets.APP_CSS)):
            for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout('",
                           "http://", "https://", "@import", "XMLHttpRequest", "WebSocket", "sendBeacon"):
                self.assertNotIn(banned, text, f"{name} must not use {banned}")
        self.assertNotRegex(web_assets.INDEX_HTML, r"<script(?![^>]*src=)", "no inline script")
        self.assertNotRegex(web_assets.INDEX_HTML, r"\son[a-z]+=", "no inline handlers")

    def test_the_page_only_fetches_its_own_api_with_get(self):
        calls = re.findall(r"fetch\(([^;]*?)\)\.then", web_assets.APP_JS, re.S)
        self.assertEqual(len(calls), 1)
        self.assertIn("'/api/'", calls[0])
        self.assertNotRegex(calls[0], r"\b(method|body|headers)\s*:", "a plain GET with no body")

    def test_worker_text_only_ever_goes_in_as_text(self):
        self.assertGreater(len(re.findall(r"textContent", web_assets.APP_JS)), 5)
        self.assertNotRegex(web_assets.APP_JS, r"\.(inner|outer)HTML|\.src\s*=|\.href\s*=")


class ApiFixture:
    """A real local API server for a real repository with one running job, and the web server in front of it."""

    DOING = "reading the code"
    HARNESS_EXTRA = ""

    def start(self, base: Path):
        self.repo = make_repo(base / "repo", self.HARNESS_EXTRA).resolve()
        job = self.repo / ".rig" / "jobs" / "job-7"
        job.mkdir(parents=True)
        (job / "meta.json").write_text(json.dumps({"job_id": "job-7", "worker": "claude", "role": "worker", "status": "running",
                                                   "files": ["a.txt"], "dir": str(job), "started": "2026-10-20T08:00:00Z",
                                                   "doing": self.DOING}))
        (job / "stdout.log").write_text("starting\nworking <b>hard</b>\n")
        (job / "change-evidence.json").write_text(json.dumps({"scoped_changed_paths": ["a.txt"], "out_of_scope_observations": []}))
        env = {k: v for k, v in os.environ.items() if k not in {"RIG_JOB_ID", "RIG_JOB_DIR"}}
        self.api = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "local_api.py"), "serve", "--repo", str(self.repo)],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                local_api.call(self.repo, "api.ping", timeout=0.5)
                break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("API did not start")
        self.server = web_board.Server(self.repo)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cookie = {"Cookie": f"rig_web={self.server.token}"}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        if self.api.poll() is None:
            self.api.terminate()
        self.api.wait(timeout=10)


class RealApi(ApiFixture, unittest.TestCase):
    """The web layer in front of a real local API server for a real repository."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.start(Path(self.td.name))

    def tearDown(self):
        self.stop()
        self.td.cleanup()

    def fetch(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=15)
        conn.request("GET", path, headers=self.cookie)
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()
        return response.status, body

    def test_the_board_log_and_evidence_come_through_unchanged(self):
        status, body = self.fetch("/api/board.snapshot?rows=50")
        self.assertEqual(status, 200)
        view = body["result"]["view"]
        self.assertEqual([row["id"] for row in view["jobs"]], ["job-7"])
        direct = local_api.call(self.repo, "board.snapshot", {"rows": 50, "start": 0})["result"]["view"]
        self.assertEqual(view["jobs"][0]["label"], direct["jobs"][0]["label"], "the same server-built view, not a copy")
        status, body = self.fetch("/api/log.follow?id=job-7&lines=5")
        self.assertIn("working <b>hard</b>", body["result"]["text"])
        status, body = self.fetch("/api/job.evidence?id=job-7")
        self.assertEqual([s["title"] for s in body["result"]["sections"]], ["Ran", "Change", "Contract", "Checks", "Ports"])
        self.assertEqual(self.fetch("/api/job.evidence?id=ghost")[1]["error"]["code"], local_api.INVALID_PARAMS)

    def test_the_chat_experiment_reads_as_off_and_its_transcript_is_not_served(self):
        status, body = self.fetch("/api/chat.status")
        self.assertEqual((status, body["result"]["enabled"], body["result"]["sessions"]), (200, False, []))
        status, body = self.fetch("/api/chat.transcript?session=c-00000000")
        self.assertIn("experiment is off", body["error"]["message"])
        self.assertEqual(self.fetch("/api/chat.transcript?session=bad")[0], 400)

    def test_a_token_in_a_log_never_reaches_the_browser(self):
        (self.repo / ".rig" / "jobs" / "job-7" / "stdout.log").write_text('{"owner_token": "abcdef0123456789abcdef"}\n')
        self.assertNotIn("abcdef0123456789abcdef", json.dumps(self.fetch("/api/log.follow?id=job-7")[1]))


def find_chrome():
    for candidate in [os.environ.get("RIG_TEST_CHROME", ""), *sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")),
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or "", shutil.which("google-chrome") or ""]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


FAKE_DC = r'''#!/bin/sh
F="$FAKE_FIXTURES"
san() { sed "s#/work/repo#$FAKE_REPO#g"; }
case "$1" in
  --version) echo "dc 0.23.0" ;;
  ls) if [ "$2" = "--all" ]; then san < $F/ls-all.json; else san < $F/ls.json; fi ;;
  stats) san < $F/stats.json ;;
  forward) san < $F/forward-status.txt ;;
  doctor) san < $F/doctor-ok.json ;;
  recover) san < $F/recover-ready.json; exit 1 ;;
  *) exit 64 ;;
esac
'''


@unittest.skipUnless(find_chrome(), "no Chromium found (set RIG_TEST_CHROME)")
class RealBrowser(ApiFixture, unittest.TestCase):
    """Headless Chromium loads the page for real: cookie hand-off, fetches, scripts, DOM."""

    DOING = "<img src=x onerror=document.title='pwned'> & <script>document.title='pwned'</script>"
    HARNESS_EXTRA = "\n[experiments]\nchat = true\n"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        fake = base / "dc"
        fake.write_text(FAKE_DC)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env_patch = mock.patch.dict(os.environ, {"RIG_DC_BIN": str(fake), "FAKE_FIXTURES": str(FIXTURES),
                                                      "FAKE_REPO": str((base / "repo").resolve())})
        self.env_patch.start()
        (base / "repo").mkdir()  # resolved above so the fake dc's fixture paths match the repository
        self.start(base)

    def tearDown(self):
        self.stop()
        self.env_patch.stop()
        self.td.cleanup()

    def dom(self, fragment=""):
        done = subprocess.run([find_chrome(), "--headless", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=8000", "--dump-dom",
                               self.server.url() + fragment], capture_output=True, text=True, timeout=90, cwd=self.td.name)
        return done.stdout

    @staticmethod
    def text(dom):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dom))

    def test_the_jobs_tab_renders_from_the_server_built_view(self):
        text = self.text(self.dom())
        for expected in ("Rig · repo", "read-only", "Jobs 1", "● Running", "job-7", "slots 1/3"):
            self.assertIn(expected, text)

    def test_worker_text_cannot_become_markup(self):
        dom = self.dom()
        self.assertNotIn("<img", dom)
        self.assertNotIn("pwned</title>", dom)
        self.assertIn("&lt;img src=x onerror=", dom, "shown as text, escaped by the serializer")
        self.assertNotRegex(dom, r"<script>document\.title")

    def test_the_evidence_deep_link_opens_the_drawer(self):
        text = self.text(self.dom("#Jobs/job-7/evidence"))
        for expected in ("Evidence · job-7", "Ran", "Change", "a.txt", "Contract", "Checks", "Ports"):
            self.assertIn(expected, text)

    def test_the_other_tabs_render(self):
        self.assertIn("This repository", self.text(self.dom("#Fleet")))
        self.assertIn("dc-cli", self.text(self.dom("#Workspace")))
        logs = self.text(self.dom("#Logs"))
        self.assertIn("Logs · job-7", logs)
        self.assertIn("working &lt;b&gt;hard&lt;/b&gt;", logs, "log text is shown literally (the serializer escapes it)")

    def test_the_chat_tab_appears_with_the_experiment_and_shows_a_transcript_as_text(self):
        import rig_chat
        with mock.patch.dict(os.environ, {"RIG_EXPERIMENT_CHAT": ""}):
            meta = rig_chat.new_session(self.repo, "claude")
            rig_chat.append(self.repo, meta["id"], "user", "what is <b>in</b> a.txt?")
            rig_chat.append(self.repo, meta["id"], "assistant", "It says one.")
            rig_chat.append(self.repo, meta["id"], "tool", "Read /repo/a.txt")
        dom = self.dom("#Chat")
        text = self.text(dom)
        for expected in ("Chat", "EXPERIMENT", "you › what is &lt;b&gt;in&lt;/b&gt; a.txt?", "claude › It says one.", "tool › Read /repo/a.txt", meta["id"]):
            self.assertIn(expected, text)
        self.assertNotIn("<b>in</b>", dom)
        self.assertNotIn("send-box", dom.lower(), "this page has no way to send")

    def test_a_dead_api_keeps_the_page_and_says_so(self):
        self.api.terminate()
        self.api.wait(timeout=10)
        text = self.text(self.dom())
        self.assertIn("refresh failed", text)
        self.assertIn("no snapshot yet", text)


class Cli(unittest.TestCase):
    def test_prints_the_url_serves_it_and_exits_when_idle(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "web_board.py"), "--repo", str(repo), "--idle", "1"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    env={k: v for k, v in os.environ.items() if k not in {"RIG_JOB_ID", "RIG_JOB_DIR"}})
            try:
                line = proc.stdout.readline().strip()
                match = re.fullmatch(r"rig web: (http://127\.0\.0\.1:(\d+)/\?token=[A-Za-z0-9_-]{40,})", line)
                self.assertTrue(match, line)
                conn = http.client.HTTPConnection("127.0.0.1", int(match.group(2)), timeout=5)
                conn.request("GET", "/?token=" + match.group(1).split("token=")[1])
                self.assertEqual(conn.getresponse().status, 302)
                proc.wait(timeout=20)
                self.assertEqual(proc.returncode, 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                for pid_line in subprocess.run(["pgrep", "-f", "local_api.py serve --repo " + str(repo)], capture_output=True, text=True).stdout.split():
                    try:
                        os.kill(int(pid_line), 15)
                    except OSError:
                        pass

    def test_a_disabled_project_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            done = subprocess.run([sys.executable, str(ROOT / "scripts" / "web_board.py"), "--repo", td], capture_output=True, text=True,
                                  env={k: v for k, v in os.environ.items() if k not in {"RIG_JOB_ID", "RIG_JOB_DIR"}}, timeout=30)
        self.assertNotEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()
