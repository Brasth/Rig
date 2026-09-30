"""Read generated bootstraps with their mandatory reference closure for policy tests.

Assertions against this bundle check preserved behavior, not duplicated prose in
several discovery surfaces. test_generated_protocol separately checks the actual
small bootstrap, prerequisite links, source mapping and installed file closure.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def skill_bundle(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    refs = list(dict.fromkeys(re.findall(r'\]\((references/[a-z-]+\.md)\)', text)))
    if not refs:
        raise AssertionError(f'no required references in delegation bootstrap: {path}')
    for ref in refs:
        target = path.parent / ref
        if not target.is_file():
            raise AssertionError(f'missing mandatory reference: {target}')
        text += '\n' + target.read_text(encoding='utf-8')
    return text


def read_guidance(path):
    path = Path(path)
    if path.name == 'SKILL.md' and path.parent.name == 'delegate-harness':
        return skill_bundle(path)
    if path == ROOT / 'bin/rig':
        path = ROOT / 'templates/agents-protocol.md'
    text = path.read_text(encoding='utf-8')
    if path.name in ('AGENTS.md', 'agents-protocol.md'):
        skill = (ROOT / 'skills/delegate-harness/SKILL.md' if path in
                 (ROOT / 'AGENTS.md', ROOT / 'templates/agents-protocol.md') else
                 path.parent / '.agents/skills/delegate-harness/SKILL.md')
        text = text.replace('<!-- rig:end -->', '\n' + skill_bundle(skill) + '<!-- rig:end -->')
    return text
