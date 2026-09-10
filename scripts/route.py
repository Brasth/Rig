#!/usr/bin/env python3
"""Pick worker, model, and reasoning from kind. Case is English fallback. User is not asked."""
from __future__ import annotations

import argparse
import json
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
    ("codex", "mini"): ("gpt-5.3-codex-mini", "low"),
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
    ("codex", "mini"): "explorer",
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
    ("explore", ("explor", "scout", "locate", "trace", "where is", "find file", "read-only")),
    ("bulk", ("bulk", "rename-only", "mechanical", "format-only")),
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
            "update the skill",
            "update usage.md",
        ),
    ),
    ("hard", ("architect", "security", "multi-file", "cross-module", "hard refactor")),
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


def classify(role: str, case: str) -> str:
    role_n = (role or "").lower().strip()
    mapped = EXPLICIT_KIND.get(role_n)
    if mapped:
        return mapped
    text = (case or "").lower()
    for kind, words in KEYWORDS:
        if any(w in text for w in words):
            return kind
    return "implement"


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


def choose_worker(kind: str, effective: list[str], live: str) -> tuple[str, str]:
    """Return (worker, spawn) where spawn is run-worker, native, or stay."""
    if kind == "stay":
        return live, "stay"
    if kind in {"explore", "mini", "bulk"} and live in NATIVE_PARENTS:
        return live, "native"
    # Cross-CLI first: Grok (when not live), then Claude. Cursor/Codex are last
    # resort — a Grok parent with Cursor on PATH was always spawning Cursor.
    if kind == "review":
        order = ("claude", "grok", "cursor", "opencode", "omp", "pi", "agy", "codex")
    else:
        order = ("grok", "claude")
    for worker in order:
        if worker in effective:
            return worker, "run-worker"
    if kind in {"implement", "hard"} and live in NATIVE_PARENTS:
        return live, "native"
    for worker in ("cursor", "opencode", "omp", "pi", "agy", "codex"):
        if worker in effective:
            return worker, "run-worker"
    if live in NATIVE_PARENTS:
        return live, "native"
    return "", "none"


def pick(
    live: str,
    effective: list[str],
    role: str,
    case: str,
    catalogs: dict[str, list[str]] | None = None,
) -> dict:
    kind = classify(role, case)
    worker, spawn = choose_worker(kind, effective, live)
    if spawn == "stay":
        return {
            "kind": kind,
            "worker": worker or live,
            "spawn": "stay",
            "model": "",
            "effort": "",
            "native_agent": "",
            "reason": (
                "parent keeps ask / plan / advise / vision / computer-use / chrome-profile. "
                "spawn a worker only if this CLI cannot do it."
            ),
        }
    if not worker:
        return {
            "kind": kind,
            "worker": "",
            "spawn": "none",
            "model": "",
            "effort": "",
            "native_agent": "",
            "reason": "no effective worker; use cheaper same-CLI workers. That is success.",
        }
    model, effort = resolved_model_for(worker, kind, catalogs)
    native_agent = NATIVE.get((worker, kind), "") if spawn == "native" else ""
    if spawn == "native":
        reason = f"{kind}: cheap same-CLI {worker} {native_agent} ({model} {effort or 'default'})"
    else:
        reason = f"{kind}: {worker} child {model}" + (f" effort={effort}" if effort else "")
    return {
        "kind": kind,
        "worker": worker,
        "spawn": spawn,
        "model": model,
        "effort": effort,
        "native_agent": native_agent,
        "reason": reason,
    }


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
    lines.append(choice.get("reason") or "")
    if choice.get("spawn") == "run-worker" and choice.get("worker"):
        env = f"RIG_LIVE=1 RIG_ROLE={choice['kind']} RIG_MODEL={choice['model']}"
        if choice.get("effort"):
            env += f" RIG_EFFORT={choice['effort']}"
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
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.cmd == "allow":
        err = assert_child_model(args.model)
        if err:
            print(err, file=sys.stderr)
            return 1
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
    choice = pick(args.live, effective, args.role, args.case)
    if args.json:
        print(json.dumps(choice, indent=2))
    else:
        print(format_text(choice))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
