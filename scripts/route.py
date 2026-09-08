#!/usr/bin/env python3
"""Pick worker, model, and reasoning from the case. User is not asked."""
from __future__ import annotations

import argparse
import json
import sys

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
}

KEYWORDS = (
    ("review", ("review", "audit", "nitpick")),
    ("explore", ("explor", "scout", "locate", "trace", "where is", "find file", "read-only")),
    ("bulk", ("bulk", "rename-only", "mechanical", "format-only")),
    ("mini", ("typo", "comment-only", "one-line", "tiny", "trivial")),
    ("hard", ("architect", "security", "multi-file", "cross-module", "hard refactor")),
)


def classify(role: str, case: str) -> str:
    text = f"{role} {case}".lower()
    for kind, words in KEYWORDS:
        if any(w in text for w in words):
            return kind
    role = (role or "implement").lower().strip()
    if role in {"explorer", "explore"}:
        return "explore"
    if role in {"worker", "implement"}:
        return "implement"
    if role in {"hard", "implement-hard"}:
        return "hard"
    if role in {"bulk", "review", "mini"}:
        return role
    return "implement"


def model_for(worker: str, kind: str) -> tuple[str, str]:
    return MODELS.get((worker, kind), MODELS.get((worker, "implement"), ("", "")))


def choose_worker(kind: str, effective: list[str], live: str) -> tuple[str, str]:
    """Return (worker, spawn) where spawn is run-worker or native."""
    if kind in {"explore", "mini", "bulk"} and live in {"codex", "grok"}:
        return live, "native"
    order = ("claude", "grok", "codex") if kind == "review" else ("grok", "claude", "codex")
    for worker in order:
        if worker in effective:
            return worker, "run-worker"
    if live in {"codex", "grok"}:
        return live, "native"
    return "", "none"


def pick(live: str, effective: list[str], role: str, case: str) -> dict:
    kind = classify(role, case)
    worker, spawn = choose_worker(kind, effective, live)
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
    model, effort = model_for(worker, kind)
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
    parser.add_argument("--role", default="implement")
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
        model, effort = model_for(worker, kind)
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
