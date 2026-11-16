"""Read-only public sync (public.py requirements)."""
import unittest

import _support  # noqa: F401
from _support import Sandbox, entry, FakeFetch
from explib import public, schema
from explib.store import Store
from unittest import mock
from plugin._shared import atomic
import json
import hashlib
from datetime import date, timedelta
import urllib.parse
from pathlib import Path


class PublicSync(Sandbox):
    def library(self, values):
        url = "https://library.example.test/index.json"
        self.configure({"explib": {"public": {"index_url": url}}})
        index, responses = [], {}
        for value in values:
            path = "entries/" + value["app"] + "/" + value["id"] + ".json"
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            index.append(dict(path=path, sha256=hashlib.sha256(data).hexdigest()))
            responses["https://library.example.test/" + urllib.parse.quote(path, safe="/")] = (200, {}, data)
        responses[url] = (200, {"ETag": '"v1"'}, json.dumps({"v": 1, "entries": index}).encode())
        return url, index, FakeFetch(responses)
    def test_good_entries_land_read_only(self):
        """Pass: valid entries are stored under public/ with source "public"; local/ untouched."""
        local = self.save(entry())
        local_path = self.store.path(local)
        before = local_path.read_bytes()
        value = entry(app="sample app.exe")
        _, _, fake = self.library([value])
        result = public.sync(self.base, fake)
        self.assertEqual(result, dict(added=1, kept=0, dropped=0, not_modified=False))
        store = Store(self.base)
        synced = store.get(value["id"])
        self.assertEqual(synced["source"], "public")
        self.assertEqual(local_path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "read-only"):
            store.put(synced)
        repeated = public.sync(self.base, fake)
        self.assertEqual((repeated["added"], repeated["kept"]), (0, 1))

    def test_bad_entries_are_dropped(self):
        """Pass: wrong sha256, schema failure, oversize entry, other-host URL, http:// index ->
        dropped/refused and counted; the previous public/ stays intact on a failed sync."""
        original = self.save(entry(source="public"))
        original_path = self.store.path(original, "public")
        before = original_path.read_bytes()
        good, wrong_hash, bad_schema, oversize = [entry() for _ in range(4)]
        bad_schema["works"] = -1
        url, index, fake = self.library([good, wrong_hash, bad_schema, oversize])
        index[1]["sha256"] = "0" * 64
        huge = b"x" * (public.ENTRY_LIMIT + 1)
        fake.values["https://library.example.test/" + index[3]["path"]] = (200, {}, huge)
        index[3]["sha256"] = hashlib.sha256(huge).hexdigest()
        index.append(dict(path="https://evil.example.test/entry.json", sha256="0" * 64))
        fake.values[url] = (500, {}, b"failure")
        with self.assertRaises(ValueError):
            public.sync(self.base, fake)
        self.assertEqual(original_path.read_bytes(), before)
        fake.values[url] = (200, {}, json.dumps(dict(v=1, entries=index)).encode())
        result = public.sync(self.base, fake)
        self.assertEqual((result["added"], result["dropped"]), (1, 4))
        self.assertNotIn("evil", " ".join(c[0] for c in fake.calls))
        self.assertEqual(Store(self.base).get(good["id"])["source"], "public")
        self.configure({"explib": {"public": {"index_url": "http://library.example.test/index.json"}}})
        count = len(fake.calls)
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            public.sync(self.base, fake)
        self.assertEqual(len(fake.calls), count)

    def test_not_modified(self):
        """Pass: a 304 for the index changes nothing and reports not_modified."""
        value = entry()
        url, _, fake = self.library([value])
        public.sync(self.base, fake)
        path = self.store.path(dict(value, source="public"), "public")
        before = path.read_bytes()
        fake.values[url] = (304, {}, b"")
        result = public.sync(self.base, fake)
        self.assertEqual(result, dict(added=0, kept=1, dropped=0, not_modified=True))
        self.assertEqual(fake.calls[-1][1], {"If-None-Match": '"v1"'})
        self.assertEqual(path.read_bytes(), before)

    def test_sync_keeps_feedback_and_merges_new_factory_counters(self):
        today = date.today().isoformat()
        local_day = (date.today() - timedelta(days=5)).isoformat()
        old = (date.today() - timedelta(days=10)).isoformat()
        value = entry(works=1, last_verified=old)
        _, _, fake = self.library([value])
        public.sync(self.base, fake)
        Store(self.base).feedback(value["id"], True, local_day)
        overlay = self.store.path(dict(value, source="public"), "feedback")
        before = overlay.read_bytes()
        # Both a newer and an older factory date retain local success counts.
        for verified in (today, old):
            value.update(works=5, last_verified=verified)
            _, _, fake = self.library([value])
            public.sync(self.base, fake)
            hit = Store(self.base).lookup(value["app"])[0]
            self.assertEqual((hit["works"], hit["entry"]["last_verified"]), (6, max(verified, local_day)))
            self.assertEqual(overlay.read_bytes(), before)
        # Existing feedback cannot expose or confirm a new public draft.
        value["status"] = "draft"
        _, _, fake = self.library([value])
        public.sync(self.base, fake)
        self.assertEqual(Store(self.base).lookup(value["app"]), [])
        with self.assertRaisesRegex(ValueError, "confirm"):
            Store(self.base).feedback(value["id"], True, today)
        self.assertEqual(overlay.read_bytes(), before)
        value["status"] = "valid"
        # Removing an entry must not let its retained overlay create a hit.
        _, _, fake = self.library([])
        public.sync(self.base, fake)
        self.assertEqual(Store(self.base).lookup(value["app"]), [])
        self.assertEqual(overlay.read_bytes(), before)
        _, _, fake = self.library([value])
        public.sync(self.base, fake)
        self.assertEqual(Store(self.base).get(value["id"])["works"], 6)

    def test_index_size_count_and_staging_failure_preserve_previous_library(self):
        value = self.save(entry(source="public"))
        path = self.store.path(value, "public")
        before = path.read_bytes()
        url, _, fake = self.library([])
        for body in (b"x" * (public.INDEX_LIMIT + 1), json.dumps({"v": 1, "entries": [{}] * 5001}).encode()):
            fake.values[url] = (200, {}, body)
            with self.assertRaises(ValueError):
                public.sync(self.base, fake)
            self.assertEqual(path.read_bytes(), before)
        url, _, fake = self.library([entry()])
        with mock.patch("explib.public.atomic", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                public.sync(self.base, fake)
        self.assertEqual(path.read_bytes(), before)
        def fail_metadata(target, data):
            if target.name == "public-sync.json":
                raise OSError("metadata failure")
            return atomic(target, data)
        with mock.patch("explib.public.atomic", side_effect=fail_metadata):
            with self.assertRaises(OSError):
                public.sync(self.base, fake)
        self.assertEqual(path.read_bytes(), before)
        # A killed process after public -> public.old is recovered even if
        # the next request is a 304.
        path.parents[1].rename(self.base / "experience/public.old")
        fake.values[url] = (304, {}, b"")
        public.sync(self.base, fake)
        self.assertEqual(path.read_bytes(), before)

    def test_readers_see_a_complete_snapshot_during_directory_exchange(self):
        old = self.save(entry(source="public"))
        new = entry()
        _, _, fake = self.library([new])
        rename = Path.rename
        def observe_gap(path, target):
            result = rename(path, target)
            if path == self.base / "experience/public" and target == self.base / "experience/public.old":
                hit = Store(self.base).lookup("sample.exe")[0]
                self.assertEqual(hit["entry"]["id"], old["id"])
            return result
        with mock.patch.object(Path, "rename", new=observe_gap):
            public.sync(self.base, fake)
        self.assertEqual(Store(self.base).lookup("sample.exe")[0]["entry"]["id"], new["id"])
        # Also replace a tree after a reader has finished scanning it but
        # before it commits its in-memory index. The reader must retry.
        third = entry()
        _, _, fake = self.library([third])
        scan = Store._scan
        changed = []
        def replace_mid_read(store, directory, source):
            result = scan(store, directory, source)
            if source == "public" and not changed:
                changed.append(True)
                public.sync(self.base, fake)
            return result
        with mock.patch.object(Store, "_scan", new=replace_mid_read):
            hit = Store(self.base).lookup("sample.exe")[0]
        self.assertEqual(hit["entry"]["id"], third["id"])


if __name__ == "__main__":
    unittest.main()
