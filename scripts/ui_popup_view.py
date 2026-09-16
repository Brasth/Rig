"""Human-readable popup content without process and storage implementation fields."""
import json
import textwrap
from datetime import datetime

from ui_snapshot import format_parent_action, scrub_secrets


def _wrap_lines(lines, width):
    result = []
    for line in lines:
        for part in str(line).splitlines() or ['']:
            result.extend(textwrap.wrap(part, max(10, width), replace_whitespace=False) or [''])
    return result


def is_workflow(item):
    return isinstance(item, dict) and bool(item.get('workflow_id')) and 'required' in item and 'accepted' in item


def workflow_detail_lines(item, width=80):
    item = scrub_secrets(item if isinstance(item, dict) else {})
    lines = [
        str(item.get('workflow_id') or 'workflow'),
        'Status: ' + str(item.get('status') or 'unknown'),
        'Accepted/required: ' + str(item.get('accepted', 0)) + '/' + str(item.get('required', 0)),
        'Running: ' + str(item.get('running', 0)) + '  ASK: ' + str(item.get('ask', 0)),
        'Blocker: ' + str(item.get('blocker') or 'none'),
        'Next parent action: ' + format_parent_action(item.get('next_parent_action')),
    ]
    if item.get('title'):
        lines.append('Title: ' + str(item['title']))
    return _wrap_lines(lines, width)


def detail_lines(job, width=80):
    if is_workflow(job):
        return workflow_detail_lines(job, width)
    job = scrub_secrets(job if isinstance(job, dict) else {})
    ask = job.get('ask') or {}
    lines = [str(job.get('task') or job.get('job_id') or 'Job'),
             'Status: ' + str(job.get('display_state') or job.get('effective') or job.get('status') or 'unknown'),
             'Worker: ' + ' · '.join(str(job[k]) for k in ('worker', 'display_model', 'role') if job.get(k))]
    if job.get('display_reason'): lines.append(str(job['display_reason']))
    if ask:
        lines += ['', 'Approval requested: ' + str(ask.get('tool_name') or 'tool'), str(ask.get('preview') or '')]
        if ask.get('input') or ask.get('tool_input'):
            lines.append(json.dumps(ask.get('input') or ask.get('tool_input'), ensure_ascii=False, indent=2))
    for label, key in [('Started', 'started_at'), ('Finished', 'ended_at'), ('Current activity', 'doing'), ('Updated', 'doing_updated_at')]:
        if job.get(key): lines.append(label + ': ' + str(job[key]))
    reservation = job.get('reservation') or {}
    held = bool(reservation) and reservation.get('stage') != 'released'
    files = (reservation.get('files') or job.get('files') or []) if held else []
    lines += ['', 'Held files: ' + (', '.join(str(x) for x in files) if files else 'none')]
    if job.get('files') and not held: lines.append('Files: ' + ', '.join(job['files']))
    if reservation.get('held_reason'): lines.append(str(reservation['held_reason']))
    if job.get('activities'):
        lines += ['', 'Recent activity']
        for activity in job['activities'][-20:]:
            lines.append(str(activity.get('text') or activity.get('message') or activity.get('summary') or activity.get('type') or '') if isinstance(activity, dict) else str(activity))
    return _wrap_lines(lines, width)


def row_text(item, tab):
    item = scrub_secrets(item if isinstance(item, dict) else {})
    if tab == 'notices':
        at = item.get('at')
        stamp = datetime.fromtimestamp(at).strftime('%H:%M') if isinstance(at, (int, float)) else ''
        return ('! ' if item.get('attention') else '') + stamp + ' ' + str(item.get('text') or '')
    if tab in {'workflows', 'Workflows'} or is_workflow(item):
        action = item.get('next_parent_action') or {}
        kind = action.get('kind') if isinstance(action, dict) else action
        return ' · '.join(str(value) for value in (
            item.get('workflow_id'), item.get('status'),
            f"{item.get('accepted', 0)}/{item.get('required', 0)}",
            kind or item.get('blocker') or '',
        ) if value)
    return ' · '.join(str(value) for value in (item.get('job_id') or item.get('id'), item.get('display_state') or item.get('effective') or item.get('status'), item.get('task') or item.get('text')) if value)
