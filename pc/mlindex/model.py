"""model - pure rules: thread key, request state, thread state.

Requirements (docs/protocol.en.md section 5; no I/O in this module)
  * thread_key(request): explicit valid thread_id, else "<agent>/<topic>",
    topic "release" for publish-* types, "chat" otherwise. The ONLY place
    this rule lives.
  * request_state(req_id, records): pure function of the records that exist
    now -> (state, final_read). Precedence as in docs/protocol.en.md section 5.
  * thread_state(states): waiting_user > active > done.
  * trust(record): from the reply's directory (archive/outbox/quarantine).
  * release_card(request, final_reply): the publish-* card fields, or None.
"""

import re

# re.fullmatch with a "$" anchor still accepts a trailing newline in some
# Python versions; \Z is the strict end of the whole string.
_THREAD_RX = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_FINAL_STATES = ("replied", "needs_user", "rejected")
_ACTIVE_STATES = ("delivered", "claimed", "acked")
_NEEDS_USER = ("needs-user", "needs_user")


def _valid_thread_id(value):
    return isinstance(value, str) and _THREAD_RX.match(value) is not None


def thread_key(request):
    """Return the thread id for a request record (see docs/protocol.en.md section 5).

    ``request`` is a plain mapping with at least ``type`` and either
    ``agent`` (v3 agent_id / v1 from) or ``from``.  An explicit but invalid
    ``thread_id`` is treated as if it were absent.
    """
    if not isinstance(request, dict):
        return "unknown/chat"
    explicit = request.get("thread_id")
    if _valid_thread_id(explicit):
        return explicit
    agent = request.get("agent")
    if not isinstance(agent, str) or not agent:
        agent = request.get("from")
    if not isinstance(agent, str) or not agent:
        agent = "unknown"
    kind = request.get("type")
    topic = "release" if isinstance(kind, str) and kind.startswith("publish-") else "chat"
    return "%s/%s" % (agent, topic)


def _status(value):
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _is_final(record):
    if record.get("kind") != "reply":
        return False
    if record.get("parse_error"):
        return False
    if record.get("dir") == "quarantine":
        # A reply the client rejected is not a Muse answer, so it can never
        # close a request or count as an interim ACK.
        return False
    # sources.py already resolved "final": an explicit boolean wins, the
    # status fallback ("info" = interim) applies only when it is absent -
    # the same rule as muse-call's Test-FinalReply. Re-deriving it from the
    # status here turned an explicit "final": false + "status": "ok" (an
    # --interim progress note) into a final reply (review 2026-11-12).
    if isinstance(record.get("final"), bool):
        return record["final"]
    return _status(record.get("status")) not in ("", "info")


def _seq(record):
    value = record.get("seq")
    if isinstance(value, int):
        return value
    return 0


def request_state(req_id, records):
    """Return ``(state, final_read)`` for one request id.

    ``records`` are all physical records for that id (requests and replies,
    in every directory).  The result is a pure function of those records.
    """
    requests = [r for r in records if r.get("kind") == "request"]
    replies = [r for r in records if r.get("kind") == "reply"]
    dirs = set(r.get("dir") for r in requests)
    finals = [r for r in replies if _is_final(r)]
    finals.sort(key=lambda r: (_seq(r), r.get("path") or ""))
    final = finals[-1] if finals else None
    final_read = bool(
        (final is not None and final.get("dir") == "archive")
        or "done" in dirs
    )
    if "rejected" in dirs:
        return "rejected", final_read
    if final is not None:
        status = _status(final.get("status"))
        return ("needs_user" if status in _NEEDS_USER else "replied"), final_read
    interim = [r for r in replies if not _is_final(r) and not r.get("parse_error")
               and r.get("dir") != "quarantine"]
    if interim:
        return "acked", final_read
    if "done" in dirs:
        # The done/ marker says the final reply was read, even if the reply
        # file itself has since been removed.  The status (ok/error) is not
        # recoverable from the current files, so the state is the terminal
        # "replied" without inventing a status.
        return "replied", True
    if dirs & set(("claimed", "pending")):
        return "claimed", final_read
    return "delivered", final_read


def thread_state(states, pending_enroll=False):
    """waiting_user > active > done (docs/protocol.en.md section 5)."""
    states = list(states)
    if pending_enroll or "needs_user" in states:
        return "waiting_user"
    if any(s in _ACTIVE_STATES for s in states):
        return "active"
    return "done"


def trust(record):
    """Trust label for a reply record, from the directory it is in now."""
    if not isinstance(record, dict) or record.get("kind") != "reply":
        return None
    directory = record.get("dir")
    if directory == "archive":
        return "read"
    if directory == "outbox":
        return "unread_unverified"
    if directory == "quarantine":
        return "rejected_by_client"
    return None


def _str(value):
    return value if isinstance(value, str) else ""


def _int(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def release_card(request, final_reply):
    """Return the publish-* card fields, or None for a non-release request."""
    if not isinstance(request, dict):
        return None
    kind = request.get("type")
    if not isinstance(kind, str) or not kind.startswith("publish-"):
        return None
    payload = request.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    status = ""
    if isinstance(final_reply, dict):
        status = _str(final_reply.get("status"))
    return {
        "version": _str(payload.get("version")),
        "package_path": _str(payload.get("package_path")),
        "package_size": _int(payload.get("package_size")),
        "sha256": _str(payload.get("sha256")),
        "setup_path": _str(payload.get("setup_path")),
        "setup_size": _int(payload.get("setup_size")),
        "setup_sha256": _str(payload.get("setup_sha256")),
        "test": bool(payload.get("test", False)),
        "status": status,
    }
