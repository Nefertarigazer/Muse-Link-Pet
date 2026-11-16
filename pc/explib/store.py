"""store - entries as files, an in-memory index, lookup.

Requirements
  1. Root = <tool dir>\\experience (tool dir = parent of pc/explib, override
     with root= for tests). Paths only from normalized app + validated id;
     anything resolving outside the root -> ValueError.
  2. Writes are atomic (temp + os.replace) under a named mutex
     "muselink-explib-<install hash>" (reuse plugin._shared.mutex). Readers
     never lock.
  3. load(): scan local/ and public/, validate each file; an invalid file is
     skipped and reported in load().skipped (never deleted, never fatal).
  4. lookup(app, goal="", ui="", tags=(), limit=3): candidates with the same
     normalized app; score = keyword overlap of goal/ui/tags (CJK-aware:
     character bigrams for CJK text, words otherwise). Order: local before
     public, valid before review before stale, then score, then newer
     last_verified. Drafts are never returned. Each hit carries
     {entry, source, days_since_verified}.
  5. put(entry) validates first; public/ is written only by public.py.
     Public feedback lives in feedback/<app>/<id>.json, separate from sync.
     Its successes add to the current public works; last_verified is the
     later date. Same app/id retains feedback across public replacements.
"""


import copy
from datetime import date
from pathlib import Path
import json
import re

from plugin._shared import atomic, mutex, tool_dir
from . import schema


def keywords(text):
    result = set(re.findall(r"[a-z0-9_]+", text.lower()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return result


class Store:
    def __init__(self, root=None):
        self.base = tool_dir(root)
        self.root = self.base / "experience"
        self.entries = {}
        self.skipped = []
        self.load()

    def path(self, entry, source="local"):
        schema.validate(entry)
        path = self.root / source / entry["app"] / (entry["id"] + ".json")
        if self.root.resolve() not in path.resolve().parents or path.resolve() != path.absolute():
            raise ValueError("app: path escapes experience root")
        return path

    def load(self):
        local, skipped = self._scan(self.root / "local", "local")
        # Directory replacement needs two renames on Windows. Read the old
        # immutable snapshot during the gap and retry if the selected tree
        # changes while scanning. Readers never acquire a writer's mutex.
        while True:
            directory = self.root / "public"
            backup = self.root / "public.old"
            if not directory.exists() and backup.exists():
                directory = backup
            before = self._identity(directory)
            public, public_skipped = self._scan(directory, "public")
            empty = before is None and not (self.root / "public").exists() and not backup.exists()
            if before == self._identity(directory) and (before is not None or empty):
                break
        self.entries, self.skipped = local, skipped + public_skipped
        for entry_id, value in public.items():
            try:
                value = self._merge_feedback(value, self._read_feedback(value))
            except (OSError, ValueError, TypeError, RecursionError):
                self.skipped.append(str(self.root / "feedback" / value["app"] / (entry_id + ".json")))
            self.entries.setdefault(entry_id, value)
        self.entries = {key: schema.age_out(value, date.today().isoformat())
                        for key, value in self.entries.items()}
        return self

    @staticmethod
    def _identity(path):
        try:
            value = path.stat()
            return value.st_dev, value.st_ino
        except FileNotFoundError:
            return None

    def _scan(self, directory, source):
        entries, skipped = {}, []
        if directory.resolve() != directory.absolute():
            return entries, [str(directory)]
        for path in sorted(directory.glob("*/*.json")):
            try:
                if path.resolve() != path.absolute():
                    raise ValueError("linked entry path")
                if path.stat().st_size > 32768:
                    raise ValueError("entry too large")
                entry = json.loads(path.read_text(encoding="utf-8-sig"))
                schema.validate(entry)
                expected = directory / entry["app"] / (entry["id"] + ".json")
                if entry["source"] != source or path != expected or self.root.resolve() not in path.resolve().parents:
                    raise ValueError("entry path/source mismatch")
                entries.setdefault(entry["id"], entry)
            except (OSError, ValueError, TypeError, RecursionError):
                skipped.append(str(path))
        return entries, skipped

    def get(self, entry_id):
        entry = self.entries.get(entry_id)
        if entry is None:
            raise ValueError("id: not found")
        return copy.deepcopy(entry)

    def put(self, entry):
        schema.validate(entry)
        if entry["source"] != "local":
            raise ValueError("source: public library is read-only")
        with mutex(self.base, "explib"):
            for path in (self.root / "local", self.root / "local" / entry["app"]):
                if path.is_symlink() or self.root.resolve() not in path.resolve().parents:
                    raise ValueError("app: linked or escaping local directory")
            self.load()
            previous = self.entries.get(entry["id"])
            if previous and (previous["source"] != "local" or previous["app"] != entry["app"]):
                raise ValueError("id: collision")
            atomic(self.path(entry), schema.render(entry))
            self.entries[entry["id"]] = copy.deepcopy(entry)
        return copy.deepcopy(entry)

    def update(self, entry_id, change):
        with mutex(self.base, "explib"):
            self.load()
            return self.put(change(self.get(entry_id)))

    def _read_feedback(self, entry):
        path = self.path(entry, "feedback")
        try:
            if path.stat().st_size > 32768:
                raise ValueError("feedback: too large")
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return dict(v=1, works=0, fails=0, last_verified=None)
        if (not isinstance(value, dict) or set(value) != {"v", "works", "fails", "last_verified"} or
                type(value["v"]) is not int or value["v"] != 1 or
                any(type(value[k]) is not int or value[k] < 0 for k in ("works", "fails"))):
            raise ValueError("feedback: invalid counters")
        if value["works"]:
            schema._day(value["last_verified"], "feedback.last_verified")
        elif value["last_verified"] is not None:
            raise ValueError("feedback: date without success")
        return value

    @staticmethod
    def _merge_feedback(entry, feedback):
        result = copy.deepcopy(entry)
        # An overlay never confirms a draft, even if the repository changed
        # the same id back to draft after feedback was recorded.
        if result["status"] == "draft":
            return result
        result["works"] += feedback["works"]
        if feedback["works"]:
            result.update(status="valid", fails=0,
                          last_verified=max(result["last_verified"], feedback["last_verified"]))
        # Only the trailing failures matter after a success. Before the first
        # local success, they extend the public failure streak. Three steps
        # reach stale from any non-draft state; keep the full count separately.
        steps = min(feedback["fails"], 3)
        for _ in range(steps):
            result = schema.feedback(result, False, date.today().isoformat())
        result["fails"] += feedback["fails"] - steps
        return schema.validate(result)

    def feedback(self, entry_id, worked, today):
        with mutex(self.base, "explib"):
            self.load()
            entry = self.get(entry_id)
            updated = schema.feedback(entry, worked, today)
            if entry["source"] == "local":
                return self.put(updated)
            # Read again strictly: a corrupt overlay is reported by load(),
            # but must never be silently overwritten by the next feedback.
            value = self._read_feedback(entry)
            if worked:
                value.update(works=value["works"] + 1, fails=0,
                             last_verified=max(value["last_verified"] or today, today))
            else:
                value["fails"] += 1
            updated["last_verified"] = max(entry["last_verified"], updated["last_verified"])
            atomic(self.path(entry, "feedback"), value)
            self.entries[entry_id] = updated
            return copy.deepcopy(updated)

    def lookup(self, app, goal="", ui="", tags=(), limit=3):
        app = schema.normalize_app(app)
        if type(limit) is not int or not 1 <= limit <= 3:
            raise ValueError("limit: requires 1..3")
        words = keywords(" ".join([goal, ui, *tags]))
        today = date.today().isoformat()
        matches = []
        for entry in self.entries.values():
            if entry["app"] != app or entry["status"] == "draft":
                continue
            entry = schema.age_out(entry, today)
            score = len(words & keywords(" ".join([entry["goal"], entry.get("ui", ""), *entry.get("tags", [])])))
            order = (entry["source"] != "local", schema.STATUSES.index(entry["status"]), -score,
                     -date.fromisoformat(entry["last_verified"]).toordinal(), entry["id"])
            matches.append((order, entry))
        return [dict(entry=copy.deepcopy(e), source=e["source"],
                     days_since_verified=max(0, (date.today() - date.fromisoformat(e["last_verified"])).days),
                     works=e["works"]) for _, e in sorted(matches)[:limit]]

    def drafts(self, agent=None):
        return [copy.deepcopy(e) for e in self.entries.values()
                if e["status"] == "draft" and (agent is None or e.get("agent") == agent)]
