#!/usr/bin/env python3
"""Which workers can run inside the workspace container, and what changes there.

Data, not behaviour: the provider reads this. A worker is supported only when its
MCP wiring is scoped to its own job directory, because the container sees the
repository and the Rig runtime read-only/at the same path but not the user's
global agent configuration. Unsupported workers are refused with the reason.
"""
from __future__ import annotations

CREDENTIAL_MODES = ("mount-ro", "none")

_GLOBAL_CONFIG = "wires its MCP through global host configuration that a container cannot share"

MODES = {
    "claude": {
        "supported": True, "binary": "claude",
        # Job-scoped: --mcp-config JOB/mcp.json --strict-mcp-config.
        "permission_replace": [],
        "credential_paths": [".claude", ".claude.json"],
    },
    "codex": {
        "supported": True, "binary": "codex",
        # The container is the boundary; Codex's own sandbox inside it would be a second one.
        "permission_replace": [(["-s", "workspace-write"], ["-s", "danger-full-access"])],
        "credential_paths": [".codex"],
    },
    "grok": {"supported": False, "reason": "grok inherits ~/.grok/config.toml; it " + _GLOBAL_CONFIG},
    "cursor": {"supported": False, "reason": "cursor uses a host plugin directory and a runtime tripwire that cannot be scoped to a container"},
    "opencode": {"supported": False, "reason": "opencode's config is the whole application config; it " + _GLOBAL_CONFIG},
    "omp": {"supported": False, "reason": "omp passes its MCP through an environment path on the host; it " + _GLOBAL_CONFIG},
    "pi": {"supported": False, "reason": "pi's agent directory is the whole agent configuration; it " + _GLOBAL_CONFIG},
    "agy": {"supported": False, "reason": "agy edits global host settings around each launch; it " + _GLOBAL_CONFIG},
    "devin": {"supported": False, "reason": "devin edits repository and host configuration around each launch; it " + _GLOBAL_CONFIG},
    "mimo": {"supported": False, "reason": "mimo needs a host configuration directory; it " + _GLOBAL_CONFIG},
}


def mode(worker: str) -> dict:
    if worker not in MODES:
        import adapters
        manifest = adapters.get(worker)
        if manifest is not None:
            if not manifest["container"]["supported"]:
                return {"supported": False, "reason": f"adapter {worker} does not declare container support"}
            info = adapters.container_mode(manifest)
            return {"supported": True, "binary": info["binary"], "permission_replace": [],
                    "credential_paths": info["credential_paths"]}
    return MODES.get(worker) or {"supported": False, "reason": f"unknown worker {worker!r}"}


def supported_workers() -> list[str]:
    import adapters
    found = [name for name, value in MODES.items() if value.get("supported")]
    return found + [name for name in adapters.names() if mode(name).get("supported")]


def apply_permissions(worker: str, argv: list[str]) -> list[str]:
    """Replace each configured consecutive token run; leave everything else as it was."""
    out = list(argv)
    for old, new in mode(worker).get("permission_replace", []):
        i = 0
        while i <= len(out) - len(old):
            if out[i:i + len(old)] == old:
                out[i:i + len(old)] = new
                i += len(new)
            else:
                i += 1
    return out


def credential_problem(worker: str, credentials: dict) -> str:
    """Empty when the worker may run in a container; otherwise what to do about it."""
    info = mode(worker)
    if not info.get("supported"):
        return f"worker '{worker}' cannot run in a container: {info.get('reason')}"
    choice = (credentials or {}).get(worker)
    if choice not in CREDENTIAL_MODES:
        return (f"worker '{worker}' has no credential choice for container mode; set it under [sandbox.workers] in "
                f".rig/harness.toml: {worker} = \"mount-ro\" (its login directory is mounted read-only) or \"none\"")
    return ""
