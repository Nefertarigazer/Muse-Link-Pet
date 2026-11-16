"""Retire a real temporary legacy process, without touching installed state."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == 'nt', 'Windows installer migration')
class InstallerProcessTests(unittest.TestCase):
    def test_cleanup_stops_only_the_temporary_legacy_mailbox(self):
        with tempfile.TemporaryDirectory(prefix='muselink-retire-') as temp:
            root = Path(temp)
            mailbox = root / 'mailbox'
            mailbox.mkdir()
            app = mailbox / 'app.py'
            app.write_text('import time\ntime.sleep(60)\n', encoding='ascii')
            for directory in ('Desktop', 'Programs', 'Startup'):
                (root / directory).mkdir()
            script = root / 'migrate.ps1'
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            script.write_text(
                "$ErrorActionPreference='Stop'\n"
                "function Get-ScheduledTask {}\n"
                '. ' + quote(ROOT / 'pc/mailbox/retire-standalone.ps1') + '\n'
                'Remove-MLStandaloneMailbox -Apply -ToolDir ' + quote(root)
                + ' -Desktop ' + quote(root / 'Desktop')
                + ' -Programs ' + quote(root / 'Programs')
                + ' -Startup ' + quote(root / 'Startup') + '\n', encoding='ascii')
            child = subprocess.Popen([sys.executable, str(app)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                result = subprocess.run(['powershell', '-NoProfile', '-File', str(script)],
                    capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode, 0, result.stderr)
                child.wait(timeout=5)
                self.assertIn('stop legacy mailbox process: ' + str(child.pid), result.stdout)
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)
