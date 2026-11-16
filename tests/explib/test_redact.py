"""Redaction and the zero-hit re-check (docs/architecture.en.md section 10)."""
import unittest

import _support  # noqa: F401
from _support import Sandbox, entry
from explib import redact, share, schema
from unittest import mock
import json


class Redact(Sandbox):
    def test_configured_literals_and_environment(self):
        """Pass: user name, host name, tool dir, bridge root and configured literals are replaced,
        case-insensitively, longest first; placeholders as documented."""
        self.configure({"explib": {"redact": {"literals": ["PrivateTenant", "Alice long", "Alice"]}},
                        "bridge": {"root": "D:\\private-bridge"}, "tailscale": {"device_name": "private-tail"}})
        with mock.patch.dict("os.environ", USERNAME="TestUser", USERDOMAIN="TestDomain", COMPUTERNAME="TestHost"):
            lits = redact.literals(self.base)
        value = entry(goal="TestUser TestDomain TESTHOST PrivateTenant alice long ALICE",
                      ui=str(self.base), verify="D:\\private-bridge private-tail")
        clean, hits = redact.redact(value, lits)
        raw = schema.render(clean)
        self.assertEqual(hits, [])
        for private in ("testuser", "testdomain", "testhost", "privatetenant", "alice", "private-bridge", "private-tail"):
            self.assertNotIn(private, raw.lower())
        self.assertIn("<USER>", raw)
        self.assertIn("<HOST>", raw)
        self.assertIn("<PATH>", raw)
        self.assertNotIn("long", clean["goal"])

    def test_patterns(self):
        """Pass: profile path, e-mail, phone, IPv4, IPv6, URL query, long token each replaced;
        an ordinary Chinese sentence and a short version string are untouched."""
        samples = [(r"C:\Users\x\data", "%USERPROFILE%"), ("a@example.com", "<EMAIL>"),
                   ("+49 151 12345678", "<PHONE>"), ("192.168.12.4", "<IP>"),
                   ("2001:db8::1", "<IP>"), ("https://example.test/page?private=yes", "https://example.test/page"),
                   ("AbcDE1234fghIJ5678klmNO90", "<TOKEN>")]
        for text, marker in samples:
            with self.subTest(text=text):
                value = entry(goal=text)
                self.assertTrue(redact.check(value, {}))
                clean, hits = redact.redact(value, {})
                self.assertEqual(hits, [])
                self.assertIn(marker, clean["goal"])
                self.assertNotIn(text, clean["goal"])
        value = entry(goal="这是一条普通中文经验", app_version="1.4.2")
        clean, hits = redact.redact(value, {})
        self.assertEqual((clean["goal"], clean["app_version"], hits), (value["goal"], "1.4.2", []))
        clean, hits = redact.redact(entry(goal="https://example.test/page?email=a@example.com&secret=private"), {})
        self.assertEqual(clean["goal"], "https://example.test/page")
        self.assertEqual(hits, [])
        clean, hits = redact.redact(entry(goal="AbCdEfGhIjKlMnOpQrStUvWx"), {})
        self.assertEqual(clean["goal"], "<TOKEN>")
        self.assertEqual(hits, [])
        numeric = entry(method=[{"step": "numeric arg", "args": {"contact": 4915112345678}}])
        self.assertIn("phone", redact.check(numeric, {}))

    def test_content_fields_removed(self):
        """Pass: step args text/value/clipboard/title/file/path/expect_text and >80-char args are
        removed; agent becomes "community"."""
        args = {k: "PRIVATE" for k in ("text", "value", "clipboard", "title", "file", "path", "expect_text")}
        args.update(too_long="q" * 81, query={"name": "editor", "text": "PRIVATE"}, short="safe")
        value = entry(method=[{"step": "Find editor", "tool": "resolve", "args": args}])
        clean, hits = redact.redact(value, {})
        self.assertEqual(clean["method"][0]["args"], {"query": {"name": "editor"}, "short": "safe"})
        self.assertEqual(clean["agent"], "community")
        self.assertNotIn("PRIVATE", schema.render(clean))
        self.assertEqual(hits, [])
        self.assertIn("text", value["method"][0]["args"])

    def test_remaining_hit_blocks_sharing(self):
        """Pass: a literal hidden where pass 1 cannot reach (e.g. inside tags) is reported by check();
        share.candidates() then excludes the entry."""
        value = self.save(entry(tags=["private-tag"]))
        self.configure({"explib": {"redact": {"literals": ["private-tag"]}}})
        clean, hits = redact.redact(value, {"private-tag": "<PRIVATE>"})
        self.assertTrue(hits)
        self.assertTrue(redact.check(clean, {"private-tag": "<PRIVATE>"}))
        self.assertEqual(share.candidates(self.store, self.base), [])
        self.assertTrue((self.base / "experience/share-blocked.json").exists())


if __name__ == "__main__":
    unittest.main()
