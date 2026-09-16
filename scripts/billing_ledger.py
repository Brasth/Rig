#!/usr/bin/env python3
"""Provider-neutral local invoice-dollar ledger. Never estimates or stores secrets."""
from __future__ import annotations

import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import job_metadata
import jobs as rig_jobs
import route as rig_route

SCHEMA = 1
SECRET_KEYS = frozenset({
    "api_key", "apikey", "api-key", "secret", "token", "access_token", "refresh_token",
    "password", "authorization", "credential", "credentials", "owner_token", "x-api-key",
    "bearer", "private_key",
})
INFERRED_KEYS = frozenset({
    "estimated", "estimate", "list_price", "list_prices", "subscription", "amortized",
    "token_cost", "token_cost_usd", "total_cost_usd", "implied_usd", "per_job_usd",
})
RECEIPT_KEYS = frozenset({
    "receipt_id", "provider", "amount_usd", "currency", "period", "scope", "cohort",
    "source", "source_identity", "evidence", "imported_at",
})
RECEIPT_SOURCES = frozenset({"import", "openai_sync", "anthropic_sync", "generic_import"})
EVIDENCE_KINDS = frozenset({"import", "sync", "validate", "dry_run"})
EVIDENCE_METHODS = frozenset({"file", "injected_fetch", "network", "manual"})
REF_KEYS = frozenset({"env", "config_id", "profile", "organization_id", "project_id"})
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SECRET_PREFIXES = ("sk-", "sk-ant-", "bearer ")
OPENAI_ENDPOINT = "https://api.openai.com/v1/organization/costs"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/organizations/cost_report"
JOB_ATTRIBUTION_NOTE = (
    "dollars cannot be assigned to individual jobs if the provider reports only cohort aggregates"
)


class LedgerError(ValueError):
    """Malformed receipt, config, or sync payload."""


def _parent_only() -> None:
    if os.environ.get("RIG_JOB_ID") or os.environ.get("RIG_JOB_DIR"):
        raise LedgerError("billing mutations are parent-only")


def _looks_like_secret(value) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    lowered = text.lower()
    return bool(text) and lowered.startswith(SECRET_PREFIXES)


def _walk_secrets(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            raw = str(key).strip().lower()
            name = raw.replace("-", "_")
            if name in SECRET_KEYS or raw in SECRET_KEYS:
                raise LedgerError("billing must not persist credentials")
            if name in INFERRED_KEYS or raw in INFERRED_KEYS:
                raise LedgerError("billing must not persist inferred dollar fields")
            if _looks_like_secret(item):
                raise LedgerError("billing must not persist credentials")
            _walk_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _walk_secrets(item)
    elif _looks_like_secret(value):
        raise LedgerError("billing must not persist credentials")


def redact_secrets(value):
    """Drop credential keys/values from a public payload. Never guesses dollars."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            raw = str(key).strip().lower().replace("-", "_")
            if raw in SECRET_KEYS or str(key).strip().lower() in SECRET_KEYS:
                continue
            if _looks_like_secret(item):
                continue
            out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def public_json(value) -> str:
    return json.dumps(redact_secrets(value), indent=2)


def normalize_usd(value) -> str:
    """Exact non-negative decimal string. Fractions beyond cents are kept."""
    if type(value) is not str or isinstance(value, bool):
        raise LedgerError("amount_usd must be a decimal string")
    text = value.strip()
    if not text or any(ch in text for ch in "eE+") or text.startswith("-"):
        raise LedgerError("amount_usd must be a non-negative decimal string")
    if text.count(".") > 1:
        raise LedgerError("amount_usd must be a non-negative decimal string")
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise LedgerError("amount_usd must be a non-negative decimal string") from error
    if not amount.is_finite() or amount < 0:
        raise LedgerError("amount_usd must be a non-negative decimal string")
    return format(amount, "f")


def add_usd(*amounts: str) -> str:
    total = sum((Decimal(normalize_usd(raw)) for raw in amounts), Decimal("0"))
    return format(total, "f")


def sub_usd(left: str, right: str) -> str:
    amount = Decimal(normalize_usd(left)) - Decimal(normalize_usd(right))
    if amount < 0:
        raise LedgerError("usd difference is negative")
    return format(amount, "f")


def config_path(repo: Path) -> Path:
    return Path(repo) / ".rig" / "billing.json"


def ledger_dir(repo: Path, scope: str = "default") -> Path:
    return Path(repo) / ".rig" / "billing" / scope


def _default_config() -> dict:
    return {
        "schema_version": SCHEMA,
        "active_scope": "default",
        "scopes": {
            "default": {
                "id": "default",
                "providers": sorted(rig_route.PROVIDERS),
                "credential_refs": {},
            }
        },
    }


def _normalize_refs(raw) -> dict:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict) or isinstance(raw, bool):
        raise LedgerError("credential_refs must be an object of config identifiers")
    _walk_secrets(raw)
    out = {}
    for provider, spec in raw.items():
        if provider not in rig_route.PROVIDERS:
            raise LedgerError(f"unknown billing provider {provider}")
        if not isinstance(spec, dict) or isinstance(spec, bool):
            raise LedgerError("credential_refs entries must be objects")
        extra = set(spec) - REF_KEYS
        if extra:
            raise LedgerError(f"unknown credential_ref field {sorted(extra)[0]}")
        entry = {}
        for key in sorted(REF_KEYS):
            if key not in spec:
                continue
            value = spec[key]
            if type(value) is not str or not value.strip():
                raise LedgerError("credential_ref values must be nonempty identifier strings")
            if _looks_like_secret(value):
                raise LedgerError("billing must not persist credentials")
            entry[key] = value.strip()
        if not entry:
            raise LedgerError("credential_refs require env or config identifiers, never secrets")
        out[provider] = entry
    return out


def load_config(repo: Path) -> dict:
    path = config_path(repo)
    raw = _default_config() if not path.is_file() else job_metadata.read_json_object(path)
    _walk_secrets(raw)
    if raw.get("schema_version") != SCHEMA or not isinstance(raw.get("scopes"), dict) or not raw["scopes"]:
        raise LedgerError("billing.json schema_version must be 1 with named scopes")
    active = str(raw.get("active_scope") or "default")
    if active not in raw["scopes"]:
        raise LedgerError("active_scope is not a configured billing scope")
    scopes = {}
    for name, spec in raw["scopes"].items():
        body = spec if isinstance(spec, dict) else None
        providers = body.get("providers") if body else None
        if not isinstance(name, str) or not SAFE_ID.match(name) or not isinstance(providers, list) or not providers:
            raise LedgerError("billing scope ids must be filesystem-safe with providers")
        unknown = [item for item in providers if item not in rig_route.PROVIDERS]
        if unknown:
            raise LedgerError(f"unknown billing provider {unknown[0]}")
        scopes[name] = {
            "id": name,
            "providers": list(providers),
            "credential_refs": _normalize_refs(body.get("credential_refs")),
        }
    return {"schema_version": SCHEMA, "active_scope": active, "scopes": scopes}


def ensure_config(repo: Path) -> dict:
    if config_path(repo).is_file():
        return load_config(repo)
    cfg = _default_config()
    job_metadata.write_json_atomic(config_path(repo), cfg)
    return cfg


def _scope(repo: Path, scope: str = "") -> str:
    cfg = load_config(repo)
    name = (scope or cfg["active_scope"]).strip() or cfg["active_scope"]
    if name not in cfg["scopes"]:
        raise LedgerError(f"unknown billing scope {name}")
    return name


def _normalize_period(value) -> dict:
    if isinstance(value, str) and value.strip():
        return {"label": value.strip()}
    if not isinstance(value, dict) or isinstance(value, bool):
        raise LedgerError("receipt.period is required")
    start = str(value.get("start") or "").strip()
    end = str(value.get("end") or "").strip()
    label = str(value.get("label") or value.get("period") or "").strip()
    if start or end:
        return {"start": start, "end": end}
    if label:
        return {"label": label}
    raise LedgerError("receipt.period is required")


def _normalize_evidence(value, *, default_kind: str, default_method: str) -> dict:
    raw = value if isinstance(value, dict) else {}
    if value not in (None, "") and not isinstance(value, dict):
        raise LedgerError("receipt.evidence must be an object")
    _walk_secrets(raw)
    kind = str(raw.get("kind") or default_kind).strip() or default_kind
    method = str(raw.get("method") or default_method).strip() or default_method
    if kind not in EVIDENCE_KINDS:
        raise LedgerError("receipt.evidence.kind is invalid")
    if method not in EVIDENCE_METHODS:
        raise LedgerError("receipt.evidence.method is invalid")
    identity = str(raw.get("source_path") or raw.get("identity") or "").strip()
    if _looks_like_secret(identity):
        raise LedgerError("billing must not persist credentials")
    out = {
        "kind": kind,
        "method": method,
        "at": str(raw.get("at") or raw.get("retrieved_at") or rig_jobs.iso_now()),
    }
    if identity:
        out["identity"] = identity
    note = str(raw.get("note") or "").strip()
    if note:
        out["note"] = note
    return out


def _source_for_provider(provider: str, source: str = "") -> str:
    if source:
        return source
    if provider == "openai":
        return "openai_sync"
    if provider == "anthropic":
        return "anthropic_sync"
    return "generic_import"


def normalize_receipt(value, *, scope: str, source: str = "import", cohort: str = "") -> dict:
    if not isinstance(value, dict) or isinstance(value, bool):
        raise LedgerError("receipt must be an object")
    _walk_secrets(value)
    if value.get("job_id") not in (None, ""):
        raise LedgerError(JOB_ATTRIBUTION_NOTE)
    extra = set(value) - RECEIPT_KEYS
    if extra:
        raise LedgerError(f"unknown receipt field {sorted(extra)[0]}")
    receipt_id = str(value.get("receipt_id") or "").strip()
    provider = str(value.get("provider") or "").strip()
    if not receipt_id or not SAFE_ID.match(receipt_id):
        raise LedgerError("receipt_id must be a filesystem-safe id")
    if provider not in rig_route.PROVIDERS:
        raise LedgerError("receipt.provider must be a known route provider")
    if (str(value.get("currency") or "USD").strip() or "USD") != "USD":
        raise LedgerError("receipt.currency must be USD")
    src = str(value.get("source") or source).strip() or source
    if src not in RECEIPT_SOURCES:
        raise LedgerError("receipt.source is invalid")
    wanted = str(value.get("scope") or scope).strip() or scope
    if wanted != scope:
        raise LedgerError("receipt.scope does not match the target billing scope")
    cohort_name = str(value.get("cohort") or cohort or scope).strip() or scope
    if not SAFE_ID.match(cohort_name):
        raise LedgerError("receipt.cohort must be filesystem-safe")
    identity = str(value.get("source_identity") or f"{provider}:{scope}").strip()
    if not identity or _looks_like_secret(identity):
        raise LedgerError("receipt.source_identity must be a config identifier, never a secret")
    method = "file" if src in {"import", "generic_import"} else "injected_fetch"
    return {
        "receipt_id": receipt_id,
        "provider": provider,
        "amount_usd": normalize_usd(value.get("amount_usd")),
        "currency": "USD",
        "period": _normalize_period(value.get("period")),
        "scope": scope,
        "cohort": cohort_name,
        "source": src,
        "source_identity": identity,
        "evidence": _normalize_evidence(
            value.get("evidence"), default_kind="import" if src in {"import", "generic_import"} else "sync",
            default_method=method,
        ),
    }


def _store_receipt(repo: Path, body: dict, *, dry_run: bool = False) -> dict:
    path = ledger_dir(repo, body["scope"]) / "receipts" / f"{body['receipt_id']}.json"
    if path.is_file():
        existing = job_metadata.read_json_object(path)
        comparable = {key: existing.get(key) for key in body}
        if comparable != body:
            raise LedgerError(f"receipt {body['receipt_id']} conflicts with an existing payload")
        return {**existing, "created": False, "dry_run": dry_run}
    if dry_run:
        return {**body, "created": False, "dry_run": True}
    stored = {**body, "imported_at": rig_jobs.iso_now()}
    job_metadata.write_json_atomic(path, stored)
    return {**stored, "created": True, "dry_run": False}


def import_receipt(repo: Path, value, *, scope: str = "", source: str = "import",
                   dry_run: bool = False) -> dict:
    _parent_only()
    ensure_config(repo)
    name = _scope(repo, scope)
    cfg = load_config(repo)
    if not source:
        provider = str((value or {}).get("provider") or "").strip()
        source = _source_for_provider(provider, "import")
        if provider not in {"openai", "anthropic"}:
            source = "generic_import" if provider else "import"
    body = normalize_receipt(value, scope=name, source=source)
    if body["provider"] not in cfg["scopes"][name]["providers"]:
        raise LedgerError(f"provider {body['provider']} is outside billing scope {name}")
    return redact_secrets(_store_receipt(repo, body, dry_run=dry_run))


def load_receipts(repo: Path, *, scope: str = "", cohort: str = "") -> list[dict]:
    folder = ledger_dir(repo, _scope(repo, scope)) / "receipts"
    if not folder.is_dir():
        return []
    rows = [job_metadata.read_json_object(path) for path in sorted(folder.glob("*.json"))]
    if cohort:
        rows = [row for row in rows if str(row.get("cohort") or "") == cohort]
    return rows


def provider_totals(receipts) -> dict[str, str]:
    sums: dict[str, Decimal] = {}
    for row in receipts or []:
        name = str(row.get("provider") or "")
        sums[name] = sums.get(name, Decimal("0")) + Decimal(normalize_usd(row.get("amount_usd")))
    out = {name: format(amount, "f") for name, amount in sorted(sums.items())}
    out["all"] = add_usd(*[row.get("amount_usd") for row in receipts or []]) if receipts else "0"
    return out


def cohort_totals(receipts) -> dict[str, str]:
    sums: dict[str, Decimal] = {}
    for row in receipts or []:
        name = str(row.get("cohort") or "")
        sums[name] = sums.get(name, Decimal("0")) + Decimal(normalize_usd(row.get("amount_usd")))
    return {name: format(amount, "f") for name, amount in sorted(sums.items())}


def job_dollar_allocation(*_args, **_kwargs):
    raise LedgerError(JOB_ATTRIBUTION_NOTE)


class ReadOnlySyncAdapter:
    """Named read-only receipt adapter. Network is explicit; fetch is injected."""

    def __init__(self, provider: str, *, endpoint: str = "", credential_ref=None, fetch=None):
        if provider not in rig_route.PROVIDERS:
            raise LedgerError("adapter provider must be a known route provider")
        self.provider = provider
        self.endpoint = endpoint
        self.credential_ref = _normalize_refs({provider: credential_ref} if credential_ref else {}).get(provider, {})
        self.fetch = fetch

    def plan(self, *, network: bool = False, dry_run: bool = False) -> dict:
        if network is not True:
            network = False
        return redact_secrets({
            "provider": self.provider,
            "endpoint": self.endpoint,
            "credential_ref": dict(self.credential_ref),
            "network": network,
            "dry_run": bool(dry_run),
            "note": "network calls are opt-in; adapters never print credential values",
        })

    def parse(self, payload) -> list[dict]:
        rows = _coerce_receipt_rows(payload, provider=self.provider)
        return [normalize_receipt(row, scope="default", source=_source_for_provider(self.provider)) for row in rows]

    def receipts(self, *, network: bool = False):
        if network is not True:
            raise LedgerError(
                f"{self.provider} sync requires explicit network=true or a receipt file; "
                "no automatic network calls"
            )
        if self.fetch is None:
            raise LedgerError(
                f"{self.provider} sync requires an injected read-only fetch; no automatic network calls"
            )
        rows = self.fetch()
        return _coerce_receipt_rows(rows, provider=self.provider)


def openai_adapter(*, fetch=None, credential_ref=None) -> ReadOnlySyncAdapter:
    return ReadOnlySyncAdapter("openai", endpoint=OPENAI_ENDPOINT, credential_ref=credential_ref, fetch=fetch)


def anthropic_adapter(*, fetch=None, credential_ref=None) -> ReadOnlySyncAdapter:
    return ReadOnlySyncAdapter("anthropic", endpoint=ANTHROPIC_ENDPOINT, credential_ref=credential_ref, fetch=fetch)


def _alias_amount_usd(amount):
    """Map amount aliases to an exact decimal string. Do not coerce floats."""
    if isinstance(amount, str):
        return amount
    if isinstance(amount, Decimal):
        return format(amount, "f")
    if type(amount) is int:
        return format(Decimal(amount), "f")
    if isinstance(amount, dict) and not isinstance(amount, bool):
        raw = amount.get("value")
        if raw is None:
            raw = amount.get("amount_usd")
        if raw is None:
            raw = amount.get("usd")
        return _alias_amount_usd(raw) if raw is not None else None
    return None


def _prepare_adapter_receipt(row, *, provider: str) -> dict:
    """Map amount/period aliases, then drop non-ledger metadata before validation."""
    if not isinstance(row, dict) or isinstance(row, bool):
        raise LedgerError("sync fetch must return a list of receipts")
    _walk_secrets(row)
    if row.get("job_id") not in (None, ""):
        raise LedgerError(JOB_ATTRIBUTION_NOTE)
    item = dict(row)
    item.setdefault("provider", provider)
    if "amount_usd" not in item:
        mapped = _alias_amount_usd(item.get("amount"))
        if mapped is not None:
            item["amount_usd"] = mapped
    elif not isinstance(item.get("amount_usd"), str):
        mapped = _alias_amount_usd(item.get("amount_usd"))
        if mapped is not None:
            item["amount_usd"] = mapped
    if "period" not in item:
        start, end = item.get("start"), item.get("end")
        if start or end:
            item["period"] = {"start": str(start or ""), "end": str(end or "")}
    return {key: item[key] for key in RECEIPT_KEYS if key in item}


def _coerce_receipt_rows(payload, *, provider: str) -> list[dict]:
    if payload is None:
        raise LedgerError("sync fetch must return receipts")
    if isinstance(payload, dict) and not isinstance(payload, bool):
        if "amount_usd" in payload or "amount" in payload or "receipt_id" in payload:
            rows = [payload]
        else:
            rows = payload.get("receipts") or payload.get("data") or payload.get("invoices")
            if not isinstance(rows, list):
                raise LedgerError("sync fetch must return a list of receipts")
    elif isinstance(payload, list):
        rows = payload
    else:
        raise LedgerError("sync fetch must return a list of receipts")
    return [_prepare_adapter_receipt(row, provider=provider) for row in rows]


def sync_receipts(
    repo: Path,
    provider: str,
    *,
    adapter=None,
    receipts=None,
    scope: str = "",
    network: bool = False,
    dry_run: bool = False,
    validate_only: bool = False,
) -> dict:
    _parent_only()
    known = {"openai", "anthropic"}
    if provider not in known and provider not in rig_route.PROVIDERS:
        raise LedgerError("sync provider must be a known route provider")
    dry = bool(dry_run or validate_only)
    source = _source_for_provider(provider, "generic_import" if provider not in known else "")
    if receipts is None:
        if adapter is None:
            if provider == "openai":
                adapter = openai_adapter()
            elif provider == "anthropic":
                adapter = anthropic_adapter()
            else:
                adapter = ReadOnlySyncAdapter(provider)
        if adapter.provider != provider:
            raise LedgerError("sync adapter provider mismatch")
        plan_out = adapter.plan(network=network is True, dry_run=dry)
        if adapter.fetch is not None:
            receipts = _coerce_receipt_rows(adapter.fetch(), provider=provider)
        elif network is True:
            receipts = _coerce_receipt_rows(adapter.receipts(network=True), provider=provider)
        else:
            raise LedgerError(
                f"{provider} sync requires a receipt file, an injected fetch, or explicit network=true; "
                "no automatic network calls"
            )
    else:
        receipts = _coerce_receipt_rows(receipts, provider=provider)
        plan_out = (adapter or ReadOnlySyncAdapter(provider)).plan(network=False, dry_run=dry)
    imported = []
    for row in receipts:
        payload = dict(row)
        payload.setdefault("provider", provider)
        payload.setdefault("source", source)
        if dry:
            payload.setdefault("evidence", {"kind": "validate" if validate_only else "dry_run", "method": "file"})
        imported.append(import_receipt(repo, payload, scope=scope, source=source, dry_run=dry))
    return redact_secrets({
        "provider": provider,
        "count": len(imported),
        "dry_run": dry,
        "network": network is True,
        "plan": plan_out,
        "receipts": imported,
        "note": JOB_ATTRIBUTION_NOTE,
    })


def build_report(repo: Path, *, scope: str = "") -> dict:
    name = _scope(repo, scope)
    rows = load_receipts(repo, scope=name)
    return redact_secrets({
        "scope": name,
        "receipts": len(rows),
        "dollar_totals": provider_totals(rows),
        "cohort_totals": cohort_totals(rows),
        "job_attribution": "unavailable",
        "note": (
            "actual invoice dollars only; no estimates or subscription amortization. "
            + JOB_ATTRIBUTION_NOTE
        ),
    })


def format_report(report: dict) -> str:
    totals = report.get("dollar_totals") or {}
    extra = " ".join(f"{name}={amount}" for name, amount in totals.items() if name != "all")
    return (
        f"billing scope={report.get('scope')} receipts={report.get('receipts')} "
        f"usd={totals.get('all', '0')} {extra} job_attribution=unavailable"
    ).strip()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="billing_ledger.py")
    parser.add_argument("cmd", choices=["import", "sync", "report"])
    parser.add_argument("provider_pos", nargs="?", default="", metavar="provider")
    parser.add_argument("--repo", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--scope", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--network", action="store_true")
    args = parser.parse_args()
    repo = rig_jobs.repo_root(args.repo or None)
    try:
        if args.cmd == "import":
            raw = json.loads(Path(args.file).read_text() if args.file else sys.stdin.read())
            provider = str((raw or {}).get("provider") or "")
            source = "generic_import" if provider and provider not in {"openai", "anthropic"} else "import"
            result = import_receipt(repo, raw, scope=args.scope, source=source, dry_run=args.dry_run or args.validate)
        elif args.cmd == "sync":
            provider = (args.provider_pos or args.provider).strip()
            if args.provider_pos and args.provider and args.provider_pos != args.provider:
                raise LedgerError("conflicting positional provider and --provider")
            if not provider:
                raise LedgerError("billing sync requires provider openai or anthropic")
            raw = json.loads(Path(args.file).read_text()) if args.file else None
            result = sync_receipts(
                repo, provider, receipts=raw, scope=args.scope,
                network=args.network is True, dry_run=args.dry_run, validate_only=args.validate,
            )
        else:
            result = build_report(repo, scope=args.scope)
        print(public_json(result) if args.json or args.cmd != "report" else format_report(result))
    except (LedgerError, OSError, json.JSONDecodeError) as error:
        print(public_json({"error": str(error)}) if args.json else str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
