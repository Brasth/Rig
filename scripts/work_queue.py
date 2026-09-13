#!/usr/bin/env python3
"""User work queue (.rig/queue/). Park text. Does not spawn. Not ASK. Not inbox.

Module name is work_queue so it does not shadow stdlib queue.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import admission  # noqa: E402
import harness as rig_harness  # noqa: E402
import jobs as rig_jobs  # noqa: E402

TEXT_CAP = 2000
OCCUPIED_SHOW = 8
STATUSES = frozenset({"pending", "cancelled", "claimed", "spawned", "done"})
WRITER_ROLES = frozenset({"implement", "hard", "worker", "bulk"})
NON_WRITER_ROLES = frozenset({"explore", "explorer", "reviewer", "review"})
THREAD_ENV = (
    "RIG_THREAD",
    "GROK_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)


QueueError = admission.AdmissionError


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def queue_dir(repo: Path) -> Path:
    return Path(repo) / ".rig" / "queue"


def item_path(repo: Path, item_id: str) -> Path:
    return queue_dir(repo) / f"{admission._id(item_id, 'queue id')}.json"


def current_thread() -> str:
    for key in THREAD_ENV:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return ""


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _clip_text(text: str) -> str:
    line = " ".join(str(text).split())
    if not line:
        raise ValueError("queue text required")
    if len(line) > TEXT_CAP:
        line = line[: TEXT_CAP - 1] + "…"
    return line


def _queue_cfg(repo: Path) -> dict:
    return rig_harness.parse_harness(rig_harness.harness_path(Path(repo))).get("queue") or {}


def max_running(repo: Path) -> int:
    raw = _queue_cfg(repo).get("max_running", 3)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 3
    return max(1, n)


def max_per_worker(repo: Path, worker: str = "") -> int:
    cfg = _queue_cfg(repo)
    name = str(worker or "").strip()
    table = cfg.get("per_worker") or {}
    if name and name in table:
        try:
            return max(0, int(table[name]))
        except (TypeError, ValueError):
            pass
    try:
        n = int(cfg.get("max_per_worker", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, n)


def worker_live_count(repo: Path, worker: str, jobs_snapshot=None) -> int:
    name = str(worker or "").strip()
    if not name:
        return 0
    return sum(1 for row in _held_rows(repo, jobs_snapshot) if row.get("slot_held") and row.get("worker") in {"", name})


def clip_priority(raw) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 0
    return max(0, min(9, n))


def parse_slash(text: str) -> dict | None:
    """Parse /queue, /prompts:queue, $queue, $rig-queue. None if a normal prompt."""
    raw = str(text or "").strip()
    if not raw:
        return None
    sigil = raw[0]
    if sigil not in {"/", "$"}:
        return None
    line = raw[1:]
    low = line.lower()
    rest = ""
    dollar = sigil == "$"
    if low.startswith("rig-queue"):
        rest = line[len("rig-queue") :].strip()
    elif low.startswith("prompts:queue"):
        rest = line[len("prompts:queue") :].strip()
    elif low.startswith("queue"):
        rest = line[len("queue") :].strip()
    else:
        return None
    if dollar:
        low_rest = rest.lower()
        if low_rest == "park":
            rest = ""
        elif low_rest.startswith("park ") or low_rest.startswith("park\t"):
            rest = rest[4:].strip()
    if not rest:
        return {"action": "list"}
    priority = 0
    worker = ""
    parts = rest.split()
    out: list[str] = []
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok in {"--priority", "-p"} and i + 1 < len(parts):
            priority = clip_priority(parts[i + 1])
            i += 2
            continue
        if tok.startswith("--priority="):
            priority = clip_priority(tok.split("=", 1)[1])
            i += 1
            continue
        if tok in {"--worker", "-w"} and i + 1 < len(parts):
            worker = parts[i + 1].strip()
            i += 2
            continue
        if tok.startswith("--worker="):
            worker = tok.split("=", 1)[1].strip()
            i += 1
            continue
        out.append(tok)
        i += 1
    body = " ".join(out).strip()
    if not body:
        return {"action": "list"}
    if body.lower().startswith("cancel"):
        bits = body.split(None, 1)
        if len(bits) < 2:
            return {"action": "list"}
        return {"action": "cancel", "id": bits[1].strip()}
    return {
        "action": "add",
        "text": body,
        "priority": priority,
        "worker": worker,
    }


def prompt_from_hook_payload(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("prompt", "text", "userPrompt", "user_prompt", "content"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict):
            inner = val.get("text") or val.get("prompt") or ""
            if str(inner).strip():
                return str(inner)
        if isinstance(val, list):
            bits = []
            for item in val:
                if isinstance(item, str):
                    bits.append(item)
                elif isinstance(item, dict) and item.get("text"):
                    bits.append(str(item.get("text")))
            joined = " ".join(bits).strip()
            if joined:
                return joined
    return ""


def apply_slash(repo: Path, text: str) -> dict | None:
    """Run a /queue slash. None if not a queue command. list is a no-op dict."""
    parsed = parse_slash(text)
    if not parsed:
        return None
    action = parsed.get("action")
    if action == "list":
        return {"action": "list"}
    if action == "cancel":
        obj = cancel_item(repo, str(parsed.get("id") or ""))
        return {"action": "cancel", "item": obj}
    obj = add_item(
        repo,
        str(parsed.get("text") or ""),
        priority=parsed.get("priority") or 0,
        worker=str(parsed.get("worker") or ""),
    )
    return {"action": "add", "item": obj}


def live_jobs(repo: Path, jobs_snapshot=None) -> list[dict]:
    snapshot = rig_jobs.list_jobs(Path(repo)) if jobs_snapshot is None else jobs_snapshot
    return [
        job
        for job in snapshot
        if job.get("effective") in {"running", "ask"}
    ]


def live_count(repo: Path, jobs_snapshot=None) -> int:
    return slot_count(repo, jobs_snapshot)


def is_writer(role: str) -> bool:
    name = str(role or "").strip().lower()
    if name in NON_WRITER_ROLES:
        return False
    if name in WRITER_ROLES:
        return True
    return True


def normalize_files(raw) -> list[str]:
    parts: list[str] = []
    if raw is None:
        items: list = []
    elif isinstance(raw, str):
        items = raw.replace(",", "\n").split()
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [raw]
    for item in items:
        text = str(item or "") if not isinstance(raw, str) else str(item or "").strip()
        while text.startswith("./"):
            text = text[2:]
        if text and text not in parts:
            parts.append(text)
    return parts


def files_overlap(left, right) -> bool:
    a = set(normalize_files(left))
    b = set(normalize_files(right))
    return bool(a & b)


def job_files(job: dict) -> list[str]:
    return normalize_files((job or {}).get("files"))


def _held_rows(repo, jobs_snapshot=None, include_claimed=True):
    if jobs_snapshot is None:
        jobs_snapshot = rig_jobs.list_jobs(Path(repo))
    reservations = getattr(jobs_snapshot, "reservations", None)
    if reservations is None:
        reservations = admission.list_reservations(repo)
    reservations = [row for row in reservations if row.get("stage") != "released"]
    jobs_bound = {row.get("job_id") for row in reservations if row.get("job_id")}
    queues_bound = {row.get("queue_id") for row in reservations if row.get("queue_id")}
    rows = list(reservations)
    for job in live_jobs(repo, jobs_snapshot):
        if job.get("job_id") not in jobs_bound:
            rows.append({**job, "slot_held": True, "access": job.get("access") or ("write" if is_writer(job.get("role")) else "read")})
    if include_claimed:
        for item in list_items(repo, status=None):
            if item.get("status") in {"claimed", "spawned"} and item.get("id") not in queues_bound and item.get("job_id") not in jobs_bound:
                rows.append({**item, "slot_held": True, "queue_id": item.get("id"), "access": item.get("access") or "write"})
    seen, unique = set(), []
    for row in rows:
        key = row.get("job_id") or row.get("queue_id") or row.get("reservation_id")
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def unlabeled_writers(
    repo: Path,
    *,
    ignore_job_id: str = "",
    include_claimed: bool = True,
    jobs_snapshot=None,
) -> list[str]:
    """Live/claimed writers with no listed files (cannot prove disjoint)."""
    names: list[str] = []
    skip = str(ignore_job_id or "").strip()
    for job in _held_rows(repo, jobs_snapshot, include_claimed):
        jid = str(job.get("job_id") or job.get("queue_id") or job.get("reservation_id") or "")
        if skip and jid == skip:
            continue
        if job.get("access") != "write":
            continue
        if not job_files(job) and jid and jid not in names:
            names.append(jid)
    return names


def occupied_files(
    repo: Path,
    *,
    ignore_job_id: str = "",
    include_claimed: bool = True,
    jobs_snapshot=None,
) -> tuple[set[str], bool]:
    files: set[str] = set()
    unknown = False
    skip = str(ignore_job_id or "").strip()
    for job in _held_rows(repo, jobs_snapshot, include_claimed):
        if skip and str(job.get("job_id") or "") == skip:
            continue
        listed = job_files(job)
        if not listed:
            unknown = True
        files |= set(listed)
    return files, unknown


def slot_count(repo: Path, jobs_snapshot=None) -> int:
    return sum(bool(row.get("slot_held")) for row in _held_rows(repo, jobs_snapshot))


def overlap_reason(
    repo: Path,
    files,
    *,
    ignore_job_id: str = "",
    include_claimed: bool = True,
) -> str:
    listed = normalize_files(files)
    occ, unknown = occupied_files(
        repo, ignore_job_id=ignore_job_id, include_claimed=include_claimed
    )
    if unknown:
        names = unlabeled_writers(
            repo, ignore_job_id=ignore_job_id, include_claimed=include_claimed
        )
        who = ", ".join(names) if names else "unknown id"
        return (
            f"a live writer has no listed files ({who}); "
            "wait for it or rig job finish (cannot prove disjoint)"
        )
    hit = sorted(set(listed) & occ)
    if hit:
        return "files overlap live/claimed work: " + ", ".join(hit)
    if not listed and occ:
        return "listed files required while other writers are live"
    return ""


def _lock_path(repo: Path) -> Path:
    folder = queue_dir(repo)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / ".lock"


def _with_lock(repo: Path, fn):
    with admission.transaction(repo):
        return fn()


def live_ids_line(repo: Path) -> str:
    jobs = live_jobs(repo)
    if not jobs:
        return ""
    return "live: " + ", ".join(str(j.get("job_id") or "") for j in jobs)


def check_start(repo: Path, job_id: str = "", files=None, role: str = "worker", worker: str = "") -> None:
    """Compatibility preview only. A successful preview never authorizes launch."""
    owner = admission.caller_owner("parent")
    access = "write" if is_writer(role) else "read"
    _, canonical = admission.canonical_files(repo, normalize_files(files))
    with admission.transaction(repo) as root:
        admission._validate(root, worker, role, "", access, owner, allow_unknown_worker=True)
        if job_id and (root / ".rig" / "jobs" / admission._id(job_id) / "meta.json").exists():
            raise QueueError("existing job id is not launch authorization")
        admission._capacity(root, admission._accounting(root), worker, access, canonical)


def claim_next(repo: Path, files=None, item_id: str = "", *, worker="", access="write",
               owner=None, owner_session="", job_id="") -> dict:
    with admission.transaction(repo):
        pending = list_items(repo, status="pending")
        if item_id:
            pending = [item for item in pending if item["id"] == item_id]
        elif len(pending) > 1:
            raise QueueError("claim needs id when more than one pending item; list then claim by id")
        if not pending:
            raise QueueError("no pending queue item" + (f" {item_id}" if item_id else ""))
        item = pending[0]
        record = admission.reserve(repo, queue_id=item["id"], job_id=job_id,
                                   worker=worker, access=access, files=normalize_files(files),
                                   owner=owner, owner_session=owner_session)
        return {**load_item(repo, item["id"]), **admission.credentials(record), "owner": record["owner"]}


def unclaim(repo: Path, item_id: str, *, reservation_id="", attempt_id="", owner_token="",
            owner=None, owner_session="") -> dict:
    with admission.transaction(repo):
        item = load_item(repo, item_id)
        if not item:
            raise FileNotFoundError(item_id)
        record = admission.assert_owned(repo, reservation_id=reservation_id, attempt_id=attempt_id,
                                        owner_token=owner_token, owner=owner, owner_session=owner_session)
        if record.get("queue_id") != item_id or record.get("claim_consumed"):
            raise QueueError("unclaim needs the matching unconsumed attempt")
        admission.release(repo, reservation_id=reservation_id, attempt_id=attempt_id, owner_token=owner_token,
                          owner=owner, owner_session=owner_session, mode="launch_failed", rationale="parent returned unlaunched claim")
        return load_item(repo, item_id)


def mark_spawned(repo: Path, item_id: str, job_id: str, files=None, *, worker="", access="",
                 reservation_id="", attempt_id="", owner_token="", owner=None, owner_session="") -> dict:
    with admission.transaction(repo):
        record = admission.assert_owned(repo, reservation_id=reservation_id, attempt_id=attempt_id,
                                        owner_token=owner_token, owner=owner, owner_session=owner_session)
        if record.get("queue_id") != item_id or record.get("job_id") != job_id or not record.get("launch_started"):
            raise QueueError("spawned acknowledgement needs the matching activated attempt")
        if worker and worker != record["worker"] or access and access != record["access"]:
            raise QueueError("spawned worker or access mismatch")
        if files is not None and admission.canonical_files(repo, normalize_files(files))[1] != record["files"]:
            raise QueueError("spawned files mismatch")
        if record.get("stopped") or record.get("stage") == "released":
            return load_item(repo, item_id)
        admission._queue_update(Path(repo).resolve(), record, "spawned")
        return load_item(repo, item_id)


def mark_done_for_job(repo: Path, job_id: str, *, reservation_id="", attempt_id="", owner_token="",
                      owner=None, owner_session="") -> dict | None:
    with admission.transaction(repo):
        record = admission.assert_owned(repo, reservation_id=reservation_id, attempt_id=attempt_id,
                                        owner_token=owner_token, owner=owner, owner_session=owner_session)
        if record.get("job_id") != job_id or not record.get("stopped"):
            raise QueueError("queue completion requires its confirmed-stopped attempt")
        admission._queue_update(Path(repo).resolve(), record, "done")
        return load_item(repo, record["queue_id"]) if record.get("queue_id") else None


def _add_item_unlocked(
    repo: Path,
    text: str,
    *,
    thread: str = "",
    priority: int = 0,
    worker: str = "",
) -> dict:
    line = _clip_text(text)
    item_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + f"-{os.getpid()}"
    n = 0
    while item_path(repo, item_id).is_file() and n < 20:
        n += 1
        item_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + f"-{os.getpid()}-{n}"
    obj = {
        "id": item_id,
        "text": line,
        "status": "pending",
        "created_at": iso_now(),
        "claimed_at": "",
        "job_id": "",
        "files": [],
        "priority": clip_priority(priority),
        "worker": str(worker or "").strip(),
        "thread": (thread or current_thread()).strip(),
    }
    _write_json(item_path(repo, item_id), obj)
    return obj


def add_item(repo: Path, text: str, *, thread="", priority=0, worker="") -> dict:
    with admission.transaction(repo):
        return _add_item_unlocked(repo, text, thread=thread, priority=priority, worker=worker)


def load_item(repo: Path, item_id: str) -> dict | None:
    name = str(item_id or "").strip()
    if not name:
        return None
    return _read_json(item_path(repo, name))


def list_items(repo: Path, *, status: str | None = "pending") -> list[dict]:
    folder = queue_dir(repo)
    if not folder.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        obj = _read_json(path)
        if not obj:
            continue
        if not obj.get("id"):
            obj["id"] = path.stem
        if status and str(obj.get("status") or "") != status:
            continue
        out.append(obj)
    out.sort(
        key=lambda item: (
            -clip_priority(item.get("priority") or 0),
            str(item.get("created_at") or ""),
            str(item.get("id") or ""),
        )
    )
    return out


def _cancel_item_unlocked(repo: Path, item_id: str) -> dict:
    name = str(item_id or "").strip()
    if not name:
        raise ValueError("queue id required")
    obj = load_item(repo, name)
    if not obj:
        raise FileNotFoundError(name)
    st = str(obj.get("status") or "")
    if st not in {"pending", "claimed"}:
        raise ValueError(f"cannot cancel {name} status={st or '-'}")
    obj["status"] = "cancelled"
    _write_json(item_path(repo, name), obj)
    return obj


def cancel_item(repo: Path, item_id: str) -> dict:
    with admission.transaction(repo):
        return _cancel_item_unlocked(repo, item_id)


def format_occupied_line(repo: Path, jobs_snapshot=None) -> str:
    occ, unknown = occupied_files(repo, include_claimed=True, jobs_snapshot=jobs_snapshot)
    if unknown:
        names = unlabeled_writers(repo, include_claimed=True, jobs_snapshot=jobs_snapshot)
        who = ", ".join(names) if names else "unknown id"
        return f"occupied  unknown (a live writer has no listed files: {who})"
    if not occ:
        return ""
    paths = sorted(occ)
    extra = max(0, len(paths) - OCCUPIED_SHOW)
    shown = paths[:OCCUPIED_SHOW]
    line = "occupied  " + " ".join(shown)
    if extra:
        line += f" +{extra} more"
    return line


def format_block(repo: Path, *, live: int | None = None, jobs_snapshot=None) -> str:
    if jobs_snapshot is None:
        jobs_snapshot = rig_jobs.list_jobs(Path(repo))
    pending = list_items(repo, status="pending")
    cap = max_running(repo)
    n_live = slot_count(repo, jobs_snapshot) if live is None else int(live)
    rows = [
        f"QUEUE    {len(pending)} pending / live {n_live}/{cap}    "
        "rig queue add|list|cancel|claim"
    ]
    occ_line = format_occupied_line(repo, jobs_snapshot=jobs_snapshot)
    if occ_line:
        rows.append(occ_line)
    shown = 0
    for status in ("pending", "claimed", "spawned"):
        for item in list_items(repo, status=status):
            if shown >= 8:
                break
            text = str(item.get("text") or "")
            if len(text) > 64:
                text = text[:63] + "…"
            extra_bits = ""
            pri = clip_priority(item.get("priority") or 0)
            if pri:
                extra_bits += f" p{pri}"
            want = str(item.get("worker") or "").strip()
            if want:
                extra_bits += f" {want}"
            rows.append(
                f"{status:<8} {str(item.get('id') or '-'):<32} {text}{extra_bits}"
            )
            shown += 1
        if shown >= 8:
            break
    extra = len(pending) + len(list_items(repo, status="claimed")) + len(
        list_items(repo, status="spawned")
    ) - shown
    if extra > 0:
        rows.append(f"         +{extra} more")
    return "\n".join(rows)


def format_list(repo: Path) -> str:
    return format_block(repo)


def _print_obj(obj: dict, label: str, repo: Path, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2))
        return
    print(f"{label} {obj.get('id')}")
    if obj.get("text"):
        print(obj["text"])
    print(format_block(repo))


def main() -> int:
    parser = argparse.ArgumentParser(prog="work_queue.py")
    parser.add_argument(
        "cmd",
        nargs="?",
        default="list",
        choices=["add", "list", "cancel", "claim", "unclaim", "spawned", "gate"],
    )
    parser.add_argument("rest", nargs="*")
    parser.add_argument("--repo")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", default="")
    parser.add_argument("--files", default="")
    parser.add_argument("--files-json")
    parser.add_argument("--access", choices=["read", "write"], default="write")
    parser.add_argument("--reservation-id", default="")
    parser.add_argument("--attempt-id", default="")
    parser.add_argument("--owner-session", default="")
    parser.add_argument("--job", default="")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--role", default="worker")
    parser.add_argument("--priority", type=int, default=0)
    parser.add_argument("--worker", default="")
    args = parser.parse_args()
    repo = rig_jobs.repo_root(args.repo)
    try:
        files = json.loads(args.files_json) if args.files_json is not None else args.files
        auth = {"reservation_id": args.reservation_id or os.environ.get("RIG_RESERVATION_ID", ""),
                "attempt_id": args.attempt_id or os.environ.get("RIG_ATTEMPT_ID", ""),
                "owner_token": os.environ.get("RIG_OWNER_TOKEN", ""), "owner_session": args.owner_session}
        if args.cmd == "add":
            text = (args.text or " ".join(args.rest)).strip()
            if not text:
                raise SystemExit('usage: rig queue add "text"')
            _print_obj(
                add_item(
                    repo,
                    text,
                    priority=args.priority,
                    worker=args.worker,
                ),
                "queued",
                repo,
                args.json,
            )
            return 0
        if args.cmd == "cancel":
            name = (args.rest[0] if args.rest else "").strip()
            if not name:
                raise SystemExit("usage: rig queue cancel <id>")
            _print_obj(cancel_item(repo, name), "cancelled", repo, args.json)
            return 0
        if args.cmd == "claim":
            name = (args.rest[0] if args.rest else "").strip()
            obj = claim_next(repo, files=files, item_id=name, worker=args.worker, access=args.access,
                             owner_session=args.owner_session)
            _print_obj(obj, "claimed", repo, args.json)
            return 0
        if args.cmd == "unclaim":
            name = (args.rest[0] if args.rest else "").strip()
            if not name:
                raise SystemExit("usage: rig queue unclaim <id>")
            _print_obj(unclaim(repo, name, **auth), "unclaimed", repo, args.json)
            return 0
        if args.cmd == "spawned":
            name = (args.rest[0] if args.rest else "").strip()
            jid = (args.job or args.job_id or (args.rest[1] if len(args.rest) > 1 else "")).strip()
            obj = mark_spawned(repo, name, jid, files=files or None, worker=args.worker, access=args.access, **auth)
            _print_obj(obj, "spawned", repo, args.json)
            return 0
        if args.cmd == "gate":
            jid = (args.job_id or args.job or (args.rest[0] if args.rest else "")).strip()
            check_start(
                repo, jid, files=files, role=args.role, worker=args.worker
            )
            return 0
        items = list_items(repo, status="pending")
        if args.json:
            print(json.dumps(items, indent=2))
        else:
            print(format_list(repo))
        return 0
    except QueueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"queue item not found: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
