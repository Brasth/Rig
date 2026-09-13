"""Disposable curses client; actions belong to the repository service."""
from __future__ import annotations
import curses
import json
import os
import sys
import time
import uuid
from pathlib import Path
from tui_editor import Draft, InputDecoder
from ui_popup_client import Client
from ui_popup_view import detail_lines, row_text


def draft_path(repo, session):
    import hashlib
    return Path(repo) / '.rig' / 'ui' / 'drafts' / (hashlib.sha256(session.encode()).hexdigest()[:24] + '.json')


def load_draft(path):
    try:
        value = json.loads(path.read_text())
        text = str(value['text'])[:2000]
        draft = Draft(text=text, cursor=len(text), submission_id=str(value['submission_id']))
        draft.action_id = value.get('action_id')
        return draft
    except (OSError, ValueError, KeyError):
        return Draft()


def save_draft(path, draft):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'text': draft.text, 'submission_id': draft.submission_id, 'action_id': getattr(draft, 'action_id', None)}, stream)
    os.replace(temp, path)


def paint(screen, repo, session, add=False):
    client = Client(repo)
    path = draft_path(repo, session)
    draft = load_draft(path)
    draft.active = add
    decoder = InputDecoder()
    snapshot, tab, selected, footer = {}, 'jobs', 0, ''
    details, confirm, next_refresh = None, None, 0
    actions = {}
    if getattr(draft, 'action_id', None):
        actions[draft.action_id] = 'enqueue'
        draft.submitting = True
    detail_scroll = 0
    curses.curs_set(0)
    if hasattr(curses, 'set_escdelay'): curses.set_escdelay(25)
    screen.keypad(True)
    screen.timeout(30)
    def line(y, text):
        h, w = screen.getmaxyx()
        if y < h - 1:
            try: screen.addnstr(y, 0, str(text).replace('\n', ' '), max(0, w - 1))
            except curses.error: pass
    bracketed = sys.stdout.isatty()
    if bracketed:
        sys.stdout.write('\x1b[?2004h')
        sys.stdout.flush()
    try:
        while True:
            while not client.results.empty():
                payload, value, error = client.results.get_nowait()
                if error:
                    footer = 'Request failed: ' + error
                    draft.submitting = False
                elif payload['op'] == 'snapshot':
                    snapshot = value.get('snapshot', value)
                elif payload['op'] == 'details' and value.get('error'):
                    footer = str(value['error'])
                elif payload['op'] == 'details':
                    details = value.get('job', value)
                    detail_scroll = 0
                    footer = 'PgUp/PgDn details; y allow / n deny; Esc back'
                else:
                    action_id = value.get('action_id')
                    kind = actions.get(action_id, payload['op'])
                    state = value.get('status')
                    if state == 'pending' and action_id:
                        actions[action_id] = kind
                        footer = 'Action accepted; waiting for completion'
                    else:
                        actions.pop(action_id, None)
                        footer = str(value.get('error') or value.get('result') or state or value)
                        if kind == 'enqueue':
                            draft.submitting = False
                            draft.action_id = None
                            if state == 'done':
                                draft = Draft()
                                save_draft(path, draft)
            if time.monotonic() >= next_refresh:
                if actions:
                    client.send({'op': 'action', 'action_id': next(iter(actions))})
                client.send({'op': 'snapshot', 'session': session})
                next_refresh = time.monotonic() + 0.5
            rows = snapshot.get(tab, [])
            selected = min(selected, max(0, len(rows) - 1))
            row = rows[selected] if rows else None
            screen.erase()
            line(0, f'Rig {Path(repo).name} | Tab Jobs/Queue/Notices | e add | Enter details | x stop | a acknowledge | Esc close')
            line(1, 'QUEUE %s | slots %s/%s | %s' % (len(snapshot.get('pending', [])), snapshot.get('slots', '?'), snapshot.get('cap', '?'), tab))
            h, w = screen.getmaxyx()
            if details is not None:
                lines = detail_lines(details, w - 2)
                for index, value in enumerate(lines[detail_scroll:detail_scroll + max(0, h - 6)]): line(index + 2, value)
            else:
                start = max(0, selected - max(0, h - 7))
                for index, item in enumerate(rows[start:start + max(0, h - 6)]):
                    line(index + 2, ('> ' if index + start == selected else '  ') + row_text(item, tab))
            if confirm: line(h - 4, 'Stop selected execution? Press Y to confirm; any other key cancels.')
            elif draft.active: line(h - 4, 'Queue (Enter save, Esc retain and close): ' + draft.text)
            line(h - 3, footer or snapshot.get('error') or (str(sum(bool(item.get('unread')) for item in snapshot.get('notices', []))) + ' unread notices; a acknowledges' if snapshot.get('notices') else ''))
            screen.refresh()
            try: incoming = decoder.feed(screen.get_wch())
            except curses.error: incoming = decoder.flush()
            for key, pasted in incoming:
                if key == curses.KEY_RESIZE: continue
                if pasted and not draft.active: continue
                if key == '\x1b' and not pasted:
                    if details is not None: details = None; continue
                    return
                if draft.active:
                    action = draft.key(key, pasted=pasted)
                    save_draft(path, draft)
                    if action == 'submit':
                        draft.action_id = draft.submission_id
                        save_draft(path, draft)
                        actions[draft.action_id] = 'enqueue'
                        draft.submitting = client.send({'op': 'enqueue', 'text': draft.text, 'submission_id': draft.submission_id, 'action_id': draft.submission_id})
                        footer = 'Submitting' if draft.submitting else 'Busy; Enter retries'
                    continue
                if confirm:
                    if key == 'Y':
                        footer = 'Stop requested' if client.send({'op': 'stop', 'target': confirm, 'action_id': uuid.uuid4().hex}) else 'Busy; retry stop'
                    confirm = None
                    continue
                if details is not None and key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                    detail_scroll = max(0, detail_scroll + (max(1, h - 6) if key == curses.KEY_NPAGE else -max(1, h - 6)))
                    continue
                if key == 'q': return
                if key == '\t': tab = {'jobs': 'pending', 'pending': 'notices', 'notices': 'jobs'}[tab]; selected = 0; details = None
                elif key in ('j', curses.KEY_DOWN): selected = min(selected + 1, max(0, len(rows) - 1)); details = None
                elif key in ('k', curses.KEY_UP): selected = max(0, selected - 1); details = None
                elif key == 'e': draft.active = True
                elif key == 'a': client.send({'op': 'ack', 'all': True, 'session': session})
                elif key in ('\n', '\r') and row and tab == 'jobs': client.send({'op': 'details', 'job_id': row['job_id']})
                elif key == 'x' and row and tab != 'notices':
                    if tab == 'pending': client.send({'op': 'cancel_queue', 'id': row['id'], 'expected_status': 'pending', 'action_id': uuid.uuid4().hex})
                    else: confirm = row.get('identity', row)
                elif key in ('y', 'n') and details is not None and row:
                    client.send({'op': 'reply', 'target': details.get('identity', row.get('identity', row)), 'ask': details.get('ask', details.get('pending_ask')), 'action_id': uuid.uuid4().hex, 'behavior': 'allow' if key == 'y' else 'deny'})
    finally:
        if bracketed:
            sys.stdout.write('\x1b[?2004l')
            sys.stdout.flush()
        save_draft(path, draft)


def main(repo, session, add=False):
    try:
        curses.wrapper(lambda screen: paint(screen, Path(repo), session, add))
    except KeyboardInterrupt:
        pass
    return 0
