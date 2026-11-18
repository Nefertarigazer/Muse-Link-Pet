"""indexer.py + store.py: sync == rebuild, crash safety, concurrency, budgets."""
import os
import random
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from _mailbox import Mailbox
from mlindex import indexer, query, store, verify

TABLES = ("files", "threads", "messages", "requests", "releases", "attachments")


def dump(index_path):
    conn = sqlite3.connect(index_path)
    try:
        result = {}
        for table in TABLES:
            rows = [tuple(row) for row in conn.execute("SELECT * FROM %s" % table)]
            rows.sort(key=lambda row: repr(row))
            result[table] = rows
        return result
    finally:
        conn.close()


class Index(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mb = Mailbox(self.tmp.name)
        self.index = os.path.join(self.tmp.name, "index", "threads.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sync_equals_rebuild(self):
        """random sequence of writes/moves, sync after each; then rebuild. Pass: identical rows."""
        rng = random.Random(20261112)
        ids = ["r1", "r2", "r3", "r4"]
        self.mb.write_request("r1", "inbox", v=2, from_="codex", text="first",
                              thread_id="t1")
        self.mb.write_request("r2", "inbox", v=3, agent="kiro",
                              type_="publish-release", text="release",
                              payload={"version": "1.0.0", "package_path": "p",
                                       "package_size": 1, "sha256": "a" * 64})
        indexer.sync(self.tmp.name, self.index)
        for step in range(16):
            req = rng.choice(ids)
            action = rng.randrange(6)
            try:
                if action == 0:
                    self.mb.write_request(req, "inbox", v=2, from_="codex",
                                          text="request %s" % req,
                                          thread_id="t1" if req in ("r1", "r3") else None)
                elif action == 1:
                    self.mb.write_reply(req, step + 1, "outbox",
                                        status="info", text="ack %d" % step)
                elif action == 2:
                    self.mb.write_reply(req, step + 1, "archive", status="ok",
                                        final=True, text="final %d" % step)
                elif action == 3:
                    self.mb.move(req, "inbox", "claimed")
                elif action == 4:
                    self.mb.move(req, "claimed", "done")
                elif action == 5 and os.path.exists(self.mb.path("outbox", req + ".json")):
                    self.mb.remove("outbox", req + ".json")
            except FileNotFoundError:
                pass
            indexer.sync(self.tmp.name, self.index)

        self.assertGreater(len(query.list_threads(self.index)), 1)
        before = dump(self.index)
        indexer.rebuild(self.tmp.name, self.index)
        after = dump(self.index)
        self.assertEqual(before, after)

    def test_delete_and_rebuild(self):
        """delete threads.sqlite (+ -wal/-shm), rebuild. Pass: verify --all clean."""
        self.mb.write_request("r1", "inbox", v=2, text="hello", thread_id="t1")
        self.mb.write_reply("r1", 1, "outbox", status="info", text="ack")
        indexer.rebuild(self.tmp.name, self.index)
        result = verify.verify(self.tmp.name, self.index, check_all=True)
        self.assertTrue(result["ok"], result)
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.index + suffix)
            except FileNotFoundError:
                pass
        indexer.rebuild(self.tmp.name, self.index)
        result = verify.verify(self.tmp.name, self.index, check_all=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(query.list_threads(self.index)), 1)

    def test_crash_during_rebuild(self):
        """rebuild interrupted mid-way. Pass: old index still opens and answers."""
        self.mb.write_request("r1", "inbox", v=2, text="old", thread_id="t1")
        indexer.rebuild(self.tmp.name, self.index)
        old = dump(self.index)
        self.mb.write_request("r2", "inbox", v=2, text="new", thread_id="t2")
        with mock.patch.object(indexer, "_replace", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                indexer.rebuild(self.tmp.name, self.index)
        self.assertEqual(dump(self.index), old)
        self.assertEqual([t["id"] for t in query.list_threads(self.index)], ["t1"])
        # No build leftovers beside the index.
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.index))
                          if ".build-" in n], [])

    def test_sync_and_rebuild_clean_up_orphan_build_files(self):
        self.mb.write_request("r1", "inbox", v=2, text="hello", thread_id="t1")
        indexer.rebuild(self.tmp.name, self.index)
        idx_dir = os.path.dirname(self.index)
        for suffix in ("", "-wal", "-shm"):
            with open(os.path.join(idx_dir, "threads.sqlite.build-99999-deadbeef" + suffix), "wb") as fh:
                fh.write(b"orphan")
        indexer.sync(self.tmp.name, self.index)
        self.assertEqual([n for n in os.listdir(idx_dir) if ".build-" in n], [])
        for suffix in ("", "-wal", "-shm"):
            with open(os.path.join(idx_dir, "threads.sqlite.build-88888-cafebabe" + suffix), "wb") as fh:
                fh.write(b"orphan")
        indexer.rebuild(self.tmp.name, self.index)
        self.assertEqual([n for n in os.listdir(idx_dir) if ".build-" in n], [])

    def test_single_writer_and_live_readers(self):
        """second sync -> busy (exit 3); a reader queries during sync. Pass: no lock error."""
        self.mb.write_request("r1", "inbox", v=2, text="hello", thread_id="t1")
        indexer.rebuild(self.tmp.name, self.index)
        writer = store.open_writer(self.index)
        try:
            with self.assertRaises(store.WriterBusy):
                indexer.sync(self.tmp.name, self.index)
            # A reader does not need the writer lock and sees the old snapshot.
            self.assertEqual([t["id"] for t in query.list_threads(self.index)], ["t1"])
        finally:
            writer.close()
        self.assertEqual(indexer.sync(self.tmp.name, self.index)["changed"], 0)

    def test_rebuild_with_live_reader(self):
        """A reader open during rebuild does not block the writer (Windows)."""
        self.mb.write_request("r1", "inbox", v=2, text="old", thread_id="t1")
        indexer.rebuild(self.tmp.name, self.index)
        reader = store.open_reader(self.index)
        try:
            self.mb.write_request("r2", "inbox", v=2, text="new", thread_id="t2")
            indexer.rebuild(self.tmp.name, self.index)
            self.assertEqual([t["id"] for t in query.list_threads(self.index)],
                             ["t1", "t2"])
        finally:
            reader.close()
        self.assertEqual({t["id"] for t in query.list_threads(self.index)},
                         {"t1", "t2"})

    def test_budgets(self):
        """10 000 messages: rebuild < 30 s; no-change sync < 1 s. Pass: timings."""
        for i in range(1000):
            req = "r%04d" % i
            self.mb.write_request(req, "sent", v=2, text="bulk %d" % i,
                                  thread_id="bulk")
            for seq in range(1, 10):
                self.mb.write_reply(req, seq, "archive", status="ok",
                                    final=(seq == 9), text="reply %d %d" % (i, seq))
        start = time.time()
        result = indexer.rebuild(self.tmp.name, self.index)
        rebuild_s = time.time() - start
        self.assertEqual(result["messages"], 10000)
        self.assertLess(rebuild_s, 30.0, rebuild_s)
        start = time.time()
        result = indexer.sync(self.tmp.name, self.index)
        sync_s = time.time() - start
        self.assertEqual(result["changed"], 0)
        self.assertLess(sync_s, 1.0, sync_s)

    def test_seven_digit_iso_timestamps_parse(self):
        self.mb.write_request("r7", "sent", v=2, from_="opencode", text="ps time",
                              created_at="2026-11-16T14:41:03.5554890+01:00")
        indexer.rebuild(self.tmp.name, self.index)
        view = query.thread(self.index, "opencode/chat")
        self.assertGreater(view["messages"][0]["sort_ts"], 1700000000)


if __name__ == "__main__":
    unittest.main()
