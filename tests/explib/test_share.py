"""Consent: accumulate 5 valid entries before asking once; never ask again after decline."""
import unittest

import _support  # noqa: F401
from _support import Sandbox, entry, FakeAsk, FakeGh, atomic
from explib import share, schema
from explib.store import Store
from plugin import ask
from unittest import mock
from datetime import datetime, timezone, timedelta
import base64
import json
import inspect


class Share(Sandbox):
    def check(self, answer=None, status="ok", now=None, kind="choice"):
        fake = FakeAsk(answer, status, kind)
        result = share.check_once(self.base, self.store, fake, now=now)
        return result, fake

    def save_batch(self, count=5, **kwargs):
        return [self.save(entry(**kwargs)) for _ in range(count)]

    def test_candidates(self):
        """Pass: only local + valid + no consent state + zero redaction hits (works>=0)."""
        with mock.patch("explib.schema.secrets.token_hex", side_effect=["123456789012", "abcdef123456"]):
            wanted = self.save(entry(works=0))
        for changes in ({"status": "review"}, {"status": "draft"}, {"source": "public"}):
            self.save(entry(**changes))
        already = self.save(entry())
        atomic(self.base / "experience/share.json", {"entries": {already["id"]: "shared"}})
        self.assertEqual([e["id"] for e in share.candidates(self.store, self.base)], [wanted["id"]])

    def test_confirm_makes_entry_candidate_without_works_threshold(self):
        local = self.save(entry(status="draft", works=0))
        public = self.save(entry(source="public", works=0))
        store = Store(self.base)
        self.assertEqual(share.candidates(store, self.base), [])
        today = datetime.now().date().isoformat()
        store.put(schema.confirm(local, today))
        self.assertEqual([e["id"] for e in share.candidates(Store(self.base), self.base)], [local["id"]])
        self.assertNotIn(public["id"], [e["id"] for e in share.candidates(Store(self.base), self.base)])

    def test_accumulates_five_entries_before_asking(self):
        """Pass: 1..4 entries ask nothing; once 5 accumulate, asks once for the batch."""
        now = datetime.now(timezone.utc)
        for n in range(1, 5):
            self.save(entry(works=0))
            res, fake = self.check(share.t(share.SHARE), now=now)
            self.assertEqual(fake.calls, [])
            self.assertEqual(res["reason"], "not-enough-candidates")
            self.assertEqual((res["count"], res["needed"]), (n, 5))
        self.save(entry(works=0))
        res, fake = self.check(share.t(share.SHARE), now=now)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(res["status"], "outboxed")
        self.assertEqual(len(res["ids"]), 5)
        for eid in res["ids"]:
            self.assertTrue((self.base / "experience/outbox" / (eid + ".json")).is_file())
            self.assertEqual(self.state()["entries"][eid], "outboxed")

    def test_never_means_never_on_decline(self):
        """Pass: after declining (NEVER), check_once() asks nothing ever again."""
        self.save_batch(5)
        now = datetime.now(timezone.utc)
        result, first = self.check(share.t(share.NEVER), now=now)
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(result["reason"], "never")
        self.assertTrue(self.state()["never"])
        for offset in (1, 30, 365):
            self.save_batch(5)
            result, later = self.check(share.t(share.SHARE), now=now + timedelta(days=offset))
            self.assertEqual(later.calls, [])
            self.assertEqual(result["reason"], "never")
        self.assertFalse((self.base / "experience/outbox").exists())

    def test_next_batch_of_five_prompts_without_24h_throttle_after_share(self):
        """Pass: sharing 5 entries does not block a second batch of 5; popup-pending yields."""
        self.save_batch(10)
        now = datetime.now(timezone.utc)
        r1, f1 = self.check(share.t(share.SHARE), now=now)
        r2, f2 = self.check(share.t(share.SHARE), now=now + timedelta(minutes=1))
        self.assertEqual((len(f1.calls), len(f2.calls)), (1, 1))
        self.assertEqual(len(set(r1["ids"]) | set(r2["ids"])), 10)
        self.save_batch(5)
        atomic(self.base / "popup-pending.txt", "muse-card")
        r3, f3 = self.check(share.t(share.SHARE), now=now + timedelta(days=2))
        self.assertEqual((r3["status"], f3.calls), ("busy", []))

    def test_outbox_only_after_share_and_bytes_match_the_card(self):
        """Pass: no outbox file and no submit for expired/preempted/busy; expired throttles 24h;
        after SHARE the outbox bytes for all 5 entries equal the redacted entries shown on the card."""
        items = [self.save(entry(goal=f"Send #{i} to private@example.com")) for i in range(5)]
        original = {}
        now = datetime.now(timezone.utc)
        with mock.patch("explib.share.submit") as submit:
            for i, (status, answer, kind) in enumerate((("preempted", share.t(share.SHARE), "choice"), ("busy", share.t(share.SHARE), "choice"),
                                                       ("ok", share.t(share.SHARE), "pet_done"), ("expired", "", "expired"))):
                result, fake = self.check(answer, status, now + timedelta(days=i * 2), kind)
                self.assertEqual(len(fake.calls), 1)
                self.assertFalse((self.base / "experience/outbox").exists())
                submit.assert_not_called()
                if status in ("preempted", "busy") or kind == "pet_done":
                    self.assertEqual(self.state(), original)
                elif status == "expired":
                    self.assertIn("last_expired", self.state())
                    self.assertNotIn("entries", self.state())
            # Throttled within 24h after expired
            throttled, f_throt = self.check(share.t(share.SHARE), "ok", now + timedelta(days=6, hours=1))
            self.assertEqual((throttled["reason"], f_throt.calls), ("throttled", []))
        result, card = self.check(share.t(share.SHARE), now=now + timedelta(days=20))
        self.assertEqual(len(card.calls), 1, result)
        uploaded = []
        for it in items:
            path = self.base / "experience/outbox" / (it["id"] + ".json")
            raw = path.read_bytes()
            self.assertNotIn(b"private@example.com", raw)
            obj = json.loads(raw.decode("utf-8"))
            self.assertEqual(raw, schema.render(obj).encode("utf-8"))
            uploaded.append(obj)
        uploaded.sort(key=lambda e: (e["last_verified"], e["id"]), reverse=True)
        self.assertEqual(card.calls[0]["text"], share.batch_card_text(uploaded) + share.t(share.CARD_NOTE))
        self.assertNotIn("private@example.com", card.calls[0]["text"])
        self.assertEqual(result["status"], "outboxed")

    def test_submit_without_gh_leaves_outbox(self):
        """Pass: no repo configured or `gh auth status` fails -> status outboxed with manual steps;
        with a fake gh runner the PUT body equals the outbox bytes and state becomes shared."""
        batch = self.save_batch(5)
        value = batch[0]
        result, _ = self.check(share.t(share.SHARE))
        self.assertEqual(result["status"], "outboxed")
        self.assertIn("unchanged", result["how"])
        fake = FakeGh()
        self.assertEqual(share.submit(self.base, value["id"], fake)["status"], "outboxed")
        self.assertEqual(fake.calls, [])
        self.configure({"explib": {"public": {"repo": "team/library"}}})
        self.assertEqual(share.submit(self.base, value["id"], FakeGh(False))["status"], "outboxed")
        fake = FakeGh()
        result = share.submit(self.base, value["id"], fake)
        self.assertEqual(result["status"], "shared")
        put = next(kwargs for args, kwargs in fake.calls if "PUT" in args)
        body = json.loads(put["input"])
        payload = (self.base / "experience/outbox" / (value["id"] + ".json")).read_bytes()
        self.assertEqual(base64.b64decode(body["content"]), payload)
        self.assertEqual(body["author"]["name"], "MuseLink Community")
        self.assertEqual(self.state()["entries"][value["id"]], "shared")
        self.assertEqual(self.state()["prs"][value["id"]], result["url"])

    def test_card_is_silent_and_named(self):
        """Pass: ask_user is called with silent=True, display "经验库", mode choice, 2 options,
        expires_in 600; plugin.ask accepts the two new keywords."""
        self.save_batch(5)
        _, fake = self.check(share.t(share.NEVER))
        call = fake.calls[0]
        for key, expected in dict(agent="explib", silent=True, display="经验库", mode="choice",
                                  options=[share.t(share.SHARE), share.t(share.NEVER)], expires_in=600).items():
            self.assertEqual(call[key], expected)
        self.assertTrue({"silent", "display"} <= inspect.signature(ask.ask_user).parameters.keys())
        with mock.patch("plugin.ask._spawn") as spawn, mock.patch("plugin.ask._wait", return_value={"status": "expired"}):
            ask.ask_user("explib", "entry", "choice", [share.t(share.SHARE)], base=self.base, silent=True, display="经验库")
        command = spawn.call_args[0][0]
        self.assertEqual(command[command.index("-Silent") + 1], "true")
        self.assertTrue((self.base / "popup-text.txt").read_text(encoding="utf-8").startswith("经验库 问你：\n"))

    def test_submit_without_consent_cannot_upload_an_arbitrary_file(self):
        fake = FakeGh()
        value = entry()
        atomic(self.base / "experience/outbox" / (value["id"] + ".json"), schema.render(value))
        self.assertEqual(share.submit(self.base, value["id"], fake)["status"], "refused")
        self.assertEqual(fake.calls, [])

    def test_changed_outbox_is_not_the_payload_the_user_approved(self):
        batch = self.save_batch(5)
        value = batch[0]
        self.check(share.t(share.SHARE))
        path = self.base / "experience/outbox" / (value["id"] + ".json")
        altered = json.loads(path.read_text(encoding="utf-8"))
        altered["goal"] = "A different goal"
        atomic(path, schema.render(altered))
        fake = FakeGh()
        self.configure({"explib": {"public": {"repo": "team/library"}}})
        self.assertEqual(share.submit(self.base, value["id"], fake)["reason"], "outbox-differs-from-consent")
        self.assertEqual(fake.calls, [])

    def test_card_to_outbox_to_pr_runs_as_one_consented_path(self):
        batch = [self.save(entry(goal=f"Send #{i} to private@example.com")) for i in range(5)]
        self.configure({"explib": {"public": {"repo": "team/library"}}})
        fake = FakeGh()
        with mock.patch("explib.share.subprocess.run", side_effect=fake):
            result, card = self.check(share.t(share.SHARE))
        self.assertEqual(result["status"], "shared")
        self.assertEqual(len(result["ids"]), 5)
        uploaded = []
        for it in batch:
            outbox = (self.base / "experience/outbox" / (it["id"] + ".json")).read_bytes()
            uploaded.append(json.loads(outbox.decode("utf-8")))
            self.assertEqual(self.state()["entries"][it["id"]], "shared")
        uploaded.sort(key=lambda e: (e["last_verified"], e["id"]), reverse=True)
        self.assertEqual(card.calls[0]["text"], share.batch_card_text(uploaded) + share.t(share.CARD_NOTE))
        puts = [json.loads(kwargs["input"]) for args, kwargs in fake.calls if "PUT" in args]
        self.assertEqual(len(puts), 5)


if __name__ == "__main__":
    unittest.main()


class ShareCardReadable(Sandbox):
    """Kiro review: the card must be readable and must not be cut off."""

    def test_card_lists_every_content_field_in_plain_lines(self):
        value = entry(method=["定位输入框", {"step": "粘贴", "tool": "act", "args": {"app": "sample.exe", "key": "ctrl+v"}}])
        text = share.card_text(value)
        for part in ("应用：sample.exe 1.4.2", "界面：Electron", "目标：" + value["goal"], "1. 定位输入框",
                     '2. 粘贴（act {"app":"sample.exe","key":"ctrl+v"}）', "验证：消息出现", "标签：input"):
            self.assertIn(part, text)
        self.assertNotIn("{\"id\"", text)

    def test_an_entry_too_long_for_the_card_is_not_offered(self):
        long_steps = ["第%d步：在设置页里找到对应的开关并逐项核对它当前的状态是否正确" % i for i in range(12)]
        self.save(entry(method=long_steps))
        self.assertEqual(share.candidates(self.store, self.base), [])
        blocked = json.loads((self.base / "experience/share-blocked.json").read_text(encoding="utf-8"))
        self.assertIn("card-too-long", blocked["reasons"])
