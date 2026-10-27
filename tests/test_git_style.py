#!/usr/bin/env python3
"""Git text Rig writes: Conventional Commits subjects, bodies, branch names, PR titles, the linter and the hook."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import git_style as gs  # noqa: E402


class Subjects(unittest.TestCase):
    def test_a_task_becomes_an_imperative_conventional_subject_within_the_limit(self):
        s = gs.subject("Please add plan meters to the settings Agents card so people can see usage", ["src/renderer/a.tsx", "src/renderer/b.tsx"])
        self.assertEqual(s, "feat(renderer): add plan meters to the settings")
        self.assertLessEqual(len(s), gs.SUBJECT)
        self.assertFalse(s.endswith("."))

    def test_politeness_tense_and_the_final_stop_are_removed(self):
        self.assertEqual(gs.imperative("Can you please fixed the login crash."), "fix the login crash")
        self.assertEqual(gs.imperative("Added tests for fleet"), "add tests for fleet")
        self.assertEqual(gs.imperative("DeepSeek Harness worker"), "DeepSeek Harness worker", "a name keeps its capitals")

    def test_the_type_comes_from_the_words_then_the_files(self):
        self.assertEqual(gs.classify("fix the crash when the parent is missing"), "fix")
        self.assertEqual(gs.classify("tidy things", ["README.md", "docs/a.md"]), "docs")
        self.assertEqual(gs.classify("cover the gate", ["tests/test_gate.py"]), "test")
        self.assertEqual(gs.classify("refactor the merge gate"), "refactor")
        self.assertEqual(gs.classify("bump electron"), "build")
        self.assertEqual(gs.classify("add plan meters\n\nit was slow before"), "feat", "only the first line names the change")
        self.assertEqual(gs.classify("something odd"), "chore")

    def test_a_fix_does_not_repeat_itself(self):
        self.assertEqual(gs.subject("Fixed the crash when the parent is not installed.", ["scripts/rig_chat.py"]),
                         "fix(rig-chat): crash when the parent is not")

    def test_scope_is_the_file_or_the_shared_folder_below_the_generic_ones(self):
        self.assertEqual(gs.scope_of(["tests/test_fleet.py"]), "fleet")
        self.assertEqual(gs.scope_of(["src/renderer/a.tsx", "src/renderer/b.tsx"]), "renderer")
        self.assertEqual(gs.scope_of(["scripts/a.py", "scripts/b.py"]), "")
        self.assertEqual(gs.scope_of(["a/x.py", "b/y.py"]), "")

    def test_long_text_is_cut_at_a_word_with_no_ellipsis(self):
        s = gs.subject("implement a very long description that keeps going and going well past any sensible subject length at all", [])
        self.assertLessEqual(len(s), gs.SUBJECT)
        self.assertNotIn("…", s)
        self.assertFalse(s.endswith((" ", ",")))
        self.assertLessEqual(len(gs.subject("x" * 200, [], limit=999)), gs.HARD)

    def test_plain_style_is_the_summary_alone(self):
        self.assertEqual(gs.subject("Add plan meters", ["src/a.py"], "plain"), "add plan meters")

    def test_the_message_has_a_wrapped_body_then_footers(self):
        text = gs.message("Add a thing\n\n" + "Because users asked for it and it was slow before. " * 4, ["a/b.py", "a/c.py"],
                          [("Rig-Job", "job-1"), ("Rig-Worker", "codex")])
        lines = text.rstrip("\n").split("\n")
        self.assertEqual(lines[1], "")
        self.assertTrue(all(len(l) <= gs.BODY_WIDTH for l in lines[2:]))
        self.assertEqual(lines[-2:], ["Rig-Job: job-1", "Rig-Worker: codex"])
        self.assertEqual(gs.lint(text), [])

    def test_a_task_the_subject_had_to_shorten_is_kept_whole_in_the_body(self):
        task = "implement the settings screen with all of its sections and the live editor that checks every line"
        text = gs.message(task, [], [("Rig-Job", "j")])
        self.assertIn("live editor", " ".join(text.split("\n\n", 1)[1].split()))


class Branches(unittest.TestCase):
    def test_patterns(self):
        task = "Add plan meters to the settings Agents card"
        self.assertEqual(gs.branch_name("{type}/{slug}", task), "feat/add-plan-meters-to-the-settings", "the slug is cut at a word, at 36")
        self.assertEqual(gs.branch_name("rig/{topic}", task), "rig/add-plan-meters-to-the-settings")
        self.assertEqual(gs.branch_name("{topic}", "Add x"), "add-x")
        self.assertEqual(gs.branch_name("nonsense", "fix the crash"), "fix/crash")

    def test_names_are_lowercase_kebab_and_at_most_50(self):
        name = gs.branch_name("feature/{topic}", "Implement " + "very long words " * 12)
        self.assertLessEqual(len(name), gs.BRANCH_MAX)
        self.assertRegex(name, r"^[a-z0-9/-]+$")
        self.assertFalse(name.endswith(("-", "/")))


class Lint(unittest.TestCase):
    def test_what_it_catches(self):
        self.assertEqual(gs.lint("feat(x): add a thing"), [])
        self.assertIn("full stop", " ".join(gs.lint("feat: add a thing.")))
        self.assertIn("not 'type(scope): summary'", " ".join(gs.lint("Added the thing")))
        self.assertIn("at most 72", " ".join(gs.lint("feat: " + "y" * 80)))
        self.assertIn("second line must be blank", " ".join(gs.lint("feat: a\nbody right away")))
        self.assertIn("wrap the body", " ".join(gs.lint("feat: a\n\n" + "word " * 30)))
        self.assertEqual(gs.lint("Added the thing", style="plain"), [])
        self.assertEqual(gs.lint(""), ["the subject is empty"])

    def test_merges_reverts_and_fixups_are_exempt(self):
        for text in ("Merge feat(x): add a thing", "Revert \"feat: x\"", "fixup! feat: x", "Merge pull request #1 from a/b"):
            self.assertEqual(gs.lint(text), [], text)


class Config(unittest.TestCase):
    def test_defaults_and_the_harness_values(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / ".rig").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"RIG_HOME": str(Path(td) / "home")}):
                self.assertEqual(gs.config(repo), {"style": "conventional", "limit": 50, "branch": "{type}/{slug}"})
                (repo / ".rig" / "harness.toml").write_text('[git]\ncommit_style = "plain"\nsubject_limit = 60\n\n[ui]\nbranch = "rig/{topic}"\n')
                self.assertEqual(gs.config(repo), {"style": "plain", "limit": 60, "branch": "rig/{topic}"})
                (repo / ".rig" / "harness.toml").write_text('[git]\ncommit_style = "weird"\nsubject_limit = 9999\n')
                self.assertEqual((gs.config(repo)["style"], gs.config(repo)["limit"]), ("conventional", gs.HARD))


class PullRequest(unittest.TestCase):
    def test_body_has_summary_why_checks_jobs_and_the_repos_template_last(self):
        text = gs.pull_request_body([{"subject": "feat: add x", "why": "Users asked.", "job": "j1"}], [{"name": "unit", "ok": True}],
                                    "## Checklist\n- [ ] docs")
        self.assertEqual(text.split("\n")[0], "## Summary")
        for part in ("- feat: add x", "## Why", "Users asked.", "- unit: passed", "Rig jobs: j1"):
            self.assertIn(part, text)
        self.assertTrue(text.rstrip().endswith("- [ ] docs"))

    def test_title_keeps_a_conventional_subject_and_counts_the_rest(self):
        import applies
        conf = {"style": "conventional", "limit": 50, "branch": "{type}/{slug}"}
        self.assertEqual(applies._pr_title(["feat(x): add a thing"], conf), "feat(x): add a thing")
        self.assertEqual(applies._pr_title(["Add a thing please", "Fix b", "Fix c"], conf), "feat: add a thing please (+2 more)")
        self.assertLessEqual(len(applies._pr_title(["Implement " + "long " * 40, "b"], conf)), gs.HARD)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class Hook(unittest.TestCase):
    def test_rig_git_hook_refuses_a_bad_message_and_accepts_a_good_one(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.email", "a@b.c")
            git(repo, "config", "user.name", "t")
            env = {**os.environ, "RIG_HOME": str(Path(td) / "home")}
            done = subprocess.run([sys.executable, str(ROOT / "scripts" / "git_style.py"), "hook", "--repo", str(repo)], capture_output=True, text=True, env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
            hook = repo / ".git" / "hooks" / "commit-msg"
            # the installed hook calls `rig git lint`; point `rig` at this checkout
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir()
            shim = bin_dir / "rig"
            shim.write_text(f'#!/bin/sh\nshift\nexec "{sys.executable}" "{ROOT}/scripts/git_style.py" "$@" --repo "{repo}"\n')
            shim.chmod(0o755)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            (repo / "a.txt").write_text("x\n")
            git(repo, "add", ".")
            bad = subprocess.run(["git", "-C", str(repo), "commit", "-m", "Added the thing."], capture_output=True, text=True, env=env)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("full stop", bad.stderr)
            good = subprocess.run(["git", "-C", str(repo), "commit", "-m", "feat: add the thing"], capture_output=True, text=True, env=env)
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertTrue(hook.is_file())
            # a hook that is not Rig's is never replaced
            hook.write_text("#!/bin/sh\nexit 0\n")
            again = subprocess.run([sys.executable, str(ROOT / "scripts" / "git_style.py"), "hook", "--repo", str(repo)], capture_output=True, text=True, env=env)
            self.assertEqual(again.returncode, 2)
            self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 0\n")


if __name__ == "__main__":
    unittest.main()
