#!/usr/bin/env python3
"""Forward Codex plugin UserPromptSubmit stdin to the kit queue hook."""
from __future__ import annotations

import os
import runpy
from pathlib import Path

kit = Path(os.environ.get("RIG_HOME") or (Path.home() / ".rig"))
hook = kit / "scripts" / "queue_submit_hook.py"
if not hook.is_file():
    raise SystemExit(0)
runpy.run_path(str(hook), run_name="__main__")
