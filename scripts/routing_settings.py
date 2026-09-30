"""Transactional domain settings and read-only previews for the human TUI."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

import catalog
import harness
import route
import routing_config as config
import routing_domains as domains
import routing_evidence


class StaleSettings(config.ConfigError):
    """The file changed since the editor opened; reopen instead of overwriting."""


def content_fingerprint(content: bytes | None) -> str:
    return hashlib.sha256(b"absent" if content is None else b"file\0" + content).hexdigest()


def _read_content(path: Path) -> bytes | None:
    # Do not follow an unexpected link or replace a directory/device.
    if path.is_symlink():
        raise config.ConfigError("routing.json must not be a symlink")
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise config.ConfigError("routing.json must be a regular file")
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _decode(content: bytes | None) -> dict | None:
    if content is None:
        return None
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=config._reject_duplicate_keys)
    except (UnicodeError, ValueError) as exc:
        raise config.ConfigError(f"invalid routing.json: {exc}") from exc
    if not isinstance(value, dict):
        raise config.ConfigError("routing.json must be a JSON object")
    return value


def _project(repo: Path) -> dict:
    harness.assert_project_enabled(repo)
    if (Path(repo) / ".rig").is_symlink():
        raise config.ConfigError("routing settings require a real project .rig directory")
    return harness.parse_harness(harness.harness_path(repo))


@dataclass(frozen=True)
class SettingsDocument:
    raw: dict | None
    expected_fingerprint: str
    harness: dict


def open_settings(repo: Path) -> SettingsDocument:
    parsed = _project(repo)
    content = _read_content(config.routing_json_path(repo))
    raw = _decode(content)
    config.validate_candidate(raw, harness=parsed)
    return SettingsDocument(raw, content_fingerprint(content), parsed)


def domain_candidate(raw: dict | None, edits: dict) -> dict | None:
    """Copy only edited domain policies, preserving unrelated JSON settings.

    None removes an explicit domain override. No edits keep schemas 1–3 intact;
    the first domain edit upgrades to schema 4 without changing other settings.
    """
    if not edits:
        return copy.deepcopy(raw)
    candidate = copy.deepcopy(raw) if raw is not None else {"schema_version": 4}
    candidate["schema_version"] = 4
    policies = candidate.setdefault("domains", {})
    for name, policy in edits.items():
        if policy is None:
            policies.pop(name, None)
        else:
            policies[name] = copy.deepcopy(policy)
    if not policies:
        candidate.pop("domains", None)
    return candidate


def save_settings(repo: Path, candidate: dict | None, *, expected_fingerprint: str) -> dict:
    """Validate then atomically replace, with a serialized optimistic content check.

    A stale or invalid candidate never changes the original. Concurrent editors
    cooperate through the project lock; external file edits are checked again
    immediately before replace. This is not a general filesystem transaction.
    """
    repo = Path(repo)
    parsed = _project(repo)
    config.validate_candidate(candidate, harness=parsed)
    path = config.routing_json_path(repo)
    lock_path = path.with_name(".routing-settings.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    tmp = None
    try:
        with os.fdopen(fd, "a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = _read_content(path)
            if content_fingerprint(previous) != expected_fingerprint:
                raise StaleSettings("routing.json changed; cancel and reopen Settings before saving")
            if candidate == _decode(previous):
                return {"status": "unchanged", "fingerprint": expected_fingerprint}
            if candidate is None:
                raise config.ConfigError("removing routing.json is not supported by this editor")
            payload = (json.dumps(candidate, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
            mode = stat.S_IMODE(path.stat().st_mode) if previous is not None else 0o600
            tmp_fd, name = tempfile.mkstemp(prefix=".routing-", suffix=".tmp", dir=path.parent)
            tmp = Path(name)
            with os.fdopen(tmp_fd, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _project(repo)  # A concurrently disabled project must remain disabled.
            if content_fingerprint(_read_content(path)) != expected_fingerprint:
                raise StaleSettings("routing.json changed; cancel and reopen Settings before saving")
            os.replace(tmp, path)
            tmp = None
            return {"status": "saved", "fingerprint": content_fingerprint(payload)}
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def preview(repo: Path, candidate: dict | None, *, role="implement", case="", task_domain="general",
            complexity="", risk="", uncertainty="", exclude="", research_sources=None,
            review_mode="standalone", writer_job_id="", catalog_snapshot=None) -> dict:
    """Use route.pick with current local gates and detached catalog metadata.

    Nothing is launched, refreshed, persisted or sent to a routing provider.
    Results are ephemeral and cannot be used as launch evidence.
    """
    repo = Path(repo)
    parsed = _project(repo)
    if config.harness_mode(parsed) != "smart":
        raise config.ConfigError("routing preview requires smart mode; current project uses legacy")
    cfg = config.validate_candidate(candidate, harness=parsed)
    live = harness.live_parent()
    effective = harness.effective_workers(repo, live)
    snapshot = catalog_snapshot if catalog_snapshot is not None else catalog.readonly_catalog_snapshot()
    return route.pick(live, effective, role, case, repo=repo, task_domain=task_domain,
                      complexity=complexity, risk=risk, uncertainty=uncertainty, exclude=exclude,
                      research_sources=research_sources, review_mode=review_mode, writer_job_id=writer_job_id,
                      preview_config=cfg, catalog_snapshot=snapshot)


def preview_lines(choice: dict) -> list[str]:
    routing = choice["routing"]
    selected = routing.get("selected_profile") or {}
    parent = choice.get("executor_kind") == "parent"
    effort = choice.get("effort") or ("not specified" if selected else "unknown")
    lines = [
        "Read-only preview; rerun pick before launch (no admission authority)",
        f"Result: {choice.get('spawn') or 'none'}{' (parent only)' if parent else ''}",
        f"worker={choice.get('worker') or 'unknown'} model={choice.get('model') or 'unknown'}",
        f"effort={effort} tier={selected.get('tier') or routing.get('required_tier') or 'n/a'}",
        str(choice.get("reason") or ""),
    ]
    if routing.get("picker", {}).get("fallback") == "preview-provider-not-invoked":
        lines.append("Jev was not invoked: local fallback shown; a live Jev pick may differ")
    lines.extend(routing_evidence.explain_lines(choice))
    return lines


def settings_rows(repo: Path) -> list[dict]:
    try:
        document = open_settings(repo)
        cfg = config.validate_candidate(document.raw, harness=document.harness)
        return [{"id": "domains", "state": "configured", "text":
                 f"Domain routing: {len(cfg.domains)} overrides (e edit / preview)"}]
    except (OSError, ValueError) as exc:
        return [{"id": "domains", "state": "blocked", "text": f"Domain routing: {exc}"}]
