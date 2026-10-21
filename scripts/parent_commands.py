#!/usr/bin/env python3
"""The parent's own skills and slash commands, for the shell's / menu.

Each parent CLI keeps skills (a folder with SKILL.md) and commands (a prompt file) in its own places, for the user and
for the repository. ``commands`` lists the ones the project's parent has, with the description their front matter
gives. ``expand`` turns a ``/name args`` message into what the parent's headless mode understands: claude runs its own
skills and commands from ``claude -p``, so the message goes as typed; for the others Rig does what their interactive
/ menu would: a command's prompt with the arguments put in, or an instruction to use the skill.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python before 3.11: a gemini command's toml prompt is then read with a pattern
    tomllib = None

SKILL_DIRS = {
    "claude": [".claude/skills"],
    "codex": [".codex/skills", ".agents/skills"],
    "grok": [".grok/skills", ".agents/skills"],
    "opencode": [".config/opencode/skill", ".config/opencode/skills", ".opencode/skill", ".opencode/skills", ".agents/skills"],
    "omp": [".omp/agent/skills", ".agents/skills"],
    "pi": [".pi/agent/skills", ".pi/skills", ".agents/skills"],
    "agy": [".gemini/antigravity-cli/skills", ".agents/skills"],
}
COMMAND_DIRS = {
    "claude": [".claude/commands"],
    "codex": [".codex/prompts"],
    "opencode": [".config/opencode/command", ".config/opencode/commands", ".opencode/command", ".opencode/commands"],
    "omp": [".omp/agent/prompts", ".omp/prompts"],
    "pi": [".pi/agent/prompts", ".pi/prompts"],
}
NATIVE = {"claude"}  # its headless mode runs /name itself
# each parent's own box: codex mentions a skill with $name, pi and omp call one /skill:name, the others /name
DOLLAR = {"codex"}
SKILL_PREFIX = {"pi": "skill:", "omp": "skill:"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
LIMIT = 200


def _front(text: str) -> tuple[dict, str]:
    """A markdown file's front matter (the simple key: value lines) and its body."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            meta[key.strip().lower()] = value.strip().strip("'\"")
    body = text[end + 4:]
    return meta, body.lstrip("\n")


def _first_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def _read(path: Path, limit: int = 64_000) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(limit).decode(errors="replace")
    except OSError:
        return ""


def _roots(repo) -> list[Path]:
    """The repository's own places first, then the user's: a repository's command wins over a personal one."""
    roots = [Path.home()]
    if repo:
        roots.insert(0, Path(repo))
    return roots


def _found(repo, parent: str):
    for root in _roots(repo):
        for folder in SKILL_DIRS.get(parent, []):
            base = root / folder
            if not base.is_dir():
                continue
            for skill in sorted(base.iterdir()):
                card = skill / "SKILL.md"
                if card.is_file():
                    yield "skill", skill.name, card
                elif skill.name.startswith(".") and skill.is_dir():
                    # a CLI's own bundled skills, one folder down (codex keeps them in .system)
                    for inner in sorted(skill.iterdir()):
                        if (inner / "SKILL.md").is_file():
                            yield "skill", inner.name, inner / "SKILL.md"
        for folder in COMMAND_DIRS.get(parent, []):
            base = root / folder
            if not base.is_dir():
                continue
            for item in sorted(base.rglob("*")):
                if not item.is_file() or item.suffix not in (".md", ".toml"):
                    continue
                name = ":".join(item.relative_to(base).with_suffix("").parts)  # a sub-folder is the namespace, as in claude
                if parent == "codex":
                    name = "prompts:" + name  # codex's own menu calls them /prompts:name
                yield "command", name, item


def commands(repo, parent: str) -> list[dict]:
    """The parent's skills and commands: name (without the /), what it does, skill or command, and its file."""
    seen, out = set(), []
    for kind, name, path in _found(repo, parent):
        if name in seen or not NAME.match(name):
            continue
        seen.add(name)
        text = _read(path, 8_000)
        if path.suffix == ".toml":
            match = re.search(r'^description\s*=\s*"([^"\n]*)"', text, re.M)
            help_text = match.group(1) if match else ""
        else:
            meta, body = _front(text)
            help_text = meta.get("description", "") or _first_line(body)
        out.append({"name": name, "help": " ".join(help_text.split())[:160], "kind": kind, "path": str(path)})
        if len(out) >= LIMIT:
            break
    return out


def _fill(template: str, args: str) -> str:
    """A command's prompt with its arguments: $ARGUMENTS (and gemini's {{args}}), $1 to $9, else after the prompt."""
    words = args.split()
    filled = template.replace("$ARGUMENTS", args).replace("{{args}}", args)
    filled = re.sub(r"\$([1-9])", lambda m: words[int(m.group(1)) - 1] if int(m.group(1)) <= len(words) else "", filled)
    if filled == template and args:
        filled = template.rstrip("\n") + "\n\n" + args
    return filled


def _toml_prompt(text: str) -> str:
    if tomllib is not None:
        try:
            prompt = tomllib.loads(text).get("prompt", "")
            return prompt if isinstance(prompt, str) else ""
        except tomllib.TOMLDecodeError:
            return ""
    match = re.search(r'prompt\s*=\s*"""(.*?)"""', text, re.S) or re.search(r'prompt\s*=\s*"([^"\n]*)"', text)
    return match.group(1) if match else ""


MENTION = re.compile(r"(?<!\S)\$([A-Za-z0-9][A-Za-z0-9_.:-]*)")


def _mentions(repo, parent: str, message: str) -> str:
    """$name names a skill in the message, as codex's own box does: the parent is told where each one is."""
    named = [m.group(1) for m in MENTION.finditer(message)] if parent in DOLLAR else []
    if not named:
        return message
    skills = {item["name"]: item["path"] for item in commands(repo, parent) if item["kind"] == "skill"}
    notes = []
    for name in dict.fromkeys(named):
        if name in skills:
            notes.append(f"Use your {name} skill (its instructions are in {skills[name]}).")
    return message + ("\n\n" + "\n".join(notes) if notes else "")


def expand(repo, parent: str, message: str) -> str:
    """What to send for a /name message or one naming $skills: the message itself when there is nothing to add."""
    message = _mentions(repo, parent, message)
    if parent in NATIVE or not message.startswith("/"):
        return message
    head, _, args = message[1:].partition(" ")
    args = args.strip()
    prefix = SKILL_PREFIX.get(parent, "")
    for item in commands(repo, parent):
        name = prefix + item["name"] if item["kind"] == "skill" else item["name"]
        if name != head:
            continue
        path = Path(item["path"])
        if item["kind"] == "skill":
            note = f"Use your {item['name']} skill (its instructions are in {path})."
            return note + ("\n\n" + args if args else "")
        text = _read(path)
        body = _toml_prompt(text) if path.suffix == ".toml" else _front(text)[1]
        return _fill(body, args) if body.strip() else message
    return message


def main(argv=None) -> int:
    import json
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: parent_commands.py REPO PARENT", file=sys.stderr)
        return 2
    print(json.dumps(commands(args[0], args[1]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
