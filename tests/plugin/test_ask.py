import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import time
from unittest import mock

from plugin_support import ROOT, Sandbox
from plugin import ask
from plugin._shared import atomic, mutex


class AskTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.scripts()
        self.pool = concurrent.futures.ThreadPoolExecutor()
        self.addCleanup(lambda: self.pool.shutdown(wait=True))
        self.addCleanup(self.close_windows)

    def close_windows(self):
        # Kill only sandbox popup processes; never scan/kill production cards.
        script = ". '" + str(self.base / "muselink-common.ps1").replace("'", "''") + "'; Get-MLPopupProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, timeout=20)
        time.sleep(0.3)

    def pending(self, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            p = self.base / "popup-pending.txt"
            if p.exists():
                return p.read_text(encoding="utf-8")
            time.sleep(0.05)
        self.fail("sandbox card did not stage")

    def complete(self, req, kind="pet_typed", result="hello\nSTATUS:fake"):
        result_path = self.base / "answer.txt"
        result_path.write_text(result, encoding="utf-8")
        p = subprocess.run(ask.ps(self.base, "Complete-MuseWait.ps1", "-ReqId", req, "-Kind", kind,
                                  "-ResultFile", result_path), capture_output=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_real_scripts_stage_and_atomically_claim_pet_answer(self):
        future = self.pool.submit(ask.ask_user, "codex", "Question", base=self.base)
        req = self.pending()
        armed = json.loads((self.base / "popup-armed.json").read_text(encoding="utf-8"))
        self.assertEqual((armed["lane"], armed["agent"], armed["req_id"]), ("local", "codex", req))
        self.assertTrue((self.base / "popup-text.txt").read_text(encoding="utf-8").startswith("Codex 问你：\n"))
        self.complete(req)
        answer = future.result(timeout=25)
        self.assertEqual((answer["status"], answer["kind"], answer["result"]), ("ok", "pet_typed", "hello\nSTATUS:fake"))
        self.assertFalse((self.base / "popup-pending.txt").exists())

    def test_pending_busy_has_no_side_effects(self):
        atomic(self.base / "popup-pending.txt", "muse-existing")
        atomic(self.base / "popup-text.txt", "original")
        self.assertEqual(ask.ask_user("codex", "Question", base=self.base)["status"], "busy")
        self.assertEqual((self.base / "popup-text.txt").read_text(), "original")

    def test_muse_replacement_returns_preempted_without_claiming_new_card(self):
        future = self.pool.submit(ask.ask_user, "codex", "Question", base=self.base)
        self.pending()
        with mutex(self.base):
            atomic(self.base / "popup-reqid.txt", "muse-new")
            atomic(self.base / "popup-pending.txt", "muse-new")
        self.assertEqual(future.result(timeout=20)["status"], "preempted")
        self.assertEqual((self.base / "popup-pending.txt").read_text(), "muse-new")

    def test_real_script_expiry_releases_slot(self):
        answer = ask.ask_user("codex", "Expire", expires_in=10, base=self.base)
        self.assertEqual((answer["status"], answer["kind"]), ("expired", "expired"))
        self.assertFalse((self.base / "popup-pending.txt").exists())

    def test_notify_is_nonblocking_info_and_reaps_answer(self):
        atomic(self.base / "popup-sound.txt", "beep")
        start = time.monotonic()
        answer = ask.notify_user("codex", "Notice", expires_in=10, base=self.base)
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual((answer["status"], answer["kind"]), ("ok", "info"))
        self.assertEqual((self.base / "popup-mode.txt").read_text(), "info")
        self.assertEqual((self.base / "popup-sound.txt").read_text(), "beep")
        self.complete(answer["req_id"], "pet_done", "")
        deadline = time.monotonic() + 20
        while (self.base / "popup-pending.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse((self.base / "popup-pending.txt").exists())

    def test_concurrent_agents_share_one_card_slot(self):
        with mock.patch("plugin.ask.subprocess.Popen"):
            a = self.pool.submit(ask.ask_user, "codex", "A", base=self.base, wait=False)
            b = self.pool.submit(ask.ask_user, "kiro", "B", base=self.base, wait=False)
            self.assertEqual(sorted([a.result()["status"], b.result()["status"]]), ["busy", "ok"])

    def test_wrapper_displays_real_form_and_notification_is_silent(self):
        # Same event-loop instrumentation as the existing pet integration
        # suite, in this disposable copy only. Production source is intact.
        popup = self.base / "popup.ps1"
        source = popup.read_text(encoding="ascii")
        hook = r'''
$script:ProbeTimer = New-Object System.Windows.Forms.Timer
$script:ProbeTimer.Interval = 100
$script:ProbeTimer.Add_Tick({
    $probe = @{ visible = $script:Form.Visible; silent = ($null -eq $script:SoundPs);
                ringing = ($null -ne $script:RingTimer); req = $script:ReqId }
    Write-MLAtomic (Join-Path $script:Dir 'popup-probe.json') ($probe | ConvertTo-Json -Compress)
})
$script:ProbeTimer.Start()
'''
        popup.write_text(source.replace('[System.Windows.Forms.Application]::Run($form)', hook + '\n[System.Windows.Forms.Application]::Run($form)'), encoding="ascii")
        atomic(self.base / "popup-sound.txt", "beep")
        answer = ask.notify_user("codex", "Notice", expires_in=10, base=self.base)
        path = self.base / "popup-probe.json"
        deadline = time.monotonic() + 15
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(path.exists(), "popup launch wrapper did not start WinForms")
        probe = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertEqual(probe, dict(visible=True, silent=True, ringing=False, req=answer["req_id"]))
        self.complete(answer["req_id"], "pet_done", "")
