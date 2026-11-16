"""Background rebuild/sync, busy skip, unavailable source and responsiveness."""
import concurrent.futures
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "threads"))
from _mailbox import Mailbox
from mlindex import indexer, query, store
from mailbox.syncer import Syncer


class SyncerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mb = Mailbox(Path(self.temp.name) / "bridge")
        self.mb.write_request("test", text="synthetic")
        self.index = str(Path(self.temp.name) / "index/threads.sqlite")
        self.syncer = Syncer(self.mb.root, self.index, interval=3600)
        self.addCleanup(self.syncer.stop)

    def test_missing_index_rebuild_then_incremental(self):
        with mock.patch.object(indexer, "rebuild", wraps=indexer.rebuild) as rebuild:
            self.assertEqual(self.syncer.refresh()["messages"], 1)
            self.assertEqual(rebuild.call_count, 1)
        self.mb.write_reply("test", text="new synthetic")
        with mock.patch.object(indexer, "sync", wraps=indexer.sync) as sync:
            self.assertEqual(self.syncer.refresh()["messages"], 2)
            self.assertEqual(sync.call_count, 1)
        self.assertIsNotNone(self.syncer.last_ok)
        self.assertIsNone(self.syncer.status()["last_error"])

    def test_unread_cache_follows_reply_archive_and_never_reads_on_access(self):
        self.mb.write_reply('test',text='unread fixture')
        self.syncer.refresh()
        self.assertEqual(self.syncer.unread,1)
        source=next((Path(self.mb.root)/'outbox').glob('*.reply.json'))
        source.rename(Path(self.mb.root)/'archive'/source.name)
        self.syncer.refresh()
        with mock.patch.object(query,'list_threads',side_effect=AssertionError('unread performed IO')):
            self.assertEqual(self.syncer.unread,0)

    def test_attention_ignores_routine_replies_and_clears_on_seen_or_resolve(self):
        self.mb.write_request("r_ok", "claimed", from_="codex", thread_id="codex/chat")
        self.mb.write_reply("r_ok", 1, "outbox", status="info", text="working")
        self.syncer.refresh()
        self.assertEqual(self.syncer.unread, 1)
        self.assertEqual(self.syncer.attention, 0)
        self.assertEqual(self.syncer.thread_attention("codex/chat"), 0)

        self.mb.write_request("r_wait", "claimed", from_="codex", thread_id="codex/chat")
        self.mb.write_reply("r_wait", 1, "outbox", status="needs-user", final=True, text="confirm?")
        self.syncer.refresh()
        self.assertEqual(self.syncer.attention, 1)
        self.assertEqual(self.syncer.thread_attention("codex/chat"), 1)

        self.assertEqual(self.syncer.mark_seen(["codex/chat"]), 0)
        self.assertEqual(self.syncer.attention, 0)
        self.assertEqual(self.syncer.thread_attention("codex/chat"), 0)
        restarted = Syncer(self.mb.root, self.index, interval=3600)
        self.addCleanup(restarted.stop)
        self.assertEqual(restarted.attention, 0)

        self.mb.write_reply("r_wait", 2, "outbox", status="needs-user", final=True, text="second ask")
        restarted.refresh()
        self.assertEqual(restarted.attention, 1)

        self.mb.write_reply("r_wait", 3, "archive", status="ok", final=True, text="done")
        restarted.refresh()
        self.assertEqual(restarted.attention, 0)

    def test_delete_thread_moves_files_to_trash_and_updates_index(self):
        self.mb.write_request("d1", "sent", from_="kiro", thread_id="kiro/chat", text="hi")
        self.mb.write_reply("d1", 1, "outbox", status="needs-user", final=True, text="confirm?", agent="kiro")
        self.mb.write_ack("d1", 1)
        self.syncer.refresh()
        self.assertEqual(self.syncer.attention, 1)
        res = self.syncer.delete_thread(["kiro/chat"])
        self.assertGreaterEqual(res["deleted"], 3)
        self.assertEqual(res["unread"], 0)
        self.assertEqual(self.syncer.attention, 0)
        self.assertNotIn("kiro/chat", [t["id"] for t in query.list_threads(self.index)])
        self.assertTrue(list((Path(self.mb.root) / "trash").rglob("d1*")))

    def test_schema_mismatch_rebuild(self):
        self.syncer.refresh()
        original = query.stats
        def old_schema(path):
            result = original(path)
            result["schema_version"] = -1
            return result
        with mock.patch.object(query, "stats", side_effect=old_schema), mock.patch.object(indexer, "rebuild", wraps=indexer.rebuild) as rebuild:
            self.syncer.refresh()
            self.assertEqual(rebuild.call_count, 1)

    def test_writer_busy_skip_without_error(self):
        with store.Lock(self.index):
            status = self.syncer.refresh()
        self.assertIsNone(status["last_error"])
        self.assertFalse(status["syncing"])
        self.assertIsNone(self.syncer.last_ok)
        self.assertEqual(self.syncer.refresh()["messages"], 1)
        last_ok = self.syncer.last_ok
        with store.Lock(self.index):
            self.assertIsNone(self.syncer.refresh()["last_error"])
        self.assertEqual(self.syncer.last_ok, last_ok)

    def test_source_unreadable_retains_snapshot_and_recovers(self):
        before = self.syncer.refresh()
        with mock.patch("mailbox.syncer.os.scandir", side_effect=PermissionError("private path")), mock.patch.object(indexer, "sync") as sync:
            status = self.syncer.refresh()
            sync.assert_not_called()
        self.assertTrue(status["last_error"])
        self.assertNotIn("private path", status["last_error"])
        self.assertEqual(status["messages"], before["messages"])
        self.assertFalse(status["syncing"])
        self.assertIsNone(self.syncer.refresh()["last_error"])

    def test_refresh_coalesces_and_status_does_not_block(self):
        self.syncer.refresh()
        entered, release = threading.Event(), threading.Event()
        def slow(*unused):
            entered.set()
            self.assertTrue(release.wait(5))
        with mock.patch.object(indexer, "sync", side_effect=slow) as sync:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                first = pool.submit(self.syncer.refresh)
                self.assertTrue(entered.wait(3))
                second = pool.submit(self.syncer.refresh)
                time.sleep(.05)
                start = time.perf_counter()
                self.assertTrue(self.syncer.status()["syncing"])
                self.assertLess(time.perf_counter() - start, .020)
                self.assertFalse(first.done())
                self.assertFalse(second.done())
                release.set()
                self.assertFalse(first.result(3)["syncing"])
                self.assertFalse(second.result(3)["syncing"])
            self.assertEqual(sync.call_count, 1)


if __name__ == "__main__":
    unittest.main()
