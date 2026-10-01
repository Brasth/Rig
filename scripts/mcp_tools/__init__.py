"""Domain MCP handlers. Do not import the rig_mcp facade from this package."""
from __future__ import annotations


def load_registry():
    """Return tool name to handler. Each lookup stays on the live facade context."""
    from mcp_tools import browser, context_recipes, jobs, queue_memory, reports, routing, workflows

    registry = {}
    for module in (browser, context_recipes, jobs, queue_memory, reports, routing, workflows):
        for name, handler in module.HANDLERS.items():
            if name in registry:
                raise RuntimeError("duplicate MCP handler: " + name)
            if not callable(handler):
                raise RuntimeError("MCP handler is not callable: " + name)
            registry[name] = handler
    return registry
