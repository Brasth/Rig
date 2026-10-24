#!/usr/bin/env python3
"""Fleet change: one change across up to two repositories, with linked pull requests and "who calls what".

``calls`` reads each repository's manifests (package.json, pyproject.toml, requirements*.txt, go.mod, Cargo.toml) for
what it provides and what it depends on, and its sources for imports, and says which repository uses which: the
provider is the one to merge first. Nothing is guessed from names alone: an edge needs a declared dependency or an
import of a name the other repository provides, and says which.

``ship`` opens a draft pull request in each repository (through the same ship the board uses, so agents still never
push), then rewrites every pull request's description to be the same text with the links to the others and the order
to merge them in. The free version is two repositories; more is Team."""
from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

MAX_FREE = 2
MAX_FILES = 2000
MAX_BYTES = 256_000
SKIP_DIRS = {".git", "node_modules", ".rig", "__pycache__", "dist", "build", "target", "vendor", ".venv", "venv", ".next"}
PY = {".py"}
JS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
GO = {".go"}


class FleetError(ValueError):
    pass


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def manifest(repo) -> dict:
    """{"provides": names this repository publishes, "depends": names it declares it needs}, both normalized."""
    repo = Path(repo)
    provides: set[str] = set()
    depends: set[str] = set()
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(_read(pkg))
        except ValueError:
            data = {}
        if isinstance(data.get("name"), str):
            provides.add(data["name"].strip().lower())
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            if isinstance(data.get(key), dict):
                depends.update(name.lower() for name in data[key])
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(_read(pyproject))
        except tomllib.TOMLDecodeError:
            data = {}
        project = data.get("project") or {}
        if isinstance(project.get("name"), str):
            provides.add(_norm(project["name"]))
        for spec in project.get("dependencies") or []:
            found = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(spec))
            if found:
                depends.add(_norm(found.group(1)))
    for req in repo.glob("requirements*.txt"):
        for line in _read(req).splitlines():
            found = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line.split("#", 1)[0])
            if found and not line.lstrip().startswith("-"):
                depends.add(_norm(found.group(1)))
    gomod = repo / "go.mod"
    if gomod.is_file():
        text = _read(gomod)
        found = re.search(r"^module\s+(\S+)", text, re.M)
        if found:
            provides.add(found.group(1))
        for block in re.findall(r"^require\s*\(([^)]*)\)", text, re.M | re.S):
            depends.update(m.group(1) for m in re.finditer(r"^\s*(\S+)\s+v", block, re.M))
        depends.update(m.group(1) for m in re.finditer(r"^require\s+(\S+)\s+v", text, re.M))
    cargo = repo / "Cargo.toml"
    if cargo.is_file():
        try:
            data = tomllib.loads(_read(cargo))
        except tomllib.TOMLDecodeError:
            data = {}
        if isinstance((data.get("package") or {}).get("name"), str):
            provides.add(_norm(data["package"]["name"]))
        for key in ("dependencies", "dev-dependencies", "build-dependencies"):
            if isinstance(data.get(key), dict):
                depends.update(_norm(name) for name in data[key])
    # Python packages a repository holds are importable by their folder name too
    for folder in [repo, repo / "src"]:
        if folder.is_dir():
            for init in folder.glob("*/__init__.py"):
                provides.add(_norm(init.parent.name))
    return {"provides": sorted(provides), "depends": sorted(depends)}


_PY_IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))", re.M)
_JS_IMPORT = re.compile(r"""(?:from\s+|import\s+|require\(\s*)['"]([^'"./][^'"]*)['"]""")
_GO_IMPORT = re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"([^"]+)"', re.M)


def imports(repo) -> dict[str, list[str]]:
    """Imported names -> the files (relative, first few) that import them. Bounded in files and bytes."""
    repo = Path(repo)
    found: dict[str, list[str]] = {}
    seen = 0
    stack = [repo]
    while stack and seen < MAX_FILES:
        folder = stack.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            if path.is_dir():
                if path.name not in SKIP_DIRS and not path.is_symlink():
                    stack.append(path)
                continue
            suffix = path.suffix
            if suffix not in PY | JS | GO or seen >= MAX_FILES:
                continue
            seen += 1
            try:
                text = path.read_text(errors="replace")[:MAX_BYTES]
            except OSError:
                continue
            rel = str(path.relative_to(repo))
            if suffix in PY:
                names = [(a or b).split(".")[0] for a, b in _PY_IMPORT.findall(text)]
            elif suffix in JS:
                names = ["/".join(m.split("/")[:2]) if m.startswith("@") else m.split("/")[0] for m in _JS_IMPORT.findall(text)]
            else:
                names = _GO_IMPORT.findall(text)
            for name in names:
                files = found.setdefault(name, [])
                if rel not in files and len(files) < 5:
                    files.append(rel)
    return found


def _matches(name: str, provided: str) -> bool:
    return _norm(name) == _norm(provided) or (("/" in provided) and (name == provided or name.startswith(provided + "/")))


def calls(repos) -> dict:
    """Which repository uses which. Edges say why: a declared dependency, an import, or both."""
    paths = [Path(r).resolve() for r in repos]
    info = {str(p): (manifest(p), imports(p)) for p in paths}
    edges = []
    for a in paths:
        a_manifest, a_imports = info[str(a)]
        for b in paths:
            if a == b:
                continue
            provides = info[str(b)][0]["provides"]
            via, files = [], []
            for name in provides:
                for dep in a_manifest["depends"]:
                    if _matches(dep, name):
                        via.append(f"declares {dep}")
                for imported, where in a_imports.items():
                    if _matches(imported, name):
                        via.append(f"imports {imported}")
                        files.extend(f for f in where if f not in files)
            if via:
                edges.append({"from": str(a), "to": str(b), "via": sorted(set(via)), "files": files[:5]})
    return {"repos": [{"path": str(p), "name": p.name, **info[str(p)][0]} for p in paths], "edges": edges}


def order(repos, graph: dict | None = None) -> list[str]:
    """Merge order: a repository others use goes before the ones that use it."""
    paths = [str(Path(r).resolve()) for r in repos]
    graph = graph or calls(paths)
    users = {p: {e["from"] for e in graph["edges"] if e["to"] == p} for p in paths}
    # the one used by the other first; with no edge, the order given
    return sorted(paths, key=lambda p: (-len(users[p] - {p}), paths.index(p)))


def check_repos(repos) -> list[Path]:
    paths = []
    for r in repos:
        p = Path(r).expanduser().resolve()
        if not (p / ".git").exists():
            raise FleetError(f"{p} is not a git repository")
        if p not in paths:
            paths.append(p)
    if len(paths) < 2:
        raise FleetError("a fleet change needs two repositories")
    if len(paths) > MAX_FREE:
        raise FleetError(f"the free version changes up to {MAX_FREE} repositories at once; more is part of Team")
    return paths


def description(title: str, body: str, entries: list[dict], graph: dict) -> str:
    """The one description every pull request carries: the text, the links, and the order."""
    lines = [body.strip() or title, "", f"**One change across {len(entries)} repositories**", ""]
    for number, entry in enumerate(entries, 1):
        lines.append(f"{number}. {entry['name']}: {entry['url']}")
    notes = []
    names = {str(Path(e["path"]).resolve()): e["name"] for e in entries}
    for edge in graph["edges"]:
        notes.append(f"{names.get(edge['from'], Path(edge['from']).name)} {', '.join(edge['via'])} from {names.get(edge['to'], Path(edge['to']).name)}")
    if notes:
        lines += ["", "Who calls what: " + "; ".join(notes) + ".", f"Merge {entries[0]['name']} first."]
    return "\n".join(lines)


def default_gh(args: list[str], cwd: Path) -> str:
    done = subprocess.run(["gh", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()
        raise FleetError("gh " + " ".join(args[:2]) + " failed: " + (tail[-1] if tail else "unknown error"))
    return done.stdout


def ship(repos, title: str, body: str = "", shipper=None, gh=default_gh) -> dict:
    """Draft pull requests in each repository (in merge order), then one shared description on all of them."""
    import applies
    paths = check_repos(repos)
    title = (title or "").strip()
    if not title:
        raise FleetError("a fleet change needs a title")
    graph = calls(paths)
    ship_it = shipper or applies.ship
    entries = []
    for path in [Path(p) for p in order(paths, graph)]:
        try:
            done = ship_it(path, "pr", title, body or title, draft=True)
        except applies.ApplyError as error:
            raise FleetError(f"{path.name}: {error}" + (f" (already opened: {', '.join(e['url'] for e in entries)})" if entries else "")) from error
        entries.append({"name": path.name, "path": str(path), "url": done.get("url", "")})
    text = description(title, body, entries, graph)
    for entry in entries:
        if entry["url"]:
            gh(["pr", "edit", entry["url"], "--body", text], Path(entry["path"]))
    return {"prs": entries, "description": text, "graph": graph}


def main(argv=None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog="rig fleet")
    parser.add_argument("command", choices=("map", "ship"))
    parser.add_argument("repos", nargs="+")
    parser.add_argument("--title", default="")
    parser.add_argument("--body", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "map":
            graph = calls(check_repos(args.repos))
            for r in graph["repos"]:
                print(f"{r['name']}: provides {', '.join(r['provides']) or '-'}")
            for e in graph["edges"]:
                print(f"{Path(e['from']).name} -> {Path(e['to']).name}: {'; '.join(e['via'])}" + (f"  ({', '.join(e['files'])})" if e["files"] else ""))
            if not graph["edges"]:
                print("no calls between these repositories")
        else:
            done = ship(args.repos, args.title, args.body)
            print("\n".join(f"{e['name']}: {e['url']}" for e in done["prs"]))
    except (FleetError, ValueError) as error:
        print(f"rig fleet: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
