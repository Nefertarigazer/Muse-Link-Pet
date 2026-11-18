"""store - where identity state lives and how it is written.

Requirements
  1. Layout (docs/protocol.en.md section 3). Root = $MUSELINK_ID_HOME, default
     ~/.config/muselink/identity. Files: agents.json, grants.json, seal.key,
     state.sqlite, audit.jsonl, *.lock.
  2. Permissions (POSIX): the root dir must be 0700 and owned by the
     effective uid; every file 0600 and owned by the effective uid. Anything
     looser or foreign-owned -> raise InsecureStore on open(), like ssh does
     with keys. New files are created 0600 from the start (os.open with the
     mode; never create-then-chmod). On Windows these checks are skipped
     (the developer PC only runs tests there).
  3. Atomic writes: write a temp file in the same directory, flush + fsync,
     os.replace onto the target, fsync the directory (POSIX). Readers never
     see half a file.
  4. Locks: lock(name) is a context manager on <root>/<name>.lock using
     fcntl.flock (exclusive). Fallback for Windows tests: msvcrt.locking on
     the same file. Every read-modify-write of agents.json / grants.json
     holds lock("registry") / lock("grants") for the whole cycle.
  5. sqlite (state.sqlite), opened with timeout=10 and isolation_level=None;
     writers use "BEGIN IMMEDIATE" so concurrent consumers serialize.
     Schema (PRAGMA user_version = 1):
       seen(req_id TEXT PRIMARY KEY, agent_id TEXT, digest TEXT,
            first_seen INTEGER, route TEXT)
       grant_uses(grant_id TEXT, req_id TEXT, state TEXT, at INTEGER,
            expires_at INTEGER, PRIMARY KEY(grant_id, req_id))
            state in ('consumed', 'reserved', 'released')
       revocations(grant_id TEXT PRIMARY KEY, at INTEGER, reason TEXT)
       enroll_log(req_id TEXT PRIMARY KEY, agent_id TEXT, at INTEGER)
     prune_seen(now, retention_s) deletes rows older than the retention.
  6. seal.key: 32 random bytes (secrets.token_bytes), created on first use
     under lock("seal"), 0600. seal(obj) = base64(HMAC-SHA256(seal_key,
     SEAL_DOMAIN + canonical_json(obj))), canonical_json = json.dumps(obj,
     sort_keys=True, separators=(",", ":"), ensure_ascii=False).
     check_seal() compares in constant time.
  7. audit(event, **fields): one JSON line {"ts": now, "event": event, ...}
     appended under lock("audit"); rotate at 5 MiB to audit.jsonl.1. The
     caller passes only ids, codes, decisions, digests and fact NAMES; this
     function additionally drops any field named key, secret, seal, text,
     payload or sig (defence in depth) before writing.
  8. Bridge-home helpers: paths for envelopes/, verdicts/, pending/,
     rejected/, alerts/, inbox/ under $ML_BRIDGE_HOME (created 0700 if
     missing). alert(req_id, text) writes alerts/<req_id>.txt once.
"""

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import tempfile

try:  # POSIX (the VM)
    import fcntl
except ImportError:  # Windows developer PC: tests only
    fcntl = None
    import msvcrt

from . import IdentityError, InsecureStore, SEAL_DOMAIN, KEY_BYTES


SCHEMA = """
CREATE TABLE IF NOT EXISTS seen(
    req_id TEXT PRIMARY KEY, agent_id TEXT, digest TEXT,
    first_seen INTEGER, route TEXT);
CREATE TABLE IF NOT EXISTS grant_uses(
    grant_id TEXT, req_id TEXT, state TEXT, at INTEGER,
    expires_at INTEGER, PRIMARY KEY(grant_id, req_id));
CREATE TABLE IF NOT EXISTS revocations(
    grant_id TEXT PRIMARY KEY, at INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS enroll_log(
    req_id TEXT PRIMARY KEY, agent_id TEXT, at INTEGER);
"""


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _strict_loads(text):
    from .envelope import strict_loads
    return strict_loads(text)


class Store:
    def __init__(self, id_home=None, bridge_home=None):
        """id_home: defaults to $MUSELINK_ID_HOME or ~/.config/muselink/identity.
        bridge_home: defaults to $ML_BRIDGE_HOME or ~/workspace/muse-bridge."""
        self.id_home = id_home or os.environ.get("MUSELINK_ID_HOME") or os.path.expanduser("~/.config/muselink/identity")
        self.bridge_home = bridge_home or os.environ.get("ML_BRIDGE_HOME") or os.path.expanduser("~/workspace/muse-bridge")
        self._posix = os.name == "posix"

    def open(self):
        """Create missing dirs/schema, then enforce requirement 2. Returns self."""
        if self._posix:
            os.umask(0o077)
        os.makedirs(self.id_home, mode=0o700, exist_ok=True)
        os.makedirs(self.bridge_home, mode=0o700, exist_ok=True)
        for name in ("envelopes", "verdicts", "pending", "rejected", "alerts", "inbox", "processed"):
            os.makedirs(self.bridge_path(name), mode=0o700, exist_ok=True)
        # Create the database eagerly so a fresh identity home has the schema.
        conn = self._connect()
        conn.close()
        self._enforce()
        return self

    def _enforce(self):
        if not self._posix:
            return
        euid = os.geteuid()
        st = os.stat(self.id_home)
        if stat.S_IMODE(st.st_mode) != 0o700 or st.st_uid != euid:
            raise InsecureStore("id_home", "must be 0700 and owned by the current user")
        for name in os.listdir(self.id_home):
            p = os.path.join(self.id_home, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != euid:
                raise InsecureStore(p, "must be 0600 and owned by the current user")

    def path(self, name):
        return os.path.join(self.id_home, name)

    def bridge_path(self, *parts):
        return os.path.join(self.bridge_home, *parts)

    def ensure_bridge_dir(self, name):
        p = self.bridge_path(name)
        os.makedirs(p, mode=0o700, exist_ok=True)
        return p

    @contextlib.contextmanager
    def lock(self, name):
        p = self.path(name + ".lock")
        fd = os.open(p, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(fd)

    def read_json(self, name, default):
        p = self.path(name)
        if not os.path.exists(p):
            return default
        with open(p, "rb") as f:
            raw = f.read()
        try:
            return _strict_loads(raw.decode("utf-8-sig"))
        except Exception:
            # Fail closed. Returning the default here made the next write
            # (registry.add, grants.propose) silently replace a damaged file
            # with a fresh one, dropping every enrolled agent or grant.
            raise IdentityError("store_corrupt", name)

    def write_json(self, name, obj):
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self._atomic_write(self.path(name), data)

    def _atomic_write(self, path, data):
        d = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
            self.sync_dir(d)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def write_bytes(self, path, data):
        self._atomic_write(path, data)

    def sync_dir(self, path):
        if self._posix:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _connect(self):
        p = self.path("state.sqlite")
        if not os.path.exists(p):
            fd = os.open(p, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        conn = sqlite3.connect(p, timeout=10, isolation_level=None)
        conn.executescript(SCHEMA)
        # v2: grant_uses.facts_digest makes per-request idempotency honest
        # (a second, different action under the same req_id is a new use)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(grant_uses)")]
        if "facts_digest" not in cols:
            try:
                conn.execute("ALTER TABLE grant_uses ADD COLUMN facts_digest TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # a concurrent opener added it first
        conn.execute("PRAGMA user_version = 2")
        return conn

    def db(self):
        """A new sqlite3 connection with the schema ensured (requirement 5)."""
        return self._connect()

    def _seal_key(self):
        p = self.path("seal.key")
        with self.lock("seal"):
            if not os.path.exists(p):
                data = secrets.token_bytes(KEY_BYTES)
                # O_BINARY: on Windows a raw os.open is text mode and turns a
                # 0x0a key byte into \r\n (no-op flag on POSIX)
                fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                try:
                    os.write(fd, data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            with open(p, "rb") as f:
                data = f.read()
        if len(data) != KEY_BYTES:
            # an empty or truncated key would still "seal" with a guessable key
            raise IdentityError("store_corrupt", "seal.key")
        return data

    def seal(self, obj):
        key = self._seal_key()
        msg = (SEAL_DOMAIN + canonical_json(obj)).encode("utf-8")
        return base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode("ascii")

    def check_seal(self, obj, seal):
        if not isinstance(seal, str):
            return False
        key = self._seal_key()
        msg = (SEAL_DOMAIN + canonical_json(obj)).encode("utf-8")
        want = base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode("ascii")
        return hmac.compare_digest(want, seal)

    def audit(self, event, now, **fields):
        drop = {"key", "secret", "seal", "text", "payload", "sig"}
        safe = {k: v for k, v in fields.items() if k.lower() not in drop}
        safe["ts"] = int(now)
        safe["event"] = event
        line = json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n"
        with self.lock("audit"):
            p = self.path("audit.jsonl")
            try:
                if os.path.exists(p) and os.path.getsize(p) > 5 * 1024 * 1024:
                    os.replace(p, p + ".1")
            except OSError:
                pass
            fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)

    def alert(self, req_id, text):
        d = self.ensure_bridge_dir("alerts")
        safe = "".join(c for c in str(req_id) if c.isalnum() or c in "_-") or "unknown"
        p = os.path.join(d, safe + ".txt")
        if os.path.exists(p):
            return
        try:
            fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        except FileExistsError:
            return  # another process wrote the same alert first
        try:
            os.write(fd, (str(text) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
