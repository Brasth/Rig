#!/usr/bin/env python3
"""Job-scoped child MCP config and the required inbox handshake.

After inbox, children may call doing/note/ask/own show/memory and
rig_job_coordination_request. Parent workflow tools stay hidden.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import job_metadata  # noqa: E402
import jobs as rig_jobs  # noqa: E402

PROTOCOL = 1
CONNECTED = "connected"
UNKNOWN = "unknown"
LEGACY = "legacy"
MISSING_REASON = "child MCP handshake missing"
INBOX_FIRST = "first Rig operation must be rig_job_inbox"
CURSOR_REASON = (
    "Cursor CLI has no isolated job-scoped MCP; excluded until a safe --mcp-config exists"
)
SCOPED_WORKERS = frozenset({"grok", "codex", "claude", "opencode", "omp", "pi", "agy", "devin"})
DEVIN_MCP_REL = Path(".devin") / "mcp_config.local.json"
DEVIN_LOCK_REL = Path(".rig") / "devin.lock"
DEVIN_STATE = "devin-mcp-state.json"
DEVIN_BACKUP = "devin-mcp_config.local.json.bak"
DEVIN_LIVE = frozenset(
    {
        "reserved",
        "running",
        "ask",
        "stop-requested",
        "stop-unconfirmed",
        "native-cancel-required",
    }
)
BOOTSTRAP_TOOLS = frozenset({"rig_job_inbox", "permission_prompt"})
HANDSHAKE_TOOL = "rig_job_inbox"
CHILD_COORDINATION_TOOL = "rig_job_coordination_request"
ALLOWED_JOB_MCP_SERVERS = frozenset({"rig", "rig-ask"})
FORBIDDEN_MCP_SERVERS = frozenset({
    "cua-driver", "chrome-devtools", "chrome_devtools", "playwright", "figma",
})


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def python_bin() -> str:
    for path in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return sys.executable or "python3"


def _home() -> Path:
    return Path.home()


SCHEMA_LABEL = {
    "grok": "mcp_servers.rig",
    "codex": "mcp_servers.rig",
    "opencode": "mcp.rig",
    "omp": "mcpServers.rig",
    "pi": "mcpServers.rig",
    "agy": "mcpServers.rig",
    "claude": "job mcp.json",
    "devin": "job .devin/mcp_config.local.json",
}


def schema_label(worker: str) -> str:
    return SCHEMA_LABEL.get((worker or "").strip(), "mcp")


def config_path(worker: str, home: Path | None = None) -> Path | None:
    """Installed MCP config path from `rig setup` / doctor. Claude is per-job."""
    name = (worker or "").strip()
    root = Path(home) if home is not None else _home()
    if name == "grok":
        return root / ".grok" / "config.toml"
    if name == "codex":
        return root / ".codex" / "config.toml"
    if name == "opencode":
        raw = os.environ.get("OPENCODE_CONFIG", "").strip()
        return Path(raw).expanduser() if raw else root / ".config" / "opencode" / "opencode.json"
    if name == "omp":
        raw = os.environ.get("OMP_MCP", "").strip()
        return Path(raw).expanduser() if raw else root / ".omp" / "mcp.json"
    if name == "pi":
        raw = os.environ.get("PI_CODING_AGENT_DIR") or os.environ.get("PI_AGENT_DIR") or ""
        base = Path(raw).expanduser() if raw.strip() else root / ".pi" / "agent"
        return base / "mcp.json"
    if name == "agy":
        raw = os.environ.get("AGY_MCP", "").strip()
        return Path(raw).expanduser() if raw else root / ".gemini" / "config" / "mcp_config.json"
    return None


def _toml_loads():
    try:
        import tomllib
        return tomllib.loads
    except ImportError:
        try:
            import tomli
            return tomli.loads
        except ImportError:
            return None


def _simple_toml_value(raw: str):
    text = raw.strip()
    if " #" in text and not (text.startswith('"') or text.startswith("'")):
        text = text.split(" #", 1)[0].strip()
    if text in {"true", "True"}:
        return True
    if text in {"false", "False"}:
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        items = []
        for part in inner.split(","):
            items.append(_simple_toml_value(part))
        return items
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    if text.lstrip("-").isdigit():
        return int(text)
    raise ValueError("unsupported toml value")


def _simple_toml(text: str) -> dict | None:
    tables: dict[str, dict] = {}
    root: dict = {}
    current = None
    try:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped[1:-1].strip()
                if not current:
                    return None
                tables.setdefault(current, {})
                continue
            if "=" not in stripped:
                return None
            key, _, rest = stripped.partition("=")
            key = key.strip()
            if not key:
                return None
            value = _simple_toml_value(rest)
            if current is None:
                root[key] = value
            else:
                tables[current][key] = value
    except ValueError:
        return None
    nested: dict = dict(root)
    for name, values in tables.items():
        cursor = nested
        parts = [part for part in name.split(".") if part]
        if not parts:
            return None
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        leaf = cursor.get(parts[-1])
        if isinstance(leaf, dict):
            leaf.update(values)
        else:
            cursor[parts[-1]] = dict(values)
    return nested


def _load_toml(path: Path) -> dict | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    loader = _toml_loads()
    if loader is not None:
        try:
            data = loader(text)
        except (SystemExit, Exception):
            return None
        return data if isinstance(data, dict) else None
    return _simple_toml(text)


def _command_executable(command: str) -> bool:
    if not command or "\0" in command:
        return False
    path = Path(command).expanduser()
    try:
        if path.is_file() and os.access(path, os.X_OK):
            return True
    except OSError:
        return False
    return bool(shutil.which(command))


def _bool_flag(value):
    if value is True or value is False:
        return value
    if value in (1, 0):
        return bool(value)
    if isinstance(value, str) and value in {"true", "True", "false", "False"}:
        return value in {"true", "True"}
    return None


def _stdio_entry_ready(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    if "disabled" in entry:
        disabled = _bool_flag(entry.get("disabled"))
        if disabled is None or disabled is True:
            return False
    if "enabled" in entry:
        enabled = _bool_flag(entry.get("enabled"))
        if enabled is None or enabled is False:
            return False
    kind = str(entry.get("type") or "stdio").strip().lower()
    if kind not in {"", "stdio", "local", "command"}:
        return False
    command = entry.get("command")
    args = entry.get("args")
    if isinstance(command, list):
        argv = command
        if args not in (None, []):
            return False
    elif isinstance(command, str):
        if args is None:
            args = []
        if not isinstance(args, list):
            return False
        argv = [command] + list(args)
    else:
        return False
    if not argv or any(not isinstance(item, str) or not item or "\0" in item for item in argv):
        return False
    return _command_executable(argv[0])


def _toml_has_rig(path: Path) -> bool:
    if not path.is_file():
        return False
    data = _load_toml(path)
    if not isinstance(data, dict):
        return False
    servers = data.get("mcp_servers")
    entry = servers.get("rig") if isinstance(servers, dict) else None
    return _stdio_entry_ready(entry)


def _json_rig_entry(data: dict, worker: str):
    if worker == "opencode":
        mcp = data.get("mcp")
        if not isinstance(mcp, dict):
            return None
        servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else mcp
        return servers.get("rig")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    return servers.get("rig")


def _json_has_rig(path: Path, worker: str) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return _stdio_entry_ready(_json_rig_entry(data, worker))


def _contains_pi_adapter(value) -> bool:
    if isinstance(value, str):
        return "pi-mcp-adapter" in value
    if isinstance(value, list):
        return any(_contains_pi_adapter(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_pi_adapter(item) for item in value.values())
    return False


def _pi_adapter_ready(mcp_path: Path) -> bool:
    settings = mcp_path.parent / "settings.json"
    try:
        data = json.loads(settings.read_text()) if settings.is_file() else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return _contains_pi_adapter(data)


def worker_binary(worker: str) -> str:
    name = "cursor-agent" if worker == "cursor" else (worker or "").strip()
    return shutil.which(name) or ""


def worker_mcp_ready(worker: str, *, home: Path | None = None) -> tuple[bool, str]:
    name = (worker or "").strip()
    if name == "cursor":
        return False, CURSOR_REASON
    if not name or name == "parent":
        return False, "parent work does not use child MCP"
    if name not in SCOPED_WORKERS:
        return False, f"{name} has no isolated job-scoped MCP"
    if not worker_binary(name):
        return False, f"binary '{name}' not on PATH"
    if name == "claude" or name == "devin":
        return True, ""
    path = config_path(name, home)
    if path is None:
        return False, f"{name} has no isolated job-scoped MCP"
    if name in {"grok", "codex"}:
        if not _toml_has_rig(path):
            return False, f"{name} MCP missing — run: rig setup"
        return True, ""
    if not _json_has_rig(path, name):
        return False, f"{name} MCP missing — run: rig setup"
    if name == "pi" and not _pi_adapter_ready(path):
        return False, "pi MCP adapter missing — pi install npm:pi-mcp-adapter"
    return True, ""


def job_env(job_dir: Path, job_id: str, repo: Path) -> dict[str, str]:
    return {
        "RIG_JOB_DIR": str(Path(job_dir)),
        "RIG_JOB_ID": str(job_id),
        "RIG_REPO": str(Path(repo)),
    }


def _server(job_dir: Path, job_id: str, repo: Path) -> dict:
    return {
        "command": python_bin(),
        "args": [str(HERE / "rig_mcp.py")],
        "env": job_env(job_dir, job_id, repo),
    }


def _toml_literal(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_literal(item) for item in values) + "]"


def payload_server_names(payload: dict) -> list[str]:
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    if not isinstance(servers, dict):
        return []
    return list(servers)


def assert_job_mcp_payload(payload: dict) -> dict:
    names = payload_server_names(payload)
    extra = [name for name in names if name not in ALLOWED_JOB_MCP_SERVERS]
    if extra:
        raise RuntimeError("job MCP payload must only contain rig servers, not " + ", ".join(extra))
    forbidden = sorted(set(names) & FORBIDDEN_MCP_SERVERS)
    if forbidden:
        raise RuntimeError("job MCP payload forbids " + ", ".join(forbidden))
    return payload


def mcp_payload(job_dir: Path, job_id: str, repo: Path) -> dict:
    server = _server(job_dir, job_id, repo)
    return assert_job_mcp_payload({"mcpServers": {"rig-ask": dict(server), "rig": dict(server)}})


def _codex_isolation_argv(server: dict) -> list[str]:
    """Ignore ~/.codex/config.toml and inject only job-scoped mcp_servers.rig via -c."""
    command = str(server.get("command") or "")
    args = [str(item) for item in (server.get("args") or [])]
    argv = [
        "--ignore-user-config",
        "-c", f"mcp_servers.rig.command={_toml_literal(command)}",
        "-c", f"mcp_servers.rig.args={_toml_array(args)}",
        "-c", "mcp_servers.rig.enabled=true",
        "-c", "mcp_servers.rig.startup_timeout_sec=8",
    ]
    for key, value in (server.get("env") or {}).items():
        if not key or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_" for ch in key):
            continue
        argv.extend(["-c", f"mcp_servers.rig.env.{key}={_toml_literal(str(value))}"])
    return argv


def write_job_mcp(job_dir: Path, job_id: str, repo: Path, worker: str) -> dict:
    """Job MCP is always rig/rig-ask only.

    Isolation is only as strong as the CLI:
    - claude: --mcp-config JOB/mcp.json --strict-mcp-config
    - codex: --ignore-user-config plus -c mcp_servers.rig (not ~/.codex/config.toml)
    - omp/agy: OMP_MCP / AGY_MCP point at the job mcp.json
    - grok: print-mode has no --mcp-config; inherits ~/.grok/config.toml. Do not pretend.
    - opencode: OPENCODE_CONFIG is the full app config, not an MCP-only overlay. Do not pretend.
    - pi: PI_CODING_AGENT_DIR is the whole agent dir (auth/skills). Do not hijack it.
    - cursor: excluded until a safe --mcp-config exists
    - devin: repo .devin/mcp_config.local.json
    """
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    ready, reason = worker_mcp_ready(worker)
    path = job_dir / "mcp.json"
    payload = mcp_payload(job_dir, job_id, repo)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    env = dict(job_env(job_dir, job_id, repo))
    argv: list[str] = []
    isolation = "none"
    name = (worker or "").strip()
    if name == "claude":
        argv = ["--mcp-config", str(path), "--strict-mcp-config"]
        isolation = "strict-mcp-config"
    elif name == "codex":
        argv = _codex_isolation_argv(_server(job_dir, job_id, repo))
        isolation = "ignore-user-config"
    elif name == "omp":
        env["OMP_MCP"] = str(path)
        isolation = "OMP_MCP"
    elif name == "agy":
        env["AGY_MCP"] = str(path)
        isolation = "AGY_MCP"
    elif name == "devin":
        isolation = "job-devin-mcp"
    return {
        "ready": ready,
        "reason": reason,
        "path": str(path),
        "argv": argv,
        "env": env,
        "isolation": isolation,
    }


def devin_mcp_path(repo: Path) -> Path:
    return Path(repo) / DEVIN_MCP_REL


def devin_lock_path(repo: Path) -> Path:
    return Path(repo) / DEVIN_LOCK_REL


def _devin_job_live(job_dir: Path) -> bool:
    meta = rig_jobs._read_meta_dict(Path(job_dir))
    if str(meta.get("worker") or "").strip() != "devin":
        return False
    status = str(meta.get("status") or "").strip()
    return status in DEVIN_LIVE


def live_devin_job_ids(repo: Path, *, skip_id: str = "") -> list[str]:
    root = Path(repo) / ".rig" / "jobs"
    if not root.is_dir():
        return []
    skip = (skip_id or "").strip()
    out: list[str] = []
    try:
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    for name in names:
        if name == skip:
            continue
        if _devin_job_live(root / name):
            out.append(name)
    return out


def _lock_holder(repo: Path) -> str:
    path = devin_lock_path(repo)
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def devin_busy_reason(repo: Path, *, skip_id: str = "") -> str:
    live = live_devin_job_ids(repo, skip_id=skip_id)
    if live:
        return f"devin already running in this repo ({live[0]}); one Devin job at a time"
    holder = _lock_holder(repo)
    skip = (skip_id or "").strip()
    if holder and holder != skip:
        job_dir = Path(repo) / ".rig" / "jobs" / holder
        if _devin_job_live(job_dir):
            return f"devin already running in this repo ({holder}); one Devin job at a time"
    return ""


def acquire_devin_lock(repo: Path, job_id: str) -> None:
    job_id = (job_id or "").strip()
    if not job_id:
        raise RuntimeError("devin lock requires a job id")
    busy = devin_busy_reason(repo, skip_id=job_id)
    if busy:
        raise RuntimeError(busy)
    lock = devin_lock_path(repo)
    lock.parent.mkdir(parents=True, exist_ok=True)
    payload = (job_id + "\n").encode("utf-8")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        holder = _lock_holder(repo)
        if holder == job_id:
            return
        if holder and not _devin_job_live(Path(repo) / ".rig" / "jobs" / holder):
            try:
                lock.unlink()
            except OSError:
                pass
            else:
                return acquire_devin_lock(repo, job_id)
        raise RuntimeError(
            f"devin already running in this repo ({holder or 'unknown'}); one Devin job at a time"
        )
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def release_devin_lock(repo: Path, job_id: str) -> None:
    job_id = (job_id or "").strip()
    lock = devin_lock_path(repo)
    holder = _lock_holder(repo)
    # A missing/corrupt state file must never remove another job's lock.
    if not job_id or holder != job_id:
        return
    try:
        lock.unlink()
    except OSError:
        return


def _merge_devin_payload(existing: dict | None, job_dir: Path, job_id: str, repo: Path) -> dict:
    payload = mcp_payload(job_dir, job_id, repo)
    if not isinstance(existing, dict):
        return payload
    merged = dict(existing)
    servers = merged.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    else:
        servers = dict(servers)
    for key, value in payload["mcpServers"].items():
        servers[key] = dict(value)
    merged["mcpServers"] = servers
    return merged


def install_devin_repo_mcp(job_dir: Path, job_id: str, repo: Path) -> dict:
    """Swap in job-scoped Rig stdio MCP. Backup an existing local file; lock the repo."""
    job_dir = Path(job_dir)
    repo = Path(repo)
    job_dir.mkdir(parents=True, exist_ok=True)
    acquire_devin_lock(repo, job_id)
    try:
        path = devin_mcp_path(repo)
        created_dir = not path.parent.is_dir()
        existed = path.is_file()
        existing = None
        if existed:
            raw = path.read_bytes()
            (job_dir / DEVIN_BACKUP).write_bytes(raw)
            try:
                parsed = json.loads(raw.decode("utf-8"))
                existing = parsed if isinstance(parsed, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                existing = None
        payload = _merge_devin_payload(existing, job_dir, job_id, repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")
        state = {
            "job_id": str(job_id),
            "path": str(path),
            "created_file": not existed,
            "created_dir": created_dir,
        }
        (job_dir / DEVIN_STATE).write_text(json.dumps(state, indent=2) + "\n")
        return state
    except Exception:
        release_devin_lock(repo, job_id)
        raise


def restore_devin_repo_mcp(job_dir: Path, repo: Path) -> None:
    """Restore a pre-existing .devin/mcp_config.local.json or remove a file we created."""
    job_dir = Path(job_dir)
    repo = Path(repo)
    state: dict = {}
    state_path = job_dir / DEVIN_STATE
    try:
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state = loaded
    except (OSError, json.JSONDecodeError, UnicodeError):
        state = {}
    path = Path(state.get("path") or devin_mcp_path(repo))
    backup = job_dir / DEVIN_BACKUP
    try:
        if backup.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(backup.read_bytes())
        elif state.get("created_file"):
            try:
                path.unlink()
            except OSError:
                pass
            if state.get("created_dir"):
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
    finally:
        release_devin_lock(repo, str(state.get("job_id") or ""))


def display_status(meta: dict | None, *, running: bool = False) -> str:
    obj = meta if isinstance(meta, dict) else {}
    status = str(obj.get("child_mcp_status") or "").strip()
    if status:
        return status
    if "child_mcp_protocol" in obj or "child_mcp_connected_at" in obj:
        return UNKNOWN
    return UNKNOWN if running else LEGACY


def handshake_connected(job_dir: Path) -> bool:
    meta = rig_jobs._read_meta_dict(Path(job_dir))
    try:
        protocol = int(meta.get("child_mcp_protocol") or 0)
    except (TypeError, ValueError):
        protocol = 0
    return str(meta.get("child_mcp_status") or "") == CONNECTED and protocol == PROTOCOL


def record_handshake(job_dir: Path) -> dict:
    return rig_jobs.patch_meta(
        Path(job_dir),
        child_mcp_status=CONNECTED,
        child_mcp_connected_at=iso_now(),
        child_mcp_protocol=PROTOCOL,
    )


def mark_unknown(job_dir: Path) -> dict:
    """Stamp unknown onto an existing complete job record. Never create a stub."""

    def mutate(meta: dict) -> dict:
        if meta.get("child_mcp_status"):
            return meta
        if not meta.get("job_id") or not meta.get("worker") or not meta.get("status"):
            return meta
        meta["child_mcp_status"] = UNKNOWN
        meta["child_mcp_protocol"] = PROTOCOL
        return meta

    return job_metadata.update_meta(Path(job_dir), mutate, create=False)


def require_inbox(job_dir: Path, name: str) -> str | None:
    """Block non-bootstrap child tools, including coordination request, until inbox handshake."""
    if name in BOOTSTRAP_TOOLS:
        return None
    if handshake_connected(job_dir):
        return None
    return INBOX_FIRST


def require_success(job_dir: Path) -> str | None:
    if handshake_connected(job_dir):
        return None
    return MISSING_REASON


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: child_mcp.py prepare|require-success|install-devin|restore-devin|busy-devin "
            "<job-dir> [job-id repo worker]",
            file=sys.stderr,
        )
        return 2
    cmd = argv[1]
    if cmd == "prepare":
        if len(argv) < 6:
            print("usage: child_mcp.py prepare <job-dir> <job-id> <repo> <worker>", file=sys.stderr)
            return 2
        spec = write_job_mcp(argv[2], argv[3], argv[4], argv[5])
        print(json.dumps(spec))
        return 0
    if cmd == "require-success":
        reason = require_success(argv[2] if len(argv) > 2 else "")
        if reason:
            print(reason, file=sys.stderr)
            return 1
        return 0
    if cmd == "install-devin":
        if len(argv) < 5:
            print("usage: child_mcp.py install-devin <job-dir> <job-id> <repo>", file=sys.stderr)
            return 2
        try:
            spec = install_devin_repo_mcp(argv[2], argv[3], argv[4])
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(spec))
        return 0
    if cmd == "restore-devin":
        if len(argv) < 4:
            print("usage: child_mcp.py restore-devin <job-dir> <repo>", file=sys.stderr)
            return 2
        restore_devin_repo_mcp(argv[2], argv[3])
        return 0
    if cmd == "busy-devin":
        repo = argv[2] if len(argv) > 2 else ""
        skip = argv[3] if len(argv) > 3 else ""
        reason = devin_busy_reason(repo, skip_id=skip)
        if reason:
            print(reason, file=sys.stderr)
            return 1
        return 0
    print(f"unknown child_mcp command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
