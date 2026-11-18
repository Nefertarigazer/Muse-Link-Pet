"""Source and installed pets must observe only their own executor streams."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'pc'))
from pet.app import event_directory, instance_scope, source_layout
from mailbox.shell import Instance


class RuntimeTests(unittest.TestCase):
    def test_event_directory_does_not_depend_on_log_or_current_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pet = root / 'pc' / 'pet'
            pet.mkdir(parents=True)
            self.assertEqual(event_directory(pet), pet.parent)
            (root / '.git').write_text('gitdir: example')
            self.assertEqual(event_directory(pet), pet.parent)  # unrelated Git ancestor
            (root / '.git').unlink()
            (root / '.git').mkdir()
            self.assertEqual(event_directory(pet), pet.parent)
            (root / 'vm').mkdir()
            (root / 'tools').mkdir()
            (root / 'tools/check-public.py').touch()
            self.assertEqual(event_directory(pet), pet)
            (root / '.git').rmdir()
            self.assertEqual(event_directory(pet), pet)  # source archive

    def test_singleton_follows_popup_bus_layout_not_event_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source/pc/pet'
            source.mkdir(parents=True)
            (root / 'source/vm').mkdir()
            (root / 'source/tools').mkdir()
            (root / 'source/tools/check-public.py').touch()
            installed = root / 'installed/pet'
            installed.mkdir(parents=True)
            self.assertTrue(source_layout(source))
            self.assertFalse(source_layout(installed))
            scope = instance_scope(source)
            self.assertIsNotNone(scope)
            self.assertIsNone(instance_scope(installed))
            # Neither an override pointing at pc/ nor one pointing elsewhere
            # participates in the singleton scope selection.
            for events in (source, source.parent, installed.parent, root / 'other'):
                with self.subTest(events=events):
                    from pet import app
                    with mock.patch.object(app.ctypes.windll.shell32, 'IsUserAnAdmin', return_value=False), \
                            mock.patch.object(app, '_runtime_available', return_value=True), \
                            mock.patch.object(app, 'instance_scope', side_effect=lambda: instance_scope(source)), \
                            mock.patch.object(app, 'Instance') as factory:
                        factory.return_value.primary = False
                        self.assertEqual(app.main(['--events-dir', str(events)]), 0)
                        factory.assert_called_once_with('pet', scope=scope)
                    with mock.patch.object(app.ctypes.windll.shell32, 'IsUserAnAdmin', return_value=False), \
                            mock.patch.object(app, '_runtime_available', return_value=True), \
                            mock.patch.object(app, 'instance_scope', side_effect=lambda: instance_scope(installed)), \
                            mock.patch.object(app, 'Instance') as factory:
                        factory.return_value.primary = False
                        self.assertEqual(app.main(['--events-dir', str(events)]), 0)
                        factory.assert_called_once_with('pet', scope=None)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows single-instance mutex')
    def test_instance_scope_isolates_mutex_pipe_and_key_but_keeps_pet_protocol(self):
        with mock.patch('mailbox.shell.ctypes.WinDLL') as dll, mock.patch.object(Instance, '_key', return_value=b'test'):
            dll.return_value.CreateMutexW.return_value = 1
            installed = Instance('pet')
            first_mutex = dll.return_value.CreateMutexW.call_args.args[2]
            source = Instance('pet', scope='checkout-one')
            second_mutex = dll.return_value.CreateMutexW.call_args.args[2]
            other = Instance('pet', scope='checkout-two')
            again = Instance('pet', scope='checkout-one')
            self.assertNotEqual(first_mutex, second_mutex)
            self.assertEqual(source.name, 'pet')
            self.assertNotEqual(source.address, installed.address)
            self.assertNotEqual(source._key_name, installed._key_name)
            self.assertNotEqual(source.address, other.address)
            self.assertEqual(source.address, again.address)
            self.assertEqual(source._key_name, again._key_name)

    def test_diag_log_rotates_and_sanitizes_tracebacks(self):
        from pet.app import _DiagLog
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'pet-runtime.log'
            path.write_text('x' * 200, encoding='utf-8')
            diag = _DiagLog(path, max_bytes=100)
            diag.open()
            try:
                diag.install_hooks()
                diag.write('hello')
                try:
                    raise ValueError('PRIVATE_SECRET_VALUE')
                except ValueError:
                    diag._write_tb(sys.exc_info()[2])
            finally:
                diag.close()
            self.assertTrue(path.with_suffix('.log.1').exists())
            text = path.read_text(encoding='utf-8')
            self.assertIn('hello', text)
            self.assertNotIn('PRIVATE_SECRET_VALUE', text)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows launcher wait')
    def test_pet_ps1_does_not_hang_on_detached_grandchild(self):
        import os, subprocess, time
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='muselink-pet-ps1-') as temp:
            tdir = Path(temp)
            (tdir / 'pet').mkdir()
            pyw = Path(sys.executable).with_name('pythonw.exe')
            if not pyw.is_file():
                pyw = Path(sys.executable)
            src_ps1 = (root / 'pc/pet/pet.ps1').read_text(encoding='ascii')
            patched = src_ps1.replace(
                "$python = Join-Path (Split-Path -Parent $PSScriptRoot) 'mailbox\\.venv\\Scripts\\pythonw.exe'",
                "$python = '%s'" % str(pyw).replace("'", "''"),
            )
            (tdir / 'pet/pet.ps1').write_text(patched, encoding='ascii')
            (tdir / 'pet/app.py').write_text(
                "import subprocess, sys, time\n"
                "from pathlib import Path\n"
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'],\n"
                "                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))\n"
                "Path(__file__).with_name('grand.pid').write_text(str(p.pid), encoding='ascii')\n"
                "time.sleep(0.2)\n",
                encoding='ascii',
            )
            env = dict(os.environ, TEMP=str(tdir), TMP=str(tdir))
            t0 = time.monotonic()
            try:
                res = subprocess.run(
                    ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(tdir / 'pet/pet.ps1'), '--flag'],
                    capture_output=True, text=True, timeout=10, env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                elapsed = time.monotonic() - t0
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertLess(elapsed, 6.0)
                self.assertEqual(list(tdir.glob('pet-args-*.json')), [])
            finally:
                gpid = tdir / 'pet/grand.pid'
                if gpid.exists():
                    subprocess.run(['taskkill', '/F', '/PID', gpid.read_text().strip()], capture_output=True)


if __name__ == '__main__':
    unittest.main()
