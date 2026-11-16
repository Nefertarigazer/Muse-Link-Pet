"""Fixed signing vectors (spec/test-vectors/identity.json).

    python -m unittest discover -s tests/identity -t tests/identity
"""
import json
import unittest

from _support import M, VECTORS, envelope, mac
import base64


def load():
    with open(VECTORS, encoding="utf-8") as f:
        return json.load(f)


class StubRegistry:
    def __init__(self, key):
        self.key = key

    def key_for(self, agent_id, key_id):
        if (agent_id, key_id) != ("kiro", "kiro#1"):
            raise M.AuthError("unknown_key")
        return self.key


class VectorTests(unittest.TestCase):
    def setUp(self):
        self.v = load()
        self.key = base64.b64decode(self.v["key_b64"])

    def test_vector_file_is_self_consistent(self):
        # guards the vector file itself: plain stdlib must agree with it
        for c in self.v["cases"]:
            self.assertEqual(mac(self.key, c["domain"], c["signed"]), c["sig_b64"], c["name"])

    def test_sign_matches_every_case(self):
        for c in self.v["cases"]:
            self.assertEqual(envelope.sign(self.key, c["domain"], c["signed"]), c["sig_b64"], c["name"])

    def test_check_sig_accepts_and_rejects(self):
        for c in self.v["cases"]:
            self.assertTrue(envelope.check_sig(self.key, c["domain"], c["signed"], c["sig_b64"]), c["name"])
            raw = bytearray(base64.b64decode(c["sig_b64"]))
            raw[-1] ^= 1
            self.assertFalse(envelope.check_sig(self.key, c["domain"], c["signed"], base64.b64encode(bytes(raw)).decode()), c["name"])
            self.assertFalse(envelope.check_sig(self.key, c["domain"], c["signed"], "not base64!"), c["name"])

    def test_domains_are_separated(self):
        by = {c["name"]: c for c in self.v["cases"]}
        self.assertNotEqual(by["reply"]["sig_b64"], by["request-domain-on-reply-text"]["sig_b64"])
        self.assertEqual(M.REQ_DOMAIN, "muselink-req-v3\n")
        self.assertEqual(M.REPLY_DOMAIN, "muselink-reply-v3\n")

    def test_example_envelope_verifies_without_reserialization(self):
        outer = self.v["envelope_example"]
        inner = json.loads(outer["signed"])
        vr = envelope.verify_request(outer, StubRegistry(self.key), now=inner["ts"], max_age_s=604800, max_future_s=600)
        self.assertEqual(vr.agent_id, "kiro")
        self.assertEqual(vr.key_id, "kiro#1")
        self.assertEqual(vr.inner["text"], inner["text"])


if __name__ == "__main__":
    unittest.main()
