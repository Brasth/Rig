#!/usr/bin/env python3
"""Rig terminal companion entrypoints. Host execution never goes through MCP."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "exec":
        import ui_launch
        return ui_launch.main(argv[1:])
    if argv and argv[0] in {"enable", "disable"}:
        import ui_shell
        return ui_shell.main(argv)
    parser = argparse.ArgumentParser(description="Rig status and popup manager")
    parser.add_argument("command", choices=["serve", "status", "popup", "sessions", "attach"])
    parser.add_argument("id", nargs="?")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--session", default="")
    parser.add_argument("--add", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--manager-key", default="F8")
    parser.add_argument("--add-key", default="F9")
    args = parser.parse_args(argv)
    if args.command == "serve":
        from ui_service import serve
        return serve(Path(args.repo))
    if args.command == "popup":
        import ui_popup
        return ui_popup.main(Path(args.repo), args.session, add=args.add)
    if args.command in {"sessions", "attach"}:
        import ui_launch
        if args.command == "attach":
            return ui_launch.attach(args.id or args.session)
        value = ui_launch.sessions()
        if value is not None:
            print(json.dumps(value, indent=2) if args.json else "\n".join(value))
        return 0
    if args.command == "status":
        from ui_service import request
        from ui_store import runtime_directory
        try:
            value = request(runtime_directory(Path(args.repo)) / "control.sock",
                            {"op": "snapshot", "session": args.session, "width": args.width,
                             "manager_key": args.manager_key, "add_key": args.add_key}, timeout=0.3)
            line = value.get("status_line") or "Rig · status unavailable · F8 Manage"
        except (OSError, ValueError):
            value = {"error": "Rig observer unavailable"}
            line = "Rig · status unavailable · F8 Manage"
        # tmux receives literal data, never a worker-supplied format expansion.
        print(json.dumps(value, ensure_ascii=False) if args.json else line.replace("#", "##"))
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"rig ui: {error}", file=sys.stderr)
        raise SystemExit(1)
