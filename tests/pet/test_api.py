import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pc'))
from pet.api import Api
from pet.actions import ActionError
from pet.channels import Channels
from pet.watch import Watch
from pet.make_confirm_wav import render
from pet.geometry import WINDOW_WIDTH, WINDOW_HEIGHT

CONTRACT = json.loads((ROOT / 'pc/pet/ui/fixtures/contract.json').read_text(encoding='utf-8'))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tool = Path(self.temp.name)
        self.watch = Watch(self.tool)
        self.addCleanup(self.watch.stop)
        self.actions = mock.Mock()
        self.channels = Channels(self.tool, self.watch)
        self.api = Api(self.watch, self.actions, self.channels)
        self.window = mock.Mock()
        self.api._attach(self.window)

    def data(self, response):
        self.assertEqual(set(response), {'ok', 'data'}, response)
        self.assertIs(response['ok'], True)
        return response['data']

    def test_contract_all_methods_and_keys(self):
        self.assertEqual({n for n in dir(self.api) if not n.startswith('_')}, set(CONTRACT['methods']))
        for method in ('i18n', 'config', 'state'):
            self.assertEqual(set(self.data(getattr(self.api, method)())), set(CONTRACT[method]))
        for language in self.data(self.api.languages()):
            self.assertEqual(set(language), set(CONTRACT['language']))
        self.assertEqual(self.data(self.api.config()), dict(display_name='Muse Pet', pack='jolly', bubble_ms=8000, poll_ms=1000, sound=True))
        for method in ('_tunnel', '_host'):
            patch = mock.patch.object(self.channels, method, return_value=('unknown', 'unavailable'))
            patch.start()
            self.addCleanup(patch.stop)
        channels = self.data(self.api.channels())
        self.assertEqual(set(channels), set(CONTRACT['channels']))
        self.assertEqual([x['id'] for x in channels['items']], CONTRACT['enums']['channel_id'])
        for item in channels['items']:
            self.assertEqual(set(item), set(CONTRACT['channel_item']))
            self.assertIn(item['level'], CONTRACT['enums']['level'])
        (self.tool / 'popup-pending.txt').write_text('req_one')
        (self.tool / 'popup-armed.json').write_text(json.dumps(dict(req_id='req_one', mode='input', summary='Q')))
        self.watch.poll()
        snap = self.data(self.api.state())
        self.assertEqual(set(snap['ask']), set(CONTRACT['ask']))
        self.assertIn(snap['ask']['mode'], CONTRACT['enums']['ask_mode'])
        (self.tool / 'popup-kind.txt').write_text('pet_done')
        (self.tool / 'popup-done.txt').write_text('2026-11-12T10:00:00Z\nREQ:req_one\n')
        self.watch.poll()
        answer = self.data(self.api.state())['last_answer']
        self.assertEqual(set(answer), set(CONTRACT['last_answer']))
        self.assertIn(answer['by'], CONTRACT['enums']['answer_by'])
        self.actions.done.return_value = {'delivered': True}
        self.assertEqual(set(self.data(self.api.done('req_one'))), set(CONTRACT['done_result']))
        for via in CONTRACT['enums']['say_via']:
            self.actions.say.return_value = dict(via=via, req_id='req_one')
            self.assertEqual(set(self.data(self.api.say('  text  '))), set(CONTRACT['say_result']))
            self.actions.say.assert_called_with('text', None)
        self.window.set_avatar.return_value = True
        self.assertEqual(set(self.data(self.api.set_avatar())), set(CONTRACT['set_avatar_result']))
        for response in (self.api.open_mailbox(), self.api.open_session(), self.api.hide(), self.api.exit(),
                         self.api.region([dict(x=0, y=0, w=WINDOW_WIDTH, h=WINDOW_HEIGHT)]), self.api.activate(False)):
            self.assertIsNone(self.data(response))

    def test_language_and_exit_methods_forward_to_their_owners(self):
        languages = [dict(tag='en', name='English')]
        bundle = dict(tag='en', dir='ltr', font='Segoe UI', messages={})
        with mock.patch('i18n.languages', return_value=languages) as get_languages, mock.patch('i18n.set_language', return_value=bundle) as set_language:
            self.assertEqual(self.data(self.api.languages()), languages)
            get_languages.assert_called_once_with()
            result = self.data(self.api.set_language('en'))
            self.assertEqual(result, bundle)
            self.assertEqual(set(result), set(CONTRACT['set_language']))
            set_language.assert_called_once_with('en')
        self.assertIsNone(self.data(self.api.exit()))
        self.window.exit.assert_called_once_with()
        self.window.hide.assert_not_called()

    def test_language_validation_and_write_failure_use_bridge_errors(self):
        for tag in (None, [], {}, '', '../en', 'en-US', 'fr'):
            with self.subTest(tag=tag):
                response = self.api.set_language(tag)
                self.assertEqual(set(response), {'ok', 'error', 'code'})
                self.assertIs(response['ok'], False)
                self.assertEqual(response['code'], 'invalid_input')
        with mock.patch('i18n.set_language', side_effect=PermissionError('PRIVATE PATH')):
            response = self.api.set_language('en')
        self.assertIs(response['ok'], False)
        self.assertEqual(response['code'], 'backend')
        self.assertNotIn('PRIVATE', response['error'])

    def test_validation(self):
        calls = [lambda: self.api.say(''), lambda: self.api.say(' \n '), lambda: self.api.say('x' * 2001),
                 lambda: self.api.say({}), lambda: self.api.say('x', 'bad id'), lambda: self.api.done('../a'),
                 lambda: self.api.activate(1), lambda: self.api.region(None), lambda: self.api.region([{}]),
                 lambda: self.api.region([dict(x=0, y=0, w=1, h=1)] * 33),
                 lambda: self.api.open_mailbox('bad thread')]
        for rect in (dict(x=-1, y=0, w=1, h=1), dict(x=WINDOW_WIDTH - 1, y=0, w=2, h=1),
                     dict(x=0, y=WINDOW_HEIGHT - 1, w=1, h=2), dict(x=True, y=0, w=1, h=1),
                     dict(x=0., y=0, w=1, h=1), dict(x=0, y=0, w=-1, h=1)):
            calls.append(lambda rect=rect: self.api.region([rect]))
        for call in calls:
            response = call()
            self.assertEqual(set(response), {'ok', 'error', 'code'})
            self.assertFalse(response['ok'])
            self.assertEqual(response['code'], 'invalid_input')
        self.actions.say.return_value = dict(via='bridge', req_id='req_one')
        self.data(self.api.say('世' * 2000))
        self.data(self.api.region([]))

    def test_private_failures_timeouts_and_cached_snapshot_budget(self):
        for error, code in [(RuntimeError('PRIVATE SECRET'), 'backend'),
                            (ActionError('already_answered', 'Already answered.'), 'already_answered'),
                            (subprocess.TimeoutExpired(['private'], 10), 'timeout')]:
            self.actions.done.side_effect = error
            response = self.api.done('req_one')
            self.assertEqual(response['code'], code)
            self.assertNotIn('PRIVATE', json.dumps(response))
        start = time.perf_counter()
        for _ in range(1000):
            self.data(self.api.state())
        self.assertLess((time.perf_counter() - start) / 1000, .005)

    def test_channels_cache_and_stale_bridge(self):
        bridge = self.tool / 'muse-bridge'
        (bridge / 'inbox').mkdir(parents=True)
        (bridge / 'archive').mkdir()
        path = bridge / 'inbox/old.json'
        path.write_text('{}')
        os.utime(path, (time.time() - 181,) * 2)
        (bridge / 'archive/one.1.reply.json').write_text('{"replied_at":"2026-11-12T10:00:00Z"}')
        (bridge / 'archive/legacy.json').write_text('{"replied_at":"2026-11-12T10:30:00+02:00"}')
        with mock.patch.object(self.channels, '_tunnel', return_value=('ok', 'online')) as tunnel, mock.patch.object(self.channels, '_host', return_value=('ok', 'version')):
            first = self.channels.check()
            first['items'][0]['detail'] = 'bad'
            second = self.channels.check()
            self.assertEqual(tunnel.call_count, 1)
            self.assertEqual(second['items'][-1]['level'], 'warn')
            from datetime import datetime, timezone
            latest = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc).timestamp()
            self.assertIn(datetime.fromtimestamp(latest).strftime('%m-%d %H:%M'), second['items'][-1]['detail'])
            self.assertEqual(second['items'][0]['detail'], 'online')

    def test_confirm_wav_format(self):
        path = self.tool / 'confirm.wav'
        render(path)
        with wave.open(str(path)) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (44100, 1, 2))
            self.assertGreater(wav.getnframes(), 22000)
            self.assertNotEqual(set(wav.readframes(wav.getnframes())), {0})

    def test_channels_language_change_invalidates_five_second_cache(self):
        import i18n
        from pet.watch import iso
        self.channels.clock = lambda: 1800000000.
        with mock.patch('i18n.preference_path', return_value=self.tool / 'ui-language.txt'), mock.patch.object(self.channels, '_tunnel', return_value=('ok', 'online')) as tunnel, mock.patch.object(self.channels, '_host', return_value=('ok', 'version')):
            self.addCleanup(i18n.active.cache_clear)
            i18n.set_language('en')
            english = self.channels.check()
            self.channels.check()
            self.assertEqual(tunnel.call_count, 1)
            i18n.set_language('zh-Hans')
            chinese = self.channels.check()
            self.channels.check()
            self.assertEqual(tunnel.call_count, 2)
            self.assertEqual(chinese['checked_at'], iso(self.channels.clock()))
            self.assertNotEqual(english['items'][0]['label'], chinese['items'][0]['label'])
            self.assertNotEqual(english['items'][2]['detail'], chinese['items'][2]['detail'])

    def test_shell_exit_uses_tray_shutdown_gate(self):
        from pet.app import Shell
        from pet.tray import Tray
        shell = object.__new__(Shell)
        shell.window = mock.Mock()
        tray = object.__new__(Tray)
        tray.shell, tray.on_exit = shell, mock.Mock()
        tray._icon, tray._exiting = mock.Mock(), False
        shell._exit_callback = tray.exit
        shell.exit()
        self.assertTrue(tray.closing())
        tray._icon.stop.assert_called_once_with()
        tray.on_exit.assert_called_once_with()
        shell.window.destroy.assert_called_once_with()
        shell.window.hide.assert_not_called()

    def test_shared_static_server_only_serves_ui(self):
        from mailbox.shell import static_server_class
        root = self.tool / 'ui'
        root.mkdir()
        (root / 'index.html').write_text('test')
        (root / 'a.js').write_text('test')
        (self.tool / 'secret.txt').write_text('PRIVATE')
        _, _, server = static_server_class(root).start_server([], 0)
        self.addCleanup(server._httpd.server_close)
        self.addCleanup(server._httpd.shutdown)
        with urllib.request.urlopen(server.address + 'a.js') as response:
            self.assertEqual(response.headers.get_content_type(), 'text/javascript')
        for path, method in [('js_api', 'POST'), ('../secret.txt', 'GET')]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(server.address + path, method=method))
            self.assertIn(error.exception.code, (403, 404, 405))

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_shell_guards_and_second_launch(self):
        from pet import app
        with mock.patch.object(app.ctypes.windll.shell32, 'IsUserAnAdmin', return_value=True), mock.patch.object(app, '_dialog'), mock.patch.object(app, 'Instance') as instance:
            self.assertEqual(app.main([]), 1)
            instance.assert_not_called()
        with mock.patch.object(app.ctypes.windll.shell32, 'IsUserAnAdmin', return_value=False), mock.patch.object(app, '_runtime_available', return_value=False), mock.patch.object(app, '_dialog'), mock.patch.object(app, 'Instance') as instance:
            self.assertEqual(app.main([]), 1)
            instance.assert_not_called()
        instance = mock.Mock(primary=False)
        with mock.patch.object(app.ctypes.windll.shell32, 'IsUserAnAdmin', return_value=False), mock.patch.object(app, '_runtime_available', return_value=True), mock.patch.object(app, 'Instance', return_value=instance):
            self.assertEqual(app.main([]), 0)
        instance.forward.assert_called_once_with({'show': True})
        instance.close.assert_called_once()

    def test_track_pointer_emits_false_when_region_updates_after_fast_leave(self):
        from pet.app import Shell
        import threading
        shell = Shell.__new__(Shell)
        shell.visible = True
        shell.rects = []
        shell._region_seq = 0
        shell._ui = lambda fn: None
        shell.stop_event = threading.Event()
        shell.window = mock.Mock()
        calls = []
        shell.window.evaluate_js.side_effect = lambda js: calls.append(js)
        shell._inside = mock.Mock(return_value=False)
        orig_wait = shell.stop_event.wait
        steps = [False, False, True]
        def fake_wait(timeout):
            idx = len(steps)
            if idx == 3:
                steps.pop(0)
                return False
            if idx == 2:
                shell.region([{'x': 0, 'y': 0, 'w': 10, 'h': 10}])
                steps.pop(0)
                return False
            return True
        shell.stop_event.wait = fake_wait
        shell._track_pointer()
        self.assertEqual(calls, ['window.MusePet && MusePet.pointer(false)'])


if __name__ == '__main__':
    unittest.main()
