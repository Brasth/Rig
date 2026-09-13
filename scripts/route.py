#!/usr/bin/env python3
"""Pick worker, model, and reasoning from kind. Case is English fallback. User is not asked."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import catalog as rig_catalog  # noqa: E402

PARENT_ONLY = frozenset(
    {
        "sol",
        "astra",
        "fable",
        "gpt-5.6-sol",
        "gpt-6-astra",
        "gpt-5.6-fable",
        "grok-4-fable",
        "claude-fable",
        "claude-fable-5",
        "claude-fable-5-1",
    }
)

# Pin full Claude Code IDs. Aliases (haiku/sonnet/opus) track "latest" and
# `haiku` has resolved to Sonnet on some CLIs. Opus is allowed as a child.
# (worker, kind) -> (model, effort)
MODELS = {
    ("codex", "explore"): ("gpt-5.3-codex-mini", "low"),
    ("codex", "mini"): ("gpt-5.6-luna", "low"),
    ("codex", "bulk"): ("gpt-5.6-luna", "low"),
    ("codex", "implement"): ("gpt-5.6-luna", "low"),
    ("codex", "hard"): ("gpt-5.6-terra", "medium"),
    ("codex", "review"): ("gpt-5.6-terra", "high"),
    ("grok", "explore"): ("grok-4.5", "low"),
    ("grok", "mini"): ("grok-4.5", "low"),
    ("grok", "bulk"): ("grok-4.5", "low"),
    ("grok", "implement"): ("grok-4.6", "high"),
    ("grok", "hard"): ("grok-4.6", "high"),
    ("grok", "review"): ("grok-4.6", "high"),
    ("claude", "explore"): ("claude-haiku-4-5-20251001", "low"),
    ("claude", "mini"): ("claude-haiku-4-5-20251001", "low"),
    ("claude", "bulk"): ("claude-haiku-4-5-20251001", "low"),
    ("claude", "implement"): ("claude-sonnet-5", "medium"),
    ("claude", "hard"): ("claude-opus-5", "high"),
    ("claude", "review"): ("claude-opus-5", "high"),
    ("cursor", "explore"): ("composer-2.5-fast", ""),
    ("cursor", "mini"): ("composer-2.5-fast", ""),
    ("cursor", "bulk"): ("composer-2.5-fast", ""),
    ("cursor", "implement"): ("composer-2.5", ""),
    ("cursor", "hard"): ("cursor-grok-4.6-high", ""),
    ("cursor", "review"): ("claude-opus-5-thinking-high", ""),
    # OpenCode effort is --variant (minimal/high/max). OMP/Pi --thinking. agy --effort.
    ("opencode", "explore"): ("openai/gpt-5.4-mini", "minimal"),
    ("opencode", "mini"): ("openai/gpt-5.4-mini", "minimal"),
    ("opencode", "bulk"): ("openai/gpt-5.4-mini", "minimal"),
    ("opencode", "implement"): ("openai/gpt-5.6-luna", "high"),
    ("opencode", "hard"): ("openai/gpt-5.6-terra", "max"),
    ("opencode", "review"): ("openai/gpt-5.6-terra", "max"),
    ("omp", "explore"): ("grok-4.5", "low"),
    ("omp", "mini"): ("grok-4.5", "low"),
    ("omp", "bulk"): ("grok-4.5", "low"),
    ("omp", "implement"): ("grok-4.6", "high"),
    ("omp", "hard"): ("grok-4.6", "high"),
    ("omp", "review"): ("claude-opus-5", "high"),
    ("pi", "explore"): ("grok-4.5", "low"),
    ("pi", "mini"): ("grok-4.5", "low"),
    ("pi", "bulk"): ("grok-4.5", "low"),
    ("pi", "implement"): ("grok-4.6", "high"),
    ("pi", "hard"): ("grok-4.6", "high"),
    ("pi", "review"): ("claude-opus-5", "high"),
    ("agy", "explore"): ("gemini-3.8-flash-low", "low"),
    ("agy", "mini"): ("gemini-3.8-flash-low", "low"),
    ("agy", "bulk"): ("gemini-3.8-flash-low", "low"),
    ("agy", "implement"): ("gemini-3.8-flash-high", "high"),
    ("agy", "hard"): ("gemini-3.1-pro-high", "high"),
    ("agy", "review"): ("gemini-3.1-pro-high", "high"),
}

NATIVE = {
    ("codex", "explore"): "explorer",
    ("codex", "mini"): "worker",
    ("codex", "bulk"): "bulk",
    ("codex", "implement"): "worker",
    ("codex", "hard"): "worker",
    ("codex", "review"): "reviewer",
    ("grok", "explore"): "explore",
    ("grok", "mini"): "explore",
    ("grok", "implement"): "worker",
    ("grok", "hard"): "worker",
    ("opencode", "explore"): "explore",
    ("opencode", "mini"): "explore",
    ("opencode", "bulk"): "bulk",
    ("opencode", "implement"): "worker",
    ("opencode", "hard"): "worker",
    ("omp", "explore"): "explore",
    ("omp", "mini"): "explore",
    ("omp", "bulk"): "bulk",
    ("omp", "implement"): "worker",
    ("omp", "hard"): "worker",
    ("pi", "explore"): "explore",
    ("pi", "mini"): "explore",
    ("pi", "bulk"): "bulk",
    ("pi", "implement"): "worker",
    ("pi", "hard"): "worker",
    ("agy", "explore"): "explore",
    ("agy", "mini"): "explore",
    ("agy", "bulk"): "bulk",
    ("agy", "implement"): "worker",
    ("agy", "hard"): "worker",
}

NATIVE_PARENTS = frozenset({"codex", "grok", "opencode", "omp", "pi", "agy"})
LAST_RESORT = ("opencode", "omp", "pi", "agy", "codex", "cursor")
WORKER_NAMES = frozenset(
    {"grok", "claude", "cursor", "opencode", "omp", "pi", "agy", "codex", "native"}
)
PROVIDERS = frozenset({"openai", "anthropic", "xai", "google", "cursor"})

KEYWORDS = (
    (
        "stay",
        (
            "computer use",
            "computer-use",
            "chrome profile",
            "chrome-profile",
            "real chrome",
            "live desktop",
            "advise",
            "do you think",
            "technical question",
            "architectural guidance",
        ),
    ),
    ("review", ("review", "audit", "nitpick")),
    (
        "explore",
        (
            "explore", "explores", "explored", "exploring", "exploration",
            "explorations", "exploratory", "explorer", "explorers",
            "scout", "locate", "trace", "where is", "find file", "read-only",
        ),
    ),
    ("bulk", ("bulk", "rename-only", "mechanical", "format-only")),
    (
        "hard",
        (
            "architect", "architects", "architecture", "architectures", "architectural",
            "security", "multi-file", "cross-module", "hard refactor",
        ),
    ),
    (
        "mini",
        (
            "typo",
            "comment-only",
            "one-line",
            "tiny",
            "trivial",
            "docs only",
            "skill only",
            "readme only",
            "documentation only",
        ),
    ),
)

# Keep fallback English rules bounded: arbitrary word fragments misroute
# requests such as "fix preview". Explicit parent roles remain authoritative.
KEYWORD_RULES = tuple(
    (kind, phrase, re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"))
    for kind, phrases in KEYWORDS
    for phrase in phrases
)
STAY_OPENING = re.compile(
    r"^(?:please[\s,]+)?(?:(?:how|why|what|explain|plan)\b|(?:can|could)\s+you\s+explain\b)"
)
DOCS_REQUEST = re.compile(
    r"(?:please[\s,]+)?(?:update the skill|update usage\.md)[.!?]*"
)

# Explicit pick kinds skip case keywords. Job role "worker" and "auto" do not.
EXPLICIT_KIND = {
    "stay": "stay",
    "parent": "stay",
    "vision": "stay",
    "computer-use": "stay",
    "chrome": "stay",
    "chrome-profile": "stay",
    "explore": "explore",
    "explorer": "explore",
    "mini": "mini",
    "bulk": "bulk",
    "hard": "hard",
    "implement-hard": "hard",
    "review": "review",
    "reviewer": "review",
    "implement": "implement",
}


def classify_details(role: str, case: str) -> dict[str, str]:
    """Return deterministic kind and the explicit role or fallback rule used."""
    role_n = (role or "").lower().strip()
    mapped = EXPLICIT_KIND.get(role_n)
    if mapped:
        return {"kind": mapped, "source": "explicit", "rule": f"explicit:{role_n}"}
    text = " ".join((case or "").lower().split())
    if STAY_OPENING.search(text):
        return {"kind": "stay", "source": "fallback", "rule": "stay:opening"}
    for kind, phrase, pattern in KEYWORD_RULES:
        if pattern.search(text):
            return {"kind": kind, "source": "fallback", "rule": f"{kind}:{phrase}"}
    # A docs shortcut covers the whole request, never an embedded clause of a fix.
    if DOCS_REQUEST.fullmatch(text):
        return {"kind": "mini", "source": "fallback", "rule": "mini:whole-request-docs"}
    return {"kind": "implement", "source": "fallback", "rule": "implement:default"}


def classify(role: str, case: str) -> str:
    return classify_details(role, case)["kind"]


def provider_for(model: str) -> str:
    """Identify underlying model providers across CLI wrappers, never aliases."""
    raw = (model or "").strip().lower()
    if not raw or re.search(r"\s", raw):
        return ""
    family = raw.rsplit("/", 1)[-1]
    if family.startswith("cursor-"):
        family = family[len("cursor-"):]
    families = (
        ("openai", r"(?:gpt-\d|o[134](?:\b|[-.])|codex-mini-)"),
        ("anthropic", r"claude-(?:(?:haiku|sonnet|opus|fable)-\d|\d)"),
        ("xai", r"grok-\d"),
        ("google", r"gemini-\d"),
        ("cursor", r"composer-\d"),
    )
    for provider, pattern in families:
        if re.match(pattern, family):
            return provider
    # An explicit provider/model ID is meaningful; custom bare aliases are not.
    parts = raw.split("/")
    return parts[-2] if len(parts) > 1 and parts[-2] in PROVIDERS and parts[-1] else ""


def _writer_context(
    *, writer_job_id: str = "", writer_cli: str = "", writer_model: str = "",
    writer_provider: str = "", review_mode: str = "standalone", repo: Path | None = None,
    jobs_snapshot: list[dict] | None = None,
    hash_cache: dict | None = None,
) -> tuple[dict, str]:
    """Resolve actual writer identity and its current parent acceptance."""
    if review_mode not in {"standalone", "independent"}:
        raise ValueError("review_mode must be standalone or independent")
    supplied_cli, supplied_model = writer_cli.strip().lower(), writer_model.strip()
    supplied_provider = writer_provider.strip().lower()
    if supplied_provider == "unknown":
        supplied_provider = ""
    if supplied_provider and supplied_provider not in PROVIDERS:
        raise ValueError("writer_provider must be a canonical provider or unknown")
    cli = model = provider = provider_source = snapshot_id = ""
    job_id = writer_job_id.strip()
    acceptance_reason = ""
    if job_id:
        import jobs as rig_jobs
        import verification

        root = Path(repo or Path.cwd())
        if jobs_snapshot is None:
            try:
                job = rig_jobs.resolve_job(root, job_id)
            except SystemExit as exc:
                raise ValueError(str(exc)) from exc
        else:
            if job_id in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", job_id):
                raise ValueError(f"invalid writer job id '{job_id}'")
            matches = [j for j in jobs_snapshot if str(j.get("job_id") or j.get("id")) == job_id]
            if not matches:
                matches = [j for j in jobs_snapshot if job_id in str(j.get("job_id") or j.get("id"))]
            if len(matches) != 1:
                raise ValueError(f"{'ambiguous' if matches else 'no such'} writer job id '{job_id}'")
            job = matches[0]
        job_id = str(job.get("job_id") or job.get("id") or job_id)
        cli = str(job.get("worker") or "").strip().lower()
        if cli not in WORKER_NAMES - {"native"}:
            cli = ""
        actual = job.get("model_source") in {"selected", "observed"} and not job.get("model_inferred")
        if actual:
            model = str(job.get("model") or "").strip()
            provider = provider_for(model)
            provider_source = "model" if provider else ""
        recorded_provider = str(job.get("provider") or "").strip().lower()
        recorded_source = job.get("provider_source")
        if recorded_provider in PROVIDERS and (
            recorded_source == "explicit" or (actual and recorded_source in {"model", "selected", "observed"})
        ):
            if provider and provider != recorded_provider:
                raise ValueError("recorded writer provider conflicts with its actual model")
            provider = recorded_provider
            provider_source = str(recorded_source)
        assessment = verification.assessment(root, job, refresh=True, cache=hash_cache)
        if assessment.get("state") == "verified" and assessment.get("acceptance") == "accepted":
            snapshot_id = str(assessment.get("snapshot_id") or "")
        if not snapshot_id:
            acceptance_reason = str(assessment.get("reason") or "writer has no current parent-accepted snapshot")
    for label, recorded, supplied in (
        ("CLI", cli, supplied_cli), ("model", model, supplied_model),
        ("provider", provider, supplied_provider),
    ):
        if recorded and supplied and recorded != supplied:
            raise ValueError(f"supplied writer {label} conflicts with recorded actual {label}")
    cli, model = cli or supplied_cli, model or supplied_model
    derived = provider_for(model)
    if derived and (provider or supplied_provider) and derived != (provider or supplied_provider):
        raise ValueError("writer provider conflicts with writer model")
    if not provider:
        provider = derived or supplied_provider
        provider_source = "model" if derived else "explicit" if supplied_provider else "unknown"
    context = {
        "writer_job_id": job_id, "writer_snapshot_id": snapshot_id,
        "writer_cli": cli, "writer_model": model, "writer_provider": provider,
        "writer_provider_source": provider_source or "unknown", "review_mode": review_mode,
    }
    reason = ""
    if review_mode == "independent":
        if not job_id:
            reason = "independent review requires writer_job_id and a current parent-accepted snapshot"
        elif not snapshot_id:
            reason = f"independent review requires a current parent-accepted writer snapshot: {acceptance_reason}"
        elif not provider:
            reason = "independent review requires a known actual writer provider"
    return context, reason


def _review_model(model: str, context: dict) -> tuple[str, str]:
    """Return independence and any disqualifying actual-model reason."""
    banned = assert_child_model(model)
    if banned:
        return "unavailable", banned
    provider, writer = provider_for(model), context["writer_provider"]
    if writer and provider == writer:
        return "unavailable", f"reviewer provider {provider} matches writer provider {writer}"
    if context["review_mode"] == "independent" and not provider:
        return "unavailable", "independent review requires a known reviewer model provider"
    return ("confirmed" if writer and provider else "unknown"), ""


def model_for(worker: str, kind: str) -> tuple[str, str]:
    return MODELS.get((worker, kind), MODELS.get((worker, "implement"), ("", "")))


def resolved_model_for(
    worker: str,
    kind: str,
    catalogs: dict[str, list[str]] | None = None,
) -> tuple[str, str]:
    preferred, effort = model_for(worker, kind)
    model = rig_catalog.resolve_model(worker, kind, preferred, catalogs=catalogs)
    return model, effort


def parse_exclude(exclude: str | list | tuple | set | None) -> set[str]:
    if exclude is None or exclude == "" or exclude == () or exclude == []:
        return set()
    if isinstance(exclude, str):
        parts = exclude.split(",")
    else:
        parts = list(exclude)
    out = {str(p).strip().lower() for p in parts if str(p).strip()}
    return {p for p in out if p in WORKER_NAMES}


def choose_worker(
    kind: str,
    effective: list[str],
    live: str,
    exclude: str | list | tuple | set | None = None,
) -> tuple[str, str]:
    """Return (worker, spawn) where spawn is run-worker, native, stay, or none."""
    blocked = parse_exclude(exclude)
    avail = [w for w in effective if w not in blocked]
    skip_native = bool(live) and (live in blocked or "native" in blocked)
    if kind == "stay":
        return live, "stay"
    if kind in {"explore", "mini", "bulk"} and live in NATIVE_PARENTS and not skip_native:
        return live, "native"
    if kind == "review":
        order = ("claude", "grok") + LAST_RESORT
    else:
        order = ("grok", "claude")
    for worker in order:
        if worker in avail:
            return worker, "run-worker"
    if kind in {"implement", "hard"} and live in NATIVE_PARENTS and not skip_native:
        return live, "native"
    last_avail = [w for w in LAST_RESORT if w in avail]
    if blocked and last_avail == ["cursor"]:
        return "", "none"
    for worker in LAST_RESORT:
        if worker in avail:
            return worker, "run-worker"
    if kind == "review":
        return "", "none"
    if live in NATIVE_PARENTS and not skip_native:
        return live, "native"
    return "", "none"


def _base_choice(
    kind: str,
    worker: str,
    spawn: str,
    *,
    classification: dict[str, str],
    model: str = "",
    effort: str = "",
    native_agent: str = "",
    reason: str = "",
    parent_writes: bool = False,
    executor_kind: str = "",
    model_source: str = "unknown",
    review: dict | None = None,
) -> dict:
    choice = {
        "kind": kind,
        "worker": worker,
        "spawn": spawn,
        "model": model,
        "effort": effort,
        "native_agent": native_agent,
        "reason": reason,
        "parent_writes": bool(parent_writes),
        "classification_source": classification["source"],
        "classification_rule": classification["rule"],
        "executor_kind": executor_kind,
        "model_source": model_source,
        "provider": provider_for(model) if model_source in {"selected", "observed"} else "",
    }
    choice["provider_source"] = "model" if choice["provider"] else "unknown"
    choice.update(review or {})
    return choice


def pick(
    live: str,
    effective: list[str],
    role: str,
    case: str,
    catalogs: dict[str, list[str]] | None = None,
    exclude: str | list | tuple | set | None = None,
    parent_model: str = "",
    parent_effort: str = "",
    writer_job_id: str = "",
    writer_cli: str = "",
    writer_model: str = "",
    writer_provider: str = "",
    review_mode: str = "standalone",
    repo: Path | None = None,
    jobs_snapshot: list[dict] | None = None,
    hash_cache: dict | None = None,
) -> dict:
    classification = classify_details(role, case)
    kind = classification["kind"]
    blocked = parse_exclude(exclude)
    if review_mode not in {"standalone", "independent"}:
        raise ValueError("review_mode must be standalone or independent")
    if kind == "review":
        context, reason = _writer_context(
            writer_job_id=writer_job_id, writer_cli=writer_cli, writer_model=writer_model,
            writer_provider=writer_provider, review_mode=review_mode, repo=repo,
            jobs_snapshot=jobs_snapshot,
            hash_cache=hash_cache,
        )
        failures = []
        remaining = [w for w in effective if w != live]
        while not reason:
            if blocked and {w for w in remaining if w not in blocked} == {"cursor"}:
                reason = "confirm before live Cursor; do not auto-spawn cursor"
                break
            worker, spawn = choose_worker(kind, remaining, live, exclude=exclude)
            if not worker or spawn == "none":
                reason = "review needs a different vendor; no eligible reviewer"
                if failures:
                    reason += ": " + "; ".join(failures)
                break
            model, effort = resolved_model_for(worker, kind, catalogs)
            independence, rejection = _review_model(model, context)
            if not rejection:
                return _base_choice(
                    kind, worker, spawn, classification=classification, model=model, effort=effort,
                    reason=f"review: {worker} child {model}" + (f" effort={effort}" if effort else ""),
                    executor_kind="wrapper", model_source="selected",
                    review={**context, "independence": independence},
                )
            failures.append(f"{worker}: {rejection}")
            remaining = [w for w in remaining if w != worker]
        return _base_choice(
            kind, "", "none", classification=classification, reason=reason,
            review={**context, "independence": "unavailable"},
        )
    worker, spawn = choose_worker(kind, effective, live, exclude=exclude)
    actual_model = (parent_model or "").strip()
    actual_effort = (parent_effort or "").strip() if actual_model else ""
    if spawn == "stay":
        return _base_choice(
            kind,
            worker or live,
            "stay",
            classification=classification,
            model=actual_model,
            effort=actual_effort,
            executor_kind="parent",
            model_source="observed" if actual_model else "unknown",
            reason=(
                "parent keeps ask / plan / advise / vision / computer-use / chrome-profile. "
                "Figma, computer-use, and chrome-profile stay with the parent."
            ),
        )
    if spawn == "none" or not worker:
        if kind == "review":
            reason = (
                "review needs a different vendor; do not self-review. tell the user."
            )
        elif blocked:
            leftover = [w for w in LAST_RESORT if w in effective and w not in blocked]
            if leftover == ["cursor"] or (
                not leftover and "cursor" in {str(x).strip().lower() for x in effective}
            ):
                reason = "confirm before live Cursor; do not auto-spawn cursor"
            else:
                reason = (
                    "no effective worker after exclude; do not unlock a disabled worker."
                )
        else:
            reason = "no effective worker; use cheaper same-CLI workers. That is success."
        return _base_choice(kind, "", "none", classification=classification, reason=reason)
    native_agent = NATIVE.get((worker, kind), "") if spawn == "native" else ""
    parent_writes = spawn == "native" and kind in {"implement", "hard"}
    if parent_writes:
        model, effort = actual_model, actual_effort
        executor_kind = "parent"
        model_source = "observed" if model else "unknown"
        reason = (
            f"{kind}: this parent writes. "
            "record with rig job record. do not spawn a second same-CLI session."
        )
    else:
        model, effort = resolved_model_for(worker, kind, catalogs)
        executor_kind = "native_child" if spawn == "native" else "wrapper"
        model_source = "selected"
        if spawn == "native":
            reason = f"{kind}: cheap same-CLI {worker} {native_agent} ({model} {effort or 'default'})"
        else:
            reason = f"{kind}: {worker} child {model}" + (f" effort={effort}" if effort else "")
    return _base_choice(
        kind,
        worker,
        spawn,
        classification=classification,
        model=model,
        effort=effort,
        native_agent=native_agent,
        reason=reason,
        parent_writes=parent_writes,
        executor_kind=executor_kind,
        model_source=model_source,
    )


def assert_child_model(model: str) -> str | None:
    raw = (model or "").strip().lower()
    if not raw:
        return None
    for banned in PARENT_ONLY:
        if banned in raw:
            return f"refuse parent-only model '{model}'"
    return None


def format_text(choice: dict) -> str:
    lines = [
        f"kind={choice['kind']} worker={choice.get('worker') or '-'} spawn={choice['spawn']}",
        f"model={choice.get('model') or '-'} effort={choice.get('effort') or '-'}",
    ]
    if choice.get("native_agent"):
        lines.append(f"native_agent={choice['native_agent']}")
    if choice.get("parent_writes"):
        lines.append("parent_writes=true")
    lines.append(choice.get("reason") or "")
    if choice.get("spawn") == "run-worker" and choice.get("worker"):
        env = f"RIG_LIVE=1 RIG_ROLE={shlex.quote(choice['kind'])} RIG_MODEL={shlex.quote(choice['model'])}"
        if choice.get("effort"):
            env += f" RIG_EFFORT={shlex.quote(choice['effort'])}"
        for field in ("writer_job_id", "writer_cli", "writer_model", "writer_provider", "review_mode"):
            if choice.get(field):
                env += f" RIG_{field.upper()}={shlex.quote(choice[field])}"
        lines.append(
            f"{env} \"$HOME/.rig/scripts/run-worker.sh\" {choice['worker']} <id> .rig/jobs/<id>/brief.md"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="route.py")
    parser.add_argument("cmd", choices=["pick", "allow", "env"])
    parser.add_argument("--live", default="")
    parser.add_argument("--effective", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--case", default="")
    parser.add_argument("--worker", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--parent-model", default="")
    parser.add_argument("--parent-effort", default="")
    parser.add_argument("--writer-job-id", default="")
    parser.add_argument("--writer-cli", default="")
    parser.add_argument("--writer-model", default="")
    parser.add_argument("--writer-provider", default="")
    parser.add_argument("--review-mode", choices=["standalone", "independent"], default="standalone")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--exclude", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    writer_args = dict(
        writer_job_id=args.writer_job_id, writer_cli=args.writer_cli,
        writer_model=args.writer_model, writer_provider=args.writer_provider,
        review_mode=args.review_mode, repo=args.repo,
    )

    if args.cmd == "allow":
        err = assert_child_model(args.model)
        provider = provider_for(args.model)
        metadata = {"provider": provider, "provider_source": "model" if provider else "unknown"}
        if not err and classify(args.role, args.case) == "review":
            try:
                context, err = _writer_context(**writer_args)
            except ValueError as exc:
                parser.error(str(exc))
            if not err:
                independence, err = _review_model(args.model, context)
                metadata.update(context, independence=independence)
        if err:
            print(err, file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(metadata))
        return 0

    if args.cmd == "env":
        kind = classify(args.role, args.case)
        worker = args.worker or "codex"
        model, effort = resolved_model_for(worker, kind)
        print(f"RIG_ROLE={kind}")
        print(f"RIG_MODEL={model}")
        print(f"RIG_EFFORT={effort}")
        return 0

    effective = [x.strip() for x in args.effective.split(",") if x.strip()]
    try:
        choice = pick(
            args.live, effective, args.role, args.case, exclude=args.exclude,
            parent_model=args.parent_model, parent_effort=args.parent_effort, **writer_args,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(choice, indent=2))
    else:
        print(format_text(choice))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
