# Rig terminal board

`rig tui` uses the supplied terminal wireframes as its visual reference. The
board keeps the existing Jobs, Queue, Workflows, and Settings tabs and keyboard
controls. It does not change job execution, acceptance, routing, or ownership.

## Layout and reading order

At 100 columns or wider, the list uses 52% of the terminal width, rounded to a
whole cell and clamped to 52–72 columns. A separator and a padded detail pane
occupy the remaining columns. At 40–99 columns, Enter opens full-screen details.
Below 40 columns or 8 rows, a resize notice replaces the board; q still quits.

The header shows project, attention count, Running count, and occupied slots.
Jobs and Workflows tab counts use `!n` for attention; Queue uses `n pending` at
80 columns or wider. Checking and Reserved do not count as Running.

Jobs are grouped into needs attention, active, and finished when space permits;
permission requests come first and Unverified entries lead finished history.
Below 50 list columns or 12 terminal rows, headings disappear but order remains.
Jobs and Queue use two-line entries at 12 rows or taller. Compact rows retain
task titles; activity and outcome remain available in details.

The selected entry has a `>` marker and a filled background across both lines.
Focus uses a heavy heading rule; state has a glyph and word. The viewport budgets
headings and overflow indicators and never splits the selected entry. IDs are
secondary: list rows show them only at 70 columns or wider and when the task fits.
Full-screen details retain the full ID and show a breadcrumb, read-only label,
line position, and scrolling hints.

Permission details show the actual recorded request before worker/model,
verification, held scope, task, and metadata. Only real ASK rows offer y/n.
Read-only details direct users back to the board to answer. Refresh failures
retain the last snapshot, mark it stale, and keep the retry hint visible.

The enqueue editor uses the right pane on wide terminals and the main region
on narrow ones. The existing task field wraps while preserving the draft and
cursor through resizing. Enter submits, arrows edit, and Esc discards. Domain
routing retains its existing fields: Tab moves fields, Enter/arrows cycle
choices, F5 previews, and F2 saves preferences. Editing never enables a worker.

## Terminal compatibility

Colors use a 256-color palette where available, standard terminal colors
otherwise, and the terminal's default background where supported. `NO_COLOR`
(including an empty value) selects monochrome. Selection, focus, and every state
remain identifiable without color. Terminals without color support do not need
color initialization.

Non-UTF locales use ASCII glyphs automatically. Set `RIG_TUI_ASCII=1` to force
ASCII output, or `RIG_TUI_CJK=1` for ASCII UI glyphs while retaining Unicode task
text on terminals that render ambiguous-width glyphs as two cells.

## Scope relative to the wireframes

Sample data does not define new product capabilities. The implementation keeps
current settings and routing semantics, numeric queue priorities, and actual
recorded data. It does not add project selection, priority/worker editor fields,
live routing while typing, display settings, rule add/delete/reorder, workflow
node jumps, or extra confirmation steps. Tab/Shift-Tab still switch views from
full-screen details, and Esc still dismisses action errors; refresh errors remain
until a successful refresh. No progress percentages, ETA, or inferred model
identity are introduced. Unverified and Verified remain distinct, and a stop
request remains distinct from confirmed termination.
