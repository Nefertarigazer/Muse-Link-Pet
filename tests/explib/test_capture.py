"""Trace -> draft (docs/architecture.en.md section 10, capture.py requirements)."""
import unittest

import _support  # noqa: F401
from _support import Sandbox, atomic
from explib import capture, schema
from plugin import tools
from unittest import mock
from datetime import datetime, timezone
import json


class Capture(Sandbox):
    def rows(self, rows):
        atomic(self.base / "plugin/trace/codex.jsonl", "\n".join(json.dumps(r) for r in rows))

    def row(self, tool, status="ok", app="sample.exe", **fields):
        return dict(tool=tool, app=[app], status=status, query={"name": "editor"}, **fields)
    def test_failed_attempt_replaced_later_is_dropped(self):
        """Pass: trace act(fail) -> act(ok) on the same app keeps only the ok step; repeated
        observes merge; the last verify becomes `verify`; status draft, source local."""
        self.rows([self.row("observe"), self.row("observe"), self.row("act", "fail"),
                   self.row("act"), self.row("verify")])
        value = capture.draft_from_trace(self.base, "codex", "Sample.EXE", "发送")
        self.assertEqual([s["tool"] for s in value["method"]], ["observe", "act"])
        self.assertEqual(value["verify"], "verify editor")
        self.assertEqual((value["status"], value["source"]), ("draft", "local"))
        self.assertEqual(schema.validate(value), value)

    def test_only_the_latest_run_for_the_app(self):
        """Pass: rows for another app in between end the run; `since` limits it further."""
        self.rows([self.row("resolve", at="2026-11-12T00:00:00+00:00"), self.row("verify"),
                   self.row("observe", app="other.exe"), self.row("observe", at="2026-11-13T01:00:00+00:00"),
                   self.row("act", at="2026-11-13T02:00:00+00:00"),
                   self.row("verify", at="2026-11-13T03:00:00+00:00")])
        value = capture.draft_from_trace(self.base, "codex", "sample.exe", "goal")
        self.assertEqual([s["tool"] for s in value["method"]], ["observe", "act"])
        limited = capture.draft_from_trace(self.base, "codex", "sample.exe", "goal", since="2026-11-13T02:00:00Z")
        self.assertEqual([s["tool"] for s in limited["method"]], ["act"])
        with self.assertRaises(ValueError):
            capture.draft_from_trace(self.base, "codex", "sample.exe", "goal", since="2026-11-13T04:00:00Z")

    def test_nothing_beyond_the_trace(self):
        """Pass: the draft contains no field value that is not present in a trace row
        (no typed text, no expect text) and validates against the schema."""
        row = self.row("act")
        row.update(text="SECRET", readback="SECRET", expect={"text": "SECRET"})
        row["query"]["text"] = "SECRET"
        self.rows([row, self.row("uia_read_text")])
        value = capture.draft_from_trace(self.base, "codex", "sample.exe", "goal")
        self.assertNotIn("SECRET", schema.render(value))
        self.assertEqual(value["method"][0]["args"], {"app": "sample.exe", "query": {"name": "editor"}})
        schema.validate(value)

    def test_plugin_trace_rows_carry_at(self):
        """Pass: a real plugin tools.invoke() writes a trace row with an ISO `at`; old rows
        without it are still read."""
        with mock.patch("plugin.executor_client.call", return_value={"status": "ok"}):
            self.assertEqual(tools.invoke("observe", {"app": "sample.exe"}, "codex", self.base)["status"], "ok")
        row = capture.read_trace(self.base, "codex")[0]
        self.assertIsNotNone(datetime.fromisoformat(row["at"]).tzinfo)
        old = self.row("verify")
        self.rows([row, old])
        self.assertEqual(len(capture.read_trace(self.base, "codex")), 2)
        self.assertEqual(capture.draft_from_trace(self.base, "codex", "sample.exe", "goal")["verify"], "verify editor")

    def test_unresolved_failure_or_no_final_verify_refuses_draft(self):
        for rows in ([self.row("observe"), self.row("act", "unknown"), self.row("verify")],
                     [self.row("observe"), self.row("act")]):
            self.rows(rows)
            with self.assertRaises(ValueError):
                capture.draft_from_trace(self.base, "codex", "sample.exe", "goal")


if __name__ == "__main__":
    unittest.main()
