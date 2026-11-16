"""store - the SQLite file.

Requirements
  * Path: muselink.json index.path, default <bridge root>\\index\\threads.sqlite.
    journal_mode=WAL, busy_timeout, meta(schema_version, built_at, fts).
  * Tables: files (path, size, mtime_ns, sha256), threads, messages (verbatim
    text, payload JSON, source_path, source_sha256, role, trust, seq),
    requests (state, final_read), releases, attachments (path, size, sha256).
  * FTS5 table over message text with tokenize='trigram'; if FTS5 or trigram
    is missing, build without it and record fts=false (search uses LIKE).
  * One writer at a time: exclusive lock on index\\sync.lock (msvcrt on
    Windows); a second writer exits at once with "busy". Readers never lock.
  * Schema change = bump SCHEMA_VERSION and rebuild; no migrations (cache).
"""

import os
import sqlite3

from . import SCHEMA_VERSION

try:  # msvcrt is the Windows file-locking API; fcntl keeps the tests portable.
    import msvcrt
except ImportError:  # pragma: no cover - only reached on non-Windows hosts
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


class WriterBusy(Exception):
    """Another process holds the sync lock."""


class SchemaError(Exception):
    """The index has an unexpected or unreadable schema."""


SCHEMA_SQL = (
    """CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        dir TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        parse_error TEXT,
        record_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        participants TEXT NOT NULL,
        status TEXT NOT NULL,
        last_activity TEXT NOT NULL,
        last_ts REAL NOT NULL,
        unread INTEGER NOT NULL,
        snippet TEXT NOT NULL,
        message_count INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        rowid INTEGER PRIMARY KEY,
        message_id TEXT NOT NULL UNIQUE,
        req_id TEXT NOT NULL,
        seq INTEGER,
        thread_id TEXT NOT NULL,
        role TEXT NOT NULL,
        type TEXT NOT NULL,
        status TEXT NOT NULL,
        final INTEGER NOT NULL,
        text TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        replied_at TEXT NOT NULL,
        sort_ts REAL NOT NULL,
        trust TEXT,
        state TEXT NOT NULL,
        dir TEXT NOT NULL,
        parse_error TEXT,
        source_path TEXT NOT NULL,
        source_sha256 TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS requests (
        req_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        state TEXT NOT NULL,
        final_read INTEGER NOT NULL,
        agent TEXT NOT NULL,
        type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reply_to TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS releases (
        req_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        version TEXT NOT NULL,
        package_path TEXT NOT NULL,
        package_size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        setup_path TEXT NOT NULL,
        setup_size INTEGER NOT NULL,
        setup_sha256 TEXT NOT NULL,
        test INTEGER NOT NULL,
        status TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS attachments (
        id INTEGER PRIMARY KEY,
        req_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        path TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        UNIQUE (req_id, kind, path)
    )""",
)

FTS_SQL = """CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    tokenize='trigram'
)"""

_TABLES = ("messages_fts", "attachments", "releases", "requests", "messages",
           "threads", "files", "meta")


def fts_available(conn):
    """True when this SQLite has both FTS5 and the trigram tokenizer."""
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.__mlindex_fts_probe USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE temp.__mlindex_fts_probe")
        return True
    except sqlite3.Error:
        return False


def _drop_all(conn):
    for name in _TABLES:
        conn.execute("DROP TABLE IF EXISTS %s" % name)


def _create(conn, fts):
    for sql in SCHEMA_SQL:
        conn.execute(sql)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                 (str(SCHEMA_VERSION),))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts', ?)",
                 ("1" if fts else "0",))
    if fts:
        conn.execute(FTS_SQL)


def _schema_state(conn):
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def configure(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")


def init_schema(conn, fts=None):
    """Create the schema on an empty connection (used by rebuild's temp file)."""
    configure(conn)
    if fts is None:
        fts = fts_available(conn)
    _drop_all(conn)
    _create(conn, bool(fts))
    conn.commit()
    return bool(fts)


def ensure_schema(conn):
    """Create the schema, or reset it when SCHEMA_VERSION changed."""
    configure(conn)
    state = _schema_state(conn)
    if state == str(SCHEMA_VERSION):
        # Keep an older index usable when only optional tables are missing.
        for sql in SCHEMA_SQL:
            conn.execute(sql)
        conn.commit()
        return
    fts = fts_available(conn)
    _drop_all(conn)
    _create(conn, fts)
    conn.commit()


def _lock_path(path):
    return os.path.join(os.path.dirname(os.path.abspath(path)), "sync.lock")


def _acquire(path):
    lock_path = _lock_path(path)
    directory = os.path.dirname(lock_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fp = open(lock_path, "a+b")
    try:
        if fp.seek(0, os.SEEK_END) == 0:
            fp.write(b"0")
            fp.flush()
        fp.seek(0)
        if msvcrt is not None:
            try:
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise WriterBusy("busy")
        elif fcntl is not None:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise WriterBusy("busy")
        else:  # pragma: no cover - no supported locking primitive
            raise WriterBusy("busy")
    except Exception:
        fp.close()
        raise
    return fp


def _release(fp):
    try:
        fp.seek(0)
        if msvcrt is not None:
            msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            fp.close()
        except OSError:
            pass


class Lock:
    """The one-writer lock, without opening the database file."""

    def __init__(self, path):
        self.path = path
        self.fp = _acquire(path)

    def close(self):
        if self.fp is not None:
            _release(self.fp)
            self.fp = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class Writer:
    def __init__(self, conn, lock, path):
        self.conn = conn
        self.lock = lock
        self.path = path

    def close(self):
        try:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
        finally:
            if self.lock is not None:
                self.lock.close()
                self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def open_writer(path):
    """Open the index for writing, or raise WriterBusy at once."""
    path = os.path.abspath(path)
    lock = Lock(path)
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
    except Exception:
        lock.close()
        raise
    return Writer(conn, lock, path)


def open_reader(path):
    """Open a read-only-use connection; never take the writer lock.

    ``query_only`` keeps accidental writes out of the UI path.  A normal
    SQLite connection (not URI mode=ro) is used because read-only WAL opens
    fail when a crashed writer left a -wal file but no -shm file.
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA query_only=ON")
    return conn


def meta(conn):
    result = {}
    try:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
    except sqlite3.Error:
        return result
    for row in rows:
        result[row[0]] = row[1]
    return result
