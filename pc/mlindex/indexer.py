"""indexer - sync and rebuild.

Requirements
  * rebuild(): new database file next to the old one, full scan, then atomic
    replace; a crash leaves the old index usable.
  * sync(): rescan only files whose (size, mtime_ns) changed or that
    appeared/disappeared, then recompute every affected request and thread
    from the records (model.py). Result must equal rebuild() row for row.
  * Budgets: no-change sync < 1 s; rebuild of 10 000 messages < 30 s.
  * Returns counts (files, messages, threads, changed, skipped, errors).
"""

import json
import os
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from . import SCHEMA_VERSION, model, sources, store

_FRAC_RX = re.compile(r"(\.\d{6})\d+")

_REQUEST_DIR_PRIORITY = {
    "sent": 0, "inbox": 1, "claimed": 2, "pending": 3, "rejected": 4, "done": 5,
}
_REPLY_DIR_PRIORITY = {"archive": 0, "outbox": 1, "quarantine": 2}


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _parse_time(text):
    if not isinstance(text, str) or not text.strip():
        return 0.0
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    value = _FRAC_RX.sub(r"\1", value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.timestamp()
    except (OverflowError, OSError):
        return 0.0


def _sort_ts(record):
    if record.get("kind") == "request":
        value = _parse_time(record.get("created_at"))
    else:
        value = _parse_time(record.get("replied_at"))
    if not value:
        ts = record.get("ts")
        if isinstance(ts, int) and not isinstance(ts, bool):
            value = float(ts)
    return value


def _time_text(record):
    if record.get("kind") == "request":
        text = record.get("created_at")
    else:
        text = record.get("replied_at")
    if isinstance(text, str) and text:
        return text
    ts = record.get("ts")
    if isinstance(ts, int) and not isinstance(ts, bool):
        try:
            return datetime.fromtimestamp(ts, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return ""


def _pick(records, priority):
    return min(records, key=lambda r: (
        1 if r.get("parse_error") else 0,
        priority.get(r.get("dir"), 99),
        r.get("path") or "",
    ))


def _first_line(text, limit):
    if not isinstance(text, str):
        return ""
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def _snippet(text, limit=120):
    if not isinstance(text, str):
        return ""
    value = " ".join(text.split())
    return value[:limit]


def _dump_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_valid_final(record):
    return bool(
        record.get("kind") == "reply"
        and not record.get("parse_error")
        and record.get("dir") != "quarantine"
        and record.get("final")
    )


def _final_reply(records):
    finals = [r for r in records if _is_valid_final(r)]
    finals.sort(key=lambda r: (int(r.get("seq") or 0), r.get("path") or ""))
    return finals[-1] if finals else None


def _fts_enabled(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()
    if row is None:
        return False
    meta = store.meta(conn)
    return meta.get("fts", "0") == "1"


def _request_message(rec, thread_id, state):
    sort_ts = _sort_ts(rec)
    return {
        "message_id": rec["req_id"],
        "req_id": rec["req_id"],
        "seq": None,
        "thread_id": thread_id,
        "role": "agent",
        "type": rec.get("type") or "",
        "status": "",
        "final": 0,
        "text": rec.get("text") or "",
        "payload": rec.get("payload") or {},
        "created_at": rec.get("created_at") or "",
        "replied_at": "",
        "sort_ts": sort_ts,
        "trust": None,
        "state": state,
        "dir": rec.get("dir") or "",
        "parse_error": rec.get("parse_error"),
        "source_path": rec["path"],
        "source_sha256": rec["sha256"],
    }


def _reply_message(rec, thread_id, state):
    sort_ts = _sort_ts(rec)
    seq = int(rec.get("seq") or 0)
    return {
        "message_id": "%s#%d" % (rec["req_id"], seq),
        "req_id": rec["req_id"],
        "seq": seq,
        "thread_id": thread_id,
        "role": "muse",
        "type": "",
        "status": rec.get("status") or "",
        "final": 1 if rec.get("final") else 0,
        "text": rec.get("text") or "",
        "payload": rec.get("payload") or {},
        "created_at": "",
        "replied_at": rec.get("replied_at") or "",
        "sort_ts": sort_ts,
        "trust": model.trust(rec),
        "state": state,
        "dir": rec.get("dir") or "",
        "parse_error": rec.get("parse_error"),
        "source_path": rec["path"],
        "source_sha256": rec["sha256"],
    }


def _insert_message(conn, msg, fts):
    cur = conn.execute(
        """INSERT INTO messages
           (message_id, req_id, seq, thread_id, role, type, status, final,
            text, payload, created_at, replied_at, sort_ts, trust, state,
            dir, parse_error, source_path, source_sha256)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (msg["message_id"], msg["req_id"], msg["seq"], msg["thread_id"],
         msg["role"], msg["type"], msg["status"], msg["final"], msg["text"],
         _dump_json(msg["payload"]), msg["created_at"], msg["replied_at"],
         msg["sort_ts"], msg["trust"], msg["state"], msg["dir"],
         msg["parse_error"], msg["source_path"], msg["source_sha256"]))
    if fts:
        conn.execute("INSERT INTO messages_fts(rowid, text) VALUES (?, ?)",
                     (cur.lastrowid, msg["text"]))


def _write_files(conn, records):
    conn.execute("DELETE FROM files")
    for rec in sorted(records, key=lambda r: _norm(r["path"])):
        conn.execute(
            """INSERT INTO files
               (path, dir, name, kind, size, mtime_ns, sha256, parse_error,
                record_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (rec["path"], rec["dir"], rec["name"], rec["kind"], rec["size"],
             rec["mtime_ns"], rec["sha256"], rec.get("parse_error"),
             _dump_json(rec)))


def _build_model(records):
    """Turn physical files into messages, requests, threads, releases."""
    by_req = defaultdict(list)
    for rec in records:
        req_id = rec.get("req_id")
        if req_id:
            by_req[req_id].append(rec)

    request_groups = defaultdict(list)
    reply_groups = defaultdict(list)
    for rec in records:
        req_id = rec.get("req_id")
        if not req_id:
            continue
        if rec.get("kind") == "request":
            request_groups[req_id].append(rec)
        elif rec.get("kind") == "reply":
            reply_groups[(req_id, int(rec.get("seq") or 0))].append(rec)

    canonical_requests = {}
    for req_id, group in request_groups.items():
        canonical_requests[req_id] = _pick(group, _REQUEST_DIR_PRIORITY)
    canonical_replies = {}
    for key, group in reply_groups.items():
        canonical_replies[key] = _pick(group, _REPLY_DIR_PRIORITY)

    req_states = {}
    req_finals = {}
    req_thread = {}
    for req_id in sorted(by_req):
        state, final_read = model.request_state(req_id, by_req[req_id])
        req_states[req_id] = (state, final_read)
        req_finals[req_id] = _final_reply(by_req[req_id])
        request = canonical_requests.get(req_id)
        if request is not None:
            req_thread[req_id] = model.thread_key(request)
        else:
            # An orphan reply keeps an explicit thread only through its
            # request; without one it falls back to the normal default rule.
            synthetic = {"type": "chat", "agent": "", "thread_id": None}
            for rec in by_req[req_id]:
                if rec.get("kind") == "reply" and rec.get("agent"):
                    synthetic["agent"] = rec.get("agent")
                    break
            req_thread[req_id] = model.thread_key(synthetic)

    messages = []
    for req_id, rec in canonical_requests.items():
        state = req_states[req_id][0]
        messages.append(_request_message(rec, req_thread[req_id], state))
    for (req_id, seq), rec in canonical_replies.items():
        state = req_states.get(req_id, ("delivered", False))[0]
        messages.append(_reply_message(rec, req_thread.get(req_id, "unknown/chat"), state))
    messages.sort(key=lambda m: (
        m["sort_ts"], 0 if m["role"] == "agent" else 1, m["req_id"],
        -1 if m["seq"] is None else m["seq"], m["source_path"]))

    by_thread = defaultdict(list)
    for msg in messages:
        by_thread[msg["thread_id"]].append(msg)

    thread_rows = []
    for thread_id in sorted(by_thread):
        group = by_thread[thread_id]
        req_ids = [req_id for req_id, thread in req_thread.items() if thread == thread_id]
        req_ts = {}
        reply_targets = set()
        for req_id in req_ids:
            creq = canonical_requests.get(req_id)
            if creq is not None:
                req_ts[req_id] = _sort_ts(creq)
                if creq.get("reply_to"):
                    reply_targets.add(creq["reply_to"])
            else:
                req_ts[req_id] = max((_sort_ts(rec) for rec in by_req.get(req_id, [])), default=0.0)
        latest_req_ts = max(req_ts.values(), default=0.0)
        states = []
        for req_id in req_ids:
            st = req_states[req_id][0]
            if st == "needs_user" and (req_id in reply_targets or req_ts[req_id] < latest_req_ts):
                st = "replied"
            states.append(st)
        pending_enroll = False
        for idx, req_id in enumerate(req_ids):
            if req_states[req_id][0] in ("replied", "rejected") or req_id in reply_targets or req_ts[req_id] < latest_req_ts:
                continue
            has_final_reply = any(
                rec.get("kind") == "reply" and bool(rec.get("final"))
                for rec in by_req.get(req_id, [])
            )
            is_pending_enroll = any(
                rec.get("kind") == "request" and rec.get("dir") == "pending"
                and rec.get("type") == "enroll"
                for rec in by_req.get(req_id, [])
            )
            if has_final_reply:
                if is_pending_enroll:
                    states[idx] = "rejected"
                continue
            if is_pending_enroll:
                pending_enroll = True
        status = model.thread_state(states, pending_enroll)

        first = None
        for req_id in sorted(req_ids):
            rec = canonical_requests.get(req_id)
            if rec is None:
                continue
            if first is None or (_sort_ts(rec), req_id) < (_sort_ts(first), first["req_id"]):
                first = rec
        title = _first_line(first.get("text") if first else "", 60)
        if not title:
            title = next((_first_line(m.get("text"), 60) for m in group if _first_line(m.get("text"), 60)), "")
        if not title:
            title = thread_id

        participants = set()
        for req_id in req_ids:
            rec = canonical_requests.get(req_id)
            if rec is not None and rec.get("agent"):
                participants.add(rec["agent"])
        for msg in group:
            if msg["role"] == "muse":
                participants.add("muse")
        participants = sorted(participants)

        newest = max(group, key=lambda m: (m["sort_ts"], m["message_id"]))
        last_activity = ""
        if newest["sort_ts"]:
            last_activity = newest["replied_at"] or newest["created_at"]
        unread = sum(1 for m in group
                     if m["role"] == "muse" and m["trust"] == "unread_unverified")
        thread_rows.append({
            "id": thread_id,
            "title": title,
            "participants": participants,
            "status": status,
            "last_activity": last_activity,
            "last_ts": max(m["sort_ts"] for m in group),
            "unread": unread,
            "snippet": _snippet(newest["text"]),
            "message_count": len(group),
        })
        for msg in group:
            msg["thread_id"] = thread_id

    return messages, req_states, req_finals, req_thread, canonical_requests, thread_rows


def _write_model(conn, records, fts):
    messages, req_states, req_finals, req_thread, canonical_requests, thread_rows = \
        _build_model(records)

    for msg in messages:
        _insert_message(conn, msg, fts)
    for row in thread_rows:
        conn.execute(
            """INSERT INTO threads
               (id, title, participants, status, last_activity, last_ts,
                unread, snippet, message_count)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (row["id"], row["title"], json.dumps(row["participants"], ensure_ascii=False),
             row["status"], row["last_activity"], row["last_ts"], row["unread"],
             row["snippet"], row["message_count"]))

    for req_id in sorted(req_states):
        state, final_read = req_states[req_id]
        request = canonical_requests.get(req_id)
        agent = request.get("agent") if request else ""
        kind = request.get("type") if request else ""
        created_at = request.get("created_at") if request else ""
        reply_to = request.get("reply_to") if request else None
        conn.execute(
            """INSERT INTO requests
               (req_id, thread_id, state, final_read, agent, type, created_at,
                reply_to)
               VALUES (?,?,?,?,?,?,?,?)""",
            (req_id, req_thread.get(req_id, "unknown/chat"), state,
             1 if final_read else 0, agent or "", kind or "", created_at or "",
             reply_to))
        card = model.release_card(request, req_finals.get(req_id)) if request else None
        if card is None:
            continue
        conn.execute(
            """INSERT INTO releases
               (req_id, thread_id, version, package_path, package_size, sha256,
                setup_path, setup_size, setup_sha256, test, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (req_id, req_thread.get(req_id, "unknown/chat"), card["version"],
             card["package_path"], card["package_size"], card["sha256"],
             card["setup_path"], card["setup_size"], card["setup_sha256"],
             1 if card["test"] else 0, card["status"]))
        for kind_name, path, size, sha in (
                ("package", card["package_path"], card["package_size"], card["sha256"]),
                ("setup", card["setup_path"], card["setup_size"], card["setup_sha256"])):
            if not path:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO attachments
                   (req_id, message_id, kind, path, size, sha256)
                   VALUES (?,?,?,?,?,?)""",
                (req_id, req_id, kind_name, path, size, sha))

    return len(messages), len(thread_rows)


def _replace(conn, records):
    fts = _fts_enabled(conn)
    with conn:
        if fts:
            conn.execute("DELETE FROM messages_fts")
        for table in ("attachments", "releases", "requests", "messages",
                      "threads", "files"):
            conn.execute("DELETE FROM %s" % table)
        _write_files(conn, records)
        message_count, thread_count = _write_model(conn, records, fts)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('built_at', ?)",
            (datetime.now(timezone.utc).isoformat(),))
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),))
    return {
        "files": len(records),
        "messages": message_count,
        "threads": thread_count,
        "errors": sum(1 for r in records if r.get("parse_error")),
    }


def _counts(conn):
    def one(sql):
        row = conn.execute(sql).fetchone()
        return int(row[0]) if row else 0
    return {
        "files": one("SELECT COUNT(*) FROM files"),
        "messages": one("SELECT COUNT(*) FROM messages"),
        "threads": one("SELECT COUNT(*) FROM threads"),
        "errors": one("SELECT COUNT(*) FROM files WHERE parse_error IS NOT NULL"),
    }


def _cleanup_stale_builds(index_path):
    """Remove leftover .build-* temp databases under the single-writer lock."""
    directory = os.path.dirname(index_path)
    prefix = os.path.basename(index_path) + ".build-"
    if not directory or not os.path.isdir(directory):
        return
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith(prefix):
                    _remove(entry.path)
    except OSError:
        pass


def sync(bridge_root, index_path):
    """Incremental update.  Returns counts (files, messages, threads, ...)."""
    index_path = os.path.abspath(index_path)
    entries = list(sources.list_files(bridge_root))
    disk = set(_norm(path) for path, _, _, _ in entries)
    writer = store.open_writer(index_path)
    try:
        _cleanup_stale_builds(index_path)
        conn = writer.conn
        rows = {}
        for row in conn.execute("SELECT path, size, mtime_ns, record_json FROM files"):
            rows[_norm(row["path"])] = row
        changed = []
        unchanged = 0
        for path, dir_name, size, mtime_ns in entries:
            row = rows.get(_norm(path))
            if row is not None and int(row["size"]) == size and int(row["mtime_ns"]) == mtime_ns:
                unchanged += 1
            else:
                changed.append((path, dir_name, size, mtime_ns))
        deleted = set(rows) - disk
        result = _counts(conn)
        result.update({"changed": len(changed), "skipped": unchanged})
        if not changed and not deleted:
            return result

        records = []
        for path, dir_name, size, mtime_ns in entries:
            row = rows.get(_norm(path))
            if row is not None and int(row["size"]) == size and int(row["mtime_ns"]) == mtime_ns:
                try:
                    records.append(json.loads(row["record_json"]))
                    continue
                except Exception:
                    pass
            rec = sources.parse_file(path, dir_name)
            if rec is not None:
                records.append(rec)
        written = _replace(conn, records)
        result.update(written)
        result.update({"changed": len(changed) + len(deleted), "skipped": unchanged})
        return result
    finally:
        writer.close()


def _checkpoint_old(path):
    """Checkpoint the old WAL.  Returns the number of blocking readers."""
    if not os.path.exists(path):
        return 0
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            return int(row[0]) if row else 1
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return 1


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _backup_into(temp, target):
    """Copy a finished database over a live one (Windows readers hold it open)."""
    source = sqlite3.connect(temp, timeout=5.0)
    dest = None
    try:
        dest = sqlite3.connect(target, timeout=5.0)
        dest.execute("PRAGMA busy_timeout=5000")
        source.backup(dest)
    finally:
        try:
            source.close()
        finally:
            if dest is not None:
                dest.close()


def rebuild(bridge_root, index_path):
    """Full scan into a new file, then atomically replace the old index."""
    index_path = os.path.abspath(index_path)
    directory = os.path.dirname(index_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with store.Lock(index_path):
        _cleanup_stale_builds(index_path)
        records = list(sources.scan(bridge_root))
        temp = "%s.build-%d-%s" % (index_path, os.getpid(), uuid.uuid4().hex[:8])
        conn = None
        try:
            conn = sqlite3.connect(temp, timeout=5.0)
            conn.row_factory = sqlite3.Row
            fts = store.init_schema(conn)
            written = _replace(conn, records)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            conn = None
            _remove(temp + "-wal")
            _remove(temp + "-shm")
            busy = _checkpoint_old(index_path)
            replaced = False
            if not busy:
                # No reader was holding the old WAL.  Remove stale sidecars
                # and do the atomic replace.
                _remove(index_path + "-wal")
                _remove(index_path + "-shm")
                try:
                    os.replace(temp, index_path)
                    replaced = True
                except OSError:
                    # A reader opened between checkpoint and replace (or
                    # Windows denied the delete of an open file).  Fall back
                    # to SQLite's online backup below.
                    pass
            if not replaced:
                _backup_into(temp, index_path)
            result = dict(written)
            result.update({"changed": len(records), "skipped": 0})
            return result
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            _remove(temp)
            _remove(temp + "-wal")
            _remove(temp + "-shm")
