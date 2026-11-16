"""mlindex - thread index over the PC mailbox (SQLite WAL + FTS5).

Conventions: docs/protocol.en.md section 5. Python 3.8+ stdlib only.
Read-only towards mailbox files; the index is a cache that `rebuild`
recreates from the files at any time.
"""

SCHEMA_VERSION = 2
THREAD_ID_RX = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$"
REQUEST_STATES = ("delivered", "claimed", "acked", "replied", "needs_user", "rejected")
THREAD_STATES = ("waiting_user", "active", "done")
TRUST = ("read", "unread_unverified", "rejected_by_client")
ROLES = ("agent", "muse", "user")
MAILBOX_DIRS = ("inbox", "claimed", "pending", "rejected", "outbox", "archive",
                "ack", "done", "sent", "quarantine")
