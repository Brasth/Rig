#!/usr/bin/env python3
"""Git text Rig writes: commit subjects, bodies, branch names and pull request titles, in the usual best-practice form.

Conventional Commits 1.0 and the git norms:

* subject ``type(scope): imperative summary``, 50 characters (never over 72), no trailing period, cut at a word;
* a blank line, then a body wrapped at 72 that says why;
* footers (``Rig-Job: <id>``) after another blank line;
* branches ``type/short-kebab-slug``, lowercase, at most 50 characters.

Settings, in ``[git]`` of ``.rig/harness.toml`` or ``~/.rig/config.toml``: ``commit_style`` is ``conventional`` (default)
or ``plain`` (the summary alone, still imperative and short); ``subject_limit`` defaults to 50. The branch pattern is
``[ui] branch``: ``{type}/{slug}`` (default), ``rig/{topic}``, ``{topic}`` or ``feature/{topic}``.

``rig git lint`` checks a message or a range against the same rules; ``rig git hook`` installs it as a ``commit-msg`` hook.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from pathlib import Path

TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
SUBJECT = 50
HARD = 72
BODY_WIDTH = 72
MAX_BODY = 600
BRANCH_MAX = 50
SLUG_MAX = 36
PATTERNS = ("{type}/{slug}", "rig/{topic}", "{topic}", "feature/{topic}")
DEFAULT_PATTERN = "{type}/{slug}"

_POLITE = re.compile(r"^(?:(?:please|kindly|pls)[\s,]+|(?:can|could|would) you(?: please)?[\s,]+|let'?s[\s,]+|i (?:want|need) you to[\s,]+|"
                     r"we (?:need|want) to[\s,]+|i'?d like (?:you )?to[\s,]+|need to[\s,]+|to[\s,]+)", re.I)
_VERBS = {"added": "add", "adds": "add", "adding": "add", "fixed": "fix", "fixes": "fix", "fixing": "fix",
          "updated": "update", "updates": "update", "updating": "update", "removed": "remove", "removes": "remove",
          "removing": "remove", "changed": "change", "changes": "change", "changing": "change", "implemented": "implement",
          "implements": "implement", "implementing": "implement", "created": "create", "creates": "create",
          "creating": "create", "improved": "improve", "improves": "improve", "improving": "improve",
          "renamed": "rename", "renames": "rename", "renaming": "rename", "moved": "move", "moves": "move",
          "moving": "move", "refactored": "refactor", "refactors": "refactor", "refactoring": "refactor",
          "made": "make", "makes": "make", "making": "make", "supports": "support", "supported": "support",
          "supporting": "support", "cleaned": "clean", "cleans": "clean", "cleaning": "clean", "wrote": "write",
          "writes": "write", "writing": "write", "built": "build", "builds": "build", "building": "build",
          "documented": "document", "documents": "document", "documenting": "document", "tested": "test",
          "tests": "test", "testing": "test", "replaced": "replace", "replaces": "replace", "replacing": "replace"}
_FEAT_VERBS = ("add", "implement", "support", "create", "introduce", "allow", "enable", "build", "show", "let", "provide")
_RULES = (  # first match wins; (type, words that name it)
    ("fix", ("fix", "bug", "broken", "crash", "regression", "repair", "error when", "does not work", "doesn't work", "wrong")),
    ("perf", ("perf", "faster", "speed up", "optimi", "slow")),
    ("refactor", ("refactor", "clean up", "cleanup", "rename", "simplif", "restructure", "extract", "dedupe", "tidy")),
    ("build", ("dependenc", "bump", "upgrade", "lockfile", "package.json", "pyproject")),
    ("ci", ("workflow", "pipeline", " ci ", "github action")),
    ("docs", ("doc", "readme", "changelog", "comment", "typo")),
    ("test", ("test", "spec ", "coverage")),
)
CONVENTIONAL = re.compile(r"^(?:" + "|".join(TYPES) + r")(?:\([a-z0-9][a-z0-9._/-]*\))?!?: \S")
_EXEMPT = re.compile(r"^(?:Merge |Revert |fixup! |squash! |amend! )")
_SLUG = re.compile(r"[^a-z0-9]+")
_FOOTER = re.compile(r"^[A-Za-z][A-Za-z-]*(?:: | #)\S")


def imperative(text: str) -> str:
    """A task as you would say it in a subject: no politeness, imperative first word, no final stop, one line."""
    line = " ".join(str(text or "").strip().splitlines()[:1]).strip()
    line = re.sub(r"\s+", " ", line)
    previous = None
    while previous != line:
        previous, line = line, _POLITE.sub("", line, count=1).strip()
    line = line.rstrip(" .!?;:,")
    if not line:
        return ""
    first, _, rest = line.partition(" ")
    first = _VERBS.get(first.lower(), first)
    # a leading capitalised word is a sentence start, not a name: lower it unless it is an acronym or a path-like token
    if first.isalpha() and first[:1].isupper() and first[1:].islower():
        first = first[0].lower() + first[1:]
    return (first + (" " + rest if rest else "")).strip()


def classify(task: str, files=()) -> str:
    """The Conventional Commits type for a task and the files it touched."""
    files = [f for f in (files or []) if f]
    text = f" {(str(task).strip().splitlines() or [''])[0].lower()} "  # the first line names the change; the rest is detail
    if files and all(f.lower().endswith((".md", ".rst", ".txt")) or f.startswith("docs/") for f in files):
        return "docs"
    if files and all(re.match(r"(?:tests?|spec)/|.*(?:_test|\.test|\.spec)\.", f) for f in files):
        return "test"
    for kind, words in _RULES:
        if any(word in text for word in words):
            return kind
    first = imperative(task).split(" ", 1)[0].lower()
    return "feat" if first in _FEAT_VERBS else "chore"


_GENERIC = {"src", "scripts", "lib", "app", "source", "pkg", "internal", "cmd", "tests", "test", "docs", "."}


def scope_of(files) -> str:
    """One file: its name without extension and test marker. Several: the first folder below the generic ones (src, scripts,
    lib, ...) that they all share. Otherwise none."""
    files = [f for f in (files or []) if f]
    if not files:
        return ""
    if len(files) == 1:
        stem = re.sub(r"^test[_-]|[_.-](?:test|spec)$", "", Path(files[0]).stem)
    else:
        parts = [f.split("/")[:-1] for f in files]
        common = []
        for level in zip(*parts):
            if len(set(level)) != 1:
                break
            common.append(level[0])
        named = [c for c in common if c.lower() not in _GENERIC]
        stem = named[0] if named else ""
    stem = _SLUG.sub("-", stem.lower()).strip("-")
    return stem[:20].strip("-")


def summary_for(task: str, kind: str) -> str:
    """The imperative summary, without a leading verb that only repeats the type ("fix: fix the crash")."""
    summary = imperative(task)
    if kind == "fix":
        summary = re.sub(r"^fix(?:es|ed)?\s+(?:the |a |an )?", "", summary, flags=re.I).strip() or summary
    return summary


def shorten(text: str, limit: int) -> str:
    """At most limit characters, cut at a word, with no ellipsis; a single long word is cut hard."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    head = cut[:cut.rfind(" ")] if " " in cut[:limit + 1] else ""
    head = head if len(head) >= max(8, limit // 3) else text[:limit]
    return head.rstrip(" ,;:-")


def subject(task: str, files=(), style: str = "conventional", limit: int = SUBJECT, kind: str = "") -> str:
    """The commit subject for a task. ``plain`` is the imperative summary alone."""
    limit = max(20, min(int(limit or SUBJECT), HARD))
    if style == "plain":
        return shorten(imperative(task) or "update the project", limit)
    kind = kind or classify(task, files)
    summary = summary_for(task, kind) or "update the project"
    scope = scope_of(files)
    prefix = f"{kind}({scope}): " if scope else f"{kind}: "
    if len(prefix) > limit // 2 and scope:  # a long scope costs the summary too much room
        prefix = f"{kind}: "
    return prefix + shorten(summary, limit - len(prefix))


def task_of(folder) -> tuple[str, str]:
    """The task a job was given, in full, and the worker that did it, from the job's folder."""
    import json
    folder = Path(folder)
    try:
        meta = json.loads((folder / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    for key in ("title", "task", "brief", "prompt"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), str(meta.get("worker") or "")
    try:
        text = (folder / "prompt.txt").read_text(errors="replace").strip()
    except OSError:
        text = ""
    return text or f"apply changes from job {folder.name}", str(meta.get("worker") or "")


def body(task: str, trailers=(), subject_line: str = "") -> str:
    """The message after the subject: why, wrapped at 72, then footers. Empty text gives just the footers."""
    lines = [l.rstrip() for l in str(task or "").strip().splitlines()]
    detail = "\n".join(lines[1:]).strip()
    if not detail and len(lines) == 1 and imperative(lines[0]) != subject_line.split(": ", 1)[-1] and len(lines[0]) > len(subject_line):
        detail = lines[0]  # the subject had to shorten the task: keep it whole
    parts = []
    if detail:
        detail = detail[:MAX_BODY].rsplit(" ", 1)[0] if len(detail) > MAX_BODY else detail
        parts.append("\n".join(textwrap.fill(p, BODY_WIDTH) for p in re.split(r"\n\s*\n", detail) if p.strip()))
    if trailers:
        parts.append("\n".join(f"{k}: {v}" for k, v in trailers if v))
    return "\n\n".join(p for p in parts if p)


def message(task: str, files=(), trailers=(), style: str = "conventional", limit: int = SUBJECT) -> str:
    subj = subject(task, files, style, limit)
    rest = body(task, trailers, subj)
    return subj + ("\n\n" + rest if rest else "") + "\n"


def slug(text: str, limit: int = SLUG_MAX) -> str:
    words = _SLUG.sub(" ", imperative(text).lower()).strip()
    return shorten(words, limit).replace(" ", "-").strip("-") or "changes"


def branch_name(pattern: str, task: str, files=(), kind: str = "") -> str:
    """A branch for a task from a pattern ({type}/{slug}, rig/{topic}, {topic}, feature/{topic}), at most 50 characters."""
    pattern = pattern if pattern in PATTERNS else DEFAULT_PATTERN
    kind = kind or classify(task, files)
    summary = slug(summary_for(task, kind))
    name = pattern.replace("{type}", kind).replace("{slug}", summary).replace("{topic}", summary)
    if len(name) > BRANCH_MAX:
        head, _, _ = name.rpartition("/")
        room = BRANCH_MAX - len(head) - 1 if head else BRANCH_MAX
        name = (head + "/" if head else "") + slug(summary_for(task, kind), max(8, room))
    return name.strip("/-")


def lint(text: str, style: str = "conventional", limit: int = HARD) -> list[str]:
    """What is wrong with a commit message, as short sentences; empty when it is fine."""
    lines = str(text or "").rstrip("\n").split("\n")
    first = lines[0].strip() if lines else ""
    out = []
    if not first:
        return ["the subject is empty"]
    if _EXEMPT.match(first):
        return []
    if len(first) > limit:
        out.append(f"the subject is {len(first)} characters; at most {limit} (50 is better)")
    elif len(first) > SUBJECT + 10:
        pass
    if first.endswith("."):
        out.append("the subject ends with a full stop")
    if style == "conventional" and not CONVENTIONAL.match(first):
        out.append("the subject is not 'type(scope): summary' with a type of " + ", ".join(TYPES))
    if len(lines) > 1 and lines[1].strip():
        out.append("the second line must be blank")
    for number, line in enumerate(lines[2:], 3):
        if len(line) > BODY_WIDTH + 8 and not line.startswith(("http", "    ")) and not _FOOTER.match(line):
            out.append(f"line {number} is {len(line)} characters; wrap the body at {BODY_WIDTH}")
            break
    return out


def config(repo) -> dict:
    """The settings: commit style, subject limit and branch pattern, for this repo or all repos."""
    import harness
    import ui_settings
    import ui_theme
    repo = Path(repo)
    here, everywhere = harness.harness_path(repo), ui_theme.global_path()

    def read(section, key):
        return ui_settings._read_key(here, section, key) or ui_settings._read_key(everywhere, section, key)

    style = (read("git", "commit_style") or "conventional").lower()
    try:
        limit = int(read("git", "subject_limit") or SUBJECT)
    except ValueError:
        limit = SUBJECT
    pattern = read("ui", "branch")
    return {"style": style if style in ("conventional", "plain") else "conventional", "limit": max(20, min(limit, HARD)),
            "branch": pattern if pattern in PATTERNS else DEFAULT_PATTERN}


def pull_request_body(items: list[dict], checks=(), template: str = "") -> str:
    """The pull request description: Summary, Why, Checks, then the repository's own template when it has one.
    Each item is {"subject": ..., "why": ..., "job": ...}."""
    lines = ["## Summary", ""] + [f"- {i['subject']}" for i in items if i.get("subject")]
    whys = [i["why"] for i in items if i.get("why")]
    if whys:
        lines += ["", "## Why", ""] + [textwrap.fill(w, BODY_WIDTH) for w in whys[:3]]
    if checks:
        lines += ["", "## Checks", ""] + [f"- {c['name']}: {'passed' if c['ok'] else 'failed'}" for c in checks]
    jobs = [i["job"] for i in items if i.get("job")]
    if jobs:
        lines += ["", "Rig jobs: " + ", ".join(jobs)]
    text = "\n".join(lines).rstrip() + "\n"
    if template.strip():
        text += "\n---\n\n" + template.strip() + "\n"
    return text


def _git(*args, cwd="."):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30)


HOOK = """#!/bin/sh
# installed by `rig git hook`: checks the commit message the way `rig git lint` does
exec rig git lint --file "$1"
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig git", description="Commit message and branch conventions")
    parser.add_argument("command", choices=("lint", "hook", "preview"))
    parser.add_argument("text", nargs="*")
    parser.add_argument("--file")
    parser.add_argument("--range", dest="rev_range")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    conf = config(repo)
    if args.command == "preview":
        task = " ".join(args.text) or "add plan meters to settings"
        print(message(task, (), (), conf["style"], conf["limit"]), end="")
        print(f"branch: {branch_name(conf['branch'], task)}")
        return 0
    if args.command == "hook":
        hooks = _git("rev-parse", "--git-path", "hooks", cwd=repo).stdout.strip()
        path = (repo / hooks / "commit-msg") if hooks and not Path(hooks).is_absolute() else Path(hooks) / "commit-msg"
        if path.exists() and "rig git lint" not in path.read_text(errors="replace"):
            print(f"rig git: {path} exists and is not Rig's; not replacing it", file=sys.stderr)
            return 2
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HOOK)
        path.chmod(0o755)
        print(f"installed {path}")
        return 0
    messages = []
    if args.file:
        messages = [(args.file, Path(args.file).read_text())]
    elif args.rev_range:
        done = _git("log", "--format=%H%x1f%B%x1e", args.rev_range, cwd=repo)
        for entry in done.stdout.split("\x1e"):
            if entry.strip():
                sha, _, text = entry.strip().partition("\x1f")
                messages.append((sha[:8], text))
    else:
        messages = [("message", " ".join(args.text))]
    failed = 0
    for name, text in messages:
        for problem in lint(text, conf["style"]):
            failed += 1
            print(f"{name}: {problem}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
