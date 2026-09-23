"""Small, dependency-free TypeSafe Jev routing client.

Secrets are resolved only at request time and never written into routing evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
KEY_ENV = "RIG_API_JEV_KEY"
KEYCHAIN_SERVICE = "rig"
KEYCHAIN_ACCOUNT = "jev-api-key"


class JevError(RuntimeError):
    pass


def key() -> str:
    value = os.environ.get(KEY_ENV, "").strip()
    if value:
        return value
    if os.uname().sysname != "Darwin":
        return ""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True, text=True, check=False, timeout=3,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def key_source() -> str:
    if os.environ.get(KEY_ENV, "").strip():
        return "environment"
    return "keychain" if key() else "missing"


def store_key(secret: str) -> None:
    """Store a global key without placing it in config, logs, or command argv."""
    value = str(secret or "").strip()
    if not value:
        raise JevError("credential-empty")
    if os.uname().sysname != "Darwin":
        raise JevError("keychain-unavailable")
    # security prompts when -w is its last argument. stdin keeps the secret
    # out of argv, shell history, harness files, and routing sidecars.
    result = subprocess.run(
        ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
         "-a", KEYCHAIN_ACCOUNT, "-w"],
        input=value + "\n", capture_output=True, text=True, check=False, timeout=8,
    )
    if result.returncode:
        raise JevError("keychain-store-failed")


def delete_key() -> bool:
    if os.uname().sysname != "Darwin":
        return False
    result = subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True, text=True, check=False, timeout=5,
    )
    return result.returncode == 0


def status() -> dict:
    return {"configured": bool(key()), "source": key_source(), "env": KEY_ENV}


def choice(*, summary: str, role: str, assessment: dict, traits: list[str], choices: list[dict], timeout: float = 1.0) -> dict:
    secret = key()
    if not secret:
        raise JevError("credential-unavailable")
    if not choices or len(choices) > 255:
        raise JevError("candidate-limit")
    state = {
        "task": str(summary or "")[:2000], "role": role,
        "assessment": {k: assessment.get(k, "") for k in ("complexity", "risk", "uncertainty")},
        "traits": list(traits),
        "candidates": choices,
    }
    body = {"model": "jev", "state": state, "questions": [{"key": "profile", "type": "choice", "choices": [c["id"] for c in choices]}]}
    request = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise JevError("transport-failed") from exc
    answer = (payload.get("answers") or {}).get("profile") if isinstance(payload, dict) else None
    if not isinstance(answer, dict) or not isinstance(answer.get("choice"), str):
        raise JevError("invalid-response")
    return {"id": answer["choice"], "confidence": answer.get("confidence"), "probabilities": answer.get("probabilities")}
