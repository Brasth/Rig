#!/usr/bin/env python3
"""Issue to PR (GitHub): an issue you label for Rig gets a plan comment; your thumbs-up on it queues the work; the
owner then ships it as a draft pull request.

Everything runs with your own gh login, from this machine, while Rig is open: ``poll`` is called by the board (and by
``rig issues poll``); nothing listens for webhooks and no teammate's machine is involved. Steps, each recorded in
``.rig/issues/<number>.json`` so a poll never repeats one:

1. planned  - an open issue carrying the label has no plan yet: post one comment (marked ``<!-- rig-plan -->``) with
              what Rig read from the issue, who routing would give the work to, and the branch it would use.
2. approved - YOUR thumbs-up on that comment (the account gh is signed in as; nobody else's counts). Your thumbs-down
              marks it declined and Rig leaves the issue alone.
3. queued   - the approved issue's brief is added to Rig's work queue (idempotent per issue), where the parent takes it.
4. shipped  - ``ship`` opens a draft pull request from what is ahead of the upstream branch ("Closes #N"). Agents never
              push; shipping stays the owner's.

The plan is written from the issue's own text and from routing's table, not by a model: it says what will be asked and
of whom, which is what a thumbs-up should be about."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import harness
import ui_settings

MARKER = "<!-- rig-plan -->"
MAX_ISSUES = 20
MAX_ITEMS = 8
_TASK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")
_SLUG = re.compile(r"[^a-z0-9]+")


class IssueError(ValueError):
    pass


def default_gh(args: list[str], cwd: Path) -> str:
    done = subprocess.run(["gh", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        low = (done.stderr or "").lower()
        if "auth" in low:
            raise IssueError("gh is not signed in. Run `gh auth login` in your terminal.")
        tail = (done.stderr or done.stdout).strip().splitlines()
        raise IssueError("gh " + " ".join(args[:2]) + " failed: " + (tail[-1] if tail else "unknown error"))
    return done.stdout


def config(repo) -> dict:
    path = harness.harness_path(Path(repo))
    return {"enabled": ui_settings._read_key(path, "issues", "enabled") == "true",
            "label": ui_settings._read_key(path, "issues", "label", keep_case=True) or "rig"}


def _dir(repo) -> Path:
    return Path(repo) / ".rig" / "issues"


def _load(repo, number: int) -> dict:
    try:
        return json.loads((_dir(repo) / f"{number}.json").read_text())
    except (OSError, ValueError):
        return {}


def _save(repo, state: dict) -> None:
    folder = _dir(repo)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{state['number']}.json"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=1, sort_keys=True))
    temporary.replace(path)


def branch_for(number: int, title: str) -> str:
    return f"rig/{number}-{_SLUG.sub('-', title.lower()).strip('-')[:40].strip('-') or 'issue'}"


def items(body: str) -> list[str]:
    """The task-list or bullet lines of an issue's text, as written."""
    found = [m.group(1) for line in (body or "").splitlines() if (m := _TASK.match(line))]
    return found[:MAX_ITEMS]


def plan_text(issue: dict, parent: str, enabled: list[str]) -> str:
    """The plan comment: what Rig read, who routing gives each kind of work, the branch, and how to say go."""
    import route
    lines = [MARKER, f"**Rig's plan for #{issue['number']}: {issue['title']}**", ""]
    wanted = items(issue.get("body", ""))
    if wanted:
        lines += ["What I read from this issue:", *[f"- {w}" for w in wanted], ""]
    else:
        lines += ["I found no task list in this issue, so I will work from its title and description as written.", ""]
    lines.append("Who would do what:")
    for task, kind in (("the change", "implement"), ("checks", "verify"), ("review", "review")):
        try:
            worker, spawn = route.choose_worker(kind, enabled, parent)
        except Exception:
            continue
        who = parent if spawn in ("stay", "native") or not worker else worker
        lines.append(f"- {task}: {who}")
    lines += ["", f"Branch: `{branch_for(issue['number'], issue['title'])}`. Nothing is pushed until the owner ships it, as a draft.",
              "", "React 👍 to this comment to let Rig start, or 👎 to leave this issue alone."]
    return "\n".join(lines)


def brief_text(issue: dict) -> str:
    return (f"Work on GitHub issue #{issue['number']} ({issue['url']}): {issue['title']}\n\n"
            f"{(issue.get('body') or '').strip()}\n\nThe owner approved this plan on the issue; keep to it.")


def poll(repo, gh=default_gh, parent: str = "", enabled: list[str] | None = None) -> dict:
    """One pass over the labelled issues. Returns {"issues": [state...], "did": [what this pass did]}."""
    repo = Path(repo).resolve()
    cfg = config(repo)
    if not cfg["enabled"]:
        raise IssueError("Issue to PR is off for this project; turn it on in Settings or set [issues] enabled = true")
    me = gh(["api", "user", "--jq", ".login"], repo).strip()
    listed = json.loads(gh(["issue", "list", "--label", cfg["label"], "--state", "open", "--limit", str(MAX_ISSUES),
                            "--json", "number,title,body,url"], repo) or "[]")
    did = []
    for issue in listed:
        state = _load(repo, issue["number"])
        if not state:
            posted = json.loads(gh(["api", "repos/{owner}/{repo}/issues/%d/comments" % issue["number"], "-f",
                                    f"body={plan_text(issue, parent, enabled or [])}"], repo))
            state = {"number": issue["number"], "title": issue["title"], "url": issue["url"], "state": "planned",
                     "comment": posted["id"], "branch": branch_for(issue["number"], issue["title"]), "at": time.time()}
            _save(repo, state)
            did.append(f"planned #{issue['number']}")
        if state["state"] == "planned":
            reactions = json.loads(gh(["api", "repos/{owner}/{repo}/issues/comments/%d/reactions" % state["comment"]], repo) or "[]")
            mine = {r.get("content") for r in reactions if (r.get("user") or {}).get("login") == me}
            if "-1" in mine:
                state["state"] = "declined"
                did.append(f"declined #{issue['number']}")
            elif "+1" in mine:
                state["state"] = "approved"
                did.append(f"approved #{issue['number']}")
            _save(repo, state)
        if state["state"] == "approved":
            import work_queue
            item = work_queue.add_item(repo, brief_text(issue), idempotency_key=f"issue-{issue['number']}")
            state.update(state="queued", queue=item.get("id", ""))
            _save(repo, state)
            did.append(f"queued #{issue['number']}")
    return {"issues": view(repo)["issues"], "did": did}


def view(repo) -> dict:
    folder = _dir(repo)
    states = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            states.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return {**config(repo), "issues": states}


def ship(repo, number: int, shipper=None) -> dict:
    """Open the draft pull request for a queued issue from what is ahead, closing the issue when it merges."""
    import applies
    repo = Path(repo).resolve()
    state = _load(repo, number)
    if state.get("state") not in ("queued", "shipped"):
        raise IssueError(f"#{number} is not queued: {state.get('state') or 'Rig has not planned it'}")
    ship_it = shipper or applies.ship
    body = f"Closes #{number}\n\nPlanned on the issue and approved there. Worked on by Rig's helpers; checks are in the board."
    done = ship_it(repo, "pr", state["title"], body, draft=True)
    state.update(state="shipped", pr=done.get("url", ""))
    _save(repo, state)
    return done


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="rig issues")
    parser.add_argument("command", nargs="?", default="show", choices=("show", "poll", "ship"))
    parser.add_argument("number", nargs="?", type=int)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        if args.command == "poll":
            parsed = harness.parse_harness(harness.harness_path(repo))
            enabled = [n for n, on in parsed["workers"].items() if str(on).lower() == "true"]
            print("\n".join(poll(repo, parent=parsed["parent"] or "claude", enabled=enabled)["did"]) or "nothing new")
        elif args.command == "ship":
            if args.number is None:
                parser.error("ship needs an issue number")
            print(ship(repo, args.number).get("url", ""))
        else:
            for s in view(repo)["issues"]:
                print(f"#{s['number']:<5} {s['state']:9} {s['title']}" + (f"  {s['pr']}" if s.get("pr") else ""))
    except (IssueError, ValueError) as error:
        print(f"rig issues: {error}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
