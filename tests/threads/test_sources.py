"""sources.py: every file shape is read correctly and nothing is touched.
    python -m unittest discover -s tests/threads -t tests/threads
"""
import hashlib
import os
import tempfile
import unittest

from _mailbox import Mailbox
from mlindex import sources


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mb = Mailbox(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_shapes(self):
        """v1 / v2 / v3 requests and replies, BOM: fields equal the file. Pass: exact match."""
        payload = {"version": "1.2.3", "package_size": 7}
        self.mb.write_request("v1req", "inbox", v=1, from_="old", type_="message",
                              text="v1 text", payload={"a": 1},
                              thread_id="t/v1", reply_to="v1req")
        self.mb.write_request("v2req", "claimed", v=2, from_="codex",
                              type_="publish-release", text="v2 text",
                              payload=payload)
        self.mb.write_request("v3req", "sent", v=3, agent="kiro",
                              type_="question", text="v3 text",
                              payload={"q": True}, thread_id="t3")
        self.mb.write_request("bomreq", "pending", v=2, text="\u4e2d\u6587 BOM",
                              bom=True)
        self.mb.write_reply("v1req", seq=0, dir_name="outbox", v=1,
                            status="ok", text="v1 final")
        self.mb.write_reply("v2req", seq=2, dir_name="archive", v=2,
                            status="error", final=True, text="v2 final")
        self.mb.write_reply("v3req", seq=3, dir_name="quarantine", v=3,
                            status="info", final=False, text="v3 interim",
                            agent="kiro")
        self.mb.write_ack("v2req", seq=2)

        by_name = dict((r["name"], r) for r in sources.scan(self.tmp.name))
        self.assertEqual(len(by_name), 8)

        r = by_name["v1req.json"]
        self.assertEqual((r["kind"], r["dir"], r["req_id"], r["v"]), ("request", "inbox", "v1req", 1))
        self.assertEqual((r["type"], r["text"], r["payload"], r["agent"], r["from"]),
                         ("message", "v1 text", {"a": 1}, "old", "old"))
        self.assertEqual((r["thread_id"], r["reply_to"]), ("t/v1", "v1req"))
        self.assertIsNone(r["reply_protocol"])

        r = by_name["v2req.json"]
        self.assertEqual((r["v"], r["reply_protocol"], r["type"]),
                         (2, 2, "publish-release"))
        self.assertEqual(r["payload"], payload)

        r = by_name["v3req.json"]
        self.assertEqual((r["v"], r["agent"], r["type"], r["text"], r["thread_id"]),
                         (3, "kiro", "question", "v3 text", "t3"))
        self.assertEqual(r["reply_protocol"], 3)
        self.assertIsNone(r["parse_error"])

        r = by_name["bomreq.json"]
        self.assertEqual(r["text"], "\u4e2d\u6587 BOM")
        self.assertIsNone(r["parse_error"])

        r = by_name["v1req.reply.json"]
        self.assertEqual((r["kind"], r["role"], r["seq"], r["status"], r["final"],
                          r["text"]), ("reply", "muse", 0, "ok", True, "v1 final"))

        r = by_name["v2req.002.reply.json"]
        self.assertEqual((r["seq"], r["final"], r["dir"], r["status"]),
                         (2, True, "archive", "error"))

        r = by_name["v3req.003.reply.json"]
        self.assertEqual((r["v"], r["seq"], r["agent"], r["status"], r["final"],
                          r["text"]), (3, 3, "kiro", "info", False, "v3 interim"))
        self.assertIsNone(r["parse_error"])

        r = by_name["v2req.002.ack"]
        self.assertEqual((r["kind"], r["req_id"], r["seq"]), ("ack", "v2req", 2))
        self.assertTrue(r["text"].startswith("2026-"))

        # parse_file is the same code path, and the sha256 matches the bytes.
        direct = sources.parse_file(r["path"], r["dir"])
        with open(r["path"], "rb") as fh:
            self.assertEqual(direct["sha256"], hashlib.sha256(fh.read()).hexdigest())

    def test_unreadable_files(self):
        """vanished and half-written files skipped; garbage -> parse_error record. Pass: no exception."""
        # A half-written atomic-writer temp file is not a letter yet.
        self.mb.write_raw("inbox", "half.tmp", '{"req_id": "half"')
        # A truncated .json is a real (damaged) letter: shown, not lost.
        self.mb.write_raw("outbox", "damaged.001.reply.json", '{"req_id": "damaged"')
        # Garbage with a valid request name.
        self.mb.write_raw("inbox", "garbage.json", "not json at all")
        vanished = self.mb.write_request("vanished", "inbox")
        os.remove(vanished)

        records = list(sources.scan(self.tmp.name))
        by_name = dict((r["name"], r) for r in records)
        self.assertNotIn("half.tmp", by_name)
        self.assertNotIn("vanished.json", by_name)
        self.assertIn("json", by_name["damaged.001.reply.json"]["parse_error"])
        self.assertIn("json", by_name["garbage.json"]["parse_error"])
        self.assertEqual(by_name["garbage.json"]["req_id"], "garbage")
        self.assertIsNone(sources.parse_file(vanished, "inbox"))

    def test_read_only(self):
        """after scan every mailbox file has the same bytes and mtime. Pass: unchanged."""
        self.mb.write_request("r1", "inbox", v=2, text="one")
        self.mb.write_reply("r1", 1, "outbox", status="info", text="two")
        self.mb.write_ack("r1", 1)
        before = {}
        for dir_name in ("inbox", "outbox", "ack"):
            directory = os.path.join(self.tmp.name, dir_name)
            for name in os.listdir(directory):
                path = os.path.join(directory, name)
                st = os.stat(path)
                with open(path, "rb") as fh:
                    payload = fh.read()
                before[path] = (payload, st.st_mtime_ns, st.st_size)
        list(sources.scan(self.tmp.name))
        for path, (data, mtime_ns, size) in before.items():
            st = os.stat(path)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), data, path)
            self.assertEqual((st.st_mtime_ns, st.st_size), (mtime_ns, size), path)


if __name__ == "__main__":
    unittest.main()
