"""Opt-in Windows desktop acceptance, in a disposable tool installation.

Run: PYTHONUTF8=1 python tests/explib/acceptance.py
Creates its own tiny WinForms app and unchanged copied executor host. No
production paths, scheduled tasks, network services or user apps are used.
Cards are answered through the copied Complete-MuseWait script, as specified
by the implementer prompt. This script is deliberately outside discovery.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pc"))

from plugin import ask, tools
from plugin._shared import atomic
from explib import share
from explib.store import Store


APP_SOURCE = r'''
using System;
using System.IO;
using System.Windows.Forms;
class Acceptance {
    [STAThread] static void Main(string[] args) {
        Application.EnableVisualStyles();
        var form = new Form { Text = "MuseLink experience acceptance", Width = 600, Height = 240 };
        var text = new RichTextBox { AccessibleName = "Task result", Text = "Ready", Top = 60, Width = 550, Height = 100 };
        var button = new Button { Text = "Open details", AccessibleName = "Open details", Width = 180, Height = 40 };
        button.Click += (s, e) => { text.Text = "Details opened"; File.WriteAllText(args[0], text.Text); };
        form.Controls.Add(button); form.Controls.Add(text);
        Application.Run(form);
    }
}
'''

# Same event-loop probe used by tests/plugin; only the temporary popup copy
# is instrumented. It proves an actual visible form, a silent waiting card,
# and the ownership of the displayed request before completing it.
PROBE = r'''
$script:ProbeTimer = New-Object System.Windows.Forms.Timer
$script:ProbeTimer.Interval = 100
$script:ProbeTimer.Add_Tick({
    $probe = @{ visible = $script:Form.Visible; silent = ($null -eq $script:SoundPs);
                ringing = ($null -ne $script:RingTimer); req = $script:ReqId }
    Write-MLAtomic (Join-Path $script:Dir 'popup-probe.json') ($probe | ConvertTo-Json -Compress)
})
$script:ProbeTimer.Start()
'''


def wait_for(fn, description, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise AssertionError("timed out: " + description)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    evidence = {}
    with tempfile.TemporaryDirectory(prefix="muselink-explib-acceptance-") as directory:
        base = Path(directory)
        for name in ("executor-host.py", "uia-helper.ps1", "muselink-common.ps1", "popup.ps1",
                     "Complete-MuseWait.ps1", "wait-popup.ps1"):
            shutil.copy2(ROOT / "pc" / name, base / name)
        shutil.copytree(ROOT / "pc/i18n", base / "i18n", ignore=shutil.ignore_patterns("__pycache__"))
        popup = base / "popup.ps1"
        popup.write_text(popup.read_text(encoding="ascii").replace(
            "[System.Windows.Forms.Application]::Run($form)", PROBE + "\n[System.Windows.Forms.Application]::Run($form)"), encoding="ascii")
        source, app = base / "App.cs", base / "explib-acceptance.exe"
        source.write_text(APP_SOURCE, encoding="ascii")
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        subprocess.run([str(compiler), "/nologo", "/target:winexe", "/reference:System.Windows.Forms.dll",
                        "/reference:System.Drawing.dll", "/out:" + str(app), str(source)], check=True, capture_output=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        atomic(base / "muselink.json", {"executor": {"port": port}, "bridge": {"root": str(base / "bridge")},
                                        "explib": {"redact": {"literals": []}, "public": {"index_url": "", "repo": ""}}})
        atomic(base / "executor-token.txt", "disposable-acceptance-token")
        atomic(base / "identities/codex.json", {"v": 1, "agent_id": "codex", "display_name": "Codex"})
        atomic(base / "popup-sound.txt", "beep")
        marker = base / "task-result.txt"
        host = subprocess.Popen([sys.executable, str(base / "executor-host.py")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        application = subprocess.Popen([str(app), str(marker)])
        checker = None
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def invoke(name, args):
            result = tools.invoke(name, args, "codex", base)
            assert result["status"] == "ok", result
            return result

        def visible_card():
            pending = base / "popup-pending.txt"
            probe_path = base / "popup-probe.json"
            if pending.exists() and probe_path.exists():
                request = pending.read_text(encoding="utf-8")
                probe = read(probe_path)
                if probe["req"] == request and probe["visible"]:
                    return request, probe
            return None

        def complete(request, answer):
            answer_path = base / "answer.txt"
            answer_path.write_text(answer, encoding="utf-8")
            proc = subprocess.run(ask.ps(base, "Complete-MuseWait.ps1", "-ReqId", request, "-Kind", "choice",
                                          "-ResultFile", answer_path), capture_output=True, timeout=20)
            assert proc.returncode == 0, proc.stderr

        try:
            wait_for(lambda: tools.invoke("ping", {}, "codex", base)["status"] == "ok", "copied host")
            query = {"name": "Open details", "exact": True, "controlType": "Button"}
            located = invoke("uia_locate", {"app": app.name, "query": query})
            assert located["count"] == 1, located
            future = pool.submit(invoke, "uia_invoke", {"app": app.name, "query": query})
            request, _ = wait_for(visible_card, "input permission card")
            complete(request, "30 分钟内免问")
            clicked = future.result(timeout=40)
            assert clicked.get("invoked"), clicked
            assert wait_for(lambda: marker.exists(), "real button side effect")
            assert marker.read_text() == "Details opened"
            verified = invoke("uia_read_text", {"app": app.name, "query": {"name": "Task result", "exact": True}})
            assert verified["text"].strip() == "Details opened", verified
            draft = invoke("exp_record", {"app": app.name, "goal": "Open details and verify the result", "from_trace": True})
            assert draft["entry"]["status"] == "draft"
            confirmed = invoke("exp_record", {"confirm": draft["id"]})
            hit = invoke("exp_lookup", {"app": app.name, "goal": "Open details"})["hits"][0]
            assert hit["entry"]["id"] == draft["id"]
            # Actually use the looked-up method for a second small task.
            marker.unlink()
            invoke("uia_invoke", {"app": app.name, "query": query})
            assert wait_for(lambda: marker.exists(), "second real task")
            verified_again = invoke("uia_read_text", {"app": app.name, "query": {"name": "Task result", "exact": True}})
            assert verified_again["text"].strip() == "Details opened"
            feedback = invoke("exp_feedback", {"id": draft["id"], "worked": True})
            assert feedback["entry"]["works"] == confirmed["entry"]["works"] + 1 == 1
            store = Store(base)
            value = store.get(draft["id"])
            for i in range(4):
                extra = dict(value, id=schema.new_id(), goal=f"Extra verified goal {i}")
                store.put(extra)
            command = [sys.executable, "-m", "explib", "share-check", "--tool-dir", str(base)]
            checker = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                                       env=dict(os.environ, PYTHONPATH=str(ROOT / "pc"), PYTHONUTF8="1"))
            request, probe = wait_for(visible_card, "silent experience share card")
            assert probe["silent"] and not probe["ringing"], probe
            text = (base / "popup-text.txt").read_text(encoding="utf-8")
            assert text.startswith("经验库 问你：\n"), text
            complete(request, share.t(share.NEVER))
            stdout, stderr = checker.communicate(timeout=25)
            assert checker.returncode == 0, stderr
            first = json.loads(stdout)
            assert first == {"status": "ok", "reason": "never"}, first
            state = read(base / "experience/share.json")
            assert state["never"] is True
            second = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=20,
                                    env=dict(os.environ, PYTHONPATH=str(ROOT / "pc"), PYTHONUTF8="1"))
            assert second.returncode == 0
            assert json.loads(second.stdout) == {"status": "ok", "reason": "never"}
            assert not (base / "popup-pending.txt").exists()
            assert (base / "popup-reqid.txt").read_text(encoding="utf-8") == request
            assert not (base / "experience/outbox").exists()
            events = [json.loads(line) for line in (base / "executor-events.jsonl").read_text(encoding="utf-8").splitlines()]
            evidence = dict(status="ok", task="real UIA button invoke + read_text", readback="Details opened",
                            draft_status=draft["entry"]["status"], confirmed_status=confirmed["entry"]["status"],
                            lookup_id_matches=True, works_before=0, works_after=feedback["entry"]["works"],
                            second_task_verified=True,
                            forced_works=2, share_card=probe, first_share_check=first,
                            second_share_check=json.loads(second.stdout), never=state["never"],
                            outbox_exists=False, attributed_host_jobs=[e.get("job") for e in events if e.get("job", "").startswith("plugin:")])
        finally:
            if checker is not None and checker.poll() is None:
                checker.terminate()
                checker.communicate(timeout=10)
            # Release a pending waiter on failure, using only the temporary bus.
            pending = base / "popup-pending.txt"
            if pending.exists():
                subprocess.run(ask.ps(base, "Complete-MuseWait.ps1", "-ReqId", pending.read_text(encoding="utf-8"),
                                       "-Kind", "expired"), capture_output=True, timeout=20)
            pool.shutdown(wait=True)
            for process in (application, host):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)
            close = ". '" + str(base / "muselink-common.ps1").replace("'", "''") + "'; Get-MLPopupProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", close], capture_output=True, timeout=20)
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
