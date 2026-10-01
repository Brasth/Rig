#!/usr/bin/env python3
"""Guided `rig update`: pin official main once, preview, then ask.

Never mutates without TTY consent or --yes. `--latest` resolves official main
to one full commit and hands it to the existing pinned controller. Legacy
installations get a runnable `--migrate` bootstrap (see legacy_migration.py).
Classic --revision/--rollback/--recover/--status behavior is unchanged.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys

import legacy_migration as migration
import runtime_update
import update_gate
from runtime_update import SHA, UpdateError

INSTALL = 'curl -fsSL https://raw.githubusercontent.com/Brasth/Rig/main/install.sh | bash'


def interactive_console():
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_tty(question):
    try:
        return input(question + ' [y/N] ').strip().lower() in {'y', 'yes'}
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def parse(argv):
    parser = argparse.ArgumentParser(prog='rig update', description=__doc__.split('\n', 1)[0])
    parser.add_argument('--latest', action='store_true', help='resolve official main to one full commit')
    parser.add_argument('--revision', metavar='FULL_COMMIT_SHA')
    parser.add_argument('--migrate', action='store_true', help='bootstrap a legacy install into a new root')
    parser.add_argument('--project', metavar='PATH', help='with --migrate: existing enabled project to init')
    parser.add_argument('--new-root', metavar='PATH', help='with --migrate: absent new runtime root')
    parser.add_argument('--restore-migration', metavar='DIR', help='restore integrations from a migration backup')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--recover', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--yes', action='store_true', help='consent without a TTY prompt')
    parser.add_argument('--force', action='store_true', help='with --restore-migration: overwrite listed unverified paths')
    args = parser.parse_args(argv)
    classic = [args.rollback, args.recover, args.status, bool(args.revision and not args.migrate)]
    guided = [bool(args.latest and not args.migrate), args.migrate, bool(args.restore_migration)]
    if sum(classic) + sum(guided) > 1:
        parser.error('choose one update mode')
    if args.migrate and bool(args.latest) == bool(args.revision):
        parser.error('--migrate needs exactly one of --latest or --revision FULL_COMMIT_SHA')
    if (args.project or args.new_root) and not args.migrate:
        parser.error('--project and --new-root apply only to --migrate')
    if args.force and not args.restore_migration:
        parser.error('--force applies only to --restore-migration')
    if args.yes and any(classic):
        parser.error('--yes applies to --latest, --migrate and --restore-migration')
    if (args.yes or args.dry_run) and not any(classic) and not any(guided):
        parser.error('--yes/--dry-run need --latest, --migrate or --restore-migration')
    args.classic = any(classic)
    return args


def resolve_latest():
    """One `git ls-remote` of official main -> exactly one full commit SHA."""
    try:
        output = runtime_update.git('ls-remote', runtime_update.OFFICIAL_REPO, 'refs/heads/main').decode()
    except (OSError, subprocess.SubprocessError) as error:
        raise UpdateError('Could not resolve official main (network/Git failure); nothing changed. '
                          'Retry, or pass --revision FULL_COMMIT_SHA') from error
    rows = [line.split('\t') for line in output.splitlines() if line.strip()]
    shas = [row[0] for row in rows if len(row) == 2 and row[1] == 'refs/heads/main']
    if len(shas) != 1 or len(rows) != 1 or not SHA.fullmatch(shas[0]):
        raise UpdateError('Official main did not resolve to exactly one full 40-character commit; nothing changed')
    return shas[0]


def classify(root):
    if update_gate.pending(root):
        return 'pending'
    if (root / 'runtime-state.json').exists():
        return 'versioned'
    if (root / 'bin/rig').exists() or (root / 'scripts').is_dir():
        return 'legacy'
    return 'missing'


def enabled_project(start):
    for path in (start, *start.parents):
        if (path / '.rig/harness.toml').is_file():
            try:
                migration.check_project(path)
                return path
            except UpdateError:
                return None
    return None


def command(*args):
    return shlex.join([str(migration.cli_path()), 'update', *args])


def legacy_hint(root):
    return (f'Legacy/unversioned installation at {root} (no runtime-state.json): safe updates need a fresh,\n'
            'pinned runtime root. The legacy root stays intact. Preview the migration (changes nothing):\n'
            f'  {command("--migrate", "--latest", "--dry-run")} [--project /path/to/enabled/project]\n'
            'Then run it without --dry-run (a TTY asks for consent; non-interactive needs --yes).\n'
            'An older installed `rig` lacks these flags; use this checkout path exactly as shown.')


def instructions(root, kind):
    if kind == 'pending':
        return 'Pending runtime update: rig update --status, then rig update --recover'
    if kind == 'missing':
        return f'No Rig installation at {root}. Install: {INSTALL}'
    if kind == 'legacy':
        return legacy_hint(root)
    state, _ = runtime_update.baseline(root)
    return (f'Installed commit {state["commit"]} at {root}. Without a TTY, `rig update` changes nothing:\n'
            '  rig update --latest --dry-run            # resolve official main to one full SHA; preview\n'
            '  rig update --latest --yes                # apply that resolved commit\n'
            '  rig update --revision FULL_COMMIT_SHA    # exact pinned commit (automation)\n'
            '  rig update --rollback --dry-run | --status | --recover')


def latest(root, *, dry_run, assume_yes, interactive, ask, out):
    kind = classify(root)
    if kind != 'versioned':
        if kind == 'legacy' and interactive and not dry_run and not assume_yes \
                and ask('This is a legacy install. Start the guided migration to a new pinned root instead?'):
            return guided_migration(root, interactive=interactive, ask=ask, out=out)
        out(instructions(root, kind))
        return 2 if kind == 'legacy' else 1
    if not dry_run and not assume_yes and not interactive:
        out('Non-interactive --latest changes nothing without consent. Preview: rig update --latest --dry-run; '
            'apply: rig update --latest --yes (or rig update --revision FULL_COMMIT_SHA)')
        return 2
    state, _ = runtime_update.baseline(root)
    revision = resolve_latest()
    out(f'Official main resolves to {revision}')
    if revision == state['commit']:
        out('Already at official main; no files changed')
        return 0
    with runtime_update.fetched_source(revision) as source:
        out(runtime_update.execute(root, source=source, revision=revision, dry_run=True))
        if dry_run:
            out(f'Apply exactly this commit: rig update --revision {revision}')
            return 0
        if not assume_yes and not ask(f'Apply update {state["commit"]} -> {revision}?'):
            out('Cancelled; no files changed')
            return 1
        out(runtime_update.execute(root, source=source, revision=revision))
    return 0


def guided_migration(root, *, interactive, ask, out):
    out(legacy_hint(root))
    project = enabled_project(Path.cwd())
    if project and not ask(f'Also initialize this existing enabled project with the new runtime: {project}?'):
        project = None
    if not ask('Resolve official main and preview the migration?'):
        out('Cancelled; no files changed')
        return 1
    revision = resolve_latest()
    out(f'Official main resolves to {revision}')
    return migration.migrate(revision, old_root=root, project=project, interactive=interactive, ask=ask, out=out)


def guided(root, *, interactive, ask, out):
    kind = classify(root)
    if not interactive or kind in ('pending', 'missing'):
        out(instructions(root, kind))
        return 2 if kind in ('versioned', 'legacy') else 1
    if kind == 'legacy':
        return guided_migration(root, interactive=interactive, ask=ask, out=out)
    state, _ = runtime_update.baseline(root)
    out(f'Installed commit {state["commit"]} at {root}')
    if not ask('Check official main and preview the update?'):
        out('Cancelled; no files changed')
        return 1
    return latest(root, dry_run=False, assume_yes=False, interactive=interactive, ask=ask, out=out)


def main(argv=None, *, interactive=None, ask=None, out=print):
    argv = list(sys.argv[1:] if argv is None else argv)
    interactive = interactive_console() if interactive is None else interactive
    ask = ask or ask_tty
    if any(os.environ.get(k) for k in ('RIG_LIVE', 'RIG_JOB_ID', 'RIG_JOB_DIR')):
        print('rig update: refuse inside a worker', file=sys.stderr)
        return 1
    args = parse(argv)
    if args.classic:
        return runtime_update.main(argv)
    root = update_gate.root_path()
    options = {'interactive': interactive, 'ask': ask, 'out': out}
    try:
        if args.restore_migration:
            return migration.restore(args.restore_migration, dry_run=args.dry_run, assume_yes=args.yes,
                                     force=args.force, **options)
        if args.migrate:
            if args.revision and not SHA.fullmatch(args.revision):
                raise UpdateError('--revision requires a full 40-character commit SHA')
            if not args.dry_run and not args.yes and not interactive:
                base = [a for a in argv if a not in ('--dry-run', '--yes')]
                out('Non-interactive migration changes nothing without consent.\n'
                    f'  preview: {command(*base, "--dry-run")}\n'
                    f'  apply after review: {command(*base, "--yes")}')
                return 2
            revision = args.revision or resolve_latest()
            return migration.migrate(revision, old_root=root, new_root=args.new_root, project=args.project,
                                     dry_run=args.dry_run, assume_yes=args.yes, **options)
        if args.latest:
            return latest(root, dry_run=args.dry_run, assume_yes=args.yes, **options)
        return guided(root, **options)
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, subprocess.SubprocessError) as error:
        print('rig update: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
