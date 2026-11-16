"""Contract and read-only behavior, exclusively over the synthetic mailbox."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/threads"))
from _mailbox import Mailbox
from mlindex import indexer, query
from mailbox.api import Api
from mailbox.syncer import Syncer

CONTRACT = json.loads((ROOT / "pc/mailbox/ui/fixtures/contract.json").read_text(encoding="utf-8"))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mb = Mailbox(Path(self.temp.name) / "bridge")
        self.index = str(Path(self.temp.name) / "index/threads.sqlite")
        self.cfg = Path(self.temp.name) / "muselink.json"
        self.cfg.write_text(json.dumps({"bridge": {"root": self.mb.root}, "index": {"path": self.index}}), encoding="utf-8")
        self.mb.write_request("old", "sent", from_="codex-legion", type_="publish-release",
            text="release 中文 100%_x", payload={"version": "1.0", "package_path": "fake.zip", "sha256": "a" * 64},
            thread_id="codex-legion/release", reply_to="prior", created_at="2026-11-11T10:00:00Z")
        self.mb.write_reply("old", 1, "archive", status="ok", text="released", replied_at="2026-11-11T10:01:00Z")
        self.mb.write_request("new", from_="codex", text="release new", thread_id="codex/release", created_at="2026-11-12T10:00:00Z")
        self.mb.write_reply("orphan", 0, "archive", status="error", text="release orphan")
        indexer.rebuild(self.mb.root, self.index)
        self.syncer = Syncer(self.mb.root, self.index, interval=3600)
        self.addCleanup(self.syncer.stop)
        self.api = Api(self.syncer, str(self.cfg), avatar_path=Path(self.temp.name) / "no-avatar.png")

    def data(self, response):
        self.assertEqual(set(response), {"ok", "data"}, response)
        self.assertIs(response["ok"], True)
        return response["data"]

    def check_message(self, m, search=False):
        keys = set(CONTRACT["message"])
        if search:
            keys.update(CONTRACT["search_result_extra"])
        self.assertEqual(set(m), keys)
        for key in ("role", "trust", "state"):
            self.assertIn(m[key], CONTRACT["enums"][key])
        self.assertIsInstance(m["final_read"], bool)
        if m["release_card"]:
            self.assertEqual(set(m["release_card"]), set(CONTRACT["release_card"]))

    def snapshot(self):
        return {str(p.relative_to(self.mb.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in Path(self.mb.root).rglob("*") if p.is_file()}

    def test_all_methods_contract_and_unchanged_mailbox(self):
        before = self.snapshot()
        self.assertEqual({n for n in dir(self.api) if not n.startswith("_")},
                         set(CONTRACT["methods"]) | set(CONTRACT["window_methods"]))
        self.assertEqual(set(self.data(self.api.config())), set(CONTRACT["config"]))
        threads = self.data(self.api.threads())
        self.assertEqual([t["last_ts"] for t in threads], sorted([t["last_ts"] for t in threads], reverse=True))
        for t in threads:
            self.assertEqual(set(t), set(CONTRACT["thread_summary"]))
            self.assertIn(t["status"], CONTRACT["enums"]["thread_status"])
            detail = self.data(self.api.thread(t["id"]))
            self.assertEqual(set(detail), set(CONTRACT["thread_summary"]) | {"messages"})
            self.assertEqual([m["sort_ts"] for m in detail["messages"]], sorted(m["sort_ts"] for m in detail["messages"]))
            for m in detail["messages"]:
                self.check_message(m)
        ids = {t["id"] for t in threads}
        self.assertTrue({"codex/release", "codex-legion/release"} <= ids)
        old = self.data(self.api.thread("codex-legion/release"))
        self.assertIn("codex-legion", old["participants"])
        self.assertEqual(old["messages"][0]["agent"], "codex-legion")
        self.assertTrue(old["messages"][0]["final_read"])
        self.assertEqual(self.data(self.api.thread("unknown/chat"))["participants"], ["muse"])
        hits = self.data(self.api.search("release", 200))
        self.assertTrue(hits)
        for m in hits:
            self.check_message(m, search=True)
        self.assertTrue(next(m for m in hits if m["req_id"] == "old" and m["role"] == "agent")["release_card"])
        for method in (self.api.status, self.api.refresh):
            self.assertEqual(set(self.data(method())), set(CONTRACT["status"]))
        self.assertIsNone(self.data(self.api.avatar()))
        self.assertEqual(before, self.snapshot())

    def test_invalid_input_and_limits(self):
        self.assertIsNone(self.data(self.api.thread("missing")))
        self.assertIsNone(self.data(self.api.thread("' OR 1=1 --")))
        for q in ('" OR 1=1 --', "中文", "%", "_", 'a"b', "x" * 2000, ""):
            self.assertIsInstance(self.data(self.api.search(q)), list)
        for response in (self.api.thread({}), self.api.search([]), self.api.search("x", "bad")):
            self.assertEqual(set(response), {"ok", "error", "code"})
            self.assertFalse(response["ok"])
        with mock.patch.object(query, "search", return_value=[]) as search:
            self.data(self.api.search("x" * 300, 999))
            self.assertEqual(search.call_args.args[1:], ("x" * 200, 200))
            self.data(self.api.search("x", -5))
            self.assertEqual(search.call_args.args[2], 1)
        with mock.patch.object(query, "list_threads", side_effect=RuntimeError("PRIVATE LETTER")):
            response = self.api.threads()
            self.assertFalse(response["ok"])
            self.assertNotIn("PRIVATE", json.dumps(response))

    def test_config_override_paths_and_status_budget(self):
        cfg = json.loads(self.cfg.read_text())
        cfg["mailbox"] = {"agent_aliases": {}, "display_names": {"a": "A"}, "poll_ms": 7000}
        self.cfg.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(self.data(self.api.config()), cfg["mailbox"])
        from mlindex.__main__ import _resolve_paths
        self.assertEqual((self.api._root, self.api._index), _resolve_paths(None, None, str(self.cfg)))
        with mock.patch.object(query, "stats", side_effect=AssertionError("status performed IO")):
            elapsed = []
            for _ in range(100):
                start = time.perf_counter()
                self.data(self.api.status())
                elapsed.append(time.perf_counter() - start)
        self.assertLess(max(elapsed), .020)

    def test_legacy_entrance_delegates_runtime_guards_to_pet(self):
        from mailbox import app
        with mock.patch('pet.app.main',return_value=1) as pet_main:
            self.assertEqual(app.main([]),1)
            pet_main.assert_called_once_with(['--mailbox'])

    def test_second_launch_preserves_json_unicode_arguments(self):
        from mailbox import app
        path=Path(self.temp.name)/'args.json'
        thread='codex/中文+&"'
        path.write_text(json.dumps(['--thread',thread],ensure_ascii=False),encoding='utf-8')
        with mock.patch('pet.app.main',return_value=0) as pet_main:
            self.assertEqual(app.main(['--args-file',str(path)]),0)
            pet_main.assert_called_once_with(['--mailbox','--thread',thread])



    def test_threads_badge_tracks_needs_user_and_mark_seen(self):
        self.mb.write_request("info_only", "claimed", from_="codex", thread_id="codex/chat")
        self.mb.write_reply("info_only", 1, "outbox", status="info", text="working")
        self.mb.write_request("ask_user", "claimed", from_="kiro", thread_id="kiro/chat")
        self.mb.write_reply("ask_user", 1, "outbox", status="needs-user", final=True, text="need click")
        self.syncer.refresh()
        threads = {t["id"]: t for t in self.data(self.api.threads())}
        self.assertEqual(threads["codex/chat"]["unread"], 0)
        self.assertEqual(threads["kiro/chat"]["unread"], 1)
        self.assertEqual(self.data(self.api.mark_seen(["kiro/chat"])), {"unread": 0})
        threads = {t["id"]: t for t in self.data(self.api.threads())}
        self.assertEqual(threads["kiro/chat"]["unread"], 0)
        bad = self.api.mark_seen("not-a-list")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["code"], "invalid_input")

    def test_delete_thread_api_validation_and_removal(self):
        res = self.data(self.api.delete_thread(["codex/release"]))
        self.assertGreaterEqual(res["deleted"], 1)
        self.assertNotIn("codex/release", [t["id"] for t in self.data(self.api.threads())])
        for invalid in ([], "codex/release", [""]):
            bad = self.api.delete_thread(invalid)
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["code"], "invalid_input")

    def test_send_message_and_pet_history_merge(self):
        actions = mock.Mock()
        actions.say.return_value = {"via": "bridge", "req_id": "pet_123"}
        history = Path(self.temp.name) / "pet-history.jsonl"
        history.write_text(json.dumps({
            "ts": 1794500000.0, "req_id": "req_pop_1",
            "ask": "Confirm task?", "kind": "typed", "answer": "Looks good",
        }) + "\n", encoding="utf-8")
        api = Api(self.syncer, str(self.cfg), actions=actions, history_path=history)
        res = self.data(api.send_message("codex/chat", "  follow up  "))
        self.assertEqual(res, {"via": "bridge", "req_id": "pet_123"})
        actions.say.assert_called_once_with("follow up", None, thread="codex/chat")
        for bad_call in (lambda: api.send_message("../bad", "hi"), lambda: api.send_message("codex/chat", "   "),
                         lambda: api.send_message("codex/chat", "x" * 2001), lambda: api.send_message("codex/chat", "a\x00b")):
            r = bad_call()
            self.assertFalse(r["ok"])
            self.assertEqual(r["code"], "invalid_input")
        pet_thread = self.data(api.thread("pet/chat"))
        self.assertEqual([m["role"] for m in pet_thread["messages"]], ["muse", "user"])
        self.assertEqual([m["text"] for m in pet_thread["messages"]], ["Confirm task?", "Looks good"])
        legacy_hist = Path(self.temp.name) / "legacy-history.jsonl"
        legacy_hist.write_text(json.dumps({
            "ts": 1794500001.0, "req_id": "req_pop_2",
            "ask": (
                "代理 opencode（未签名，身份未验证） 请求执行：publish-release\n"
                "package_path=D:\\Releases\\SampleApp-1.2.0-win-x64.zip\n"
                "package_sha256=" + "a" * 64 + "\n"
                "package_size=92214917\n"
                "setup_present=True\n"
                "test=False\n"
                "version=1.2.0\n"
                "允许吗？"
            ),
            "kind": "choice", "answer": "允许",
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        legacy_api = Api(self.syncer, str(self.cfg), actions=actions, history_path=legacy_hist)
        legacy_thread = self.data(legacy_api.thread("pet/chat"))
        ask_text = legacy_thread["messages"][0]["text"]
        self.assertIn("SampleApp-1.2.0-win-x64.zip", ask_text)
        self.assertIn("87.9 MB", ask_text)
        self.assertIn("aaaaaaaa…aaaaaa", ask_text)
        self.assertNotIn("package_size=", ask_text)
        self.assertNotIn("test=False", ask_text)
        avatar_file = Path(self.temp.name) / "user-avatar.png"
        avatar_file.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
        api_with_avatar = Api(self.syncer, str(self.cfg), avatar_path=avatar_file, on_set_avatar=lambda: True)
        self.assertTrue(self.data(api_with_avatar.avatar()).startswith("data:image/png;base64,"))
        self.assertEqual(self.data(api.set_avatar()), {"updated": False})
        self.assertEqual(self.data(api_with_avatar.set_avatar()), {"updated": True})


if __name__ == "__main__":
    unittest.main()
