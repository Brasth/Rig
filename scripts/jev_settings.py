#!/usr/bin/env python3
"""Global Jev credential and per-project picker settings."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

import jev_provider


def _routing_lines(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "[routing]"), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[routing]")
        lines.extend(f'{key} = "{value}"' for key, value in updates.items())
        return "\n".join(lines) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    seen = set()
    for index in range(start + 1, end):
        key = lines[index].split("=", 1)[0].strip() if "=" in lines[index] else ""
        if key in updates:
            lines[index] = f'{key} = "{updates[key]}"'
            seen.add(key)
    for key, value in updates.items():
        if key not in seen:
            lines.insert(end, f'{key} = "{value}"')
            end += 1
    return "\n".join(lines) + "\n"


def update_project(repo: Path, **updates: str) -> None:
    path = Path(repo) / ".rig" / "harness.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else "parent = \"codex\"\n"
    path.write_text(_routing_lines(previous, updates), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rig provider jev")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").add_argument("--json", action="store_true")
    sub.add_parser("setup")
    sub.add_parser("remove")
    project = sub.add_parser("project")
    project.add_argument("--repo", required=True)
    project.add_argument("--engine", choices=("local", "jev"))
    project.add_argument("--objective", choices=("quality", "balanced", "speed", "cost"))
    args = parser.parse_args(argv)
    if args.command == "status":
        value = jev_provider.status()
        print(json.dumps(value, sort_keys=True) if args.json else f"Jev: {value['source']}")
        return 0
    if args.command == "setup":
        if not sys.stdin.isatty():
            print("rig provider jev setup requires a TTY; use RIG_API_JEV_KEY for CI", file=sys.stderr)
            return 2
        jev_provider.store_key(getpass.getpass("Jev API key: "))
        print("Jev key saved in macOS Keychain")
        return 0
    if args.command == "remove":
        print("Jev key removed" if jev_provider.delete_key() else "No Jev key was stored")
        return 0
    updates = {key: value for key, value in {"engine": args.engine, "objective": args.objective}.items() if value}
    if not updates:
        parser.error("project needs --engine and/or --objective")
    update_project(Path(args.repo), **updates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
