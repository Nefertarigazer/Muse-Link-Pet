"""Helper for check-ps-signing.ps1: the Python side of the cross-language test.

    python ps_roundtrip.py verify <envelope-file> <key_b64> <agent_id> <key_id>
        -> prints "OK <type> <text-sha256>" (exit 0) or "FAIL <code>" (exit 1)
    python ps_roundtrip.py make-reply <out-file> <key_b64> <agent_id> <key_id> <req_id> <seq>
        -> writes a signed v3 reply envelope (exit 0)
"""
import base64
import hashlib
import json
import os
import sys
import time

from _support import M, envelope


class OneKey:
    def __init__(self, agent_id, key_id, key):
        self.agent_id, self.key_id, self.key = agent_id, key_id, key

    def key_for(self, agent_id, key_id):
        if agent_id != self.agent_id:
            raise M.AuthError("unknown_agent")
        if key_id != self.key_id:
            raise M.AuthError("unknown_key")
        return self.key


def main(argv):
    if len(argv) >= 5 and argv[0] == "verify":
        path, key, agent_id, key_id = argv[1], base64.b64decode(argv[2]), argv[3], argv[4]
        file_req_id = os.path.basename(path)[:-5]
        try:
            kind, outer = envelope.read_envelope_file(path, file_req_id)
            if kind != "v3":
                print(f"FAIL kind={kind}")
                return 1
            vr = envelope.verify_request(outer, OneKey(agent_id, key_id, key), now=time.time(),
                                         max_age_s=604800, max_future_s=600)
        except M.AuthError as e:
            print(f"FAIL {e.code}")
            return 1
        digest = hashlib.sha256(vr.inner["text"].encode("utf-8")).hexdigest()
        print(f"OK {vr.inner['type']} {digest}")
        return 0
    if len(argv) >= 7 and argv[0] == "make-reply":
        out, key, agent_id, key_id, req_id, seq = argv[1], base64.b64decode(argv[2]), argv[3], argv[4], argv[5], int(argv[6])
        inner = {"req_id": req_id, "seq": seq, "agent_id": agent_id, "final": True, "status": "ok",
                 "replied_at": "2026-11-12T09:16:03+00:00", "from": "muse", "reply_protocol": 3,
                 "text": "\u5df2\u5b8c\u6210 \"ok\" \\ \U0001F600"}
        env = envelope.new_reply_envelope(agent_id, key_id, key, inner)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(env, f, ensure_ascii=False)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
