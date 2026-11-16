"""query.py: thread list, thread view, search (Chinese, fallback)."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from _mailbox import Mailbox
from mlindex import indexer, query, store

CHINESE = "\u53d1\u5e03\u8bf4\u660e"


class Query(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mb = Mailbox(self.tmp.name)
        self.index = os.path.join(self.tmp.name, "index", "threads.sqlite")
        payload = {
            "version": "1.4.0",
            "package_path": "D:\\MyApp\\out\\VB-1.4.0.zip",
            "package_size": 123,
            "sha256": "a" * 64,
            "setup_path": "D:\\MyApp\\out\\setup.exe",
            "setup_size": 45,
            "setup_sha256": "b" * 64,
            "test": True,
        }
        self.mb.write_request("r1", "claimed", v=2, from_="codex",
                              type_="publish-release",
                              text="Release 1.4.0\nsecond line",
                              payload=payload, thread_id="t1",
                              created_at="2026-11-12T10:00:00+00:00")
        self.mb.write_reply("r1", 1, "outbox", status="info", text="ack",
                            replied_at="2026-11-12T10:01:00+00:00")
        self.mb.write_reply("r1", 2, "archive", status="ok", final=True,
                            text="released", replied_at="2026-11-12T10:02:00+00:00")
        self.mb.write_request("r2", "claimed", v=3, agent="kiro",
                              type_="question", text="need a choice",
                              thread_id="t2",
                              created_at="2026-11-12T11:00:00+00:00")
        self.mb.write_reply("r2", 1, "archive", v=3, status="needs-user",
                            final=True, text="please choose", agent="kiro",
                            replied_at="2026-11-12T11:01:00+00:00")
        self.mb.write_request("r3", "inbox", v=2, from_="kiro",
                              text=CHINESE + ": 100%_done NEAR(",
                              created_at="2026-11-12T12:00:00+00:00")
        indexer.rebuild(self.tmp.name, self.index)

    def tearDown(self):
        self.tmp.cleanup()

    def test_thread_list_and_view(self):
        """order, title, unread, status, trust labels, release card. Pass: expected values."""
        threads = query.list_threads(self.index)
        self.assertEqual([t["id"] for t in threads], ["kiro/chat", "t2", "t1"])
        by_id = dict((t["id"], t) for t in threads)
        self.assertEqual(by_id["t1"]["title"], "Release 1.4.0")
        self.assertEqual(by_id["t1"]["participants"], ["codex", "muse"])
        self.assertEqual(by_id["t1"]["status"], "done")
        self.assertEqual(by_id["t1"]["unread"], 1)
        self.assertEqual(by_id["t1"]["last_activity"], "2026-11-12T10:02:00+00:00")
        self.assertEqual(by_id["t1"]["snippet"], "released")
        self.assertEqual(by_id["t2"]["status"], "waiting_user")
        self.assertEqual(by_id["kiro/chat"]["status"], "active")
        self.assertEqual(by_id["kiro/chat"]["participants"], ["kiro"])

        view = query.thread(self.index, "t1")
        self.assertIsNotNone(view)
        self.assertEqual(view["title"], "Release 1.4.0")
        self.assertEqual([m["role"] for m in view["messages"]], ["agent", "muse", "muse"])
        self.assertEqual([m["state"] for m in view["messages"]],
                         ["replied", "replied", "replied"])
        self.assertEqual([m["trust"] for m in view["messages"]],
                         [None, "unread_unverified", "read"])
        request = view["messages"][0]
        self.assertEqual(request["release_card"]["version"], "1.4.0")
        self.assertEqual(request["release_card"]["package_size"], 123)
        self.assertTrue(request["release_card"]["test"])
        self.assertEqual(request["release_card"]["status"], "ok")
        self.assertEqual([a["kind"] for a in request["attachments"]],
                         ["package", "setup"])
        self.assertIsNone(query.thread(self.index, "missing"))
        self.assertTrue(query.stats(self.index)["fts"])

    def test_search_chinese_substring(self):
        """3+ chars via trigram, 1-2 chars via LIKE. Pass: expected hits."""
        long_hits = query.search(self.index, CHINESE[:3])
        self.assertEqual([m["req_id"] for m in long_hits], ["r3"])
        short_hits = query.search(self.index, CHINESE[:2])
        self.assertEqual([m["req_id"] for m in short_hits], ["r3"])
        self.assertTrue(query.search(self.index, CHINESE[:2])[0]["thread_title"]
                        .startswith(CHINESE))

    def test_search_without_fts(self):
        """FTS5 unavailable (simulated): index builds, search works, stats fts=false. Pass: same hits."""
        fallback = os.path.join(self.tmp.name, "index", "fallback.sqlite")
        with mock.patch.object(store, "fts_available", return_value=False):
            indexer.rebuild(self.tmp.name, fallback)
        self.assertFalse(query.stats(fallback)["fts"])
        long_hits = query.search(fallback, CHINESE[:3])
        short_hits = query.search(fallback, CHINESE[:2])
        self.assertEqual([m["req_id"] for m in long_hits], ["r3"])
        self.assertEqual([m["req_id"] for m in short_hits], ["r3"])

    def test_query_is_parameterized(self):
        """quotes, %, _, FTS syntax in q. Pass: no error, no injection."""
        self.assertEqual([m["req_id"] for m in query.search(self.index, "%")], ["r3"])
        self.assertEqual([m["req_id"] for m in query.search(self.index, "_")], ["r3"])
        self.assertEqual([m["req_id"] for m in query.search(self.index, "NEAR(")], ["r3"])
        for q in ('" OR 1=1 --', "a\"b", "*", "()", "-x", "a:b", "\u4e2d\u6587"):
            self.assertIsInstance(query.search(self.index, q), list)
        # The injection attempt did not delete or change anything.
        self.assertEqual(len(query.list_threads(self.index)), 3)

    def test_mailbox_metadata_and_last_ts_are_read_only(self):
        self.mb.write_request("meta'quoted", "sent", from_="codex-legion", reply_to="r1")
        indexer.rebuild(self.tmp.name, self.index)
        before = query.stats(self.index)
        meta = query.request_meta(self.index, ["r1", "r2", "missing", "' OR 1=1 --"])
        self.assertEqual(set(meta), {"r1", "r2"})
        self.assertEqual(meta["r1"], {"agent": "codex", "final_read": True, "reply_to": None})
        self.assertEqual(meta["r2"]["agent"], "kiro")
        self.assertEqual(query.request_meta(self.index, ["meta'quoted"])["meta'quoted"]["reply_to"], "r1")
        self.assertEqual(query.request_meta(self.index, []), {})
        self.assertEqual(query.request_meta(self.index, ["missing%d" % i for i in range(1100)]), {})
        for summary in query.list_threads(self.index):
            self.assertIsInstance(summary["last_ts"], float)
            self.assertEqual(summary["last_ts"], query.thread(self.index, summary["id"])["last_ts"])
        hit = next(m for m in query.search(self.index, "Release 1.4") if m["role"] == "agent")
        self.assertEqual(hit["release_card"]["version"], "1.4.0")
        self.assertEqual(len(hit["attachments"]), 2)
        reader = store.open_reader(self.index)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                reader.execute("DELETE FROM requests")
        finally:
            reader.close()
        self.assertEqual(before, query.stats(self.index))

    def test_attention_items_include_only_needs_user_and_pending_enroll(self):
        self.mb.write_request("e1", "pending", v=3, agent="codex", type_="enroll", text="enroll me")
        indexer.rebuild(self.tmp.name, self.index)
        items = query.attention_items(self.index)
        by_req = {x["req_id"]: x for x in items}
        self.assertEqual(set(by_req), {"r2", "e1"})
        self.assertEqual(by_req["r2"]["thread_id"], "t2")
        self.assertEqual(by_req["e1"]["thread_id"], "codex/chat")

    def test_superseded_needs_user_and_declined_enroll_clear_waiting_user(self):
        # Older needs_user followed by newer conversation clears waiting_user & attention.
        self.mb.write_request("t2_followup", "claimed", v=3, agent="kiro",
                              text="looks good", thread_id="t2",
                              created_at="2026-11-12T12:30:00+00:00")
        self.mb.write_reply("t2_followup", 1, "archive", v=3, status="ok", final=True,
                            text="noted", agent="kiro",
                            replied_at="2026-11-12T12:31:00+00:00")
        # Stale pending enroll with a final declined reply in quarantine is no longer pending.
        self.mb.write_request("e_declined", "pending", v=3, agent="enroll-test", type_="enroll", text="enroll")
        self.mb.write_reply("e_declined", 1, "quarantine", v=2, status="error", final=True,
                            text="Enrollment not approved: declined", agent="enroll-test")
        indexer.rebuild(self.tmp.name, self.index)
        by_id = {t["id"]: t for t in query.list_threads(self.index)}
        self.assertEqual(by_id["t2"]["status"], "done")
        self.assertEqual(by_id["enroll-test/chat"]["status"], "done")
        self.assertEqual(query.attention_items(self.index), [])


if __name__ == "__main__":
    unittest.main()
