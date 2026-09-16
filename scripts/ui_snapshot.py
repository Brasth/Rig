"""Cached, repository-wide metadata for the independent terminal UI."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import admission
import cancellation
import change_evidence
import jobs
import work_queue

MAX_UI_WORKFLOWS = 32
_SECRET_KEYS = frozenset({"owner_token", "token", "credentials", "credentials_path"})
_WORKFLOW_LOCK = threading.Lock()
_WORKFLOW_CACHE = {}
_ATTENTION_STATUSES = frozenset({"attention", "blocked", "cancel-requested"})
_ACTIVE_STATUSES = frozenset({
    "planned", "running", "attention", "blocked", "completed-unverified",
    "failed", "cancel-requested",
})


def _stamp(path):
    try:
        value = path.stat()
        return value.st_dev, value.st_ino, value.st_mtime_ns, value.st_ctime_ns, value.st_size
    except FileNotFoundError:
        return None


def _secret_key(name):
    text = str(name or "").lower()
    return text in _SECRET_KEYS or text.endswith("_token") or "secret" in text or "credential" in text


def scrub_secrets(value):
    """Drop owner tokens and credential fields from UI payloads."""
    if isinstance(value, dict):
        return {key: scrub_secrets(item) for key, item in value.items() if not _secret_key(key)}
    if isinstance(value, list):
        return [scrub_secrets(item) for item in value]
    if isinstance(value, str) and "owner_token" in value.lower():
        return "[redacted]"
    return value


def format_parent_action(action):
    if not action:
        return "none"
    if not isinstance(action, dict):
        return str(action)
    action = scrub_secrets(action)
    kind = str(action.get("kind") or "").strip()
    if not kind:
        return "none"
    parts = [kind]
    for key in ("node_id", "job_id", "request_id", "reason"):
        item = action.get(key)
        if item not in (None, ""):
            parts.append(f"{key} {item}")
    return " ".join(parts)


def public_workflow_row(row):
    """Factual workflow display: id, counts, blocker, next parent action. No ETA."""
    row = scrub_secrets(row if isinstance(row, dict) else {})
    counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
    action = row.get("next_parent_action")
    if action and not isinstance(action, dict):
        action = {"kind": str(action)}
    elif isinstance(action, dict):
        action = scrub_secrets(action) or None
    else:
        action = None

    def _count(*keys):
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                try:
                    return int(row.get(key) or 0)
                except (TypeError, ValueError):
                    return 0
            if key in counts and counts.get(key) not in (None, ""):
                try:
                    return int(counts.get(key) or 0)
                except (TypeError, ValueError):
                    return 0
        return 0

    return {
        "workflow_id": str(row.get("workflow_id") or ""),
        "status": str(row.get("status") or "planned"),
        "accepted": _count("accepted"),
        "required": _count("required"),
        "running": _count("running"),
        "ask": _count("ask"),
        "blocker": str(row.get("blocker") or ""),
        "next_parent_action": action,
        "title": str(row.get("title") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def bound_workflows(rows, *, limit=MAX_UI_WORKFLOWS):
    attention, active, rest = [], [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = row.get("status")
        if status in _ATTENTION_STATUSES:
            attention.append(row)
        elif status in _ACTIVE_STATUSES:
            active.append(row)
        else:
            rest.append(row)
    return (attention + active + rest)[:max(0, int(limit or 0))]


def _workflow_stamp(root):
    folder = Path(root) / ".rig" / "workflows"
    if not folder.is_dir():
        return None
    stamps = [_stamp(folder)]
    try:
        for path in sorted(folder.iterdir()):
            if path.is_dir():
                stamps.append((path.name, _stamp(path / "spec.json"), _stamp(path / "state.json")))
    except OSError:
        return None
    return tuple(stamps)


def collect_workflows(repo, *, limit=MAX_UI_WORKFLOWS):
    """Read-only bounded workflow summaries. Missing or malformed data is skipped."""
    try:
        root = Path(repo).resolve()
    except OSError:
        return []
    folder = root / ".rig" / "workflows"
    if not folder.is_dir():
        return []
    stamp = _workflow_stamp(root)
    cache_key = str(root)
    with _WORKFLOW_LOCK:
        cached = _WORKFLOW_CACHE.get(cache_key)
        if cached and cached[0] == stamp:
            return copy.deepcopy(cached[1])[:max(0, int(limit or 0))]
    try:
        import workflow_state as wf
        rows = wf.list_workflows(root, include_terminal=True)
    except (OSError, ValueError, TypeError):
        rows = []
    public = []
    for row in rows:
        try:
            item = public_workflow_row(row)
        except (TypeError, ValueError):
            continue
        if item.get("workflow_id"):
            public.append(item)
    public = bound_workflows(public, limit=limit)
    with _WORKFLOW_LOCK:
        _WORKFLOW_CACHE[cache_key] = stamp, copy.deepcopy(public)
    return public


def workflow_detail(repo, workflow_id):
    """Selected workflow detail from the public reader. Never mutates state."""
    try:
        import workflow_state as wf
        spec, state = wf.load_pair(repo, workflow_id)
        if spec is None or state is None:
            return None
        payload = public_workflow_row(wf.public_record(spec, state))
        payload["case"] = str((spec or {}).get("case") or "")
        return payload
    except (OSError, ValueError, TypeError):
        return None


class Collector:
    """Stat cached metadata; decode logs and validate content only on detail requests."""

    def __init__(self, repo):
        self.repo = jobs.repo_root(str(repo))
        self._json_cache = {}
        self._job_cache = {}
        self._lock = threading.RLock()
        self._cap_stamp = None
        self._cap = 3
        self._initialized = False
        self._subjects = {}

    def _subject_stamp(self, row):
        """Only subjects accepted during this observer's lifetime need stat checks."""
        values = []
        for name in change_evidence.normalize_files(self.repo, row.get("files") or []):
            path = self.repo / name
            try:
                link = path.lstat()
                marker = (link.st_ino, link.st_mtime_ns, link.st_ctime_ns, link.st_size)
            except FileNotFoundError:
                marker = None
            values.append((name, marker, _stamp(path)))
        return tuple(values)

    def _read(self, path):
        stamp = _stamp(path)
        if stamp is None:
            self._json_cache.pop(path, None)
            return None
        cached = self._json_cache.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError(f"invalid metadata: {path.name}")
        self._json_cache[path] = stamp, value
        return value

    def _job(self, folder):
        meta = self._read(folder / "meta.json")
        if not meta:
            return None
        names = ("meta.json", "ask.json", "ask-reply.json", "verification.json",
                 "check-running.json", "activity.json", "cancel.json", "requirements.json", "checks")
        attempt = str(meta.get("attempt_id") or "")
        paths = [folder / name for name in names]
        if attempt and admission._ID.fullmatch(attempt) and attempt not in {".", ".."}:
            paths.append(folder / "cancellation" / f"{attempt}.json")
        stamp = tuple(_stamp(path) for path in paths)
        cached = self._job_cache.get(folder)
        tracked = folder in self._subjects
        subject_stamp = self._subject_stamp(cached[1]) if cached and tracked else None
        subject_changed = tracked and subject_stamp != self._subjects[folder]
        live = meta.get("status") in {"running", "ask"}
        active_check = (cached[1].get("verification_summary") or {}).get("active_check") if cached else None
        check_changed = bool(active_check and not (jobs.pid_alive(active_check.get("pid"))
                                                   or jobs.pid_alive(active_check.get("process_pid"))))
        if not cached or cached[0] != stamp or check_changed or subject_changed:
            row = jobs.load_job(folder, include_activity=False)
            if not row:
                raise ValueError(f"invalid job metadata: {folder.name}")
            directory = folder.stat()
            row["identity"] = {
                "path": str(folder), "directory_identity": [directory.st_dev, directory.st_ino],
                "job_id": meta.get("job_id") or folder.name,
                "reservation_id": meta.get("reservation_id") or "",
                "attempt_id": meta.get("attempt_id") or "",
            }
            activity = self._read(folder / "activity.json") or {}
            row["activity_updated_at"] = str(activity.get("updated_at") or "")
            row["activity_source"] = str(activity.get("source") or "")
            row["last_activity_at"] = row.get("doing_updated_at") or row["activity_updated_at"]
            row["last_activity_source"] = "doing" if row.get("doing_updated_at") else row["activity_source"]
            if not meta.get("doing") and row.get("effective") == "running":
                lines = activity.get("lines")
                if isinstance(lines, list) and lines:
                    row["doing"] = jobs._doing_from_activities([str(line) for line in lines])
                    row = jobs.project_job(row)
            changed_acceptance = bool(self._initialized and (not cached or cached[0][3] != stamp[3]))
            if tracked or (changed_acceptance and row["verification_summary"].get("acceptance") == "accepted"):
                row = jobs.project_job(row, self.repo, refresh=True, cache={})
                if row["verification_summary"].get("freshness") == "current":
                    self._subjects[folder] = self._subject_stamp(row)
                else:
                    self._subjects.pop(folder, None)
            self._job_cache[folder] = stamp, row
        row = copy.deepcopy(self._job_cache[folder][1])
        if live:
            alive = jobs.pid_alive(row.get("pid"))
            if alive != row.get("alive"):
                row["alive"] = alive
                row["effective"] = "stale" if row.get("pid") and not alive else "ask" if row.get("ask") else row["status"]
                row = jobs.project_job(row)
            row["elapsed_s"] = jobs.elapsed_seconds(row.get("started_at"), row.get("ended_at"), live=True)
        return row

    def _reservations(self):
        return [admission._public(value) for path in
                sorted((self.repo / ".rig" / "reservations").glob("*.json"))
                if (value := self._read(path))]

    def _queue(self):
        return [dict(value, id=value.get("id") or path.stem) for path in
                sorted((self.repo / ".rig" / "queue").glob("*.json"))
                if (value := self._read(path))]

    @staticmethod
    def _slots(rows, records, queue):
        # Count immutable ledger attempts, including released entries that must
        # suppress stale legacy metadata. Execution liveness cannot release slots.
        held = [record for record in records if record.get("stage") != "released"]
        attempts = {(r.get("reservation_id"), r.get("attempt_id")): r for r in records}
        bound_jobs = {r.get("job_id") for r in held if r.get("job_id")}
        bound_queue = {r.get("queue_id") for r in held if r.get("queue_id")}
        reconciled = {r.get("job_id") for r in records if r.get("legacy") and r.get("stopped")}
        occupied = list(held)
        for row in rows:
            jid = row["job_id"]
            bound = attempts.get((row.get("reservation_id"), row.get("attempt_id")))
            if jid in bound_jobs or jid in reconciled or (bound and bound.get("job_id") == jid):
                continue
            if row.get("status") in {"running", "ask"} or row.get("cancellation_state"):
                occupied.append({"job_id": jid, "slot_held": True})
        for item in queue:
            if (item.get("status") in {"claimed", "spawned"} and item["id"] not in bound_queue
                    and item.get("job_id") not in bound_jobs):
                occupied.append({"job_id": item.get("job_id"), "slot_held": True})
        seen, total = set(), 0
        for row in occupied:
            jid = row.get("job_id")
            if jid and jid in seen:
                continue
            if jid:
                seen.add(jid)
            total += bool(row.get("slot_held"))
        return total

    def collect(self):
        with self._lock:
            try:
                folder = jobs.jobs_dir(self.repo)
                paths = sorted(path for path in folder.iterdir() if path.is_dir()) if folder.is_dir() else []
                rows = [row for path in paths if (row := self._job(path))]
                existing = set(paths)
                self._job_cache = {path: value for path, value in self._job_cache.items() if path in existing}
                self._subjects = {path: value for path, value in self._subjects.items() if path in existing}
                records, queue = self._reservations(), self._queue()
                by_id = {row["job_id"]: row for row in rows}
                for record in records:
                    if record.get("stage") == "released":
                        continue
                    row = by_id.get(record.get("job_id"))
                    if row and row.get("attempt_id") == record.get("attempt_id"):
                        row["reservation"] = record
                        row.update(jobs.project_job(row))
                    elif row is None:
                        rows.append(jobs.project_job(jobs._reserved_job(self.repo, record)))
                rows.sort(key=lambda row: (jobs._hud_priority(row), -row.get("mtime", 0), row["job_id"]))
                pending = [item for item in queue if item.get("status") == "pending"]
                pending.sort(key=lambda item: (-work_queue.clip_priority(item.get("priority") or 0),
                                               item.get("created_at") or "", item["id"]))
                stamp = _stamp(self.repo / ".rig" / "harness.toml")
                if stamp != self._cap_stamp:
                    self._cap, self._cap_stamp = work_queue.max_running(self.repo), stamp
                self._initialized = True
                try:
                    workflows = collect_workflows(self.repo)
                except (OSError, ValueError, TypeError):
                    workflows = []
                return {"version": 1, "repo": str(self.repo), "jobs": rows, "pending": pending,
                        "slots": self._slots(rows, records, queue), "cap": self._cap,
                        "workflows": workflows, "captured_at": jobs.iso_now(), "error": None}
            except (OSError, ValueError, TypeError) as error:
                return {"version": 1, "repo": str(self.repo), "jobs": [], "pending": [],
                        "slots": None, "cap": self._cap, "workflows": [],
                        "captured_at": jobs.iso_now(), "error": str(error)}

    def details(self, job_id):
        """Decode only the selected job and explicitly validate its acceptance."""
        with self._lock:
            try:
                path = jobs._job_path(self.repo, str(job_id))
                if not (path / "meta.json").is_file():
                    for record in self._reservations():
                        name = record.get("job_id") or "reservation-" + record["reservation_id"]
                        if name == job_id and record.get("stage") != "released":
                            return jobs.project_job(jobs._reserved_job(self.repo, record))
                target = cancellation.capture(path)
                row = jobs._load_selected_job(path)
            except SystemExit as error:
                raise ValueError(str(error)) from error
            detail = jobs.project_job(row, self.repo, refresh=True, cache={})
            compact = self._job(path)
            # A replaced attempt must never lend its action identity to details
            # already read from the prior job. Actions revalidate this same pin.
            cancellation.current(target)
            identity = dict(target, path=str(target["path"]), directory_identity=list(target["directory_identity"]))
            if (not compact or compact.get("identity") != identity
                    or any((detail.get(key) or "") != target[key]
                           for key in ("job_id", "reservation_id", "attempt_id"))):
                raise ValueError("job identity changed; refresh before inspecting details")
            detail["identity"] = identity
            for key in ("doing_updated_at", "activity_updated_at", "activity_source",
                        "last_activity_at", "last_activity_source"):
                detail[key] = compact.get(key)
            return detail
