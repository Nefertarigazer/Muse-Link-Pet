import concurrent.futures
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pc'))
from pet.actions import Actions, ActionError, DETACH_FLAGS, FLAGS, POWERSHELL
from pet.watch import done_info
from pet.make_confirm_wav import render
STAMP_END_MS = 880   # MusePopup.AnsweredStamp.EndMs: last phase of the stamp


@unittest.skipUnless(os.name == 'nt', 'Real Windows PowerShell integration')
class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tool = Path(self.temp.name)
        shutil.copytree(ROOT / 'pc/i18n', self.tool / 'i18n', ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('Complete-MuseWait.ps1', 'muselink-common.ps1', 'muse-call.ps1', 'muselink-identity.ps1'):
            shutil.copy2(ROOT / 'pc' / name, self.tool)
        self.pending = self.tool / 'popup-pending.txt'
        self.pending.write_text('req_one')
        self.actions = Actions(self.tool)

    def test_first_wins_and_wrong_req_does_not_write(self):
        self.assertEqual(self.actions.done('req_wrong'), {'delivered': False})
        self.assertFalse((self.tool / 'popup-done.txt').exists())
        self.assertEqual(self.actions.done('req_one'), {'delivered': True})
        before = {p.name: p.read_bytes() for p in self.tool.glob('popup-*')}
        with self.assertRaises(ActionError) as error:
            self.actions.say('later', 'req_one')
        self.assertEqual(error.exception.code, 'already_answered')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.tool.glob('popup-*')})

    def test_concurrent_completers_only_one_wins(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.actions._complete, 'req_one', 'pet_typed', str(i)) for i in range(4)]
            results = []
            for future in futures:
                try:
                    results.append(future.result())
                except ActionError as exc:
                    results.append(exc.code)
        self.assertEqual(results.count(True), 1, results)
        self.assertEqual(results.count('already_answered'), 3, results)

    def test_raw_exit_code_three(self):
        self.actions.done('req_one')
        result = subprocess.run([POWERSHELL, '-NoProfile', '-File', str(self.tool / 'Complete-MuseWait.ps1'),
                                 '-Kind', 'info', '-ReqId', 'req_one'], creationflags=FLAGS, timeout=10,
                                capture_output=True)
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_say_wait_unicode_empty_and_shell_characters(self):
        text = '中文 "quotes" & $HOME `tick`\nsecond line 😀'
        self.assertEqual(self.actions.say(text, 'req_one'), dict(via='wait', req_id='req_one'))
        self.assertEqual((self.tool / 'popup-result.txt').read_text(encoding='utf-8'), text)
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'pet_typed'))

    def test_say_bridge_real_script_in_temporary_tool(self):
        text = '你好 & "quoted"\nhello'
        result = self.actions.say(text, 'req_old')
        self.assertEqual(result['via'], 'bridge')
        path = self.tool / 'muse-bridge/inbox' / (result['req_id'] + '.json')
        posted = json.loads(path.read_text(encoding='utf-8-sig'))
        self.assertEqual(posted['text'], text)
        self.assertEqual(posted['thread_id'], 'pet/chat')
        self.assertEqual(posted['from'], 'pet')
        self.assertFalse((self.tool / 'popup-done.txt').exists())

    def test_session_and_mailbox_launch(self):
        # app binds this method to its in-process MailboxWindow controller.
        with self.assertRaises(ActionError):
            self.actions.open_mailbox('pet/chat')
        chrome = str(self.tool / 'chrome.exe')
        Path(chrome).write_bytes(b'')
        apps = [dict(name='TikTok', browser=chrome, profile='Default', app_id='n' * 32),
                dict(name='Muse', browser=chrome, profile='Default', app_id='a' * 32),
                dict(name='Muse', browser=chrome, profile='Profile 4', app_id='b' * 32)]
        def session(config, installed, popen_ok=True):
            opener, popen = mock.Mock(return_value=True), mock.Mock()
            if not popen_ok:
                popen.side_effect = OSError
            Actions(self.tool, config, opener=opener, web_apps=lambda: installed, popen=popen).open_session()
            return opener, popen
        # 1. the installed Muse app; the configured profile's copy first
        opener, popen = session({}, apps)
        self.assertEqual(popen.call_args.args[0], [chrome, '--profile-directory=Default', '--app-id=' + 'a' * 32])
        self.assertEqual(popen.call_args.kwargs.get('creationflags'), DETACH_FLAGS)
        opener.assert_not_called()
        _o, popen = session(dict(session_profile='Profile 4'), apps)
        self.assertEqual(popen.call_args.args[0][1:], ['--profile-directory=Profile 4', '--app-id=' + 'b' * 32])
        # 2. no app: the configured browser and profile open the session URL
        _o, popen = session(dict(session_browser=chrome, session_profile='Profile 4'), apps[:1])
        self.assertEqual(popen.call_args.args[0], [chrome, '--profile-directory=Profile 4', 'https://muse.ai/'])
        # 3. nothing configured: the default browser; app launch failure falls through too
        opener, popen = session({}, [])
        opener.assert_called_once_with('https://muse.ai/')
        popen.assert_not_called()
        opener, _p = session(dict(session_url='https://example.test/chat'), apps, popen_ok=False)
        opener.assert_called_once_with('https://example.test/chat')
        for bad in (dict(session_url='file:///secret'), dict(session_browser='chrome.exe'),
                    dict(session_browser=str(self.tool / 'missing.exe')), dict(session_profile='..\\x')):
            with self.assertRaises(ActionError):
                session(bad, [])

    def _until(self, predicate, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.05)
        self.fail('Popup integration timeout: ' + str(self._popup_state()) + ' ' +
                  (self.tool / 'popup-stderr.log').read_text(errors='replace') + ' ' +
                  ((self.tool / 'popup-test-error.txt').read_text(errors='replace')
                   if (self.tool / 'popup-test-error.txt').exists() else ''))

    def _popup(self, mode, layer_failure=None, expires=None):
        # Instrument this temporary application's own event loop; no UIAutomation,
        # desktop clicks, scheduled tasks, production bus, or network services.
        for name, value in {'popup-reqid.txt': 'req_one', 'popup-mode.txt': mode,
                            'popup-text.txt': 'Sandbox pet interoperability',
                            'popup-options.txt': 'Option A\nOption B' if mode == 'choice' else ''}.items():
            (self.tool / name).write_text(value, encoding='utf-8')
        if expires:
            (self.tool / 'popup-expires-in.txt').write_text(str(expires))
        source = (ROOT / 'pc/popup.ps1').read_text(encoding='ascii')
        if layer_failure == 'startup':
            source = source.replace('        Initialize-AnsweredStampType', "        throw 'test layer startup failure'")
        elif layer_failure == 'paint':
            source = source.replace('        public void Draw(Graphics g, double ms) {\n',
                                    '        public void Draw(Graphics g, double ms) {\n'
                                    '            if (ms > 100) throw new Exception("test paint failure");\n')
        # Count attempts without adding test knobs to the production script.
        source = source.replace('    $script:AnswerFeedbackStarted = $true',
                                '    $script:TestStarts++; $script:TestForeground = [MusePopup.Native]::GetForegroundWindow()\n'
                                '    $script:AnswerFeedbackStarted = $true')
        hook = r'''
$script:Form.Add_FormClosed({
    $cleanup = @{ layerGone = ($null -eq $script:StampLayer) }
    Write-MLAtomic (Join-Path $script:Dir 'popup-test-cleanup.json') ($cleanup | ConvertTo-Json -Compress)
})
$script:TestTimer = New-Object System.Windows.Forms.Timer
$script:TestTimer.Interval = 100
$script:TestTimer.Add_Tick({
    try {
        $state = @{ external = $script:ExternalKind; header = $script:HeaderTitle;
            saved = $script:Saved; mode = $script:Mode; starts = $script:TestStarts;
            layer = ($null -ne $script:StampLayer); ms = $null;
            left = $script:Form.Left; width = $script:Form.Width;
            check = @($script:Form.Controls | Where-Object { $_ -is [Windows.Forms.Label] -and $_.Text -eq ([string][char]0x2713) }).Count }
        if ($script:StampLayer) {
            $state.ms = $script:StampLayer.ElapsedMs
            $state.owner = ($script:StampLayer.Parent -eq $script:Form)
            $state.aligned = ($script:StampLayer.Bounds -eq $script:Form.ClientRectangle)
            $state.top = ($script:Form.Controls.GetChildIndex($script:StampLayer) -eq 0)
            $state.focusKept = ($script:TestForeground -eq [MusePopup.Native]::GetForegroundWindow())
        }
        Write-MLAtomic (Join-Path $script:Dir 'popup-test-state.json') ($state | ConvertTo-Json -Compress)
        $command = Join-Path $script:Dir 'popup-test-command.json'
        if (Test-Path -LiteralPath $command) {
            $c = [IO.File]::ReadAllText($command) | ConvertFrom-Json
            Remove-Item -LiteralPath $command
            if ($c.action -eq 'submit') { Finish-Popup 'input' $c.text }
            elseif ($c.action -eq 'double') {
                Finish-Popup 'input' $c.text; Finish-Popup 'input' 'must not overwrite'; Sync-ExternalAnswer
                $script:TextBox.Text = 'typing during feedback must not recreate the draft'
            }
            elseif ($c.action -eq 'move') { $script:Form.Left += 17; $script:Form.Width += 12 }
            elseif ($c.action -eq 'close') { $script:Form.Close() }
        }
    } catch { [IO.File]::WriteAllText((Join-Path $script:Dir 'popup-test-error.txt'), $_.ToString()) }
})
$script:TestTimer.Start()
'''
        source = source.replace('[System.Windows.Forms.Application]::Run($form)', hook + '\n[System.Windows.Forms.Application]::Run($form)')
        script = self.tool / 'popup.ps1'
        script.write_text(source, encoding='ascii')
        out = (self.tool / 'popup-stdout.log').open('w')
        err = (self.tool / 'popup-stderr.log').open('w')
        self.addCleanup(out.close)
        self.addCleanup(err.close)
        process = subprocess.Popen([POWERSHELL, '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass',
                                    '-File', str(script)], creationflags=FLAGS, stdout=out, stderr=err)
        def cleanup():
            if process.poll() is None:
                process.kill()
                process.wait(5)
        self.addCleanup(cleanup)
        self._until(lambda: (self.tool / 'popup-test-state.json').exists())
        return process

    def _popup_state(self):
        try:
            return json.loads((self.tool / 'popup-test-state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _popup_command(self, action, text=''):
        path = self.tool / 'popup-test-command.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(action=action, text=text)))
        temp.replace(path)

    def test_popup_info_closes_after_pet_done(self):
        process = self._popup('info')
        self.actions.done('req_one')
        self.assertEqual(process.wait(6), 0)
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'pet_done'))

    def test_popup_input_pet_done_stamps_then_closes(self):
        process = self._popup('input')
        self.actions.done('req_one')
        self._until(lambda: self._popup_state().get('layer'))
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'pet_done'))
        state = self._popup_state()
        self.assertEqual(state['check'], 0)
        self.assertTrue(state['owner'] and state['aligned'] and state['top'] and state['focusKept'])
        self._until(lambda: (self._popup_state().get('ms') or 0) >= STAMP_END_MS)
        held = time.monotonic()
        self.assertIsNone(process.poll())
        self.assertEqual(process.wait(6), 0)
        self.assertGreater(time.monotonic() - held, .3)
        self.assertLess(time.monotonic() - held, 2.5)
        self.assertEqual((self.tool / 'popup-result.txt').read_text(), '')
        self.assertFalse((self.tool / 'muse-bridge/inbox').exists())
        self._assert_stamp_cleanup()

    def test_popup_choice_x_after_pet_done_leaves_result_unchanged(self):
        process = self._popup('choice')
        self.actions.done('req_one')
        self._until(lambda: self._popup_state().get('layer'))
        self._popup_command('close')
        self.assertEqual(process.wait(6), 0)
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'pet_done'))
        self.assertEqual((self.tool / 'popup-result.txt').read_text(), '')
        self.assertFalse((self.tool / 'muse-bridge/inbox').exists())
        self._assert_stamp_cleanup()

    def test_popup_pet_typed_layer_then_animation_hold_close(self):
        process = self._popup('input')
        self.actions.say('Answer from pet', 'req_one')
        self._until(lambda: self._popup_state().get('layer'))
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'pet_typed'))
        state = self._popup_state()
        self.assertEqual(state['check'], 0)
        self.assertTrue(state['owner'] and state['aligned'] and state['top'] and state['focusKept'])
        self._until(lambda: (self._popup_state().get('ms') or 0) >= STAMP_END_MS)
        held = time.monotonic()
        self.assertIsNone(process.poll())
        self.assertEqual(process.wait(6), 0)
        self.assertGreater(time.monotonic() - held, .3)
        self.assertLess(time.monotonic() - held, 2.5)
        self.assertEqual((self.tool / 'popup-result.txt').read_text(), 'Answer from pet')
        self._assert_stamp_cleanup()

    def _assert_stamp_cleanup(self):
        self.assertEqual(json.loads((self.tool / 'popup-test-cleanup.json').read_text()),
                         dict(layerGone=True))

    def test_popup_local_stamp_once_and_x_closes_mid_stamp(self):
        process = self._popup('input')
        self._popup_command('double', 'Persist before feedback')
        self._until(lambda: self._popup_state().get('layer'))
        self.assertEqual((self.tool / 'popup-result.txt').read_text(), 'Persist before feedback')
        self.assertEqual(self._popup_state()['starts'], 1)
        self.assertFalse((self.tool / 'popup-draft.txt').exists())
        self.assertTrue(self._popup_state()['focusKept'] and self._popup_state()['aligned'])
        self._popup_command('close')
        self.assertEqual(process.wait(6), 0)
        self._assert_stamp_cleanup()
        self.assertEqual((self.tool / 'popup-result.txt').read_text(), 'Persist before feedback')

    def test_popup_layer_failures_close_external_with_saved_answer(self):
        for failure in ('startup', 'paint'):
            with self.subTest(failure=failure):
                process = self._popup('input', layer_failure=failure)
                self.actions.say('Saved external', 'req_one')
                self.assertEqual(process.wait(6), 0)
                self.assertEqual(done_info(self.tool)[::2], ('req_one', 'pet_typed'))
                self.assertEqual((self.tool / 'popup-result.txt').read_text(), 'Saved external')
                self._assert_stamp_cleanup()
                for path in self.tool.glob('popup-*.txt'):
                    if path.name not in ('popup-pending.txt',):
                        path.unlink()
                for path in self.tool.glob('popup-test-*.json'):
                    path.unlink()

    def test_popup_layer_failures_close_local_with_saved_answer(self):
        for failure in ('startup', 'paint'):
            with self.subTest(failure=failure):
                process = self._popup('input', layer_failure=failure)
                self._popup_command('submit', 'Saved local')
                self.assertEqual(process.wait(8), 0)
                self.assertEqual(done_info(self.tool)[::2], ('req_one', 'input'))
                self.assertEqual((self.tool / 'popup-result.txt').read_text(), 'Saved local')
                self._assert_stamp_cleanup()
                for path in self.tool.glob('popup-*.txt'):
                    if path.name not in ('popup-pending.txt',):
                        path.unlink()
                for path in self.tool.glob('popup-test-*.json'):
                    path.unlink()

    def test_popup_expired_has_no_stamp(self):
        process = self._popup('input', expires=1)
        self.assertEqual(process.wait(6), 0)
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'expired'))
        self.assertFalse(self._popup_state()['layer'])
        self.assertIsNone(self._popup_state()['starts'])
        self._assert_stamp_cleanup()

    def test_popup_x_without_answer_has_no_stamp(self):
        process = self._popup('input')
        self._popup_command('close')
        self.assertEqual(process.wait(6), 0)
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'input'))
        self.assertEqual((self.tool / 'popup-result.txt').read_text(), '')
        self.assertFalse(self._popup_state()['layer'])
        self.assertIsNone(self._popup_state()['starts'])
        self._assert_stamp_cleanup()

    def test_popup_answer_remains_popup_kind(self):
        process = self._popup('input')
        self._popup_command('submit', 'Answer from popup')
        self._until(lambda: self._popup_state().get('layer'))
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'input'))
        self.assertEqual(process.wait(8), 0)
        self.assertEqual(done_info(self.tool)[::2], ('req_one', 'input'))
        self.assertEqual((self.tool / 'popup-result.txt').read_text(), 'Answer from popup')

    def test_stamp_frames_veil_badge_and_check(self):
        source = (ROOT / 'pc/popup.ps1').read_text(encoding='ascii')
        initialize = source[source.index('function Initialize-AnsweredStampType'):
                            source.index('function Stop-AnsweredStamp')]
        script = self.tool / 'stamp-frame-test.ps1'
        script.write_text("$ErrorActionPreference='Stop'\nAdd-Type -AssemblyName System.Windows.Forms\n"
                          "Add-Type -AssemblyName System.Drawing\n" + initialize + r'''
Initialize-AnsweredStampType
$card = New-Object Windows.Forms.Form
$card.FormBorderStyle = 'None'; $card.ClientSize = [Drawing.Size]::new(360, 300); $card.BackColor = [Drawing.Color]::White
$ink = New-Object Windows.Forms.Panel
$ink.Bounds = [Drawing.Rectangle]::new(10, 10, 80, 40); $ink.BackColor = [Drawing.Color]::Black
$card.Controls.Add($ink)
[void]$card.Handle; [void]$ink.Handle
$stamp = New-Object MusePopup.AnsweredStamp ($card, [single]1)
$out = @{ parent = ($stamp.Parent -eq $card); bounds = ($stamp.Bounds -eq $card.ClientRectangle);
          front = ($card.Controls.GetChildIndex($stamp) -eq 0);
          end = [MusePopup.AnsweredStamp]::EndMs; hold = [MusePopup.AnsweredStamp]::HoldMs }
foreach ($ms in 0, 2000) {
    $b = New-Object Drawing.Bitmap 360, 300
    $g = [Drawing.Graphics]::FromImage($b)
    $stamp.Draw($g, $ms); $g.Dispose()
    $green = 0; $white = 0
    for ($y = 110; $y -lt 190; $y++) { for ($x = 140; $x -lt 220; $x++) {
        $p = $b.GetPixel($x, $y)
        if ($p.G -gt 180 -and $p.R -lt 90 -and $p.B -lt 120) { $green++ }
        if ($p.R -gt 245 -and $p.G -gt 245 -and $p.B -gt 245) { $white++ } } }
    $out["f$ms"] = @{ ink = $b.GetPixel(50, 30).R; paper = $b.GetPixel(300, 280).R; green = $green; white = $white }
    $b.Dispose()
}
$out | ConvertTo-Json -Depth 4 -Compress
$stamp.Dispose(); $card.Dispose()
''', encoding='ascii')
        result = subprocess.run([POWERSHELL, '-NoProfile', '-STA', '-File', str(script)],
                                creationflags=FLAGS, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data['parent'] and data['bounds'] and data['front'])   # inside the card, on top
        self.assertEqual((data['end'], data['hold']), (STAMP_END_MS, 1000))
        before, after = data['f0'], data['f2000']
        self.assertEqual((before['ink'], before['green']), (0, 0))              # nothing before it starts
        self.assertTrue(40 < after['ink'] < 140, after)                         # veiled, still readable
        self.assertGreater(after['paper'], 245)
        self.assertGreater(after['green'], 2500)                                # solid badge in the middle
        self.assertGreater(after['white'], 150)                                 # white check on it
    def test_staging_armed_matches_pending_and_clips_first_line(self):
        source = (ROOT / 'pc/popup-stage.ps1').read_text(encoding='ascii')
        # The real scheduled task must never be triggered by this sandbox test.
        source = source.replace('schtasks /Run /TN popup | Out-Null', "Write-Output 'TEST_TRIGGER'")
        stage = self.tool / 'popup-stage.ps1'
        stage.write_text(source, encoding='ascii')
        text = 'Question 😀 ' + '长' * 160 + '\nsecond line'
        (self.tool / 'popup-text.txt').write_bytes(text.encode('utf-8'))
        (self.tool / 'popup-mode.txt').write_text('input')
        (self.tool / 'popup-expected-hash.txt').write_text('popup-text.txt ' + hashlib.sha256(text.encode()).hexdigest())
        result = self.actions._script(stage, '-ReqId', 'req_staged')
        self.assertEqual(result.returncode, 0, result.stderr)
        armed = json.loads((self.tool / 'popup-armed.json').read_text(encoding='utf-8'))
        self.assertEqual(set(armed), {'req_id', 'mode', 'summary', 'at'})
        self.assertEqual((armed['req_id'], armed['mode']), ('req_staged', 'input'))
        self.assertLessEqual(len(armed['summary']), 120)
        self.assertIn('😀', armed['summary'])
        self.assertNotIn('second line', armed['summary'])
        self.assertEqual(self.pending.read_text().strip(), 'req_staged')

    def test_confirmation_sound_belongs_to_visible_live_pet(self):
        pet = self.tool / 'pet'
        pet.mkdir()
        render(pet / 'pet-confirm.wav')
        source = (ROOT / 'pc/popup.ps1').read_text(encoding='ascii')
        start = source.index('function Play-ConfirmSound')
        end = source.index('function Sync-ExternalAnswer')
        sound = source[start:end].replace('$script:ConfirmPlayer.Play()', '$script:SoundInvoked = $true')
        script = self.tool / 'sound-test.ps1'
        script.write_text(". (Join-Path $PSScriptRoot 'muselink-common.ps1')\n$script:Dir=$PSScriptRoot\n"
                          "function Read-Utf8([string]$name) { Read-MLUtf8 (Join-Path $script:Dir $name) }\n" + sound +
                          "Play-ConfirmSound\nif ($script:SoundInvoked) { Write-Output 'PLAY' } else { Write-Output 'QUIET' }\n", encoding='ascii')
        for visible, seconds, expected in [(True, 0, 'QUIET'), (False, 0, 'PLAY'), (True, 10, 'PLAY')]:
            stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
            (pet / 'pet-presence.json').write_text(json.dumps(dict(visible=visible, at=stamp)))
            result = self.actions._script(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)

    def test_popup_preserves_lf_paragraphs_and_tight_message_height(self):
        text = ('Setup check: two windows stopped at **step 2** waiting for details\n\n'
                'Primary window (Account A): click「Add name and address」to fill profile and billing details\nThen click「Start free trial」\n\n'
                'Secondary window (Account B): click「Edit」beside contact info to complete address and card details\nThen click「Start free trial」')
        (self.tool / 'popup-reqid.txt').write_text('req_lf', encoding='utf-8')
        (self.tool / 'popup-mode.txt').write_text('info', encoding='utf-8')
        (self.tool / 'popup-text.txt').write_bytes(text.encode('utf-8'))
        (self.tool / 'popup-options.txt').write_text('', encoding='utf-8')
        source = (ROOT / 'pc/popup.ps1').read_text(encoding='ascii')
        probe = r'''
$script:TestTimer = New-Object System.Windows.Forms.Timer
$script:TestTimer.Interval = 120
$script:TestTimer.Add_Tick({
    param($s, $e)
    $s.Stop()
    $msg = $script:Form.Controls | Where-Object { $_.AccessibleName -eq 'Message' } | Select-Object -First 1
    $lines = $msg.GetLineFromCharIndex($msg.TextLength) + 1
    $step = $msg.Font.Height
    if ($lines -ge 2) {
        $d = $msg.GetPositionFromCharIndex($msg.GetFirstCharIndexFromLine(1)).Y - $msg.GetPositionFromCharIndex($msg.GetFirstCharIndexFromLine(0)).Y
        if ($d -gt $step) { $step = $d }
    }
    $expectedH = $lines * $step
    $idxStep = $msg.Text.IndexOf('step 2')
    $isBold = $false
    if ($idxStep -ge 0) { $msg.Select($idxStep, 6); $isBold = $msg.SelectionFont.Bold }
    $out = @{ lines = $lines; height = $msg.Height; expectedH = $expectedH; hasLf = $msg.Text.Contains("`n"); hasBold = $isBold; stripped = (-not $msg.Text.Contains('**')) }
    Write-MLAtomic (Join-Path $script:Dir 'popup-lf-probe.json') ($out | ConvertTo-Json -Compress)
    $script:Form.Close()
})
$script:TestTimer.Start()
[System.Windows.Forms.Application]::Run($form)
'''
        script = self.tool / 'popup-lf.ps1'
        script.write_text(source.replace('[System.Windows.Forms.Application]::Run($form)', probe), encoding='ascii')
        result = self.actions._script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.tool / 'popup-lf-probe.json').read_text(encoding='utf-8-sig'))
        self.assertTrue(data['hasLf'])
        self.assertTrue(data['hasBold'])
        self.assertTrue(data['stripped'])
        self.assertGreaterEqual(data['lines'], 7)
        self.assertLessEqual(abs(data['height'] - data['expectedH']), 8)


if __name__ == '__main__':
    unittest.main()
