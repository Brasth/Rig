#!/usr/bin/env python3
"""Adapter packages: manifest rules, command expansion, discovery, harness and shell integration, conformance."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import adapter_check  # noqa: E402
import adapters  # noqa: E402

EXAMPLES = ROOT / "examples" / "adapters"
FIXTURES = ROOT / "tests" / "fixtures" / "adapters"


def manifest(**overrides):
    base = {"schema": 1, "name": "acme", "command": ["{binary}", "run", "--cwd", "{work}", "--prompt-file", "{brief_file}"],
            "mcp": {"mode": "flag", "argv": ["--mcp-config", "{mcp_path}"]}}
    base.update(overrides)
    return base


def write_adapter(root, **overrides):
    raw = manifest(**overrides)
    folder = Path(root) / raw["name"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "adapter.json").write_text(json.dumps(raw))
    return folder


class Validation(unittest.TestCase):
    def bad(self, pattern, **overrides):
        raw = manifest(**{k: v for k, v in overrides.items() if v is not None})
        for key, value in overrides.items():
            if value is None:
                raw.pop(key)
        with self.assertRaisesRegex(adapters.AdapterError, pattern):
            adapters.validate(raw)

    def test_a_minimal_manifest_is_valid_and_normalized(self):
        good = adapters.validate(manifest())
        self.assertEqual((good["binary"], good["output"], good["container"]["supported"]), ("acme", {"decoder": "plain"}, False))
        self.assertEqual(good["permissions"], {"host": [], "container": []})

    def test_the_rules(self):
        self.bad("unknown key 'extra'", extra=1)
        self.bad("missing required key 'mcp'", mcp=None)
        self.bad("schema must be 1", schema=2)
        self.bad("schema must be 1", schema=True)
        self.bad("name must be", name="Acme")
        self.bad("name must be", name="a")
        self.bad("built-in worker", name="claude")
        self.bad("built-in worker", name="parent")
        self.bad("binary must be a plain executable name", binary="/usr/bin/acme")
        self.bad("binary must be a plain executable name", binary="../acme")
        self.bad("unknown placeholder {nope}", command=["{binary}", "{nope}"])
        self.bad("command must be a non-empty list", command="acme run")
        self.bad("command must be a non-empty list", command=[])
        self.bad("newlines", command=["{binary}", "a\nb"])
        self.bad("cannot use the splice token", model_args=["{mcp}"])
        self.bad(r"mcp must be", mcp={"mode": "socket"})
        self.bad("must name {mcp_path}", mcp={"mode": "flag", "argv": ["--mcp-config", "x.json"]})
        self.bad("belongs to mode env", mcp={"mode": "flag", "argv": ["{mcp_path}"], "env": {"A": "{mcp_path}"}})
        self.bad("UPPER_CASE", mcp={"mode": "env", "env": {"lower": "{mcp_path}"}})
        self.bad("must name {mcp_path}", mcp={"mode": "env", "env": {"A": "x"}})
        self.bad("permissions may only", permissions={"sandbox": ["--x"]})
        self.bad("relative paths", container={"supported": True, "credential_paths": ["/etc"]})
        self.bad("relative paths", container={"supported": True, "credential_paths": ["../x"]})
        self.bad("container must be", container={"supported": "yes"})
        self.bad("output must be", output={"decoder": "xml"})
        self.bad("dotted path", output={"decoder": "jsonl"})
        self.bad("must not name a secret", permissions={"host": ["--api-key"]})
        self.bad("must not name a secret", command=["{binary}", "--token=abc"])

    def test_directory_must_match_the_name(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "other"
            folder.mkdir()
            (folder / "adapter.json").write_text(json.dumps(manifest()))
            with self.assertRaisesRegex(adapters.AdapterError, "must match the adapter name"):
                adapters.load_dir(folder)


class Command(unittest.TestCase):
    def build(self, raw=None, **kwargs):
        args = dict(mode="host", work="/w", brief_file="/b.md", brief_text="do it", model="", effort="", job_dir="/j", job_id="j1",
                    mcp_path="/j/mcp.json")
        args.update(kwargs)
        return adapters.build_command(adapters.validate(raw or manifest()), **args)

    def test_missing_splice_tokens_are_appended_so_flags_cannot_be_forgotten(self):
        raw = manifest(model_args=["--model", "{model}"], permissions={"host": ["--yes"], "container": ["--ci"]})
        self.assertEqual(self.build(raw, model="m1"),
                         ["acme", "run", "--cwd", "/w", "--prompt-file", "/b.md", "--model", "m1", "--mcp-config", "/j/mcp.json", "--yes"])
        self.assertEqual(self.build(raw), ["acme", "run", "--cwd", "/w", "--prompt-file", "/b.md", "--mcp-config", "/j/mcp.json", "--yes"],
                         "no model, no model flags")

    def test_splice_tokens_land_where_the_manifest_puts_them(self):
        raw = manifest(command=["{binary}", "{permissions}", "go", "{mcp}", "{brief_text}"], permissions={"host": ["-y"]})
        self.assertEqual(self.build(raw), ["acme", "-y", "go", "--mcp-config", "/j/mcp.json", "do it"])

    def test_the_brief_text_stays_one_argument(self):
        raw = manifest(command=["{binary}", "{brief_text}"])
        self.assertEqual(self.build(raw, brief_text="a b; rm -rf / $(x) 'q'")[1], "a b; rm -rf / $(x) 'q'")

    def test_env_mode_adds_no_argv_and_exports_the_path(self):
        raw = manifest(mcp={"mode": "env", "env": {"ACME_MCP": "{mcp_path}"}})
        parsed = adapters.validate(raw)
        self.assertNotIn("--mcp-config", self.build(raw))
        self.assertEqual(adapters.mcp_env(parsed, "/j/mcp.json"), {"ACME_MCP": "/j/mcp.json"})

    def test_container_mode_needs_declared_support_and_uses_its_own_flags(self):
        with self.assertRaisesRegex(adapters.AdapterError, "does not support container mode"):
            self.build(mode="container")
        raw = manifest(container={"supported": True}, permissions={"host": ["--h"], "container": ["--c"]})
        self.assertEqual(self.build(raw, mode="container")[-1], "--c")
        self.assertEqual(self.build(raw)[-1], "--h")

    def test_binary_override_is_the_resolved_path(self):
        self.assertEqual(self.build(binary="/opt/bin/acme")[0], "/opt/bin/acme")


class Decoder(unittest.TestCase):
    def test_plain_takes_the_last_non_empty_line(self):
        parsed = adapters.validate(manifest())
        self.assertEqual(adapters.decode_summary(parsed, "start\nall done\n\n"), "all done")
        self.assertEqual(adapters.decode_summary(parsed, ""), "")

    def test_jsonl_reads_a_dotted_field_from_the_last_line_that_has_it(self):
        parsed = adapters.validate(manifest(output={"decoder": "jsonl", "result_field": "final.text"}))
        log = '{"final": {"text": "first"}}\n{"other": 1}\nnot json\n{"final": {"text": "second"}}\n{"x": 2}\n'
        self.assertEqual(adapters.decode_summary(parsed, log), "second")
        self.assertEqual(adapters.decode_summary(parsed, '{"x": 1}\n'), "")


class Discovery(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.env = mock.patch.dict(os.environ, {"RIG_ADAPTER_DIRS": str(self.root), "XDG_CONFIG_HOME": str(self.root / "xdg")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def test_found_by_name_with_problems_reported_not_fatal(self):
        write_adapter(self.root, name="good-one")
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "adapter.json").write_text("{not json")
        wrong = self.root / "wrong"
        wrong.mkdir()
        (wrong / "adapter.json").write_text(json.dumps(manifest(name="claude")))
        found, problems = adapters.discover()
        self.assertEqual(sorted(found), ["good-one"])
        self.assertEqual(sorted(Path(p).name for p in problems), ["broken", "wrong"])
        self.assertIn("built-in worker", next(r for p, r in problems.items() if p.endswith("wrong")))

    def test_a_built_in_cannot_be_shadowed_and_duplicates_are_refused(self):
        other = self.root / "second"
        other.mkdir()
        write_adapter(self.root, name="acme")
        write_adapter(other, name="acme")
        with mock.patch.dict(os.environ, {"RIG_ADAPTER_DIRS": f"{self.root}{os.pathsep}{other}"}):
            found, problems = adapters.discover()
        self.assertEqual(sorted(found), ["acme"])
        self.assertTrue(any("duplicate adapter 'acme'" in r for r in problems.values()))

    def test_the_user_config_directory_is_searched(self):
        write_adapter(self.root / "xdg" / "rig" / "adapters", name="from-user")
        self.assertIn("from-user", adapters.names())

    def test_cli_names_exists_and_validate(self):
        write_adapter(self.root, name="acme")
        env = dict(os.environ)
        run = lambda *a: subprocess.run([sys.executable, str(ROOT / "scripts" / "adapters.py"), *a], capture_output=True, text=True, env=env)  # noqa: E731
        self.assertEqual(run("names", "--exists", "acme").returncode, 0)
        self.assertEqual(run("names", "--exists", "ghost").returncode, 1)
        self.assertEqual(run("validate", str(self.root / "acme")).stdout.strip(), "ok: acme (acme)")
        self.assertEqual(run("validate", str(self.root / "ghost")).returncode, 1)
        self.assertEqual(run("binary", "--worker", "acme").stdout.strip(), "acme")


class Integration(unittest.TestCase):
    """The rest of Rig sees an adapter worker like any other."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        write_adapter(self.root, name="acme-agent", binary="acme-cli")
        self.env = dict(os.environ, RIG_ADAPTER_DIRS=str(self.root))

    def tearDown(self):
        self.td.cleanup()

    def py(self, code):
        return subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n%s" % (str(ROOT / "scripts"), code)],
                              capture_output=True, text=True, env=self.env)

    def test_the_worker_lists_include_adapters_after_the_built_ins(self):
        done = self.py("import harness; print(harness.WORKERS[-1], len(harness.BUILTIN_WORKERS), harness.WORKERS[:3])")
        self.assertEqual(done.stdout.split()[0], "acme-agent")
        self.assertIn("grok", done.stdout)

    def test_harness_parses_the_enable_flag_and_finds_the_adapter_binary(self):
        repo = self.root / "repo"
        (repo / ".rig").mkdir(parents=True)
        (repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n\n[workers]\nacme-agent = true\n")
        bindir = self.root / "bin"
        bindir.mkdir()
        (bindir / "acme-cli").write_text("#!/bin/sh\n")
        (bindir / "acme-cli").chmod(0o755)
        self.env["PATH"] = f"{bindir}{os.pathsep}{os.environ['PATH']}"
        done = self.py(f"import harness, pathlib; p = harness.parse_harness(pathlib.Path({str(repo / '.rig' / 'harness.toml')!r})); "
                       "print(p['workers']['acme-agent'], harness.find_worker_bin('acme-agent'))")
        self.assertEqual(done.stdout.split()[0], "true")
        self.assertTrue(done.stdout.split()[1].endswith("acme-cli"), done.stdout)

    def test_the_shell_reads_the_flag_and_the_binary_for_dashed_names(self):
        repo = self.root / "repo"
        (repo / ".rig").mkdir(parents=True)
        (repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n\n[workers]\nacme-agent = true\nclaude = true\n")
        bindir = self.root / "bin"
        bindir.mkdir()
        (bindir / "acme-cli").write_text("#!/bin/sh\n")
        (bindir / "acme-cli").chmod(0o755)
        script = (f"cd {repo} && export PATH={bindir}:$PATH RIG_HOME={ROOT} && source {ROOT}/scripts/detect-binaries.sh && "
                  "echo \"$(worker_flag acme-agent) $(worker_flag claude) $(worker_flag ghost-worker) $(find_worker_bin acme-agent)\"")
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=self.env)
        self.assertEqual(done.stdout.split()[:3], ["true", "true", "false"], done.stderr)
        self.assertTrue(done.stdout.split()[3].endswith("acme-cli"))

    def test_job_and_launch_validation_accept_it_and_still_refuse_unknown_workers(self):
        done = self.py("import jobs, worker_launch\nprint(jobs._validate_worker('acme-agent'))\n"
                       "try:\n    jobs._validate_worker('ghost-worker')\nexcept SystemExit as e:\n    print('refused')")
        self.assertEqual(done.stdout.split(), ["acme-agent", "refused"], done.stderr)

    def test_container_modes_follow_the_manifest(self):
        write_adapter(self.root, name="boxed", container={"supported": True, "credential_paths": [".boxed"]})
        done = self.py("import container_modes as c\nprint(c.mode('boxed')['credential_paths'], c.mode('acme-agent')['supported'], "
                       "c.supported_workers()[-1], c.credential_problem('boxed', {}) [:40])")
        self.assertIn("['.boxed'] False boxed", done.stdout)
        self.assertIn("has no credential choice", done.stdout)

    def test_doctor_reports_adapters_and_broken_ones(self):
        repo = self.root / "repo"
        (repo / ".rig").mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / ".rig" / "harness.toml").write_text("[project]\nenabled = true\n")
        code = (f"import doctor, pathlib\nrows = [c for c in doctor.build_report(pathlib.Path({str(repo)!r}))['checks'] if c['name'] == 'adapters']\n"
                "print(rows[0]['status'], rows[0]['detail'])")
        ok = self.py(code)
        self.assertTrue(ok.stdout.startswith("ready adapter workers: acme-agent"), ok.stdout + ok.stderr)
        (self.root / "bad").mkdir()
        (self.root / "bad" / "adapter.json").write_text("{}")
        bad = self.py(code)
        self.assertTrue(bad.stdout.startswith("failed 1 adapter package(s) are invalid: bad:"), bad.stdout + bad.stderr)


class Conformance(unittest.TestCase):
    """The same checks `rig adapters check` runs, over the shipped example and the fixture adapters."""

    def run_check(self, directory):
        results = adapter_check.check(directory)
        failed = [r.line() for r in results if not r.ok]
        self.assertEqual(failed, [], "\n".join(r.line() for r in results))
        return {r.name: r for r in results}

    def test_the_example_adapter_conforms_end_to_end(self):
        results = self.run_check(EXAMPLES / "echo-agent")
        for name in ("manifest", "dry-run argv", "job-scoped MCP payload", "live job", "MCP handshake", "output decoder",
                     "cancel stops the process tree", "container mode needs a credential choice"):
            self.assertIn(name, results)

    def test_the_deepseek_adapter_conforms_and_matches_what_rig_dsh_installs(self):
        import rig_dsh
        self.run_check(EXAMPLES / "deepseek")
        self.assertEqual(json.loads((EXAMPLES / "deepseek" / "adapter.json").read_text()), rig_dsh.MANIFEST)

    def test_an_env_delivered_plain_output_adapter_conforms(self):
        self.run_check(FIXTURES / "env-plain")

    def test_a_broken_manifest_fails_the_check_with_the_reason(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "acme"
            folder.mkdir()
            (folder / "adapter.json").write_text(json.dumps(manifest(mcp={"mode": "flag", "argv": ["--x"]})))
            results = adapter_check.check(folder)
        self.assertEqual([(r.name, r.ok) for r in results], [("manifest", False)])
        self.assertIn("must name {mcp_path}", results[0].detail)

    def test_a_worker_that_does_not_use_the_mcp_config_is_caught_by_the_handshake_check(self):
        """The manifest names the config, but this CLI never reads it, so no handshake happens."""
        with tempfile.TemporaryDirectory() as td:
            folder = write_adapter(td, name="deaf-agent", command=["{binary}", "run"],
                                   mcp={"mode": "env", "env": {"DEAF_NOTES": "{mcp_path}.unused"}})
            results = {r.name: r for r in adapter_check.check(folder, container=False)}
        self.assertFalse(results["MCP handshake"].ok)


class ContainerMode(unittest.TestCase):
    def test_an_adapter_job_runs_through_dc_exec_with_its_container_flags(self):
        from container_test_support import install_fake_dc
        manifest_raw = json.loads((EXAMPLES / "echo-agent" / "adapter.json").read_text())
        parsed = adapters.validate(manifest_raw)
        with tempfile.TemporaryDirectory() as td:
            box = adapter_check.Sandbox(parsed, EXAMPLES / "echo-agent",
                                        extra_harness='\n[sandbox]\nprovider = "devcontainer"\n\n[sandbox.workers]\necho-agent = "mount-ro"\n')
            try:
                (box.home / ".echo-agent").mkdir()
                dc = install_fake_dc(Path(td) / "dc")
                log = Path(td) / "dc.log"
                job_id, folder = box.job()
                done = box.run(job_id, folder, live=True, RIG_DC_BIN=str(dc), FAKE_DC_LOG=str(log))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                text = log.read_text()
                self.assertIn("exec --no-start", text)
                self.assertRegex(text, r"rig-supervisor .* echo-agent run --cwd \S+ --prompt-file \S+ --mcp-config \S+ --auto-approve")
                record = json.loads((folder / "standin.json").read_text())
                self.assertIn("--auto-approve", record["argv"])
                self.assertTrue(record["handshake"])
                self.assertEqual(json.loads((folder / "sandbox.json").read_text())["worker"], "echo-agent")
            finally:
                box.close()


if __name__ == "__main__":
    unittest.main()
