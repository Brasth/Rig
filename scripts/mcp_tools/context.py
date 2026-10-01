"""Live facade bindings for MCP handlers."""
from __future__ import annotations


class ToolContext:
    """Read facade attributes on every access so callers can monkeypatch them."""

    def __init__(self, bindings):
        object.__setattr__(self, "_bindings", bindings)

    def __getattr__(self, name):
        return getattr(self._bindings, name)


class CallState:
    """One tool call after the facade applies activation, repo, and child gates."""

    def __init__(self, name, args, repo, *, on_tick=None, wait_paths=None,
                 cancel_event=None, wait_targets=None):
        self.name = name
        self.args = args
        self.repo = repo
        self.on_tick = on_tick
        self.wait_paths = wait_paths
        self.cancel_event = cancel_event
        self.wait_targets = wait_targets
