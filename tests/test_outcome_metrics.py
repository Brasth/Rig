"""Recorded outcome boundaries, missing evidence, and report immutability."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import outcome_metrics as metrics

START = '2026-10-01T00:00:00+00:00'
END = '2026-10-01T00:00:10+00:00'


class Outcomes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.job = self.seed('first')

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def seed(self, name, parent=None):
        job = {'job_id': name, 'attempt_id': name + '-attempt', 'reservation_id': name + '-reservation', 'status': 'ok'}
        if parent:
            job['continues_job_id'] = parent
        folder = self.repo / '.rig/jobs' / name
        self.write(folder / 'meta.json', job)
        self.write(self.repo / '.rig/reservations' / (job['reservation_id'] + '.json'), {**job, 'created_at': START})
        binding = {key: job[key] for key in ('reservation_id', 'attempt_id')}
        self.write(folder / 'requirements.json', {'job_id': name, 'requirements': [], 'manual_criteria': ['review']})
        self.write(folder / 'verification.json', {**binding, 'state': 'verified', 'acceptance': 'accepted',
                  'accepted_at': END, 'assessed_at': END,
                  'history': [{**binding, 'state': 'pending', 'acceptance': 'pending', 'assessed_at': START}]})
        return job

    def verification(self, job=None):
        return self.repo / '.rig/jobs' / (job or self.job)['job_id'] / 'verification.json'

    def test_clean_historical_acceptance_remains_separate_from_freshness(self):
        path = self.verification()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.repo.rglob('*') if p.is_file()}
        result = metrics.report(self.repo, [self.job])
        self.assertEqual(result['attempts']['latency']['p50_s'], 10)
        self.assertEqual(result['attempts']['first_pass']['rate'], 1)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.repo.rglob('*') if p.is_file()})
        stored = json.loads(path.read_text())
        stored['history'].append({key: value for key, value in stored.items() if key != 'history'})
        stored.update(state='pending', acceptance='pending', accepted_at='')
        self.write(path, stored)
        self.assertEqual(metrics.attempt(self.repo, self.job)['latency_s'], 10)

    def test_failed_check_repair_and_rejection_are_not_first_pass(self):
        folder = self.verification().parent
        binding = {key: self.job[key] for key in ('reservation_id', 'attempt_id')}
        self.write(folder / 'checks/failed.json', {**binding, 'sequence': 1, 'requirement_id': 'tests', 'started_at': START, 'status': 'failed'})
        self.write(folder / 'checks/passed.json', {**binding, 'sequence': 2, 'requirement_id': 'tests', 'started_at': END, 'status': 'passed'})
        self.assertFalse(metrics.attempt(self.repo, self.job)['first_pass'])
        for p in (folder / 'checks').glob('*.json'):
            p.unlink()
        stored = json.loads(self.verification().read_text())
        stored['history'].append({**binding, 'state': 'failed', 'acceptance': 'rejected', 'assessed_at': START})
        self.write(self.verification(), stored)
        self.assertFalse(metrics.attempt(self.repo, self.job)['first_pass'])

    def test_failed_criterion_and_incomplete_sequence(self):
        folder = self.verification().parent
        binding = {key: self.job[key] for key in ('reservation_id', 'attempt_id')}
        self.write(folder / 'criteria/history/failure.json', {**binding, 'result': 'fail', 'recorded_at': START})
        self.assertFalse(metrics.attempt(self.repo, self.job)['first_pass'])
        (folder / 'criteria/history/failure.json').unlink()
        self.write(folder / 'checks/gap.json', {**binding, 'sequence': 2, 'requirement_id': 'tests', 'started_at': START, 'status': 'passed'})
        self.assertIsNone(metrics.attempt(self.repo, self.job)['first_pass'])

    def test_legacy_and_wrong_attempt_history_stay_unknown(self):
        stored = json.loads(self.verification().read_text())
        stored.pop('history')
        self.write(self.verification(), stored)
        result = metrics.attempt(self.repo, self.job)
        self.assertEqual(result['latency_s'], 10)
        self.assertIsNone(result['first_pass'])
        stored['attempt_id'] = 'other'
        self.write(self.verification(), stored)
        self.assertIsNone(metrics.attempt(self.repo, self.job)['latency_s'])

    def test_missing_reverse_invalid_timestamps_and_cancelled(self):
        reservation = self.repo / '.rig/reservations' / (self.job['reservation_id'] + '.json')
        value = json.loads(reservation.read_text())
        for stamp in ['bad', '2026-10-01T00:00:20+00:00']:
            value['created_at'] = stamp
            self.write(reservation, value)
            self.assertIsNone(metrics.attempt(self.repo, self.job)['latency_s'])
        reservation.unlink()
        self.verification().unlink()
        result = metrics.report(self.repo, [{**self.job, 'status': 'cancelled'}])['attempts']
        self.assertEqual(result['missing'], 1)
        self.assertEqual(result['first_pass']['unknown'], 1)
        self.assertEqual(metrics.report(self.repo, [{**self.job, 'status': 'running'}])['attempts']['pending'], 1)

    def test_explicit_continuation_broken_and_cyclic_links(self):
        child = self.seed('second', 'first')
        self.verification().unlink()
        result = metrics.report(self.repo, [self.job, child])
        self.assertEqual(result['continuation_count'], 1)
        self.assertEqual(result['continuation_chains']['latency']['p50_s'], 10)
        self.assertFalse(metrics.attempt(self.repo, child)['first_pass'])
        child['continues_job_id'] = 'missing'
        self.assertEqual(metrics.report(self.repo, [child])['continuation_chains']['measured'], 0)
        child['continues_job_id'] = 'second'
        self.assertEqual(metrics.report(self.repo, [child])['continuation_chains']['measured'], 0)

    def test_malformed_checks_and_replaced_ancestor_remain_unknown(self):
        stored = json.loads(self.verification().read_text())
        stored["check_ids"] = [{"bad": "id"}]
        self.write(self.verification(), stored)
        self.assertIsNone(metrics.attempt(self.repo, self.job)["first_pass"])
        child = self.seed("replacement", "first")
        path = self.repo / ".rig/reservations" / (child["reservation_id"] + ".json")
        reservation = json.loads(path.read_text())
        reservation["continues_reservation_id"] = "previous-generation"
        self.write(path, reservation)
        self.assertEqual(metrics.report(self.repo, [child])["continuation_chains"]["measured"], 0)

    def test_workflow_retry_and_forward_coverage(self):
        state = {'status': 'verified', 'metrics': {'runtime_from_creation': True}}
        events = [{'kind': 'runtime-state', 'status': 'verified', 'observed_at': END, 'runtime_revision': 1}]
        result = metrics.workflow_report(self.repo, {'created_at': START}, state, events, [self.job])
        self.assertEqual(result['workflow']['latency_s'], 10)
        self.assertTrue(result['workflow']['first_pass'])
        events.append({'kind': 'resolved', 'action': 'retry'})
        result = metrics.workflow_report(self.repo, {'created_at': START}, state, events, [self.job])
        self.assertFalse(result['workflow']['first_pass'])
        self.assertEqual(result['retry_count'], 1)
        result = metrics.workflow_report(self.repo, {'created_at': START}, {'status': 'running'}, [], [self.job])
        self.assertIsNone(result['workflow']['latency_s'])
        self.assertIsNone(result['workflow']['first_pass'])


if __name__ == '__main__':
    unittest.main()
