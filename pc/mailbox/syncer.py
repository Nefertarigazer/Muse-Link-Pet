"""Single background writer; bridge status reads only a small cached snapshot."""
from i18n import t
import json
import os
from pathlib import Path
import threading
import uuid
from datetime import datetime, timezone

from mlindex import MAILBOX_DIRS, SCHEMA_VERSION, indexer, query, store


class Syncer:
    def __init__(self, root, index_path, interval=10, seen_path=None):
        self._root, self._index = root, index_path
        self._interval = interval
        self._seen_path = Path(seen_path) if seen_path else Path(index_path).with_name("seen.json")
        self._cv = threading.Condition()
        self._stop = False
        self._requested = False
        self._running = False
        self._generation = 0
        self._thread = None
        self.last_ok = None
        self.unread = 0
        self.attention = 0
        self._attention_items = []
        self._attention_by_thread = {}
        self._seen = self._load_seen()
        self._snapshot = {"built_at": "", "messages": 0, "threads": 0,
                          "fts": False, "syncing": False, "last_error": None}
        try:
            self._cache_stats()
        except Exception:
            pass

    def _load_seen(self):
        try:
            data = json.loads(self._seen_path.read_text(encoding="utf-8"))
            tokens = data.get("tokens") if isinstance(data, dict) else None
            if isinstance(tokens, list):
                return [str(x) for x in tokens if isinstance(x, str) and x][-2000:]
        except (OSError, ValueError):
            pass
        return []

    def _save_seen(self, tokens):
        try:
            self._seen_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._seen_path.with_suffix(".tmp")
            temp.write_text(json.dumps({"tokens": tokens[-2000:]}, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self._seen_path)
        except OSError:
            pass

    def _recompute_attention_locked(self):
        seen_set = set(self._seen)
        counts = {}
        total = 0
        for item in self._attention_items:
            if item["token"] in seen_set:
                continue
            tid = item["thread_id"]
            counts[tid] = counts.get(tid, 0) + 1
            total += 1
        self.attention = total
        self._attention_by_thread = counts

    def _cache_stats(self):
        stats = query.stats(self._index)
        unread = sum(max(0, int(row["unread"])) for row in query.list_threads(self._index))
        items = query.attention_items(self._index)
        with self._cv:
            self.unread = unread
            self._attention_items = items
            self._recompute_attention_locked()
            for key in ("built_at", "messages", "threads", "fts"):
                self._snapshot[key] = stats[key]

    def thread_attention(self, thread_id):
        with self._cv:
            return int(self._attention_by_thread.get(thread_id, 0))

    def mark_seen(self, thread_ids=None):
        if thread_ids is None:
            targets = None
        else:
            targets = {str(t) for t in thread_ids if isinstance(t, str) and t}
            if not targets:
                with self._cv:
                    return self.attention
        snapshot_tokens = None
        with self._cv:
            seen_set = set(self._seen)
            added = False
            for item in self._attention_items:
                if targets is None or item["thread_id"] in targets:
                    tok = item["token"]
                    if tok not in seen_set:
                        seen_set.add(tok)
                        self._seen.append(tok)
                        added = True
            if len(self._seen) > 2000:
                self._seen = self._seen[-2000:]
            self._recompute_attention_locked()
            count = self.attention
            if added:
                snapshot_tokens = list(self._seen)
        if snapshot_tokens is not None:
            self._save_seen(snapshot_tokens)
        return count

    def delete_thread(self, thread_ids):
        targets = sorted({str(t) for t in (thread_ids or []) if isinstance(t, str) and t})
        if not targets:
            with self._cv:
                return {"deleted": 0, "unread": self.attention}
        req_ids = set()
        paths = set()
        try:
            conn = store.open_reader(self._index)
        except FileNotFoundError:
            conn = None
        if conn is not None:
            try:
                for start in range(0, len(targets), 400):
                    batch = targets[start:start + 400]
                    placeholders = ",".join("?" for _ in batch)
                    for row in conn.execute(
                        "SELECT req_id, source_path FROM messages WHERE thread_id IN (%s)" % placeholders,
                        batch,
                    ):
                        if row["req_id"]:
                            req_ids.add(row["req_id"])
                        if row["source_path"]:
                            paths.add(os.path.abspath(row["source_path"]))
                    for row in conn.execute(
                        "SELECT req_id FROM requests WHERE thread_id IN (%s)" % placeholders,
                        batch,
                    ):
                        if row["req_id"]:
                            req_ids.add(row["req_id"])
            finally:
                conn.close()
        root = Path(self._root).resolve()
        allowed_dirs = {d: (root / d).resolve() for d in MAILBOX_DIRS}
        candidates = []
        for p in paths:
            cand = Path(p).resolve()
            for dir_name, dir_root in allowed_dirs.items():
                if cand.is_relative_to(dir_root) and cand.is_file():
                    candidates.append((dir_name, cand))
                    break
        if req_ids:
            for dir_name, dir_root in allowed_dirs.items():
                if not dir_root.is_dir():
                    continue
                try:
                    with os.scandir(dir_root) as entries:
                        for entry in entries:
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            name = entry.name
                            matched = any(
                                name == f"{rid}.json"
                                or (name.startswith(f"{rid}.")
                                    and (name.endswith(".reply.json") or name.endswith(".ack")))
                                for rid in req_ids
                            )
                            if matched:
                                candidates.append((dir_name, Path(entry.path).resolve()))
                except OSError:
                    continue
        moved = 0
        seen_paths = set()
        trash_batch = root / "trash" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6])
        for dir_name, src in candidates:
            key = os.path.normcase(str(src))
            if key in seen_paths or not src.exists():
                continue
            seen_paths.add(key)
            dest_dir = trash_batch / dir_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            if dest.exists():
                dest = dest_dir / f"{src.stem}.{moved}{src.suffix}"
            try:
                os.replace(src, dest)
                moved += 1
            except OSError:
                pass
        self.mark_seen(targets)
        if moved:
            self.refresh()
        with self._cv:
            unread = self.attention
        return {"deleted": moved, "unread": unread}

    def start(self):
        with self._cv:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="mailbox-sync", daemon=True)
                self._thread.start()

    def status(self):
        with self._cv:
            return dict(self._snapshot)

    def request(self):
        """Nonblocking tray refresh; coalesce with a running cycle."""
        self.start()
        with self._cv:
            if not self._running:
                self._requested = True
            self._cv.notify_all()

    def refresh(self):
        # Only wait on the bridge thread: actual IO always stays in _loop.
        self.start()
        with self._cv:
            target = self._generation + 1
            if not self._running:
                self._requested = True
            self._cv.notify_all()
            self._cv.wait_for(lambda: self._generation >= target or self._stop)
            return dict(self._snapshot)

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=2)

    def _check_source(self):
        # sources tolerates missing folders and permission errors. Detect an
        # inaccessible share before sync can mistake it for deleted letters.
        with os.scandir(self._root) as entries:
            list(entries)
        for name in MAILBOX_DIRS:
            try:
                with os.scandir(os.path.join(self._root, name)) as entries:
                    list(entries)
            except FileNotFoundError:
                continue

    def _cycle(self):
        try:
            self._check_source()
            try:
                rebuild = query.stats(self._index)["schema_version"] != SCHEMA_VERSION
            except Exception:
                rebuild = True
            (indexer.rebuild if rebuild else indexer.sync)(self._root, self._index)
            self._cache_stats()
            with self._cv:
                self.last_ok = datetime.now(timezone.utc).isoformat()
                self._snapshot["last_error"] = None
        except store.WriterBusy:
            pass
        except Exception as exc:
            with self._cv:
                self._snapshot["last_error"] = t('mailbox.sync_failed', error=type(exc).__name__)

    def _loop(self):
        while True:
            with self._cv:
                if self._stop:
                    return
                self._requested = False
                self._running = True
                self._snapshot["syncing"] = True
            self._cycle()
            with self._cv:
                self._running = False
                self._snapshot["syncing"] = False
                self._generation += 1
                self._cv.notify_all()
                self._cv.wait_for(lambda: self._requested or self._stop, timeout=self._interval)
