"""sources - read mailbox files into plain records.

Requirements
  * Walk MAILBOX_DIRS under the bridge root; yield one record per file:
    path, dir, size, mtime_ns, sha256, kind (request|reply|ack), parsed fields.
  * Understand every shape that exists on disk: v1 request, v1 reply name
    (<id>.reply.json, seq 0), v2 numbered reply, v3 request/reply envelope
    (parse the `signed` inner text; never verify). UTF-8 with or without BOM.
  * A file that vanishes or is half-written mid-scan is skipped, not an error;
    an unparsable file becomes a record with parse_error set (shown, not lost).
  * Never write, move or lock a mailbox file.
"""

import hashlib
import json
import os
import re

from . import MAILBOX_DIRS

_REQUEST_RX = re.compile(r"\A(?P<id>.+)\.json\Z")
_REPLY_RX = re.compile(r"\A(?P<id>.+?)(?:\.(?P<seq>\d{3}))?\.reply\.json\Z")
_ACK_RX = re.compile(r"\A(?P<id>.+?)(?:\.(?P<seq>\d{3}))?\.ack\Z")


def _record_base(path, dir_name, name, size, mtime_ns, sha256, kind):
    return {
        "path": path,
        "dir": dir_name,
        "name": name,
        "size": int(size),
        "mtime_ns": int(mtime_ns),
        "sha256": sha256,
        "kind": kind,
        "parse_error": None,
        "req_id": None,
        "seq": None,
        "role": None,
        "v": None,
        "type": "",
        "status": "",
        "final": False,
        "text": "",
        "payload": {},
        "created_at": "",
        "replied_at": "",
        "agent": "",
        "from": "",
        "thread_id": None,
        "reply_to": None,
        "reply_protocol": None,
        "client": None,
        "ts": None,
    }


def _text(value):
    return value if isinstance(value, str) else ""


def _dict(value):
    return value if isinstance(value, dict) else {}


def _int(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _seq_from_match(match):
    value = match.groupdict().get("seq")
    return int(value) if value else 0


def _classify(name):
    """Return (kind, match) for a mailbox file name, or (None, None)."""
    # Atomic writers leave *.tmp files: they are not letters yet.
    if name.endswith(".tmp"):
        return None, None
    m = _REPLY_RX.match(name)
    if m:
        return "reply", m
    m = _ACK_RX.match(name)
    if m:
        return "ack", m
    m = _REQUEST_RX.match(name)
    if m:
        return "request", m
    return None, None


def _decode(data):
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError as exc:
        return data.decode("utf-8-sig", "replace"), "utf-8: %s" % exc


def _load_json(text, rec):
    try:
        obj = json.loads(text)
    except Exception as exc:  # JSONDecodeError is a ValueError
        rec["parse_error"] = "json: %s" % exc
        return None
    if not isinstance(obj, dict):
        rec["parse_error"] = "json: not an object"
        return None
    return obj


def _fill_request(rec, obj):
    inner = obj
    if _int(obj.get("v")) == 3:
        rec["v"] = 3
        signed = obj.get("signed")
        if not isinstance(signed, str):
            rec["parse_error"] = "v3: missing signed text"
            inner = {}
        else:
            try:
                parsed = json.loads(signed)
            except Exception as exc:
                rec["parse_error"] = "v3 signed: %s" % exc
                inner = {}
            else:
                if isinstance(parsed, dict):
                    inner = parsed
                else:
                    rec["parse_error"] = "v3 signed: not an object"
                    inner = {}
    else:
        rec["v"] = _int(obj.get("v")) or (2 if obj.get("reply_protocol") == 2 else 1)

    rec["req_id"] = inner.get("req_id") if isinstance(inner.get("req_id"), str) else rec.get("req_id")
    agent = inner.get("agent_id")
    if not isinstance(agent, str):
        agent = obj.get("agent_id")
    if not isinstance(agent, str):
        agent = obj.get("from")
    rec["agent"] = _text(agent)
    rec["from"] = _text(obj.get("from")) or rec["agent"]
    rec["type"] = _text(inner.get("type"))
    rec["text"] = _text(inner.get("text"))
    rec["payload"] = _dict(inner.get("payload"))
    rec["created_at"] = _text(inner.get("created_at"))
    rec["thread_id"] = inner.get("thread_id") if isinstance(inner.get("thread_id"), str) else None
    rec["reply_to"] = inner.get("reply_to") if isinstance(inner.get("reply_to"), str) else None
    rec["reply_protocol"] = _int(inner.get("reply_protocol"))
    rec["client"] = inner.get("client") if isinstance(inner.get("client"), dict) else None
    rec["ts"] = _int(inner.get("ts"))
    rec["role"] = "agent"


def _fill_reply(rec, obj):
    inner = obj
    if _int(obj.get("v")) == 3:
        rec["v"] = 3
        signed = obj.get("signed")
        if not isinstance(signed, str):
            rec["parse_error"] = "v3: missing signed text"
            inner = {}
        else:
            try:
                parsed = json.loads(signed)
            except Exception as exc:
                rec["parse_error"] = "v3 signed: %s" % exc
                inner = {}
            else:
                if isinstance(parsed, dict):
                    inner = parsed
                else:
                    rec["parse_error"] = "v3 signed: not an object"
                    inner = {}
    else:
        rec["v"] = _int(obj.get("v")) or (2 if ("seq" in obj or "final" in obj) else 1)

    rec["req_id"] = inner.get("req_id") if isinstance(inner.get("req_id"), str) else rec.get("req_id")
    rec["seq"] = _int(inner.get("seq"))
    if rec["seq"] is None:
        rec["seq"] = _int(obj.get("seq"))
    if rec["seq"] is None:
        rec["seq"] = 0
    rec["role"] = "muse"
    rec["status"] = _text(inner.get("status"))
    if "final" in inner and isinstance(inner.get("final"), bool):
        rec["final"] = inner["final"]
    elif "final" in obj and isinstance(obj.get("final"), bool):
        rec["final"] = obj["final"]
    else:
        rec["final"] = rec["status"].strip().lower() not in ("", "info")
    rec["text"] = _text(inner.get("text"))
    rec["payload"] = _dict(inner.get("payload"))
    rec["replied_at"] = _text(inner.get("replied_at"))
    rec["from"] = _text(inner.get("from")) or "muse"
    rec["agent"] = _text(inner.get("agent_id")) or _text(obj.get("agent_id"))
    rec["reply_protocol"] = _int(inner.get("reply_protocol"))
    rec["ts"] = _int(inner.get("ts"))


def parse_file(path, dir_name):
    """Return a record dict for one file, or None when it vanished."""
    path = os.path.abspath(path)
    name = os.path.basename(path)
    kind, match = _classify(name)
    if kind is None:
        return None
    try:
        st = os.stat(path)
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    sha = hashlib.sha256(data).hexdigest()
    rec = _record_base(path, dir_name, name, st.st_size, st.st_mtime_ns, sha, kind)
    rec["req_id"] = match.group("id")
    if kind == "ack":
        rec["seq"] = _seq_from_match(match)
        text, error = _decode(data)
        rec["text"] = text.strip()
        if error:
            rec["parse_error"] = error
        return rec

    text, error = _decode(data)
    if error:
        rec["parse_error"] = error
    obj = _load_json(text, rec)
    if obj is None:
        return rec
    seq = _seq_from_match(match)
    file_id = rec["req_id"]
    if kind == "reply":
        rec["seq"] = seq
        _fill_reply(rec, obj)
        # The file name is the identity (docs/protocol.en.md section 5).  Keep the
        # name's id/seq, but do not hide a disagreement with the content.
        if rec["req_id"] != file_id and rec["parse_error"] is None:
            rec["parse_error"] = "req_id: file name and content disagree"
        if rec["seq"] != seq and rec["parse_error"] is None:
            rec["parse_error"] = "seq: file name and content disagree"
        rec["req_id"] = file_id
        rec["seq"] = seq
    else:
        rec["seq"] = None
        _fill_request(rec, obj)
        if rec["req_id"] != file_id and rec["parse_error"] is None:
            rec["parse_error"] = "req_id: file name and content disagree"
        rec["req_id"] = file_id
    return rec


def list_files(bridge_root):
    """Yield (path, dir, size, mtime_ns) for every candidate mailbox file.

    Only the ten mailbox directories are walked.  The index directory is not
    one of them, so the index never indexes itself.  A directory that does
    not exist yet is simply empty.
    """
    root = os.path.abspath(bridge_root)
    for dir_name in MAILBOX_DIRS:
        directory = os.path.join(root, dir_name)
        try:
            entries = list(os.scandir(directory))
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        entries.sort(key=lambda entry: entry.name)
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            name = entry.name
            kind, _ = _classify(name)
            if kind is None:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            yield (os.path.abspath(entry.path), dir_name, int(st.st_size), int(st.st_mtime_ns))


def scan(bridge_root, only_changed=None):
    """-> iterator of records. only_changed: {path: (size, mtime_ns)} to skip."""
    changed = None
    if only_changed is not None:
        changed = dict((os.path.normcase(os.path.abspath(k)), tuple(v))
                       for k, v in only_changed.items())
    for path, dir_name, size, mtime_ns in list_files(bridge_root):
        if changed is not None:
            key = os.path.normcase(path)
            previous = changed.get(key)
            if previous is not None and previous == (size, mtime_ns):
                continue
        rec = parse_file(path, dir_name)
        if rec is not None:
            yield rec
