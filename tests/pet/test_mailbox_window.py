"""Pet-owned window lifecycle and failure isolation; no live mailbox IO."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pc'))
from pet.mailbox_window import MailboxWindow
from pet.actions import ActionError
from pet.api import Api

class MailboxWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        config=self.directory/'config.json'
        config.write_text(json.dumps({'bridge':{'root':str(self.directory/'bridge')}}))
        self.owner=MailboxWindow(config,self.directory)
        patch=mock.patch('pet.mailbox_window.static_server_class')
        server=patch.start();self.addCleanup(patch.stop)
        server.return_value.start_server.return_value=('http://fixture/',str(self.directory),mock.Mock(address='http://fixture/'))

    def test_reuses_one_child_closes_to_hide_and_stops_with_pet(self):
        with mock.patch('pet.mailbox_window.Syncer') as sync, mock.patch('webview.create_window') as create:
            self.owner.start();self.owner.start();sync.assert_called_once()
            sync.return_value.attention=4
            self.assertEqual(self.owner.unread(),4)
            self.owner.open('pet/chat');window=create.return_value
            self.assertEqual(create.call_args.args[1],'http://fixture/index.html')
            self.assertNotIn('tray',sys.modules)
            self.owner._ready=True;self.owner.open('pet/other')
            create.assert_called_once();window.show.assert_called_once()
            self.assertIn('pet/other',window.evaluate_js.call_args.args[0])
            self.assertFalse(self.owner._closing());window.hide.assert_called_once()
            self.owner.minimize();window.minimize.assert_called_once()
            self.owner.open();self.assertEqual(create.call_count,1)
            self.owner.stop();window.destroy.assert_called_once();sync.return_value.stop.assert_called_once()
            self.assertTrue(self.owner._closing())

    def test_failed_open_is_contained_retryable_and_api_reports_error(self):
        with mock.patch('pet.mailbox_window.Syncer'),mock.patch('webview.create_window',side_effect=[RuntimeError('private'),mock.MagicMock()]):
            with self.assertRaises(ActionError):self.owner.open()
            self.assertIsNone(self.owner.window)
            self.owner.open();self.assertIsNotNone(self.owner.window)
            self.owner._ready=True;self.owner.window.show.side_effect=RuntimeError('private')
            actions=mock.Mock();actions.open_mailbox=self.owner.open
            api=Api(mock.Mock(),actions,mock.Mock(),mailbox=self.owner)
            result=api.open_mailbox();self.assertFalse(result['ok'])
            self.assertEqual(result['code'],'open_failed');self.assertNotIn('private',str(result))
            self.owner.stop()

    def test_sync_failure_does_not_break_pet_state(self):
        with mock.patch('pet.mailbox_window.Syncer',side_effect=OSError):
            self.owner.start();self.assertEqual(self.owner.unread(),0)
            watch=mock.Mock();watch.snapshot.return_value={'state':'idle'}
            api=Api(watch,mock.Mock(),mock.Mock(),mailbox=self.owner)
            self.assertEqual(api.state()['data'],{'state':'idle','unread':0})

if __name__=='__main__':unittest.main()
