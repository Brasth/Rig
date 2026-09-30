"""In-memory routing form. Navigation and cancel never touch project files."""
from __future__ import annotations

import copy
import curses
import textwrap
import uuid

import routing_config as config
import routing_domains as domains
import routing_settings as settings
from tui_view import _add, _viewport, _cell_width, _text_width

FIELDS = (
    ("domain", "Domain", domains.DOMAINS),
    ("profiles", "Preferred IDs (comma order)", ()),
    ("fallback", "Fallback", domains.FALLBACKS),
    ("case", "Task", ()),
    ("role", "Role", ("implement", "explore", "mini", "bulk", "hard", "review", "verify", "stay")),
    ("complexity", "Complexity", ("", "low", "medium", "high")),
    ("risk", "Risk", ("", "low", "medium", "high")),
    ("uncertainty", "Uncertainty", ("", "low", "medium", "high")),
    ("exclude", "Excluded workers", ()),
    ("sources", "Research files (comma separated)", ()),
    ("review_mode", "Review mode", ("standalone", "independent")),
    ("writer_job_id", "Writer job for independent review", ()),
)


def _csv(value):
    return [part.strip() for part in value.split(",") if part.strip()]


class RoutingPanel:
    def __init__(self, document):
        self.document = document
        self.token = uuid.uuid4().hex
        self.values = {name: choices[0] if choices else "" for name, _label, choices in FIELDS}
        self.edits = {}
        self.selected = self.offset = self.output_offset = self.cursor = 0
        self.pending = ""
        self.message = "Edit domain preferences, then F5 previews; only F2 saves"
        self.output = []
        self._load_domain()

    def _load_domain(self):
        name = self.values["domain"]
        policy = self.edits.get(name, (self.document.raw or {}).get("domains", {}).get(name)) or {}
        self.values["profiles"] = ", ".join(policy.get("preferred_profiles", []))
        self.values["fallback"] = policy.get("fallback", "parent" if name in domains.PARENT_DOMAINS else "scored")
        self._help()

    def _help(self):
        cfg = config.validate_candidate(self.document.raw, harness=self.document.harness)
        name = self.values["domain"]
        mode = "parent-only boundary" if name in domains.PARENT_DOMAINS else "ordered eligible profiles"
        self.output = [f"{name}: {mode}", "Known profile IDs (editing never enables a worker):"]
        self.output += [f"{p.id}  {p.worker}  {p.selector}  {p.effort or '-'}" for p in cfg.profiles.values()]
        self.output_offset = 0

    def candidate(self):
        return settings.domain_candidate(self.document.raw, self.edits)

    def preview_args(self):
        return {key: self.values[key] for key in ("case", "role", "complexity", "risk", "uncertainty", "exclude", "review_mode", "writer_job_id")} | {
            "task_domain": self.values["domain"], "research_sources": _csv(self.values["sources"]) or None,
        }

    def _changed(self, name):
        if name in {"profiles", "fallback"}:
            self.edits[self.values["domain"]] = {"preferred_profiles": _csv(self.values["profiles"]),
                                                  "fallback": self.values["fallback"]}
        self.message = "Draft changed; F5 previews current inputs, F2 saves preferences"
        self.output = ["Preview out of date; F5 to recalculate"]
        self.output_offset = 0

    def key(self, key, *, pasted=False):
        if self.pending:
            if key == "\x1b" and self.pending != "save":
                return "cancel"
            self.message = "Saving; wait for the result" if self.pending == "save" else "Preview running; Esc cancels the form"
            return None
        if not pasted:
            if key == "\x1b":
                return "cancel"
            if key in ("\x13", curses.KEY_F2):
                return "save"
            if key in ("\x10", curses.KEY_F5):
                return "preview"
            if key == "\x12":
                self.edits[self.values["domain"]] = None
                self._load_domain()
                self.message = "Domain override removed in draft; F2 saves, Esc cancels"
                return None
            if key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                self.output_offset = max(0, self.output_offset + (8 if key == curses.KEY_NPAGE else -8))
                return None
            if key in ("\t", curses.KEY_BTAB, curses.KEY_DOWN, curses.KEY_UP):
                step = -1 if key in (curses.KEY_BTAB, curses.KEY_UP) else 1
                self.selected = (self.selected + step) % len(FIELDS)
                self.cursor = len(self.values[FIELDS[self.selected][0]])
                return None
        name, _label, choices = FIELDS[self.selected]
        if name in {"profiles", "fallback"} and self.values["domain"] in domains.PARENT_DOMAINS:
            self.message = "This domain is parent-only: no child profiles, fallback=parent"
            return None
        value = self.values[name]
        if choices:
            if not pasted and key in (" ", "\n", "\r", curses.KEY_ENTER, curses.KEY_LEFT, curses.KEY_RIGHT):
                step = -1 if key == curses.KEY_LEFT else 1
                self.values[name] = choices[(choices.index(value) + step) % len(choices)]
                if name == "domain":
                    self._load_domain()
                else:
                    self._changed(name)
            return None
        if not pasted and key in (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_HOME, curses.KEY_END):
            self.cursor = (0 if key == curses.KEY_HOME else len(value) if key == curses.KEY_END else
                           max(0, min(len(value), self.cursor + (-1 if key == curses.KEY_LEFT else 1))))
            return None
        if not pasted and key in ("\b", "\x7f", curses.KEY_BACKSPACE) and self.cursor:
            self.values[name] = value[:self.cursor - 1] + value[self.cursor:]
            self.cursor -= 1
        elif not pasted and key == curses.KEY_DC:
            self.values[name] = value[:self.cursor] + value[self.cursor + 1:]
        elif isinstance(key, str) and (key.isprintable() or (pasted and key.isspace())) and len(value) < 2000:
            inserted = (" " if key.isspace() else key)[:2000 - len(value)]
            self.values[name] = value[:self.cursor] + inserted + value[self.cursor:]
            self.cursor += len(inserted)
        else:
            return None
        self._changed(name)
        return None

    def accept(self, action, result):
        self.pending = ""
        if result.error:
            self.message = f"{action.title()} failed: {result.error}; draft retained"
            return
        if action == "preview":
            self.output = settings.preview_lines(result.value)
            self.output_offset = 0
            self.message = "Preview only; cached catalogs, no refresh or provider invocation"
        else:
            self.document = settings.SettingsDocument(copy.deepcopy(self.candidate()), result.value["fingerprint"], self.document.harness)
            self.edits = {}
            self.message = f"Routing preferences {result.value['status']}; Esc closes"


def render_panel(stdscr, panel):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    _add(stdscr, 0, 0, "Routing Settings / task preview", curses.A_REVERSE, width=w)
    if h < 8 or w < 40:
        _add(stdscr, 1, 0, "terminal too small; Esc cancels", width=w)
    else:
        count = min(len(FIELDS), max(2, (h - 5) // 2))
        panel.offset, stop = _viewport(panel.selected, len(FIELDS), count, panel.offset)
        for i in range(panel.offset, stop):
            name, label, choices = FIELDS[i]
            value = panel.values[name] or ("role default" if choices else "(empty)")
            if i == panel.selected and not choices:
                raw = panel.values[name]
                room = max(1, w - _text_width(label) - 6)
                start, used = panel.cursor, 0
                while start and used + _cell_width(raw[start - 1]) < room:
                    start -= 1
                    used += _cell_width(raw[start])
                value = ("…" if start else "") + raw[start:panel.cursor] + "│" + raw[panel.cursor:]
            text = f"{'>' if i == panel.selected else ' '} {label}: {value}"
            _add(stdscr, i - panel.offset + 1, 0, text,
                 curses.A_REVERSE if i == panel.selected else curses.A_NORMAL, width=w)
        y = count + 1
        _add(stdscr, y, 0, "Tab fields · Enter/←/→ choices · PgUp/PgDn results · Ctrl-R reset domain", width=w)
        wrapped = [piece for line in panel.output for piece in (textwrap.wrap(str(line), max(1, w - 2)) or [""])]
        available = max(0, h - y - 4)
        panel.output_offset = min(panel.output_offset, max(0, len(wrapped) - available))
        for n, line in enumerate(wrapped[panel.output_offset:panel.output_offset + available]):
            _add(stdscr, y + 1 + n, 1, line, width=max(0, w - 2))
        _add(stdscr, h - 2, 0, panel.message, width=w)
    _add(stdscr, h - 1, 0, "F5 preview · F2 save preferences · Esc cancel/close", curses.A_REVERSE, width=w)
    stdscr.refresh()
