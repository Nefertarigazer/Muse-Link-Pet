"""query - read API for the mailbox UI (part 3) and the CLI.

Requirements
  * list_threads(): id, title (first request's first line, <= 60 chars),
    participants, status, last_activity, unread count, newest snippet;
    newest first.
  * thread(thread_id): messages in time order with role, state, trust,
    release card and attachments.
  * search(q, limit): FTS5 trigram match, LIKE for q shorter than 3 chars or
    when fts=false; parameterized SQL only.
  * Never writes; works while sync() is running.
"""

import json
import sqlite3

from . import model, store

_MESSAGE_COLUMNS = (
    "message_id", "req_id", "seq", "thread_id", "role", "type", "status",
    "final", "text", "payload", "created_at", "replied_at", "sort_ts",
    "trust", "state", "dir", "parse_error", "source_path", "source_sha256",
)


def _connect(index_path):
    return store.open_reader(index_path)


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _message(row):
    msg = dict((name, row[name]) for name in _MESSAGE_COLUMNS)
    msg["payload"] = _json(msg.get("payload"), {})
    msg["final"] = bool(msg.get("final"))
    return msg


def _thread(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "participants": _json(row["participants"], []),
        "status": row["status"],
        "last_activity": row["last_activity"],
        "last_ts": float(row["last_ts"]),
        "unread": int(row["unread"]),
        "snippet": row["snippet"],
        "message_count": int(row["message_count"]),
    }


def list_threads(index_path):
    conn = _connect(index_path)
    try:
        rows = conn.execute(
            """SELECT id, title, participants, status, last_activity, last_ts,
                      unread, snippet, message_count
               FROM threads
               ORDER BY last_ts DESC, id ASC""").fetchall()
        return [_thread(row) for row in rows]
    finally:
        conn.close()


def _release_card(conn, req_id):
    row = conn.execute(
        """SELECT version, package_path, package_size, sha256, setup_path,
                  setup_size, setup_sha256, test, status
           FROM releases WHERE req_id=?""", (req_id,)).fetchone()
    if row is None:
        return None
    return {
        "version": row["version"],
        "package_path": row["package_path"],
        "package_size": int(row["package_size"]),
        "sha256": row["sha256"],
        "setup_path": row["setup_path"],
        "setup_size": int(row["setup_size"]),
        "setup_sha256": row["setup_sha256"],
        "test": bool(row["test"]),
        "status": row["status"],
    }


def _attachments(conn, req_id):
    rows = conn.execute(
        """SELECT kind, path, size, sha256 FROM attachments
           WHERE req_id=? ORDER BY kind, path""", (req_id,)).fetchall()
    return [{
        "kind": row["kind"],
        "path": row["path"],
        "size": int(row["size"]),
        "sha256": row["sha256"],
    } for row in rows]


def thread(index_path, thread_id):
    conn = _connect(index_path)
    try:
        row = conn.execute(
            """SELECT id, title, participants, status, last_activity, last_ts,
                      unread, snippet, message_count
               FROM threads WHERE id=?""", (thread_id,)).fetchone()
        if row is None:
            return None
        result = _thread(row)
        rows = conn.execute(
            """SELECT * FROM messages WHERE thread_id=?
               ORDER BY sort_ts ASC,
                        CASE role WHEN 'agent' THEN 0 ELSE 1 END ASC,
                        req_id ASC, seq ASC, message_id ASC""",
            (thread_id,)).fetchall()
        messages = []
        for msg_row in rows:
            msg = _message(msg_row)
            if msg["role"] == "agent":
                msg["release_card"] = _release_card(conn, msg["req_id"])
                msg["attachments"] = _attachments(conn, msg["req_id"])
            else:
                msg["release_card"] = None
                msg["attachments"] = []
            messages.append(msg)
        result["messages"] = messages
        return result
    finally:
        conn.close()


def _like_pattern(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _fts_on(conn):
    meta = store.meta(conn)
    if meta.get("fts", "0") != "1":
        return False
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()
    return row is not None


def search(index_path, q, limit=50):
    q = "" if q is None else str(q)
    if not q:
        return []
    conn = _connect(index_path)
    try:
        use_fts = _fts_on(conn) and len(q) >= 3
        if use_fts:
            # Quote the user's text as one FTS5 string literal.  Doubling
            # embedded quotes is FTS5's escape; % _ " * - ( ) are then data.
            match = '"' + q.replace('"', '""') + '"'
            sql = """SELECT m.*, t.title AS thread_title
                     FROM messages_fts
                     JOIN messages m ON m.rowid = messages_fts.rowid
                     LEFT JOIN threads t ON t.id = m.thread_id
                     WHERE messages_fts MATCH ?
                     ORDER BY bm25(messages_fts), m.sort_ts DESC, m.message_id
                     LIMIT ?"""
            try:
                rows = conn.execute(sql, (match, int(limit))).fetchall()
            except sqlite3.Error:
                rows = None
            if rows is not None:
                return _search_rows(conn, rows)
        sql = """SELECT m.*, t.title AS thread_title
                 FROM messages m
                 LEFT JOIN threads t ON t.id = m.thread_id
                 WHERE m.text LIKE ? ESCAPE '\\'
                 ORDER BY m.sort_ts DESC, m.message_id
                 LIMIT ?"""
        rows = conn.execute(sql, (_like_pattern(q), int(limit))).fetchall()
        return _search_rows(conn, rows)
    finally:
        conn.close()


def _search_rows(conn, rows):
    result = []
    for row in rows:
        msg = _message(row)
        msg["release_card"] = _release_card(conn, msg["req_id"]) if msg["role"] == "agent" else None
        msg["attachments"] = _attachments(conn, msg["req_id"]) if msg["role"] == "agent" else []
        msg["thread_title"] = row["thread_title"] if "thread_title" in row.keys() else ""
        result.append(msg)
    return result


def attention_items(index_path):
    """Return active requests requiring user attention (needs_user / pending enroll)."""
    conn = _connect(index_path)
    try:
        items = []
        rows = conn.execute(
            """SELECT r.req_id, r.thread_id,
                      (SELECT m.message_id FROM messages m
                       WHERE m.req_id = r.req_id
                       ORDER BY m.sort_ts DESC, COALESCE(m.seq, -1) DESC, m.rowid DESC
                       LIMIT 1) AS latest_msg_id
               FROM requests r
               WHERE r.state = 'needs_user'
                 AND NOT EXISTS (
                     SELECT 1 FROM requests r2
                     WHERE r2.thread_id = r.thread_id
                       AND r2.req_id != r.req_id
                       AND (
                           r2.reply_to = r.req_id
                           OR COALESCE((SELECT MAX(m2.sort_ts) FROM messages m2 WHERE m2.req_id = r2.req_id AND m2.role = 'agent'), 0)
                              > COALESCE((SELECT MAX(m1.sort_ts) FROM messages m1 WHERE m1.req_id = r.req_id AND m1.role = 'agent'), 0)
                       )
                 )
               ORDER BY r.thread_id ASC, r.req_id ASC"""
        ).fetchall()
        seen_reqs = set()
        for row in rows:
            seen_reqs.add(row["req_id"])
            latest = row["latest_msg_id"] or row["req_id"]
            items.append({
                "req_id": row["req_id"],
                "thread_id": row["thread_id"],
                "token": "%s:%s" % (row["req_id"], latest),
            })
        pending_rows = conn.execute(
            """SELECT sha256, record_json FROM files
               WHERE kind = 'request' AND dir = 'pending'
               ORDER BY path ASC"""
        ).fetchall()
        pending_enrolls = []
        for row in pending_rows:
            rec = _json(row["record_json"], {})
            if rec.get("type") != "enroll":
                continue
            req_id = rec.get("req_id")
            if not req_id or req_id in seen_reqs:
                continue
            seen_reqs.add(req_id)
            pending_enrolls.append((req_id, row["sha256"] or "", model.thread_key(rec)))
        if pending_enrolls:
            thread_map = {}
            resolved = set()
            req_ids = [r[0] for r in pending_enrolls]
            for start in range(0, len(req_ids), 400):
                batch = req_ids[start:start + 400]
                placeholders = ",".join("?" for _ in batch)
                trows = conn.execute(
                    """SELECT r.req_id, r.thread_id, r.state,
                              EXISTS (SELECT 1 FROM messages mf WHERE mf.req_id = r.req_id AND mf.final = 1) AS has_final,
                              EXISTS (
                                  SELECT 1 FROM requests r2
                                  WHERE r2.thread_id = r.thread_id AND r2.req_id != r.req_id
                                    AND (r2.reply_to = r.req_id
                                         OR COALESCE((SELECT MAX(m2.sort_ts) FROM messages m2 WHERE m2.req_id = r2.req_id AND m2.role = 'agent'), 0)
                                            > COALESCE((SELECT MAX(m1.sort_ts) FROM messages m1 WHERE m1.req_id = r.req_id AND m1.role = 'agent'), 0))
                              ) AS superseded
                       FROM requests r WHERE r.req_id IN (%s)""" % placeholders, batch)
                for trow in trows:
                    thread_map[trow["req_id"]] = trow["thread_id"]
                    if trow["state"] in ("replied", "rejected") or trow["has_final"] or trow["superseded"]:
                        resolved.add(trow["req_id"])
            for req_id, sha, fallback_tid in pending_enrolls:
                if req_id in resolved:
                    continue
                items.append({
                    "req_id": req_id,
                    "thread_id": thread_map.get(req_id) or fallback_tid,
                    "token": "%s:pending:%s" % (req_id, sha),
                })
        return items
    finally:
        conn.close()


def request_meta(index_path, req_ids):
    """Read request metadata in bounded batches; IDs are always parameters."""
    ids = list(dict.fromkeys(req_ids))
    if not ids:
        return {}
    conn = _connect(index_path)
    try:
        result = {}
        for start in range(0, len(ids), 400):
            batch = ids[start:start + 400]
            rows = conn.execute(
                "SELECT req_id, agent, final_read, reply_to FROM requests WHERE req_id IN ("
                + ",".join("?" for _ in batch) + ")", batch)
            for row in rows:
                result[row["req_id"]] = {"agent": row["agent"],
                    "final_read": bool(row["final_read"]), "reply_to": row["reply_to"]}
        return result
    finally:
        conn.close()


def stats(index_path):
    conn = _connect(index_path)
    try:
        meta = store.meta(conn)
        def one(sql):
            row = conn.execute(sql).fetchone()
            return int(row[0]) if row else 0
        try:
            schema_version = int(meta.get("schema_version", "0"))
        except ValueError:
            schema_version = 0
        return {
            "schema_version": schema_version,
            "built_at": meta.get("built_at", ""),
            "fts": meta.get("fts", "0") == "1",
            "files": one("SELECT COUNT(*) FROM files"),
            "messages": one("SELECT COUNT(*) FROM messages"),
            "threads": one("SELECT COUNT(*) FROM threads"),
            "requests": one("SELECT COUNT(*) FROM requests"),
            "releases": one("SELECT COUNT(*) FROM releases"),
            "parse_errors": one("SELECT COUNT(*) FROM files WHERE parse_error IS NOT NULL"),
        }
    finally:
        conn.close()
