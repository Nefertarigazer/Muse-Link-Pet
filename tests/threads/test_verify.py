"""verify.py + CLI: the task-book acceptance tool."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from _mailbox import Mailbox, PC
from mlindex import indexer, query, store, verify


class Verify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mb = Mailbox(self.tmp.name)
        self.index = os.path.join(self.tmp.name, "index", "threads.sqlite")
        self.mb.write_request("r1", "claimed", v=2, text="hello", thread_id="t1")
        self.mb.write_reply("r1", 1, "outbox", status="info", text="ack")
        self.mb.write_request("r2", "inbox", v=3, agent="kiro", text="second",
                              thread_id="t2")
        indexer.rebuild(self.tmp.name, self.index)
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = PC

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        proc = subprocess.run(
            [sys.executable, "-m", "mlindex"] + list(args),
            env=self.env, capture_output=True, text=True, encoding="utf-8")
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        return proc.returncode, lines, proc.stderr

    def test_clean_index_passes(self):
        """verify --sample 5 and --all on a fresh rebuild. Pass: exit 0."""
        for kwargs in ({"sample": 5, "seed": 7}, {"check_all": True}):
            result = verify.verify(self.tmp.name, self.index, **kwargs)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["mismatches"], [])
            self.assertEqual(result["checked"], 3)

    def test_mismatch_is_reported(self):
        """edit one row in the db. Pass: exit 1 and the field name in the output."""
        conn = sqlite3.connect(self.index)
        try:
            conn.execute("UPDATE messages SET text='tampered' WHERE req_id='r1' AND role='agent'")
            conn.commit()
        finally:
            conn.close()
        result = verify.verify(self.tmp.name, self.index, check_all=True)
        self.assertFalse(result["ok"])
        self.assertIn("text", [m["field"] for m in result["mismatches"]])

        code, lines, err = self.cli("--root", self.tmp.name, "verify", "--all")
        self.assertEqual(code, 1, err)
        self.assertEqual(len(lines), 1)
        value = json.loads(lines[0])
        self.assertFalse(value["ok"])
        self.assertIn("text", [m["field"] for m in value["mismatches"]])

    def test_cli_contract(self):
        """every command prints one JSON line; exit codes as in __main__.py. Pass: exact codes."""
        for args in (
                ("sync",),
                ("rebuild",),
                ("stats",),
                ("threads",),
                ("thread", "t1"),
                ("search", "\u4e2d\u6587"),
                ("verify", "--sample", "5")):
            code, lines, err = self.cli("--root", self.tmp.name, *args)
            self.assertEqual(code, 0, (args, err))
            self.assertEqual(len(lines), 1, (args, lines))
            json.loads(lines[0])

        code, lines, err = self.cli("--root", self.tmp.name, "no-such-command")
        self.assertEqual(code, 2)
        self.assertEqual(len(lines), 1)

        writer = store.open_writer(self.index)
        try:
            code, lines, err = self.cli("--root", self.tmp.name, "sync")
            self.assertEqual(code, 3, err)
            self.assertEqual(json.loads(lines[0])["error"], "busy")
        finally:
            writer.close()

        missing = os.path.join(self.tmp.name, "no-index.sqlite")
        code, lines, err = self.cli("--root", self.tmp.name, "--index", missing, "stats")
        self.assertEqual(code, 30)
        self.assertEqual(len(lines), 1)
        self.assertFalse(json.loads(lines[0])["ok"])


if __name__ == "__main__":
    unittest.main()
