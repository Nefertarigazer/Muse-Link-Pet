"""Schema, status machine and lookup (docs/architecture.en.md section 10).

    python -m unittest discover -s tests/explib -t tests/explib
"""
import unittest

import _support  # noqa: F401
from _support import Sandbox, entry, atomic
from explib import schema
from explib.store import Store
from datetime import date, timedelta
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from unittest import mock


class Schema(unittest.TestCase):
    def test_valid_entry_and_field_errors(self):
        """Pass: the design's sample entry validates; each wrong field (unknown key, 13 steps,
        161-char step, bad date, negative works, bad id) raises ValueError naming that field."""
        value = entry()
        self.assertIs(schema.validate(value), value)
        for field, bad in (("extra", 1), ("method", ["s"] * 13), ("method", ["s" * 161]),
                           ("created", "2026-02-30"), ("works", -1), ("id", "../escape"),
                           ("schema", True), ("fails", False), ("tags", ["t"] * 9),
                           ("goal", ""), ("source", "remote"), ("app", "../bad")):
            changed = copy.deepcopy(value)
            changed[field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaisesRegex(ValueError, field):
                schema.validate(changed)
        self.assertRegex(schema.new_id(), r"^x-[0-9a-f]{12}$")

    def test_status_machine(self):
        """Pass: draft->valid on confirm; two fails in a row valid->review; one more ->stale;
        90 days untouched ->stale; any works ->valid with fails 0; inputs are never mutated."""
        day = date.today().isoformat()
        original = entry(status="draft", works=0)
        saved = copy.deepcopy(original)
        value = schema.confirm(original, day)
        self.assertEqual(value["status"], "valid")
        self.assertEqual(original, saved)
        first = schema.feedback(value, False, day)
        self.assertEqual(first["status"], "valid")
        second = schema.feedback(first, False, day)
        self.assertEqual((second["status"], second["fails"]), ("review", 2))
        third = schema.feedback(second, False, day)
        self.assertEqual(third["status"], "stale")
        worked = schema.feedback(third, True, day)
        self.assertEqual((worked["status"], worked["works"], worked["fails"]), ("valid", 1, 0))
        self.assertEqual(third["fails"], 3)
        # Kiro review: a "worked" report must not confirm an unreviewed draft
        with self.assertRaisesRegex(ValueError, "status"):
            schema.feedback(original, True, day)
        for status in ("valid", "review"):
            old = entry(status=status, last_verified=(date.today() - timedelta(days=90)).isoformat())
            self.assertEqual(schema.age_out(old, day)["status"], "stale")
            self.assertEqual(old["status"], status)
            old["last_verified"] = (date.today() - timedelta(days=89)).isoformat()
            self.assertEqual(schema.age_out(old, day)["status"], status)

    def test_render_fits_a_card(self):
        """Pass: a maximal valid entry renders to <= 1200 chars; app name is normalized."""
        self.assertEqual(schema.normalize_app("MiniMax Code.exe"), "minimax code.exe")
        value = entry(method=["s"] * 12)
        remaining = 1200 - len(schema.render(value))
        for index in range(12):
            amount = min(remaining, 159)
            value["method"][index] += "字" * amount
            remaining -= amount
        self.assertEqual(remaining, 0)
        schema.validate(value)
        self.assertEqual(len(schema.render(value)), 1200)
        value["ui"] += "x"
        with self.assertRaisesRegex(ValueError, "render"):
            schema.validate(value)
        for unsafe in ("con", "a/b", "a\\b", "a:", "..", "nul.exe"):
            with self.assertRaisesRegex(ValueError, "app"):
                schema.normalize_app(unsafe)


class Lookup(Sandbox):
    def test_order_local_before_public_valid_before_review(self):
        """Pass: same app, equal score -> local valid, local review, public valid, public review;
        drafts never returned; at most `limit` hits with days_since_verified."""
        local = self.save(entry())
        review = self.save(entry(status="review"))
        public = self.save(entry(source="public"))
        self.save(entry(source="public", status="review"))
        self.save(entry(status="draft"))
        hits = self.store.lookup("Sample.EXE")
        self.assertEqual([h["entry"]["id"] for h in hits], [local["id"], review["id"], public["id"]])
        self.assertEqual([h["source"] for h in hits], ["local", "local", "public"])
        self.assertTrue(all(h["days_since_verified"] == 0 and h["works"] == 2 for h in hits))
        self.assertEqual(len(self.store.lookup("sample.exe", limit=1)), 1)
        self.assertEqual(self.store.lookup("other.exe"), [])

    def test_cjk_and_word_overlap(self):
        """Pass: goal "向输入框写入文字并发送" ranks the contenteditable entry above an unrelated one."""
        unrelated = self.save(entry(goal="从菜单打开设置", ui="menu", tags=["menu"]))
        desired = self.save(entry(ui="contenteditable"))
        self.assertEqual(self.store.lookup("sample.exe", goal="向输入框写入文字并发送")[0]["entry"]["id"], desired["id"])
        desired.update(goal="send message", ui="editor", tags=["paste"])
        unrelated.update(goal="open menu", ui="menu", tags=["settings"])
        self.store.put(desired)
        self.store.put(unrelated)
        self.assertEqual(self.store.lookup("sample.exe", goal="send message", tags=["paste"])[0]["entry"]["id"], desired["id"])

    def test_bad_files_are_skipped_not_deleted(self):
        """Pass: a corrupt and a path-escaping file are reported in load().skipped and stay on disk."""
        corrupt = self.base / "experience/local/sample.exe/broken.json"
        atomic(corrupt, "{")
        escape = self.base / "experience/local/sample.exe/x-aaaaaaaaaaaa.json"
        atomic(escape, entry(id="x-aaaaaaaaaaaa", app="../outside"))
        store = Store(self.base)
        self.assertEqual(set(store.skipped), {str(corrupt), str(escape)})
        self.assertTrue(corrupt.exists() and escape.exists())
        self.assertEqual(store.entries, {})

    def test_atomic_updates_do_not_lose_feedback(self):
        value = self.save(entry(works=0))
        def update(_):
            Store(self.base).update(value["id"], lambda e: schema.feedback(e, True, date.today().isoformat()))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(update, range(12)))
        self.assertEqual(Store(self.base).get(value["id"])["works"], 12)


class Feedback(Sandbox):
    def test_public_feedback_persists_without_writing_public_entry(self):
        old = (date.today() - timedelta(days=100)).isoformat()
        value = self.save(entry(source="public", works=1, last_verified=old))
        path = self.store.path(value, "public")
        before = path.read_bytes()
        today = date.today().isoformat()
        updated = self.store.feedback(value["id"], True, today)
        self.assertEqual((updated["works"], updated["status"], updated["last_verified"]), (2, "valid", today))
        overlay = self.store.path(value, "feedback")
        self.assertEqual(json.loads(overlay.read_text(encoding="utf-8")),
                         dict(v=1, works=1, fails=0, last_verified=today))
        hit = Store(self.base).lookup(value["app"])[0]
        self.assertEqual(hit["entry"], updated)
        self.assertEqual((hit["works"], hit["source"], hit["days_since_verified"]), (2, "public", 0))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.base / "experience/local").exists())
        with self.assertRaisesRegex(ValueError, "read-only"):
            self.store.put(updated)

    def test_public_failure_streak_and_success_reset(self):
        today = date.today().isoformat()
        old = (date.today() - timedelta(days=5)).isoformat()
        value = self.save(entry(source="public", works=1, fails=1, last_verified=old))
        for worked, status, works, fails, verified in (
                (False, "review", 1, 2, old), (False, "stale", 1, 3, old),
                (True, "valid", 2, 0, today), (False, "valid", 2, 1, today),
                (False, "review", 2, 2, today), (False, "stale", 2, 3, today),
                (False, "stale", 2, 4, today), (True, "valid", 3, 0, today)):
            with self.subTest(worked=worked, works=works, fails=fails):
                result = Store(self.base).feedback(value["id"], worked, today)
                restored = Store(self.base).get(value["id"])
                self.assertEqual(restored, result)
                self.assertEqual((restored["status"], restored["works"], restored["fails"], restored["last_verified"]),
                                 (status, works, fails, verified))

    def test_local_feedback_still_updates_entry_and_drafts_stay_hidden(self):
        today = date.today().isoformat()
        local = self.save(entry(works=0))
        path = self.store.path(local)
        for worked, status, works, fails in ((False, "valid", 0, 1), (False, "review", 0, 2),
                                            (False, "stale", 0, 3), (True, "valid", 1, 0)):
            result = self.store.feedback(local["id"], worked, today)
            self.assertEqual((result["status"], result["works"], result["fails"]), (status, works, fails))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)
        for source in ("local", "public"):
            draft = self.save(entry(source=source, status="draft", app="draft.exe"))
            before = self.store.path(draft, source).read_bytes()
            for worked in (True, False):
                with self.assertRaisesRegex(ValueError, "confirm"):
                    self.store.feedback(draft["id"], worked, today)
            self.assertEqual(self.store.lookup("draft.exe"), [])
            self.assertEqual(self.store.path(draft, source).read_bytes(), before)
        self.assertFalse((self.base / "experience/feedback").exists())

    def test_public_feedback_is_atomic_under_concurrent_writers(self):
        value = self.save(entry(source="public", works=1))
        def update(_):
            Store(self.base).feedback(value["id"], True, date.today().isoformat())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(update, range(12)))
        self.assertEqual(Store(self.base).get(value["id"])["works"], 13)

    def test_public_overlay_ages_and_cannot_affect_a_local_collision(self):
        old = (date.today() - timedelta(days=100)).isoformat()
        value = self.save(entry(source="public", last_verified=old))
        self.store.feedback(value["id"], True, old)
        self.assertEqual(Store(self.base).lookup(value["app"])[0]["entry"]["status"], "stale")
        # Local entries win id collisions and retain only their own feedback.
        local = dict(value, source="local", last_verified=date.today().isoformat())
        atomic(self.store.path(local), schema.render(local))
        restored = Store(self.base)
        self.assertEqual(restored.get(local["id"]), local)
        restored.feedback(local["id"], True, date.today().isoformat())
        self.assertEqual(Store(self.base).get(local["id"])["works"], local["works"] + 1)
        overlay = json.loads(self.store.path(value, "feedback").read_text(encoding="utf-8"))
        self.assertEqual(overlay["works"], 1)

    def test_bad_overlay_is_reported_and_never_overwritten(self):
        value = self.save(entry(source="public"))
        path = self.store.path(value, "feedback")
        for bad in ("{", "[]", '{"v":1,"works":true,"fails":0,"last_verified":null}',
                    '{"v":1,"works":1,"fails":0,"last_verified":"bad"}'):
            atomic(path, bad)
            restored = Store(self.base)
            self.assertEqual(restored.skipped, [str(path)])
            self.assertEqual(restored.get(value["id"]), value)
            with self.assertRaises(ValueError):
                restored.feedback(value["id"], True, date.today().isoformat())
            self.assertEqual(path.read_text(encoding="utf-8"), bad)

    def test_disk_failure_preserves_previous_feedback(self):
        value = self.save(entry(source="public"))
        today = date.today().isoformat()
        self.store.feedback(value["id"], True, today)
        path = self.store.path(value, "feedback")
        before = path.read_bytes()
        with mock.patch("explib.store.atomic", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.feedback(value["id"], True, today)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(Store(self.base).get(value["id"])["works"], value["works"] + 1)


if __name__ == "__main__":
    unittest.main()
