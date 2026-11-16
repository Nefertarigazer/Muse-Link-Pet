"""Fixture builder for the thread-index tests (not a test module).

Build a synthetic mailbox in a temp dir: write_request / write_reply /
write_ack / move(req_id, from_dir, to_dir) in every on-disk shape (v1, v2
numbered, v3 envelope, BOM, half-written file). No production data in tests.
Also put `<repo>/pc` on sys.path so `import mlindex` works.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PC = os.path.join(ROOT, "pc")
if PC not in sys.path:
    sys.path.insert(0, PC)

from mlindex import MAILBOX_DIRS  # noqa: E402

DEFAULT_CREATED = "2026-11-12T10:15:00.000+01:00"
DEFAULT_REPLIED = "2026-11-12T09:15:03+00:00"
DEFAULT_TS = 1794564900


class Mailbox:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        for name in MAILBOX_DIRS:
            os.makedirs(os.path.join(self.root, name), exist_ok=True)

    def path(self, dir_name, name):
        return os.path.join(self.root, dir_name, name)

    def _write(self, dir_name, name, obj, bom=False):
        path = self.path(dir_name, name)
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        data = text.encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def write_raw(self, dir_name, name, data, bom=False):
        path = self.path(dir_name, name)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def write_request(self, req_id, dir_name="inbox", v=2, from_="codex",
                      type_="message", text="", payload=None, created_at=None,
                      thread_id=None, reply_to=None, agent=None,
                      reply_protocol=None, bom=False):
        payload = {} if payload is None else payload
        created_at = DEFAULT_CREATED if created_at is None else created_at
        agent = from_ if agent is None else agent
        if v == 3:
            inner = {
                "req_id": req_id,
                "agent_id": agent,
                "ts": DEFAULT_TS,
                "created_at": created_at,
                "type": type_,
                "text": text,
                "payload": payload,
                "reply_protocol": 3,
            }
            if thread_id is not None:
                inner["thread_id"] = thread_id
            if reply_to is not None:
                inner["reply_to"] = reply_to
            outer = {
                "v": 3,
                "req_id": req_id,
                "agent_id": agent,
                "signed": json.dumps(inner, ensure_ascii=False, separators=(",", ":")),
                "auth": {"alg": "HMAC-SHA256", "key_id": agent + "#1", "sig": "AAAA"},
            }
            return self._write(dir_name, req_id + ".json", outer, bom)
        obj = {
            "req_id": req_id,
            "created_at": created_at,
            "from": from_,
            "type": type_,
            "text": text,
            "payload": payload,
        }
        if v == 2:
            obj["reply_protocol"] = 2 if reply_protocol is None else reply_protocol
        elif reply_protocol is not None:
            obj["reply_protocol"] = reply_protocol
        if thread_id is not None:
            obj["thread_id"] = thread_id
        if reply_to is not None:
            obj["reply_to"] = reply_to
        return self._write(dir_name, req_id + ".json", obj, bom)

    def write_reply(self, req_id, seq=1, dir_name="outbox", v=2, final=None,
                    status="info", text="", replied_at=None, agent="codex",
                    bom=False):
        replied_at = DEFAULT_REPLIED if replied_at is None else replied_at
        if final is None:
            final = status != "info"
        name = ("%s.%03d.reply.json" % (req_id, seq)) if seq else (req_id + ".reply.json")
        if v == 3:
            inner = {
                "req_id": req_id,
                "seq": seq,
                "agent_id": agent,
                "final": bool(final),
                "status": status,
                "replied_at": replied_at,
                "from": "muse",
                "reply_protocol": 3,
                "text": text,
            }
            outer = {
                "v": 3,
                "req_id": req_id,
                "seq": seq,
                "agent_id": agent,
                "signed": json.dumps(inner, ensure_ascii=False, separators=(",", ":")),
                "auth": {"alg": "HMAC-SHA256", "key_id": agent + "#1", "sig": "AAAA"},
            }
            return self._write(dir_name, name, outer, bom)
        if v == 1 or seq == 0:
            obj = {
                "req_id": req_id,
                "status": status,
                "replied_at": replied_at,
                "from": "muse",
                "text": text,
            }
        else:
            obj = {
                "req_id": req_id,
                "seq": seq,
                "final": bool(final),
                "status": status,
                "replied_at": replied_at,
                "from": "muse",
                "reply_protocol": 2,
                "text": text,
            }
        return self._write(dir_name, name, obj, bom)

    def write_ack(self, req_id, seq=1, text="2026-11-12T09:15:03+00:00"):
        name = ("%s.%03d.ack" % (req_id, seq)) if seq else (req_id + ".ack")
        return self.write_raw("ack", name, text)

    def move(self, req_id, from_dir, to_dir, name=None):
        name = name or (req_id + ".json")
        return self.move_file(name, from_dir, to_dir)

    def move_file(self, name, from_dir, to_dir):
        source = self.path(from_dir, name)
        target = self.path(to_dir, name)
        os.replace(source, target)
        return target

    def remove(self, dir_name, name):
        try:
            os.remove(self.path(dir_name, name))
        except FileNotFoundError:
            pass
