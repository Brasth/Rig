#!/usr/bin/env python3
"""Live OpenCode / OMP / Pi / agy / Devin model catalogs. Cached under ~/.rig."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CATALOG_WORKERS = frozenset({"opencode", "omp", "pi", "agy", "devin", "mimo"})
TTL_SECONDS = 3600
STALE_MAX_SECONDS = 24 * 3600
PROBE_TIMEOUT = 8.0
_CACHE_LOCK = threading.Lock()
_REFRESHING: set[str] = set()
_REFRESH_LOCK = threading.Lock()
BANNED_SUBSTR = ("sol", "astra", "fable")
CHEAP_KINDS = frozenset({"explore", "mini", "bulk"})
CHEAP_WORDS = ("mini", "flash", "haiku", "spark", "fast", "lite", "cheap", "small")
IMPLEMENT_WORDS = ("luna", "sonnet", "grok-4.6", "gpt-5.6", "gpt-5.5")
HARD_WORDS = ("terra", "opus", "grok-4.6", "pro-high", "gpt-5.6-terra")
REVIEW_WORDS = ("opus", "terra")
MINI_MARKERS = ("mini", "haiku", "spark", "fast", "lite", "cheap", "small")

PROBE_ARGV = {
    "opencode": ("models",),
    "pi": ("--list-models",),
    "agy": ("models",),
    "devin": ("models", "list", "--format", "json"),
    "mimo": ("models",),
}


def env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def cache_path() -> Path:
    override = os.environ.get("RIG_MODEL_CATALOG_CACHE", "").strip()
    if override:
        return Path(override)
    root = os.environ.get("RIG_HOME", "").strip()
    base = Path(root) if root else Path.home() / ".rig"
    return base / "cache" / "model-catalogs.json"


def is_banned(model_id: str) -> bool:
    raw = (model_id or "").strip().lower()
    return bool(raw) and any(tok in raw for tok in BANNED_SUBSTR)


def uniq_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in ids:
        item = (raw or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def pin_matches(preferred: str, catalog_id: str) -> bool:
    pin = (preferred or "").strip().lower()
    cid = (catalog_id or "").strip().lower()
    if not pin or not cid:
        return False
    if pin == cid:
        return True
    if cid.endswith("/" + pin) or pin.endswith("/" + cid):
        return True
    if cid.endswith(pin) and len(cid) > len(pin) and not cid[-len(pin) - 1].isalnum():
        return True
    if pin.endswith(cid) and len(pin) > len(cid) and not pin[-len(cid) - 1].isalnum():
        return True
    return False


def parse_opencode(text: str) -> list[str]:
    ids: list[str] = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw or "{" in raw:
            continue
        token = raw.split()[0]
        if "/" in token:
            ids.append(token)
    return uniq_ids(ids)


def parse_mimo(text: str) -> list[str]:
    ids = []
    for line in (text or "").splitlines():
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if "/" in token and all(ch.isalnum() or ch in "./_-" for ch in token):
            ids.append(token)
    return uniq_ids(ids)


def parse_omp_json(text: str) -> list[str] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    models = data.get("models") if isinstance(data, dict) else data
    if not isinstance(models, list):
        return None
    ids: list[str] = []
    for item in models:
        if isinstance(item, str):
            if item.strip():
                ids.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        selector = str(item.get("selector") or "").strip()
        ident = str(item.get("id") or "").strip()
        chosen = selector or ident
        if chosen:
            ids.append(chosen)
    return uniq_ids(ids)


def parse_omp_table(text: str) -> list[str]:
    ids: list[str] = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw or raw[:1] in "{[":
            continue
        low = raw.lower()
        if "selector" in low and ("provider" in low or low.startswith("id")):
            continue
        if low.split()[:1] == ["provider"] and "model" in low:
            continue
        parts = raw.split()
        slash = [p.strip(",") for p in parts if "/" in p]
        if slash:
            ids.append(slash[-1])
            continue
        token = parts[0].strip(",")
        if token.lower() in {"provider", "id", "selector", "model", "name"}:
            continue
        ids.append(token)
    return uniq_ids(ids)


def parse_pi(text: str) -> list[str]:
    ids: list[str] = []
    lines = [(ln or "").strip() for ln in (text or "").splitlines()]
    start = 0
    if lines:
        header = lines[0].lower()
        if "provider" in header and "model" in header:
            start = 1
    for raw in lines[start:]:
        if not raw or raw[:1] in "-=":
            continue
        parts = raw.split()
        if len(parts) >= 2:
            provider, model = parts[0], parts[1]
            if provider.lower() in {"provider"}:
                continue
            ids.append(f"{provider}/{model}")
            ids.append(model)
        elif parts:
            ids.append(parts[0])
    return uniq_ids(ids)


def parse_devin_json(text: str) -> list[str] | None:
    """Exact JSON from `devin models list --format json`. No table fallback."""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    models = data
    if isinstance(data, dict):
        models = None
        for key in ("models", "data", "items", "families"):
            if key in data:
                models = data[key]
                break
        if models is None:
            return None
    if not isinstance(models, list):
        return None
    ids: list[str] = []
    for item in models:
        if isinstance(item, str):
            if item.strip():
                ids.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        variants = item.get("variants")
        if isinstance(variants, list):
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                model_uid = str(variant.get("model_uid") or "").strip()
                if model_uid:
                    ids.append(model_uid)
            continue
        # Devin's JSON has used both model_uid/slug and id/name shapes.  Keep
        # this JSON-only, exact-selector extraction deliberately narrow.
        for key in ("model_uid", "slug", "selector", "id", "model", "name"):
            chosen = str(item.get(key) or "").strip()
            if chosen:
                ids.append(chosen)
                break
    return uniq_ids(ids)


def parse_agy(text: str) -> list[str]:
    ids: list[str] = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.lower().startswith("fetching available models"):
            continue
        token = raw.split()[0]
        if token and token[:1] not in "-=":
            ids.append(token)
    return uniq_ids(ids)


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def probe_worker(worker: str, timeout: float = PROBE_TIMEOUT) -> list[str] | None:
    if worker not in CATALOG_WORKERS:
        return None
    bin_path = shutil.which(worker)
    if not bin_path:
        return None
    if worker == "omp":
        return _probe_omp(bin_path, timeout)
    if worker == "devin":
        return _probe_devin(bin_path, timeout)
    argv = [bin_path, *PROBE_ARGV[worker]]
    proc = _run(argv, timeout)
    if proc is None or proc.returncode != 0:
        return None
    parsers = {"opencode": parse_opencode, "pi": parse_pi, "agy": parse_agy, "mimo": parse_mimo}
    ids = parsers[worker](proc.stdout or "")
    return ids or None


def probe_catalog(worker: str, timeout: float = PROBE_TIMEOUT) -> tuple[str, list[str] | None]:
    """Return (ok|empty|unavailable, ids). Empty success is distinct from probe failure."""
    if worker not in CATALOG_WORKERS:
        return "unavailable", None
    bin_path = shutil.which(worker)
    if not bin_path:
        return "unavailable", None
    if worker == "omp":
        proc = _run([bin_path, "models", "--json"], timeout)
        if proc is not None and proc.returncode == 0:
            parsed = parse_omp_json(proc.stdout or "")
            if parsed:
                return "ok", parsed
            if parsed is not None:
                return "empty", []
        proc = _run([bin_path, "models"], timeout)
        if proc is None or proc.returncode != 0:
            return "unavailable", None
        parsed = parse_omp_json(proc.stdout or "")
        if parsed:
            return "ok", parsed
        if parsed is not None:
            return "empty", []
        ids = parse_omp_table(proc.stdout or "")
        return ("ok", ids) if ids else ("empty", [])
    if worker == "devin":
        proc = _run([bin_path, "models", "list", "--format", "json"], timeout)
        if proc is None or proc.returncode != 0:
            return "unavailable", None
        parsed = parse_devin_json(proc.stdout or "")
        if parsed:
            return "ok", parsed
        if parsed is not None:
            return "empty", []
        return "unavailable", None
    argv = [bin_path, *PROBE_ARGV[worker]]
    proc = _run(argv, timeout)
    if proc is None or proc.returncode != 0:
        return "unavailable", None
    parsers = {"opencode": parse_opencode, "pi": parse_pi, "agy": parse_agy, "mimo": parse_mimo}
    ids = parsers[worker](proc.stdout or "")
    return ("ok", ids) if ids else ("empty", [])


def _probe_devin(bin_path: str, timeout: float) -> list[str] | None:
    proc = _run([bin_path, "models", "list", "--format", "json"], timeout)
    if proc is None or proc.returncode != 0:
        return None
    parsed = parse_devin_json(proc.stdout or "")
    return parsed or None


def _probe_omp(bin_path: str, timeout: float) -> list[str] | None:
    proc = _run([bin_path, "models", "--json"], timeout)
    if proc is not None and proc.returncode == 0:
        parsed = parse_omp_json(proc.stdout or "")
        if parsed:
            return parsed
        if parsed is not None:
            return None
    proc = _run([bin_path, "models"], timeout)
    if proc is None or proc.returncode != 0:
        return None
    parsed = parse_omp_json(proc.stdout or "")
    if parsed:
        return parsed
    ids = parse_omp_table(proc.stdout or "")
    return ids or None


def _read_cache_file() -> dict:
    path = cache_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache_file(data: dict) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(data, indent=2) + "\n").encode("utf-8")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)
    except OSError:
        return


def cache_get(worker: str, *, allow_stale: bool = False) -> list[str] | None:
    with _CACHE_LOCK:
        entry = _read_cache_file().get(worker)
    if not isinstance(entry, dict):
        return None
    try:
        fetched = float(entry.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fetched) or fetched <= 0 or fetched > time.time():
        return None
    ids = entry.get("ids")
    if not isinstance(ids, list):
        return None
    cleaned = [str(x) for x in ids if str(x).strip()]
    if (time.time() - fetched) > TTL_SECONDS and not allow_stale:
        return None
    return cleaned


def cache_entry(worker: str) -> dict | None:
    with _CACHE_LOCK:
        entry = _read_cache_file().get(worker)
    if not isinstance(entry, dict):
        return None
    try:
        fetched = float(entry.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fetched) or fetched <= 0 or fetched > time.time():
        return None
    ids = entry.get("ids")
    if not isinstance(ids, list):
        return None
    status = str(entry.get("status") or "ok")
    if status not in {"ok", "empty"}:
        return None
    return {
        "ids": [str(x) for x in ids if str(x).strip()],
        "fetched_at": fetched,
        "status": status,
        "age_s": time.time() - fetched,
    }


def cache_put(worker: str, ids: list[str] | None, *, status: str = "ok") -> None:
    if status == "failure":
        previous = cache_entry(worker)
        if previous and previous.get("status") in {"ok", "empty"}:
            return
        return
    with _CACHE_LOCK:
        data = _read_cache_file()
        data[worker] = {
            "ids": list(ids or []),
            "fetched_at": time.time(),
            "status": "empty" if status == "empty" or not ids else "ok",
        }
        _write_cache_file(data)


def _lookup_cached(worker: str) -> tuple[str, list[str] | None]:
    hit = cache_get(worker)
    if hit is not None:
        return "fresh", (hit or None)
    stale = cache_get(worker, allow_stale=True)
    if stale:
        return "stale", stale
    return "miss", None


def _schedule_refresh(worker: str, timeout: float, *, distinguish_empty: bool = False) -> None:
    with _REFRESH_LOCK:
        if worker in _REFRESHING:
            return
        _REFRESHING.add(worker)

    def _run() -> None:
        try:
            if distinguish_empty:
                status, ids = probe_catalog(worker, timeout=timeout)
                if status == "ok":
                    cache_put(worker, ids, status="ok")
                elif status == "empty":
                    cache_put(worker, [], status="empty")
                else:
                    cache_put(worker, None, status="failure")
                return
            probed = probe_worker(worker, timeout=timeout)
            if probed is None:
                return
            cache_put(worker, probed)
        except Exception:
            return
        finally:
            with _REFRESH_LOCK:
                _REFRESHING.discard(worker)

    threading.Thread(
        target=_run,
        name=f"rig-catalog-refresh-{worker}",
        daemon=True,
    ).start()


def load_catalog_info(worker: str, timeout: float = PROBE_TIMEOUT, *, require_fresh: bool = False) -> dict:
    """Catalog state for smart routing. Distinguishes empty, stale, and unavailable."""
    base = {
        "worker": worker,
        "ids": None,
        "state": "unavailable",
        "source": "none",
        "freshness": "unavailable",
        "confirmed_empty": False,
        "fetched_at": None,
        "age_s": None,
        "refresh_failed": False,
    }
    if worker not in CATALOG_WORKERS:
        return {**base, "state": "unverified", "source": "pin", "freshness": "unverified"}
    if env_on("RIG_SKIP_MODEL_CATALOG"):
        return {**base, "state": "skipped", "source": "skip", "freshness": "unverified"}
    entry = cache_entry(worker)
    refresh = env_on("RIG_REFRESH_MODELS") or require_fresh
    if entry and not refresh:
        age = entry["age_s"]
        ids = entry["ids"]
        empty = entry["status"] == "empty" or not ids
        payload = {
            **base,
            "ids": ids,
            "fetched_at": entry["fetched_at"],
            "age_s": age,
            "confirmed_empty": empty,
            "source": "cache",
        }
        if age <= TTL_SECONDS:
            return {
                **payload,
                "state": "empty" if empty else "fresh",
                "freshness": "empty" if empty else "fresh",
            }
        if age <= STALE_MAX_SECONDS:
            _schedule_refresh(worker, timeout, distinguish_empty=True)
            return {
                **payload,
                "state": "empty" if empty else "stale",
                "freshness": "empty" if empty else "stale",
            }
        refresh = True
    status, ids = probe_catalog(worker, timeout=timeout)
    if status == "ok":
        cache_put(worker, ids, status="ok")
        return {
            **base,
            "ids": ids,
            "state": "fresh",
            "source": "probe",
            "freshness": "fresh",
            "fetched_at": time.time(),
            "age_s": 0.0,
        }
    if status == "empty":
        cache_put(worker, [], status="empty")
        return {
            **base,
            "ids": [],
            "state": "empty",
            "source": "probe",
            "freshness": "empty",
            "confirmed_empty": True,
            "fetched_at": time.time(),
            "age_s": 0.0,
        }
    if entry and entry["status"] in {"ok", "empty"} and entry["age_s"] <= STALE_MAX_SECONDS:
        empty = entry["status"] == "empty" or not entry["ids"]
        return {
            **base,
            "ids": entry["ids"],
            "state": "empty" if empty else "stale",
            "source": "cache",
            "freshness": "empty" if empty else "stale",
            "confirmed_empty": empty,
            "fetched_at": entry["fetched_at"],
            "age_s": entry["age_s"],
            "refresh_failed": True,
        }
    return {**base, "refresh_failed": True}


def load_catalog(worker: str, timeout: float = PROBE_TIMEOUT) -> list[str] | None:
    if worker not in CATALOG_WORKERS:
        return None
    if env_on("RIG_SKIP_MODEL_CATALOG"):
        return None
    refresh = env_on("RIG_REFRESH_MODELS")
    if not refresh:
        state, ids = _lookup_cached(worker)
        if state == "fresh":
            return ids
        if state == "stale":
            _schedule_refresh(worker, timeout)
            return ids
    probed = probe_worker(worker, timeout=timeout)
    if probed is None:
        cache_put(worker, None, status="failure")
        return None
    cache_put(worker, probed)
    return probed


def load_catalogs(
    workers: list[str],
    timeout: float = PROBE_TIMEOUT,
) -> dict[str, list[str] | None]:
    out: dict[str, list[str] | None] = {}
    skip = env_on("RIG_SKIP_MODEL_CATALOG")
    refresh = env_on("RIG_REFRESH_MODELS")
    missing: list[str] = []
    for worker in workers:
        if skip or worker not in CATALOG_WORKERS:
            out[worker] = None
            continue
        if not refresh:
            state, ids = _lookup_cached(worker)
            if state == "fresh":
                out[worker] = ids
                continue
            if state == "stale":
                _schedule_refresh(worker, timeout)
                out[worker] = ids
                continue
        missing.append(worker)
        out[worker] = None
    if not missing:
        return out

    def _probe_one(name: str) -> tuple[str, list[str] | None]:
        try:
            probed = probe_worker(name, timeout=timeout)
        except Exception:
            return name, None
        if probed is None:
            cache_put(name, None, status="failure")
        else:
            cache_put(name, probed)
        return name, probed

    with ThreadPoolExecutor(max_workers=max(1, len(missing))) as pool:
        futs = [pool.submit(_probe_one, name) for name in missing]
        for fut in as_completed(futs):
            try:
                name, probed = fut.result()
            except Exception:
                continue
            out[name] = probed
    return out


def drop_banned(ids: list[str] | None) -> list[str]:
    if not ids:
        return []
    return [i for i in uniq_ids(ids) if not is_banned(i)]


def _is_mini_only(model_id: str) -> bool:
    raw = model_id.lower()
    return any(tok in raw for tok in MINI_MARKERS)


def _cheap_ok(model_id: str) -> bool:
    raw = model_id.lower()
    if "opus" in raw:
        return False
    if "pro" in raw and "flash" not in raw:
        return False
    return True


def _keyword_hit(kind: str, ids: list[str]) -> str | None:
    if kind in CHEAP_KINDS:
        words = CHEAP_WORDS
        skip_mini = False
        require_cheap = True
    elif kind == "hard":
        words = HARD_WORDS
        skip_mini = False
        require_cheap = False
    elif kind == "review":
        words = REVIEW_WORDS
        skip_mini = False
        require_cheap = False
    else:
        words = IMPLEMENT_WORDS
        skip_mini = True
        require_cheap = False
    stronger = skip_mini and any(not _is_mini_only(i) for i in ids)
    for word in words:
        for model_id in ids:
            raw = model_id.lower()
            if word not in raw:
                continue
            if require_cheap and not _cheap_ok(model_id):
                continue
            if skip_mini and stronger and _is_mini_only(model_id):
                continue
            return model_id
    return None


def pick_from_catalog(kind: str, preferred: str, ids: list[str] | None) -> str:
    usable = drop_banned(ids)
    if not usable:
        return preferred
    for catalog_id in usable:
        if pin_matches(preferred, catalog_id):
            return preferred
    hit = _keyword_hit(kind, usable)
    if hit:
        return hit
    return usable[0]


def _devin_exact(preferred: str, ids: list[str] | None) -> str:
    """Devin never substitutes: exact SWE-2 pin only, no alias/keyword/first-model fallback."""
    want = (preferred or "").strip().lower()
    if not want:
        return preferred
    for catalog_id in drop_banned(ids):
        if catalog_id.strip().lower() == want:
            return preferred
    return preferred


def resolve_model(
    worker: str,
    kind: str,
    preferred: str,
    catalog: list[str] | None = None,
    *,
    catalogs: dict[str, list[str]] | None = None,
) -> str:
    if worker not in CATALOG_WORKERS:
        return preferred
    if worker == "devin":
        if catalogs is not None:
            if worker not in catalogs:
                return preferred
            return _devin_exact(preferred, catalogs.get(worker))
        if catalog is not None:
            return _devin_exact(preferred, catalog)
        return _devin_exact(preferred, load_catalog(worker))
    if catalogs is not None:
        if worker not in catalogs:
            return preferred
        return pick_from_catalog(kind, preferred, catalogs.get(worker))
    if catalog is not None:
        return pick_from_catalog(kind, preferred, catalog)
    return pick_from_catalog(kind, preferred, load_catalog(worker))


def doctor_lines() -> list[str]:
    data = _read_cache_file()
    now = time.time()
    rows: list[str] = []
    for worker in ("opencode", "omp", "pi", "agy", "devin", "mimo"):
        entry = data.get(worker)
        if not isinstance(entry, dict):
            continue
        try:
            fetched = float(entry.get("fetched_at") or 0)
        except (TypeError, ValueError):
            continue
        if fetched <= 0 or (now - fetched) > TTL_SECONDS:
            continue
        ids = entry.get("ids") if isinstance(entry.get("ids"), list) else []
        rows.append(f"  {worker}: {len(ids)} models")
    if not rows:
        return []
    return ["Model catalogs (cached)"] + rows


def main() -> int:
    parser = argparse.ArgumentParser(prog="catalog.py")
    parser.add_argument("cmd", choices=["doctor"])
    args = parser.parse_args()
    if args.cmd == "doctor":
        for line in doctor_lines():
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
