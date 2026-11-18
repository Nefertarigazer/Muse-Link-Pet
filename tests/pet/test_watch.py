import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pc'))
from pet.watch import Watch, iso


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tool = Path(self.temp.name)
        self.now = 1800000000.
        self.watch = Watch(self.tool, clock=lambda: self.now)
        self.addCleanup(self.watch.stop)

    def put(self, name, value):
        (self.tool / name).write_text(value, encoding='utf-8')

    def arm(self, req='req_one', mode='input'):
        self.put('popup-pending.txt', req)
        self.put('popup-armed.json', json.dumps(dict(req_id=req, mode=mode, summary='Question\nsecret')))
        self.watch.poll()

    def event(self, kind, **extra):
        with (self.tool / 'executor-events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(type=kind, ts=iso(self.now), **extra)) + '\n')

    def answer(self, kind, req='req_one'):
        self.put('popup-kind.txt', kind)
        self.put('popup-done.txt', iso(self.now) + '\nREQ:' + req + '\n')
        self.watch.poll()

    def test_task_wait_is_not_user_wait_and_priority(self):
        self.put('popup-pending.txt', 'task_wait')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['state'], 'idle')
        self.event('job_begin', detail='DO NOT DISPLAY COMMAND')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['state'], 'working')
        self.assertIsNone(self.watch.snapshot()['brief'])
        self.arm()
        snap = self.watch.snapshot()
        self.assertEqual(snap['state'], 'waiting_user')
        self.assertEqual(snap['ask']['summary'], 'Question')
        self.put('popup-pending.txt', 'another_wait')
        self.watch.poll()
        self.assertIsNone(self.watch.snapshot()['ask'])

    def test_linger_boundary_and_host_restart(self):
        self.event('job_begin')
        self.watch.poll()
        self.now += 44.99
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['state'], 'working')
        self.now += .01
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['state'], 'idle')
        self.event('job_end')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['state'], 'working')
        self.event('host_start')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['state'], 'idle')

    def test_rotation_drains_old_file_and_reads_new(self):
        self.event('job_begin')
        self.watch.poll()
        self.event('phase', text='first')
        (self.tool / 'executor-events.jsonl').rename(self.tool / 'executor-events.jsonl.1')
        self.event('phase', text='hello\x00\n' + '世' * 80)
        self.watch.poll()
        snap = self.watch.snapshot()
        self.assertEqual(snap['brief'], 'hello ' + '世' * 80)
        self.assertTrue(snap['brief_id'].endswith(':2'))
        self.assertEqual(snap['brief_at'], iso(self.now))

    def test_phase_is_clean_bounded_and_empty_formats_fall_back(self):
        self.event('job_begin', job='shell:one')
        for raw, expected in [
                ('\u200b\u202e\ufeff\x00', None),
                (' a\n\tb\u200b\u2066 c ', 'a b c'),
                ('长' * 2000, '长' * 500),
                (None, None)]:
            with self.subTest(raw=repr(raw)[:40]):
                self.event('phase', text=raw)
                self.watch.poll()
                self.assertEqual(self.watch.snapshot()['brief'], expected)
                self.assertEqual(self.watch.snapshot()['activity'], 'shell')

    def test_interleaved_end_keeps_latest_begin_activity(self):
        self.event('job_begin', job='shell:A')
        self.event('job_begin', job='ad:screenshot:B')
        self.event('job_end', job='shell:A', detail='status=ok')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['activity'], 'screen')
        self.event('job_end', job='ad:screenshot:B', detail='status=ok')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['activity'], 'screen')
        self.event('host_start')
        self.event('job_end', job='shell:orphan')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['activity'], 'shell')

    def test_truncation_restarts_at_zero_and_rotation_discards_old_partial(self):
        self.event('job_begin', job='shell:one')
        self.event('phase', text='long' * 100)
        self.watch.poll()
        self.put('executor-events.jsonl', json.dumps(dict(type='phase', ts=iso(self.now), text='new')) + '\n')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['brief'], 'new')
        with (self.tool / 'executor-events.jsonl').open('a') as stream:
            stream.write('{"type":"phase","text":"unfinished')
        self.watch.poll()
        (self.tool / 'executor-events.jsonl').rename(self.tool / 'executor-events.jsonl.1')
        self.event('phase', text='rotated')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['brief'], 'rotated')

    def test_partial_line_bad_records_and_copy_snapshot(self):
        self.put('executor-events.jsonl', 'garbage\n[]\n{"type":"job_begin","ts":"' + iso(self.now) + '"}')
        self.watch.poll()
        self.assertEqual(self.watch.snapshot()['state'], 'idle')
        with (self.tool / 'executor-events.jsonl').open('a') as f:
            f.write('\n')
        self.watch.poll()
        snap = self.watch.snapshot()
        snap['state'] = 'BAD'
        self.assertEqual(self.watch.snapshot()['state'], 'working')

    def test_restart_does_not_replay_work_or_answer(self):
        self.event('job_begin')
        self.answer('pet_done')
        restarted = Watch(self.tool, clock=lambda: self.now)
        self.addCleanup(restarted.stop)
        self.assertEqual(restarted.snapshot()['state'], 'idle')
        self.assertIsNone(restarted.snapshot()['last_answer'])

    def test_separate_event_directory_created_after_start_and_restart(self):
        events_dir = self.tool / 'pet'
        events_dir.mkdir()
        watch = Watch(self.tool, clock=lambda: self.now, events_dir=events_dir)
        self.addCleanup(watch.stop)
        path = events_dir / 'executor-events.jsonl'
        def append(kind, **fields):
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(type=kind, ts=iso(self.now), **fields)) + '\n')
        self.event('job_begin')  # another stream must not affect this pet
        watch.poll()
        self.assertEqual(watch.snapshot()['state'], 'idle')
        append('job_begin', job='shell:powershell.exe', detail='shell powershell.exe')
        append('phase', text='Checking')
        watch.poll()
        self.assertEqual((watch.snapshot()['state'], watch.snapshot()['brief']), ('working', 'Checking'))
        restarted = Watch(self.tool, clock=lambda: self.now, events_dir=events_dir)
        self.addCleanup(restarted.stop)
        self.assertEqual(restarted.snapshot()['state'], 'idle')
        append('job_begin')
        append('phase', text='Reading')
        restarted.poll()
        self.assertEqual((restarted.snapshot()['state'], restarted.snapshot()['brief']), ('working', 'Reading'))
        self.arm()  # popup observation still uses the original tool directory
        watch.poll()
        self.assertEqual(watch.snapshot()['state'], 'waiting_user')

    def test_latest_activity_uses_kind_and_detail_without_exposing_commands(self):
        for job, detail, activity in [
                ('ad:screenshot', 'ad screenshot', 'screen'),
                ('custom', 'ad screenshot', 'screen'),
                ('shell:powershell.exe', 'shell powershell.exe', 'shell'),
                ('script:task.ps1', 'script task.ps1', 'script'),
                ('sem:observe', 'observe app', 'read'),
                ('uia', 'uia read_text app', 'read'),
                ('sem:act', 'act app', 'action'),
                ('seq', 'seq 3 steps', 'steps'),
                ('unknown', 'DO NOT DISPLAY COMMAND', 'working')]:
            with self.subTest(job=job, detail=detail):
                self.event('job_begin', job=job, detail=detail)
                self.watch.poll()
                snap = self.watch.snapshot()
                self.assertEqual(snap['activity'], activity)
                self.assertIsNone(snap['brief'])
                self.assertNotIn(detail, json.dumps(snap))
                self.event('job_end', job=job, detail='status=ok ms=123')
                self.watch.poll()
                self.assertEqual(self.watch.snapshot()['activity'], activity)
        self.now += 45
        self.watch.poll()
        self.assertIsNone(self.watch.snapshot()['activity'])
        self.event('job_begin', job='ad:screenshot')
        self.event('host_start')
        self.watch.poll()
        self.assertIsNone(self.watch.snapshot()['activity'])

    def test_answer_mapping_including_bus_removed_between_polls(self):
        for kind, by in [('pet_done', 'pet'), ('pet_typed', 'pet'), ('info', 'popup'),
                         ('choice', 'popup'), ('typed', 'popup'), ('input', 'popup'), ('web_ai_done', 'other')]:
            with self.subTest(kind=kind):
                req = 'req_' + kind
                self.arm(req)
                (self.tool / 'popup-pending.txt').unlink()
                self.answer(kind, req)
                answer = self.watch.snapshot()['last_answer']
                self.assertEqual((answer['req_id'], answer['by']), (req, by))
                self.assertIsNone(self.watch.snapshot()['ask'])

    def test_expiry_supersession_and_disappearance_are_not_answers(self):
        self.arm()
        self.answer('expired')
        self.assertIsNone(self.watch.snapshot()['last_answer'])
        self.arm('req_two')
        self.answer('pet_done', 'req_wrong')
        self.assertIsNone(self.watch.snapshot()['last_answer'])
        self.put('popup-pending.txt', '')
        self.watch.poll()
        self.assertIsNone(self.watch.snapshot()['last_answer'])
        self.assertEqual(self.watch.snapshot()['state'], 'idle')

    def test_watch_thread_survives_unexpected_poll_error(self):
        import threading
        errors = []
        watch = Watch(self.tool, clock=lambda: self.now, on_error=errors.append)
        self.addCleanup(watch.stop)
        calls = [0]
        recovered = threading.Event()
        orig_poll = watch.poll
        def flaky():
            calls[0] += 1
            if calls[0] == 1:
                raise ValueError('synthetic poll bug')
            orig_poll()
            if calls[0] >= 2:
                recovered.set()
        watch.poll = flaky
        watch.start()
        self.assertTrue(recovered.wait(3))
        self.assertEqual(errors, ['watch: ValueError'])


if __name__ == '__main__':
    unittest.main()
