"""Small, explicit fixtures for tests that exercise an enabled Rig project.

Do not use this for lifecycle tests covering uninitialized/disabled projects.
It creates no installed integrations, credentials, providers, or repository files.
"""
from pathlib import Path


def initialize_project(repo: Path) -> Path:
    repo = Path(repo)
    folder = repo / ".rig"
    folder.mkdir(parents=True, exist_ok=True)
    harness = folder / "harness.toml"
    if not harness.exists():
        harness.write_text('[project]\nenabled = true\n[workers]\n')
    return repo
