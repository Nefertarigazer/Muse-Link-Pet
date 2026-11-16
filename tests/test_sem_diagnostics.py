"""Probes must preserve host behavior and exclude private desktop content."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.diag = load('tools/diagnose-sem.py', 'diagnose_sem_test')
        self.host = load('pc/executor-host.py', 'diagnostic_host_test')
        self.events = []

    def write(self, phase, **fields):
        self.events.append(dict(phase=phase, **fields))

    def run_probe(self, envelope):
        process = SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr='PRIVATE STDERR')
        with mock.patch.object(subprocess, 'run', return_value=process):
            return self.diag.profile(self.host, dict(verb='resolve', app='Test.exe', query={'name': 'PRIVATE QUERY'}), self.write)

    def test_preserves_resolution_and_times_process_and_decode(self):
        original_ad, original_process = self.host.ad, self.host.subprocess
        result = self.run_probe(dict(ok=True, data={'matches': [dict(name='PRIVATE MATCH', role='button', ref_id='@s1:e1')]}))
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['match']['name'], 'PRIVATE MATCH')
        self.assertIs(self.host.ad, original_ad)
        self.assertIs(self.host.subprocess, original_process)
        phases = [e['phase'] for e in self.events]
        self.assertEqual(phases, ['ad.begin', 'process.begin', 'process.end', 'ad.end', 'host.end'])
        self.assertGreaterEqual(self.events[3]['decode_ms'], 0)
        self.assertNotIn('PRIVATE', json.dumps(self.events))

    def test_keeps_upstream_failure_and_only_numeric_counters(self):
        result = self.run_probe(dict(ok=False, error=dict(code='TIMEOUT', message='PRIVATE MESSAGE', details={
            'query_stats': {'traversal': {'nodes_visited': 123}, 'text': 'PRIVATE CONTENT'}})))
        self.assertEqual(result['error'], 'TIMEOUT')
        event = next(e for e in self.events if e['phase'] == 'ad.end')
        self.assertEqual(event['counters'], {'traversal': {'nodes_visited': 123}})
        self.assertNotIn('PRIVATE', json.dumps(self.events))

    def test_hard_timeout_is_still_unknown_and_cleans_up(self):
        original_ad, original_process = self.host.ad, self.host.subprocess
        with mock.patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('PRIVATE COMMAND', 30)):
            result = self.diag.profile(self.host, dict(verb='resolve', app='Test.exe', query={}), self.write)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['error'], 'HOST_TIMEOUT')
        self.assertIs(self.host.ad, original_ad)
        self.assertIs(self.host.subprocess, original_process)
        self.assertIn('process.timeout', [e['phase'] for e in self.events])
        self.assertNotIn('PRIVATE', json.dumps(self.events))

    def test_no_raw_host_exception_in_summary(self):
        with mock.patch.object(subprocess, 'run', side_effect=OSError('PRIVATE PATH')):
            result = self.diag.profile(self.host, dict(verb='resolve', app='Test.exe', query={}), self.write)
        self.assertIn('PRIVATE PATH', result['error'])
        self.assertNotIn('PRIVATE', json.dumps(self.events))


if __name__ == '__main__':
    unittest.main()
