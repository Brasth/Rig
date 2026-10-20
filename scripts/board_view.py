"""Presentation payload for non-curses clients of the board API.

The Go shell renders this and never re-derives job state: labels, tones, glyphs,
ordering, counts and detail text all come from the same functions the curses
board uses, so the two surfaces cannot disagree. Pure data in, JSON-safe data out.
"""
from __future__ import annotations

from pathlib import Path

from tui_chrome import project_name
from tui_rows import (
    _listing_id, attention_first_jobs, attention_first_workflows, entry_lines, job_board_rank,
    job_state, row_state_info, row_title,
)
from tui_style import state_glyph
from tui_view import _detail_lines, _workflow_detail_lines
from ui_snapshot import _ATTENTION_STATUSES

VIEW_VERSION = 1
_WIDE = 100000  # entry_lines pads and clips to a width; ask for none of that.


def _queue_detail(row: dict) -> list[str]:
    detail = [str(row.get("id") or "unknown"), f"status {row.get('status') or 'pending'}",
              f"waiting {row.get('waiting_reason') or 'Awaiting parent claim'}",
              f"worker {row.get('worker') or 'parent chooses'}  priority {row.get('priority') or 0}"]
    if row.get("thread"):
        detail.append(f"thread {row['thread']}")
    detail.append(f"task   {row.get('text') or '-'}")
    return detail


def _row(tab: str, row: dict) -> dict:
    label, tone, explanation = row_state_info(tab, row)
    lines = entry_lines(tab, row, _WIDE, info=(label, tone, explanation), two_line=tab != "Workflows")
    second = lines[1].strip() if len(lines) > 1 else ""
    out = {
        "id": str(_listing_id(tab, row) or ""),
        "title": row_title(tab, row),
        "label": label, "tone": tone, "glyph": state_glyph(label),
        "second": second, "explanation": explanation,
        "worker": str(row.get("worker") or ""),
    }
    if tab == "Jobs":
        out["detail"] = _detail_lines(row)
        out["ask"] = row.get("effective") == "ask"
        out["attention"] = job_board_rank(row) <= 1
    elif tab == "Workflows":
        out["summary"] = f"accepted {row.get('accepted', 0)}/{row.get('required', 0)}"
        out["detail"] = _workflow_detail_lines(row)
        out["attention"] = row.get("status") in _ATTENTION_STATUSES
    else:
        out["summary"] = f"p{row.get('priority') or 0}"
        out["detail"] = _queue_detail(row)
        out["attention"] = False
    return out


def build(jobs, pending, workflows, slots: int, cap: int, repo="") -> dict:
    jobs, pending, workflows = list(jobs or []), list(pending or []), list(workflows or [])
    ordered_jobs = attention_first_jobs(jobs)
    ordered_flows = attention_first_workflows(workflows)
    job_rows = [_row("Jobs", row) for row in ordered_jobs]
    queue_rows = [_row("Queue", row) for row in pending]
    flow_rows = [_row("Workflows", row) for row in ordered_flows]
    attention_jobs = sum(row["attention"] for row in job_rows)
    attention_flows = sum(row["attention"] for row in flow_rows)
    return {
        "version": VIEW_VERSION,
        "header": {
            "project": project_name(Path(repo)) if repo else "",
            "attention": attention_jobs,
            "running": sum(job_state(row)[0] == "Running" for row in jobs),
            "slots": int(slots), "cap": int(cap),
        },
        "tabs": [
            {"name": "Jobs", "count": len(job_rows), "attention": attention_jobs},
            {"name": "Queue", "count": len(queue_rows), "attention": 0},
            {"name": "Workflows", "count": len(flow_rows), "attention": attention_flows},
        ],
        "jobs": job_rows, "queue": queue_rows, "workflows": flow_rows,
    }


__all__ = ["VIEW_VERSION", "build"]
