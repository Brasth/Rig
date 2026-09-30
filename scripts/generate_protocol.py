#!/usr/bin/env python3
"""Deterministically render agent guidance from one sectioned Markdown source."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('docs/agent-protocol.md')
TOPICS = ('orchestration', 'routing', 'ownership', 'waiting', 'workflows',
          'queue', 'computer-use', 'launching', 'providers', 'maintenance')
SECTIONS = ('activation', 'agents', 'frontmatter', 'delegate', *TOPICS)
NOTICE = '<!-- Generated from docs/agent-protocol.md; run scripts/generate_protocol.py. Do not edit. -->\n'
START, END = '<!-- rig:start -->', '<!-- rig:end -->'


def parse_source(text: str) -> dict[str, str]:
    pattern = re.compile(r'^<!-- rig:section ([a-z-]+) -->\n(.*?)^<!-- rig:endsection -->$', re.M | re.S)
    matches = list(pattern.finditer(text))
    names = [m[1] for m in matches]
    if tuple(names) != SECTIONS:
        raise ValueError(f'protocol sections must occur exactly once in order: {SECTIONS}; got {names}')
    # Refuse nested/unmatched markers rather than quietly dropping source text.
    remainder = pattern.sub('', text)
    if '<!-- rig:section' in remainder or '<!-- rig:endsection' in remainder:
        raise ValueError('unmatched protocol section marker')
    result = {m[1]: m[2].strip() + '\n' for m in matches}
    if any('<!-- rig:section' in value or '<!-- rig:endsection' in value for value in result.values()):
        raise ValueError('nested protocol section marker')
    return result


def replace_managed(text: str, block: str) -> str:
    """Replace only the marked bytes, preserving all user prefix/suffix bytes."""
    if START not in text and END not in text:
        return block + ('\n' if text else '') + text
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError('expected exactly one complete managed AGENTS block')
    start, end = text.index(START), text.index(END)
    if end < start:
        raise ValueError('reversed managed AGENTS markers')
    return text[:start] + block.rstrip('\n') + text[end + len(END):]


def render(root: Path = ROOT) -> dict[Path, str]:
    sections = parse_source((root / SOURCE).read_text(encoding='utf-8'))
    block = START + '\n' + NOTICE + sections['activation'] + '\n' + sections['agents'] + END + '\n'
    skill = ('---\n' + sections['frontmatter'] + '---\n\n' + NOTICE +
             sections['activation'] + '\n' + sections['delegate'])
    outputs = {Path('templates/agents-protocol.md'): block,
               Path('skills/delegate-harness/SKILL.md'): skill}
    for topic in TOPICS:
        outputs[Path(f'skills/delegate-harness/references/{topic}.md')] = NOTICE + sections[topic]
    agents = root / 'AGENTS.md'
    outputs[Path('AGENTS.md')] = replace_managed(agents.read_bytes().decode('utf-8') if agents.exists() else '', block)
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail on generated drift without writing')
    parser.add_argument('--root', type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        outputs = render(args.root)
    except (OSError, ValueError) as exc:
        print(f'protocol generation: {exc}', file=sys.stderr)
        return 2
    changed = []
    for path, expected in outputs.items():
        target = args.root / path
        if target.exists() and target.read_bytes() == expected.encode('utf-8'):
            continue
        changed.append(str(path))
        if not args.check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(expected.encode('utf-8'))
    # Stale generated topics would hide removed rules after installation.
    refs = args.root / 'skills/delegate-harness/references'
    stale = [str(p.relative_to(args.root)) for p in sorted(refs.glob('*.md'))
             if p.relative_to(args.root) not in outputs and p.read_text(encoding='utf-8').startswith(NOTICE)]
    if stale:
        print('stale generated references (remove explicitly): ' + ', '.join(stale), file=sys.stderr)
        return 1
    if args.check and changed:
        print('generated protocol drift: ' + ', '.join(changed), file=sys.stderr)
        return 1
    print(('protocol generation current' if args.check else 'generated protocol: ' + (', '.join(changed) or 'no changes')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
