"""Selection of Rig's optional, parent-only desktop/browser skill bundle.

Bundled source files are not consent. Only a remembered backend opt-in allows
setup/init to expose these skills to host discovery. Never remove existing
skills here: a skipped install is not an uninstall request.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

OPTIONAL_SKILLS = frozenset({"computer-use", "computer-test"})
PREFERENCES = ("cua-driver.json", "browser-skill.json")


def enabled(root: Path) -> bool:
    for name in PREFERENCES:
        try:
            data = json.loads((root / name).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("opt_in") is True:
            return True
    return False


if __name__ == "__main__":
    print("true" if enabled(Path(sys.argv[1])) else "false")
