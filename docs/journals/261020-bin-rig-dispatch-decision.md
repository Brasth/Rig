# Decision: `bin/rig` stays a bash dispatcher (Phase 4.5)

Decision: no port to Go now. `rig-shell` keeps the board and does not take over the `rig` name (D4 stands).

## Evidence, measured 2026-10-20

- `bin/rig` is 1,682 lines of bash and about twenty top-level commands. It parses almost nothing itself: it finds the runtime, applies the project guard and hands the arguments to one of 117 Python scripts, where the work is.
- `rig help` runs in about 20 ms, the same as `python3 -c pass` (19 ms) on this machine. The dispatcher is not where time goes.
- Every adapter, hook, skill and document calls `rig`; the install, update and rollback path (`ui_install.py`) runs `bin/rig` itself. A Go rewrite would touch all of that for no behaviour a user could see.
- A Go dispatcher would still call the same Python scripts, so it would add a second implementation of argument handling for every subcommand and a build step (Go) to the one binary every user needs, which today needs only bash and Python 3.

## What would change this

Revisit if any of these becomes true: the shell needs to call `rig` often enough that process start-up shows (it talks to the local API directly today); bash portability breaks a supported platform; or the Python scripts are folded into Go, so the dispatcher stops being a thin layer.

## Consequence

`rig shell` stays the way to start the Go board, and `rig web` (Phase 4.3) is a Python subcommand like the rest. No code changes for this item.
