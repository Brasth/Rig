#!/usr/bin/env python3
"""Task domains are preferences inside existing authority/capability gates."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import route
import routing_config as config
import routing_domains as domains
import routing_evidence as evidence
import routing_policy as policy


class DomainRouting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        (self.repo / '.rig').mkdir()
        (self.repo / 'source.md').write_text('Locally supplied source material\n')

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, values, **extra):
        (self.repo / '.rig/routing.json').write_text(json.dumps({'schema_version': 4, 'domains': values, **extra}))

    def pick(self, task_domain='', role='implement', case='bounded task', effective=None, **kwargs):
        return route.pick('codex', ['grok', 'claude'] if effective is None else effective, role, case,
                          repo=self.repo, policy_mode='smart', catalogs=kwargs.pop('catalogs', {}),
                          task_domain=task_domain, **kwargs)

    def validate(self, choice, role='implement', **kwargs):
        args = dict(worker=choice['worker'], model=choice.get('model', ''), effort=choice.get('effort', ''),
                    role=role, routing=choice['routing'], access='read' if role in {'explore','review'} else 'write',
                    executor_kind=choice.get('executor_kind', ''), live='codex', catalogs={})
        args.update(kwargs)
        return evidence.validate_launch_tuple(self.repo, **args)

    def test_explicit_domain_is_authoritative_and_separate(self):
        c = self.pick('frontend', case='debug database query', risk='high')
        self.assertEqual(c['kind'], 'implement')
        self.assertEqual(c['task_domain'], 'frontend')
        self.assertEqual(c['routing']['required_tier'], 'strong')
        self.assertEqual(c['routing']['picker']['traits'], ['implementation', 'frontend'])
        self.assertEqual(c['routing']['task_domain']['source'], 'explicit')

    def test_domain_validation_and_normalization(self):
        self.assertEqual(domains.normalize_domain(' Frontend '), 'frontend')
        for value in ('mobile', [], 3, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.pick(value)

    def test_inferred_domains_do_not_change_role(self):
        for phrase, expected in [('CSS component','frontend'),('debug traceback','debugging'),
                                 ('backend api','backend'),('verify the UI','ui-verification'),
                                 ('UI design','ui-design'),('research sources','research')]:
            with self.subTest(phrase=phrase):
                c = self.pick(case=phrase)
                self.assertEqual(c['task_domain'], expected)
                self.assertEqual(c['kind'], 'implement')
        self.assertEqual(self.pick(case='build a sequence')['task_domain'], 'general')

    def test_review_domain_cannot_downgrade_role(self):
        with self.assertRaisesRegex(ValueError, 'role=review'):
            self.pick('review', role='mini')
        c = self.pick(role='review', writer_model='claude-sonnet-5-5')
        self.assertEqual(c['task_domain'], 'review')
        self.assertEqual(c['routing']['required_tier'], 'strong')

    def test_parent_ui_domains_never_probe_catalog_or_spawn(self):
        with patch.object(policy, '_catalog_model', side_effect=AssertionError('no probe')):
            for domain in ('ui-design','ui-verification'):
                c = self.pick(domain, effective=['opencode','claude'], complexity='high')
                self.assertEqual(c['spawn'], 'stay')
                self.assertEqual(c['executor_kind'], 'parent')
                self.assertFalse(c['parent_writes'])
                self.assertIsNone(c['routing']['selected_profile'])
                self.assertIn('no child', c['reason'])

    def test_parent_only_cannot_satisfy_independent_review(self):
        c = self.pick('ui-verification', role='review', writer_model='claude-sonnet-5-5')
        self.assertEqual(c['spawn'], 'none')
        self.assertEqual(c['independence'], 'unavailable')

    def test_preferred_profiles_override_scores_but_not_tier(self):
        self.configure({'frontend': {'preferred_profiles': ['grok-4.5-low','claude-sonnet-5-medium','claude-opus-5-high'], 'fallback':'none'}})
        normal = self.pick('frontend')
        self.assertEqual(normal['routing']['selected_profile']['id'], 'claude-sonnet-5-medium')
        high = self.pick('frontend', risk='high')
        self.assertEqual(high['routing']['selected_profile']['id'], 'claude-opus-5-high')
        self.assertEqual(high['routing']['task_domain']['selection'], 'preferred')

    def test_unavailable_excluded_and_live_parent_preferences_are_skipped(self):
        self.configure({'frontend': {'preferred_profiles':['codex-luna-low','claude-sonnet-5-medium','grok-4.7-high'], 'fallback':'none'}})
        c = self.pick('frontend', exclude='claude')
        self.assertEqual(c['worker'], 'grok')
        c = self.pick('frontend', effective=['grok'])
        self.assertEqual(c['worker'], 'grok')

    def test_exact_catalog_gate_remains(self):
        self.configure({'frontend': {'preferred_profiles':['opencode-gpt-5.6-luna-high','grok-4.7-high'], 'fallback':'none'}})
        c = self.pick('frontend', effective=['opencode','grok'], catalogs={'opencode':['unrelated']})
        self.assertEqual(c['worker'], 'grok')
        codes={d['id']:d['code'] for d in c['routing']['candidate_decisions']}
        self.assertEqual(codes['opencode-gpt-5.6-luna-high'],'catalog-miss')

    def test_review_provider_gate_remains(self):
        self.configure({'review': {'preferred_profiles':['claude-opus-5-high','grok-4.7-high'], 'fallback':'none'}})
        c = self.pick('review', role='review', writer_model='claude-sonnet-5-5')
        self.assertEqual(c['worker'],'grok')  # Different underlying provider, not merely another transport.
        unavailable = self.pick('review', role='review', writer_model='claude-sonnet-5-5', effective=['claude'])
        self.assertEqual(unavailable['spawn'], 'none')
        codes={d['id']:d['code'] for d in c['routing']['candidate_decisions']}
        self.assertEqual(codes['claude-opus-5-high'],'review-same-provider')

    def test_scored_parent_and_none_fallbacks(self):
        for fallback, spawn in [('scored','run-worker'),('parent','native'),('none','none')]:
            self.configure({'frontend': {'preferred_profiles': ['opencode-gpt-5.6-luna-high'], 'fallback':fallback}})
            c = self.pick('frontend')
            self.assertEqual(c['spawn'],spawn)
            if spawn == 'native':
                self.assertTrue(c['parent_writes'])
                self.assertEqual(c['model'],'')
            elif spawn == 'none':
                self.assertIsNone(c['routing']['selected_profile'])

    def test_domain_preferences_precede_direct_parent_ordered_and_jev(self):
        self.configure({'frontend': {'preferred_profiles':['claude-sonnet-5-medium'], 'fallback':'none'}},
                       execution={'direct_parent_low_risk':True}, picker={'engine':'jev','local_policy':'ordered-v1'})
        with patch.object(policy, '_jev_choice', side_effect=AssertionError('no external picker')):
            c = self.pick('frontend', complexity='low',risk='low',uncertainty='low')
        self.assertEqual(c['spawn'],'run-worker')
        self.assertEqual(c['worker'],'claude')
        self.assertEqual(c['routing']['picker']['selection_source'],'domain-preferred')

    def test_domains_config_validation(self):
        invalid = [[], {'unknown':{}}, {'frontend':[]}, {'frontend':{'extra':1}},
                   {'frontend':{'preferred_profiles':['missing']}}, {'frontend':{'fallback':'enable'}},
                   {'frontend':{'preferred_profiles':['grok-4.7-high']*2}},
                   {'ui-design':{'preferred_profiles':['claude-sonnet-5-medium'],'fallback':'parent'}},
                   {'ui-verification':{'fallback':'scored'}}]
        for values in invalid:
            with self.subTest(values=values):
                self.configure(values)
                with self.assertRaises(config.ConfigError): config.load_config(self.repo)

    def test_old_schemas_and_capability_overrides_remain(self):
        for version in (1,2,3):
            (self.repo/'.rig/routing.json').write_text(json.dumps({'schema_version':version,'profiles':{'claude-sonnet-5-medium':{'capability':{'frontend':91}}}}))
            cfg=config.load_config(self.repo)
            self.assertEqual(cfg.domains,{})
            self.assertEqual(cfg.profiles['claude-sonnet-5-medium'].capability.frontend,91)
        (self.repo/'.rig/routing.json').write_text(json.dumps({'schema_version':3,'domains':{}}))
        with self.assertRaises(config.ConfigError): config.load_config(self.repo)

    def test_domain_policy_changes_fingerprint(self):
        before=config.config_fingerprint(config.load_config(self.repo))
        self.configure({'frontend':{'preferred_profiles':['claude-sonnet-5-medium'],'fallback':'none'}})
        after=config.config_fingerprint(config.load_config(self.repo))
        self.assertNotEqual(before,after)

    def test_research_without_source_acquisition_stays_parent(self):
        for role in ('implement','explore'):
            c=self.pick('research',role=role)
            self.assertEqual(c['spawn'],'stay')
            self.assertFalse(c['parent_writes'])

    def test_none_fallback_blocks_missing_research_capability_but_allows_explicit_stay(self):
        self.configure({'research': {'fallback': 'none'}, 'review': {'fallback': 'none'}, 'frontend': {'fallback': 'none'}})
        blocked = self.pick('research', role='explore')
        self.assertEqual(blocked['spawn'], 'none')
        self.assertIn('fallback=none', blocked['reason'])
        for domain in ('research', 'review', 'frontend'):
            with self.subTest(domain=domain):
                planned = self.pick(domain, role='stay')
                self.assertEqual(planned['spawn'], 'stay')
                validated = self.validate(planned, role='stay', access='read')
                self.assertEqual(validated['execution_strategy'], 'stay')
                for forbidden_access in ('write', ''):
                    with self.assertRaises(ValueError):
                        self.validate(planned, role='stay', access=forbidden_access)

    def test_research_sources_require_actual_readable_files_and_read_role(self):
        c=self.pick('research',role='explore',research_sources=['source.md'])
        self.assertEqual(c['spawn'],'run-worker')
        self.assertEqual(c['routing']['task_domain']['research_sources'],['source.md'])
        checked=self.validate(c,role='explore')
        self.assertEqual(checked['task_domain']['research_sources'],['source.md'])
        with self.assertRaisesRegex(ValueError,'access=read'):
            self.validate(c,role='explore',access='write')
        self.assertEqual(self.pick('research',research_sources=['source.md'])['spawn'],'stay')

    def test_research_source_types_and_escape_attempts_fail_closed(self):
        for value in ('source.md',[1],[''],['https://example.com'],['../outside'],[str(self.repo/'source.md')],['missing.md'],['.']):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.pick('research',role='explore',research_sources=value)
        with tempfile.TemporaryDirectory() as outside:
            target=Path(outside)/'outside.md';target.write_text('outside')
            (self.repo/'link.md').symlink_to(target)
            with self.assertRaises(ValueError): self.pick('research',role='explore',research_sources=['link.md'])
        with self.assertRaises(ValueError): self.pick('frontend',research_sources=['source.md'])

    def test_research_removed_source_is_rejected_at_launch(self):
        c=self.pick('research',role='explore',research_sources=['source.md'])
        (self.repo/'source.md').unlink()
        with self.assertRaisesRegex(ValueError,'missing'):
            self.validate(c,role='explore')

    def test_launch_preserves_domain_and_rejects_conflicts(self):
        c=self.pick('frontend')
        checked=self.validate(c)
        self.assertEqual(checked['task_domain']['name'],'frontend')
        for kwargs in ({'task_domain':'backend'},{'research_sources':['source.md']}):
            with self.subTest(kwargs=kwargs),self.assertRaisesRegex(ValueError,'conflicts'):
                self.validate(c,**kwargs)

    def test_launch_rejects_missing_or_tampered_domain_metadata(self):
        c=self.pick('frontend')
        for mutate in (lambda r:r.pop('task_domain'), lambda r:r['task_domain'].update(parent_only=True),
                       lambda r:r['task_domain'].update(fallback='none'), lambda r:r.update(policy_version=1)):
            bad=copy.deepcopy(c);mutate(bad['routing'])
            with self.assertRaises(ValueError): self.validate(bad)

    def test_launch_rechecks_domain_allowlist_and_config(self):
        self.configure({'frontend':{'preferred_profiles':['claude-sonnet-5-medium'],'fallback':'none'}})
        c=self.pick('frontend')
        self.validate(c)
        forged=copy.deepcopy(c)
        grok=config.load_config(self.repo).profiles['grok-4.7-high']
        forged.update(worker='grok',model=grok.selector,effort=grok.effort)
        forged['routing']['selected_profile']=evidence.selected_profile_dict(grok,model=grok.selector,tier='standard')
        with self.assertRaisesRegex(ValueError,'domain fallback'):
            self.validate(forged)
        self.configure({'frontend':{'preferred_profiles':['claude-sonnet-5-medium'],'fallback':'scored'}})
        with self.assertRaisesRegex(ValueError,'fingerprint'): self.validate(c)

    def test_parent_only_and_none_picks_cannot_be_launched_as_workers(self):
        c=self.pick('ui-design')
        with self.assertRaisesRegex(ValueError,'parent-only'):
            self.validate(c,executor_kind='wrapper')
        with self.assertRaisesRegex(ValueError,'parent-only'):
            self.validate(c,executor_kind='parent',access='write')
        self.configure({'frontend':{'fallback':'none'}})
        c=self.pick('frontend')
        with self.assertRaisesRegex(ValueError,'fallback=none'):
            self.validate(c,executor_kind='parent')

    def test_manual_legacy_cannot_silently_claim_domain_policy(self):
        for routing in (None,{}, {'policy_mode':'manual'}, {'policy_mode':'legacy'}):
            with self.assertRaisesRegex(ValueError,'smart routing'):
                evidence.validate_launch_tuple(self.repo,worker='grok',model='grok-4.7',effort='high',role='implement',routing=routing,task_domain='frontend')
        with self.assertRaisesRegex(ValueError,'smart routing'):
            route.pick('codex',['grok'],'implement','task',policy_mode='legacy',task_domain='frontend')

    def test_explain_and_sidecar_roundtrip(self):
        c=self.pick('frontend')
        self.assertIn('task_domain=frontend','\n'.join(evidence.explain_lines(c)))
        evidence.write_sidecar(self.repo/'job','attempt',c['routing'])
        got=evidence.read_sidecar(self.repo/'job','attempt')
        self.assertEqual(got['routing']['task_domain'],c['routing']['task_domain'])
        for invalid in ([], {'name':[]}, {'name':'frontend'}):
            bad=copy.deepcopy(c['routing']);bad['task_domain']=invalid
            evidence.write_sidecar(self.repo/'job','attempt',bad)
            self.assertIsNone(evidence.read_sidecar(self.repo/'job','attempt'))


if __name__ == '__main__': unittest.main()
