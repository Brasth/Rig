"""Render extracted Mermaid diagrams as terminal text via Node."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from diagram_preview import Diagram, DiagramError

RENDER_TIMEOUT_SEC = 20
MAX_RENDER_OUTPUT = 2 * 1024 * 1024
NODE_MAX_OLD_SPACE_MB = 256
SUPPORTED_TYPES = "flowchart, state, sequence, class, ER, and XYChart"
_UNSUPPORTED = re.compile(
    r"^(?:pie|gitGraph|mindmap|journey|gantt|quadrantChart|timeline|"
    r"sankey(?:-beta)?|block(?:-beta)?|kanban|requirementDiagram|"
    r"C4Context|architecture(?:-beta)?)\b",
    re.I,
)
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_OTHER_ESC = re.compile(r"\x1b[@-Z\\-_]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")


@dataclass(frozen=True)
class RenderedDiagram:
    ok: bool
    text: str = ""
    error: str = ""


def sanitize(text: str) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _OTHER_ESC.sub("", text)
    text = text.replace("\x1b", "")
    return _CONTROLS.sub("", text)


def renderer_path() -> Path:
    path = Path(__file__).resolve().with_name("diagram-renderer.mjs")
    bundle = Path(__file__).resolve().with_name("beautiful-mermaid-ascii.mjs")
    if not path.is_file():
        raise DiagramError(f"missing diagram renderer: {path}")
    if not bundle.is_file():
        raise DiagramError(f"missing ASCII renderer bundle: {bundle}")
    return path


def find_node(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    found = shutil.which("node", path=env.get("PATH"))
    if found:
        return found
    raise DiagramError(
        "Node.js is required to render diagrams. "
        "Install Node.js from https://nodejs.org and ensure `node` is on PATH."
    )


def _first_directive(source: str) -> str:
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        return stripped
    return ""


def unsupported_reason(source: str) -> str | None:
    header = _first_directive(source)
    if not header or not _UNSUPPORTED.match(header):
        return None
    kind = header.split()[0]
    return (
        f"unsupported diagram type {kind}. "
        f"rig diagram renders {SUPPORTED_TYPES}; it does not render every Mermaid type"
    )


def _node_env(env: dict[str, str] | None) -> dict[str, str]:
    child = dict(os.environ if env is None else env)
    child["NO_COLOR"] = "1"
    child.pop("FORCE_COLOR", None)
    return child


def render_one(
    source: str,
    use_ascii: bool,
    *,
    node: str | None = None,
    renderer: Path | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> RenderedDiagram:
    reason = unsupported_reason(source)
    if reason:
        return RenderedDiagram(ok=False, error=reason)
    node_bin = node or find_node(env)
    renderer_file = renderer or renderer_path()
    wait = RENDER_TIMEOUT_SEC if timeout is None else timeout
    payload = json.dumps(
        {"source": source, "useAscii": bool(use_ascii)},
        ensure_ascii=False,
    ).encode("utf-8")
    kwargs: dict = {
        "input": payload,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": wait,
        "env": _node_env(env),
    }
    argv = [node_bin, f"--max-old-space-size={NODE_MAX_OLD_SPACE_MB}", str(renderer_file)]
    try:
        proc = subprocess.run(argv, **kwargs)
    except subprocess.TimeoutExpired:
        return RenderedDiagram(ok=False, error="diagram render timed out")
    except OSError as exc:
        return RenderedDiagram(ok=False, error=f"failed to start renderer: {exc}")
    raw_out = proc.stdout or b""
    if len(raw_out) > MAX_RENDER_OUTPUT:
        return RenderedDiagram(ok=False, error="renderer output too large")
    try:
        decoded = raw_out.decode("utf-8")
    except UnicodeDecodeError:
        return RenderedDiagram(ok=False, error="renderer output is not valid UTF-8")
    if not decoded.strip():
        err = sanitize((proc.stderr or b"").decode("utf-8", "replace"))[:500]
        detail = f": {err}" if err else ""
        if proc.returncode:
            return RenderedDiagram(
                ok=False, error=f"renderer exited {proc.returncode}{detail}"
            )
        return RenderedDiagram(ok=False, error="renderer returned no output")
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError:
        err = sanitize((proc.stderr or b"").decode("utf-8", "replace"))[:500]
        detail = f": {err}" if err else ""
        return RenderedDiagram(ok=False, error=f"renderer returned invalid JSON{detail}")
    if not isinstance(data, dict):
        return RenderedDiagram(ok=False, error="renderer returned invalid JSON")
    if data.get("ok") is True:
        text = data.get("text")
        if not isinstance(text, str):
            return RenderedDiagram(ok=False, error="renderer returned non-text")
        return RenderedDiagram(ok=True, text=text)
    error = data.get("error")
    if not isinstance(error, str) or not error.strip():
        error = "diagram render failed"
    return RenderedDiagram(ok=False, error=error)


def format_diagram(diagram: Diagram, rendered: RenderedDiagram) -> str:
    heading = sanitize(diagram.label)
    if rendered.ok:
        body = sanitize(rendered.text).rstrip("\n")
        return f"{heading}\n\n{body}"
    error = sanitize(rendered.error).strip() or "diagram render failed"
    source = sanitize(diagram.source).rstrip("\n")
    return f"{heading}\nerror: {error}\nsource:\n{source}"


def render_diagrams(
    diagrams: list[Diagram],
    *,
    use_ascii: bool = False,
    env: dict[str, str] | None = None,
    node: str | None = None,
    renderer: Path | None = None,
    timeout: float | None = None,
) -> tuple[str, int]:
    node_bin = node or find_node(env)
    renderer_file = renderer or renderer_path()
    blocks: list[str] = []
    failed = 0
    for diagram in diagrams:
        rendered = render_one(
            diagram.source,
            use_ascii,
            node=node_bin,
            renderer=renderer_file,
            timeout=timeout,
            env=env,
        )
        if not rendered.ok:
            failed += 1
        blocks.append(format_diagram(diagram, rendered))
    text = "\n\n".join(blocks)
    if text and not text.endswith("\n"):
        text += "\n"
    return sanitize(text), failed


def tmux_socket(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = env.get("TMUX") or ""
    if not value:
        raise DiagramError("--popup requires a tmux session (TMUX is unset)")
    socket = value.split(",", 1)[0]
    if not socket:
        raise DiagramError("--popup could not read tmux socket from TMUX")
    return socket


def popup_argv(tmux: str, socket: str, pane: str, less: str, path: Path) -> list[str]:
    command = shlex.join([less, "-S", str(path)])
    return [
        tmux,
        "-S",
        socket,
        "display-popup",
        "-t",
        pane,
        "-E",
        "-w",
        "90%",
        "-h",
        "80%",
        "--",
        command,
    ]


def show_popup(
    text: str,
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    runner=None,
) -> None:
    env = os.environ if env is None else env
    socket = tmux_socket(env)
    pane = env.get("TMUX_PANE") or ""
    if not pane:
        raise DiagramError("--popup requires TMUX_PANE")
    path_var = env.get("PATH")
    tmux = shutil.which("tmux", path=path_var)
    if not tmux:
        raise DiagramError("--popup requires `tmux` on PATH")
    less = shutil.which("less", path=path_var)
    if not less:
        raise DiagramError("--popup requires `less` on PATH")
    fd, name = tempfile.mkstemp(prefix="rig-diagram-", suffix=".txt", text=True)
    path = Path(name)
    run = subprocess.run if runner is None else runner
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
        argv = popup_argv(tmux, socket, pane, less, path)
        try:
            proc = run(argv, timeout=timeout, capture_output=True)
        except subprocess.TimeoutExpired as exc:
            raise DiagramError("--popup timed out waiting for tmux display-popup") from exc
        except OSError as exc:
            raise DiagramError(f"--popup failed to run tmux: {exc}") from exc
        if proc.returncode:
            err = sanitize((proc.stderr or b"").decode("utf-8", "replace")).strip()[:800]
            raise DiagramError(f"--popup failed: {err or proc.returncode}")
    finally:
        try:
            path.unlink()
        except OSError:
            pass
