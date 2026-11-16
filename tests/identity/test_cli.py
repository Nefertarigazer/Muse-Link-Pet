"""CLI and integration checks for the identity module (own tests)."""
import json
import os
import subprocess
import sys
import time
import unittest

from _support import Env, NOW, envelope, new_req_id

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.env.add_agent("codex", "whitelist")
        self.policy_path = os.path.join(self.env.tmp, "policy.json")
        with open(self.policy_path, "w", encoding="utf-8") as f:
            json.dump(self.env.policy_d, f)
        self.env_vars = dict(os.environ)
        self.env_vars.update({
            "MUSELINK_ID_HOME": self.env.id_home,
            "ML_BRIDGE_HOME": self.env.bridge,
            "MUSELINK_POLICY": self.policy_path,
            "ML_VM_DIR": os.path.join(ROOT, "vm"),
            "PYTHONPATH": os.path.join(ROOT, "vm"),
        })

    def tearDown(self):
        self.env.close()

    def cli(self, *args):
        p = subprocess.run([sys.executable, "-m", "muselink_id.cli"] + list(args),
                           cwd=ROOT, env=self.env_vars, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout, p.stderr

    def test_route_check_and_sign_reply(self):
        path, rid = self.env.signed("codex", ts=int(time.time()))
        rc, out, err = self.cli("route", path, rid)
        self.assertEqual(rc, 0, err)
        res = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((res["route"], res["auth"], res["decision"]), ("inbox", "verified", "allow"))
        rc, out, err = self.cli("check", "--req", rid, "--action", "message")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["decision"], "allow")
        rc, out, err = self.cli("check", "--req", rid, "--action", "publish-release")
        self.assertEqual(rc, 10, err)
        self.assertEqual(json.loads(out)["decision"], "ask_user")

        inner = {"req_id": rid, "seq": 1, "agent_id": "codex", "final": True, "status": "ok",
                 "replied_at": "2026-11-12T09:16:03+00:00", "from": "muse",
                 "reply_protocol": 3, "text": "done"}
        ip = os.path.join(self.env.tmp, "inner.json")
        op = os.path.join(self.env.tmp, "envelope.json")
        with open(ip, "w", encoding="utf-8") as f:
            json.dump(inner, f)
        rc, out, err = self.cli("sign-reply", ip, op)
        self.assertEqual(rc, 0, err)
        with open(op, encoding="utf-8") as f:
            env = json.load(f)
        key = self.env.keys["codex"][1]
        self.assertEqual(envelope.verify_reply(env, key, rid, "codex", 1)["text"], "done")

    def test_route_rejects_tampered_request(self):
        rid = new_req_id("codex")
        kid, key = self.env.keys["codex"]
        text = json.dumps({"req_id": rid, "agent_id": "codex", "ts": NOW, "type": "message",
                           "text": "x", "payload": {}, "reply_protocol": 3})
        outer = {"v": 3, "req_id": rid, "agent_id": "codex", "signed": text,
                 "auth": {"alg": "HMAC-SHA256", "key_id": kid,
                          "sig": envelope.sign(key, "muselink-req-v3\n", text)}}
        outer["signed"] = text.replace('"x"', '"y"')
        path = self.env.write_file(rid, outer)
        rc, out, err = self.cli("route", path, rid)
        self.assertEqual(rc, 0, err)
        res = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((res["route"], res["code"]), ("rejected", "bad_signature"))


if __name__ == "__main__":
    unittest.main()
