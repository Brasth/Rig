#!/usr/bin/env python3
"""Adapter packages: a new worker CLI is a directory with adapter.json (design: docs/journals/261020-phase-4-design.md).

Discovery reads ``<runtime>/adapters/*/adapter.json`` and every directory in RIG_ADAPTER_DIRS. The ten built-in
workers stay in code and cannot be shadowed. A manifest is data only: it is validated strictly, never executed,
and expands to an argv list, never a shell string.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = 1
BUILTIN = ("grok", "claude", "codex", "cursor", "opencode", "omp", "pi", "agy", "devin", "mimo")
NAME = re.compile(r"[a-z][a-z0-9-]{1,23}")
TOKENS = frozenset({"binary", "work", "brief_file", "brief_text", "model", "effort", "job_dir", "job_id", "session_id", "mcp_path"})
SPLICES = ("model_args", "effort_args", "mcp", "permissions")
KEYS = {"schema", "name", "binary", "provider", "command", "model_args", "effort_args", "mcp", "permissions", "container", "output"}
REQUIRED = {"schema", "name", "command", "mcp"}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_SECRETISH = re.compile(r"(token|secret|password|api[_-]?key|bearer)", re.I)


class AdapterError(ValueError):
    pass


def adapter_dirs() -> list[Path]:
    dirs = [HERE.parent / "adapters"]
    # A user directory that updates never touch.
    dirs.append(Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "rig" / "adapters")
    for raw in (os.environ.get("RIG_ADAPTER_DIRS") or "").split(os.pathsep):
        if raw.strip():
            dirs.append(Path(raw.strip()).expanduser())
    return dirs


def _argv_list(value, label, *, allow_splices=False):
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise AdapterError(f"{label} must be a non-empty list of non-empty strings")
    for item in value:
        if "\0" in item or "\n" in item:
            raise AdapterError(f"{label} must not contain newlines or NUL")
        if item.startswith("{") and item.endswith("}") and item[1:-1] in SPLICES:
            if not allow_splices:
                raise AdapterError(f"{label} cannot use the splice token {item}")
            continue
        for name in _PLACEHOLDER.findall(item):
            if name not in TOKENS:
                raise AdapterError(f"{label} uses an unknown placeholder {{{name}}}")
    return list(value)


def validate(raw: dict, *, directory: Path | None = None) -> dict:
    """The manifest, normalized, or AdapterError naming the first problem."""
    if not isinstance(raw, dict):
        raise AdapterError("adapter.json must be an object")
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise AdapterError(f"unknown key {unknown[0]!r}")
    missing = sorted(REQUIRED - set(raw))
    if missing:
        raise AdapterError(f"missing required key {missing[0]!r}")
    if raw["schema"] != SCHEMA or isinstance(raw["schema"], bool):
        raise AdapterError(f"schema must be {SCHEMA}")
    name = raw["name"]
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise AdapterError("name must be lowercase letters, digits and dashes, starting with a letter (2 to 24 characters)")
    if name in BUILTIN or name in {"parent", "rig"}:
        raise AdapterError(f"{name!r} is a built-in worker and cannot be redefined")
    if directory is not None and directory.name != name:
        raise AdapterError(f"directory {directory.name!r} must match the adapter name {name!r}")
    binary = raw.get("binary", name)
    if not isinstance(binary, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", binary):
        raise AdapterError("binary must be a plain executable name, not a path")
    provider = raw.get("provider", "")
    if not isinstance(provider, str) or len(provider) > 40 or re.search(r"\s", provider):
        raise AdapterError("provider must be a short word")
    out = {"schema": SCHEMA, "name": name, "binary": binary, "provider": provider,
           "command": _argv_list(raw["command"], "command", allow_splices=True)}
    for key in ("model_args", "effort_args"):
        out[key] = _argv_list(raw[key], key) if key in raw else []
    mcp = raw["mcp"]
    if not isinstance(mcp, dict) or set(mcp) - {"mode", "argv", "env"} or mcp.get("mode") not in {"flag", "env"}:
        raise AdapterError('mcp must be {"mode": "flag", "argv": [...]} or {"mode": "env", "env": {...}}')
    if mcp["mode"] == "flag":
        if "env" in mcp:
            raise AdapterError("mcp.env belongs to mode env")
        argv = _argv_list(mcp.get("argv"), "mcp.argv")
        if not any("{mcp_path}" in item for item in argv):
            raise AdapterError("mcp.argv must name {mcp_path}, or the worker never sees the job-scoped Rig MCP")
        out["mcp"] = {"mode": "flag", "argv": argv}
    else:
        if "argv" in mcp:
            raise AdapterError("mcp.argv belongs to mode flag")
        env = mcp.get("env")
        if not isinstance(env, dict) or not env or any(not isinstance(k, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or not isinstance(v, str) for k, v in env.items()):
            raise AdapterError("mcp.env must map UPPER_CASE names to strings")
        if not any("{mcp_path}" in v for v in env.values()):
            raise AdapterError("mcp.env must name {mcp_path}")
        out["mcp"] = {"mode": "env", "env": dict(env)}
    perms = raw.get("permissions", {})
    if not isinstance(perms, dict) or set(perms) - {"host", "container"}:
        raise AdapterError("permissions may only have host and container")
    out["permissions"] = {mode: ([] if mode not in perms else _argv_list(perms[mode], f"permissions.{mode}")) for mode in ("host", "container")}
    container = raw.get("container", {"supported": False})
    if not isinstance(container, dict) or set(container) - {"supported", "credential_paths"} or not isinstance(container.get("supported", False), bool):
        raise AdapterError("container must be {supported: bool, credential_paths: [...]}")
    paths = container.get("credential_paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p or p.startswith("/") or ".." in Path(p).parts for p in paths):
        raise AdapterError("container.credential_paths must be relative paths under the home directory")
    out["container"] = {"supported": bool(container.get("supported", False)), "credential_paths": list(paths)}
    output = raw.get("output", {"decoder": "plain"})
    if not isinstance(output, dict) or set(output) - {"decoder", "result_field"} or output.get("decoder") not in {"plain", "jsonl"}:
        raise AdapterError('output must be {"decoder": "plain"} or {"decoder": "jsonl", "result_field": "a.b"}')
    if output["decoder"] == "jsonl" and not re.fullmatch(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*", str(output.get("result_field", ""))):
        raise AdapterError("output.result_field must be a dotted path for the jsonl decoder")
    out["output"] = {"decoder": output["decoder"], **({"result_field": output["result_field"]} if output["decoder"] == "jsonl" else {})}
    for token in (out["command"] + out["model_args"] + out["effort_args"] + out["mcp"].get("argv", [])
                  + out["permissions"]["host"] + out["permissions"]["container"]):
        if _SECRETISH.search(token):
            raise AdapterError(f"argv must not name a secret: {token!r}")
    return out


def load_dir(directory) -> dict:
    directory = Path(directory)
    path = directory / "adapter.json"
    try:
        raw = json.loads(path.read_text())
    except OSError as error:
        raise AdapterError(f"cannot read {path}: {error}") from error
    except ValueError as error:
        raise AdapterError(f"{path} is not valid JSON: {error}") from error
    manifest = validate(raw, directory=directory)
    manifest["_dir"] = str(directory.resolve())
    return manifest


def discover() -> tuple[dict, dict]:
    """(adapters by name, problems by directory). A broken adapter is reported, never fatal to the others."""
    found, problems = {}, {}
    for root in adapter_dirs():
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not (child / "adapter.json").is_file():
                continue
            try:
                manifest = load_dir(child)
            except AdapterError as error:
                problems[str(child)] = str(error)
                continue
            if manifest["name"] in found:
                problems[str(child)] = f"duplicate adapter {manifest['name']!r} (first found in {found[manifest['name']]['_dir']})"
                continue
            found[manifest["name"]] = manifest
    return found, problems


def names() -> tuple[str, ...]:
    return tuple(sorted(discover()[0]))


def get(name: str) -> dict | None:
    return discover()[0].get(name)


def _expand(item: str, values: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), item)


def build_command(manifest: dict, *, mode: str, work, brief_file, brief_text, model="", effort="", job_dir="",
                  job_id="", mcp_path="", session_id="", binary="") -> list[str]:
    """The worker argv. ``mode`` is host or container; ``binary`` overrides the executable (a resolved path)."""
    if mode not in {"host", "container"}:
        raise AdapterError("mode must be host or container")
    if mode == "container" and not manifest["container"]["supported"]:
        raise AdapterError(f"adapter {manifest['name']} does not support container mode")
    values = {"binary": binary or manifest["binary"], "work": str(work), "brief_file": str(brief_file), "brief_text": brief_text,
              "model": model, "effort": effort, "job_dir": str(job_dir), "job_id": job_id, "session_id": session_id,
              "mcp_path": str(mcp_path)}
    lists = {"model_args": manifest["model_args"] if model else [],
             "effort_args": manifest["effort_args"] if effort else [],
             "mcp": manifest["mcp"].get("argv", []) if manifest["mcp"]["mode"] == "flag" else [],
             "permissions": manifest["permissions"][mode]}
    argv, used = [], set()
    for item in manifest["command"]:
        if item.startswith("{") and item.endswith("}") and item[1:-1] in SPLICES:
            used.add(item[1:-1])
            argv.extend(_expand(part, values) for part in lists[item[1:-1]])
        else:
            argv.append(_expand(item, values))
    for key in SPLICES:  # a manifest cannot forget the MCP or permission flags
        if key not in used:
            argv.extend(_expand(part, values) for part in lists[key])
    return argv


def mcp_env(manifest: dict, mcp_path) -> dict:
    if manifest["mcp"]["mode"] != "env":
        return {}
    return {key: _expand(value, {"mcp_path": str(mcp_path)}) for key, value in manifest["mcp"]["env"].items()}


def decode_summary(manifest: dict, text: str) -> str:
    """The job summary from the worker's stdout, per the manifest's decoder. Empty when nothing matches."""
    lines = [line for line in str(text).splitlines() if line.strip()]
    if manifest["output"]["decoder"] == "plain":
        return lines[-1].strip() if lines else ""
    path = manifest["output"]["result_field"].split(".")
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return ""


def container_mode(manifest: dict) -> dict:
    """The container_modes entry for an adapter worker."""
    return {"binary": manifest["binary"], "credential_paths": list(manifest["container"]["credential_paths"]),
            "supported": manifest["container"]["supported"], "permissions": list(manifest["permissions"]["container"])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rig adapters", description="Adapter packages for new worker CLIs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    check = sub.add_parser("validate")
    check.add_argument("directory")
    binary_cmd = sub.add_parser("binary")
    binary_cmd.add_argument("--worker", required=True)
    summary_cmd = sub.add_parser("summary")
    summary_cmd.add_argument("--worker", required=True)
    summary_cmd.add_argument("--log", required=True)
    names_cmd = sub.add_parser("names")
    names_cmd.add_argument("--exists", default="")
    command = sub.add_parser("command")
    command.add_argument("--worker", required=True)
    command.add_argument("--mode", default="host")
    command.add_argument("--work", required=True)
    command.add_argument("--brief-file", required=True)
    command.add_argument("--brief-text", default="")
    command.add_argument("--model", default="")
    command.add_argument("--effort", default="")
    command.add_argument("--job-dir", default="")
    command.add_argument("--job-id", default="")
    command.add_argument("--mcp-path", default="")
    command.add_argument("--binary", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            found, problems = discover()
            for name, manifest in sorted(found.items()):
                print(f"{name}  {manifest['binary']}  {manifest['_dir']}")
            for path, reason in sorted(problems.items()):
                print(f"! {path}: {reason}", file=sys.stderr)
            return 1 if problems else 0
        if args.command == "validate":
            manifest = load_dir(args.directory)
            print(f"ok: {manifest['name']} ({manifest['binary']})")
            return 0
        if args.command == "names":
            found = discover()[0]
            if args.exists:
                return 0 if args.exists in found else 1
            print("\n".join(sorted(found)))
            return 0
        manifest = get(args.worker)
        if manifest is None:
            raise AdapterError(f"no adapter named {args.worker!r}")
        if args.command == "binary":
            print(manifest["binary"])
            return 0
        if args.command == "summary":
            print(decode_summary(manifest, Path(args.log).read_text(errors="replace"))[:2000])
            return 0
        words = build_command(manifest, mode=args.mode, work=args.work, brief_file=args.brief_file, brief_text=args.brief_text,
                              model=args.model, effort=args.effort, job_dir=args.job_dir, job_id=args.job_id,
                              mcp_path=args.mcp_path, binary=args.binary)
        sys.stdout.buffer.write(b"".join(word.encode() + b"\0" for word in words))
        return 0
    except AdapterError as error:
        print(f"rig adapters: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
