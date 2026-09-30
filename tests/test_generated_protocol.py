"""Generation, mandatory safety coverage and isolated installed-reference checks."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import generate_protocol as generator
import rig_mcp
from protocol_test_support import read_guidance, skill_bundle


class GeneratedProtocol(unittest.TestCase):
    def test_checked_in_outputs_and_repeat_generation_are_deterministic(self):
        expected = generator.render()
        self.assertEqual(expected, generator.render())
        for path, text in expected.items():
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_bytes(), text.encode('utf-8'))
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/generate_protocol.py'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_detects_drift_without_writing_and_generation_preserves_user_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'docs').mkdir()
            shutil.copyfile(ROOT / generator.SOURCE, root / generator.SOURCE)
            source = 'User header — keep me\r\n\r\n' + (ROOT / 'AGENTS.md').read_text() + '\r\nUser footer Ω\r\n'
            (root / 'AGENTS.md').write_bytes(source.encode('utf-8'))
            self.assertEqual(generator.main(['--root', str(root)]), 0)
            self.assertEqual((root / 'AGENTS.md').read_bytes().decode('utf-8'), source)
            output = root / 'skills/delegate-harness/references/ownership.md'
            output.write_text(output.read_text() + '\nDRIFT\n')
            before = output.read_bytes()
            self.assertEqual(generator.main(['--root', str(root), '--check']), 1)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(generator.main(['--root', str(root)]), 0)
            self.assertEqual(generator.main(['--root', str(root), '--check']), 0)
            self.assertEqual((root / 'AGENTS.md').read_bytes().decode('utf-8'), source)

    def test_source_sections_reject_duplicates_missing_nested_and_unknown(self):
        source = (ROOT / generator.SOURCE).read_text()
        for malformed in (source.replace('rig:section routing', 'rig:section unknown'),
                          source.replace('rig:endsection', 'missing-endsection', 1),
                          source + '\n<!-- rig:section routing -->\nx\n<!-- rig:endsection -->\n',
                          source.replace('## Always enforce', '<!-- rig:section nested -->')):
            with self.subTest(source=malformed[-80:]):
                with self.assertRaises(ValueError):
                    generator.parse_source(malformed)

    def test_managed_replacement_refuses_ambiguous_markers(self):
        for malformed in (generator.START, generator.END, generator.END + generator.START,
                          generator.START + generator.START + generator.END):
            with self.subTest(text=malformed), self.assertRaises(ValueError):
                generator.replace_managed(malformed, 'replacement')
        original = 'No Rig here\n'
        self.assertEqual(generator.replace_managed(original, 'block\n'), 'block\n\n' + original)

    def test_bootstraps_fail_closed_and_topics_are_mandatory(self):
        agents = (ROOT / 'AGENTS.md').read_text()
        skill = (ROOT / 'skills/delegate-harness/SKILL.md').read_text()
        for text in (agents, skill):
            self.assertIn('[project] enabled=false', text)
            self.assertIn('missing or unreadable', text)
            self.assertIn('stop and report', text)
            self.assertIn('before any rig action', text.lower())
            self.assertIn('RIG_JOB_ID', text)
            self.assertIn('rig_job_inbox', text)
            self.assertIn('never spawn or message children', text.lower())
            self.assertIn('parent_writes', text)
            self.assertIn('Generic computer-use requests do not select Rig', text)
        refs = set(re.findall(r'\]\(references/([a-z-]+)\.md\)', skill))
        self.assertEqual(refs, set(generator.TOPICS))
        self.assertIn('mandatory before the named action', skill)
        self.assertIn('Read all references that apply, before the first action', skill)
        # These restriction summaries cannot disappear into optional references.
        for phrase in ('never authorizes ownership', 'File AND resource disjointness',
                       'stop-unconfirmed', 'completed-unverified', 'Changed content invalidates acceptance',
                       'different known actual model provider', 'No implicit setup or enabling'):
            self.assertIn(phrase, skill)

    def test_action_prerequisites_cover_full_job_workflow_queue_surfaces(self):
        skill = (ROOT / 'skills/delegate-harness/SKILL.md').read_text()
        rules = {
            'Every job operation': (('declaring requirements', 'running checks/verification',
                                     'acceptance', 'ownership recovery'), ('ownership', 'launching')),
            'Every workflow operation': (('show/report', 'resolve/approve', 'coordination'),
                                         ('workflows', 'ownership', 'waiting')),
            'Every queue operation': (('add/list/claim/unclaim/spawned/cancel', 'launch and drain'),
                                      ('queue', 'ownership')),
            'Waiting, answering ASK': (('Stop/cancel', 'continuing/retrying'), ('waiting', 'ownership')),
        }
        for trigger, (actions, topics) in rules.items():
            with self.subTest(trigger=trigger):
                line = next(line for line in skill.splitlines() if line.startswith('- ' + trigger))
                for action in actions:
                    self.assertIn(action, line)
                for topic in topics:
                    self.assertIn(f'(references/{topic}.md)', line)

    def test_invariant_migration_map_matches_source_and_installed_topics(self):
        sections = generator.parse_source((ROOT / generator.SOURCE).read_text())
        manifest = json.loads((ROOT / 'tests/fixtures/protocol-invariants.json').read_text())
        self.assertGreaterEqual(len(manifest), 18)
        for name, rule in manifest.items():
            with self.subTest(invariant=name):
                self.assertTrue(rule['previous'])
                section = rule['section']
                self.assertIn(section, sections)
                actual = ((ROOT / f'skills/delegate-harness/references/{section}.md').read_text()
                          if section in generator.TOPICS else
                          (ROOT / 'skills/delegate-harness/SKILL.md').read_text())
                for phrase in rule['requires']:
                    self.assertIn(phrase, sections[section])
                    self.assertIn(phrase, actual)

    def test_every_original_skill_paragraph_has_literal_migration_coverage(self):
        # Frozen migration evidence, not another editable protocol source. Every
        # baseline paragraph is covered; three reviewed moves/expansions split
        # literal fragments instead of weakening or dropping their restrictions.
        manifest = json.loads((ROOT / 'tests/fixtures/protocol-migration.json').read_text())
        sections = generator.parse_source((ROOT / generator.SOURCE).read_text())
        normalize = lambda value: re.sub(r'\s+', ' ', value).strip()
        self.assertEqual([entry['paragraph'] for entry in manifest['paragraphs']],
                         list(range(manifest['paragraph_count'])))
        self.assertEqual(manifest['paragraph_count'], 96)
        for entry in manifest['paragraphs']:
            with self.subTest(paragraph=entry['paragraph']):
                self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
                self.assertTrue(entry['coverage'])
                for fragment in entry['coverage']:
                    self.assertIn(fragment['text'], normalize(sections[fragment['section']]))

    def test_all_generated_mcp_tool_references_exist(self):
        known = {tool['name'] for tool in [*rig_mcp.TOOLS, *rig_mcp.CHILD_TOOLS]}
        text = skill_bundle(ROOT / 'skills/delegate-harness/SKILL.md')
        mentioned = set(re.findall(r'\brig_[a-z][a-z_]+\b', text))
        for prefix in {name for name in mentioned if name.endswith('_')}:
            self.assertTrue(any(name.startswith(prefix) for name in known), prefix)
        mentioned = {name for name in mentioned if not name.endswith('_')}
        self.assertFalse(mentioned - known, mentioned - known)

    def test_diagnostic_guidance_matches_available_parent_only_doctor(self):
        parent = {tool['name']: tool for tool in rig_mcp.TOOLS}
        self.assertIn('rig_doctor', parent)
        self.assertNotIn('rig_doctor', {tool['name'] for tool in rig_mcp.CHILD_TOOLS})
        fields = parent['rig_doctor']['inputSchema']['properties']
        self.assertEqual(set(fields), {'repo', 'task', 'parent', 'model', 'research_sources', 'smoke'})
        self.assertEqual(set(fields['task']['enum']), {'coding', 'research', 'browser', 'computer-use'})
        self.assertFalse(fields['smoke']['default'])
        source = (ROOT / 'skills/delegate-harness/references/orchestration.md').read_text()
        self.assertIn('never certify authentication', source)
        self.assertIn('never providers, browsers, installers or user-project writes', source)
        self.assertIn('does not activate it', source)

    def test_discovery_byte_budget_is_measured_not_a_token_claim(self):
        # Before the migration (03db845), these were 26,215 and 45,660 UTF-8 bytes.
        agents = (ROOT / 'AGENTS.md').read_bytes()
        skill = (ROOT / 'skills/delegate-harness/SKILL.md').read_bytes()
        self.assertLess(len(agents), 3000)
        self.assertLess(len(skill), 6500)
        self.assertLess(len(agents) + len(skill), 26215 + 45660)
        self.assertNotIn(b'<!-- rig:start -->\n', (ROOT / 'bin/rig').read_bytes())
        self.assertIn('agents-protocol.md', (ROOT / 'bin/rig').read_text())


class InstalledProtocol(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo, self.home = self.base / 'repo', self.base / 'home'
        self.repo.mkdir(); self.home.mkdir(); (self.repo / '.git').mkdir()
        self.env = {**os.environ, 'HOME': str(self.home), 'RIG_HOME': str(self.home / '.rig'),
                    'RIG_SRC': str(ROOT), 'RIG_SKIP_TMUX_INSTALL': '1', 'RIG_SKIP_UPDATE_CHECK': '1',
                    'RIG_SKIP_MODEL_CATALOG': '1'}

    def run_rig(self, *args):
        return subprocess.run([str(ROOT / 'bin/rig'), *args], cwd=self.repo, env=self.env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def test_init_copies_every_reference_preserves_user_content_and_is_idempotent(self):
        agents = self.repo / 'AGENTS.md'
        unrelated = '# User rules\nKeep naïve paths and custom instructions.\n'
        agents.write_text(unrelated)
        for _ in range(2):
            result = self.run_rig('init')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(unrelated, agents.read_text())
            self.assertEqual(agents.read_text().count(generator.START), 1)
            installed = self.repo / '.agents/skills/delegate-harness/SKILL.md'
            self.assertEqual(installed.read_bytes(), (ROOT / 'skills/delegate-harness/SKILL.md').read_bytes())
            self.assertEqual(skill_bundle(installed), skill_bundle(ROOT / 'skills/delegate-harness/SKILL.md'))
            current = {p.relative_to(self.repo): p.read_bytes() for p in [agents, *installed.parent.rglob('*.md')]}
            if _:
                self.assertEqual(current, before)
            before = current

    def test_disabled_project_keeps_agents_and_disabled_state(self):
        (self.repo / '.rig').mkdir()
        (self.repo / '.rig/harness.toml').write_text('[project]\nenabled = false\n')
        agents = self.repo / 'AGENTS.md'
        original = b'User only\n<!-- rig:start -->\nold managed text\n<!-- rig:end -->\nTail\n'
        agents.write_bytes(original)
        for _ in range(2):
            result = self.run_rig('init')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(agents.read_bytes(), original)
            self.assertIn('enabled = false', (self.repo / '.rig/harness.toml').read_text())

    def test_global_setup_copies_reference_closure_and_template_in_temporary_home(self):
        result = self.run_rig('setup', '--no-cua-driver', '--no-browser-skill', '--no-mimo')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        kit = self.home / '.rig'
        self.assertEqual((kit / 'templates/agents-protocol.md').read_bytes(),
                         (ROOT / 'templates/agents-protocol.md').read_bytes())
        for directory in ('.agents/skills', '.grok/skills', '.codex/skills', '.config/opencode/skill',
                          '.omp/agent/skills', '.pi/agent/skills', '.gemini/antigravity-cli/skills'):
            self.assertEqual(skill_bundle(self.home / directory / 'delegate-harness/SKILL.md'),
                             skill_bundle(ROOT / 'skills/delegate-harness/SKILL.md'))
        result = self.run_rig('init')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_guidance(self.repo / 'AGENTS.md'), read_guidance(ROOT / 'AGENTS.md'))

    def test_missing_template_fails_without_overwriting_user_agents(self):
        kit = self.home / '.rig'
        shutil.copytree(ROOT / 'templates', kit / 'templates')
        (kit / 'templates/agents-protocol.md').unlink()
        agents = self.repo / 'AGENTS.md'
        original = b'User instructions must survive an incomplete install.\n'
        agents.write_bytes(original)
        result = self.run_rig('init')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('generated protocol template missing', result.stderr)
        self.assertEqual(agents.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
