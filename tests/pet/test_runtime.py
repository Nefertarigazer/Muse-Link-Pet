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


if __name__ == '__main__':
    unittest.main()
