"""Security acceptance tests for the identity protocol and grants.

Written by the reviewer together with the skeleton (2026-11-12): these ARE
the specification. Make them pass; do not weaken an expectation without
listing it in the hand-over note. Add your own unit tests next to them.

    python -m unittest discover -s tests/identity -t tests/identity

Historical case labels are retained; the current contract is docs/protocol.en.md sections 3-4.
"""
import base64
import json
import multiprocessing
import os
import stat
import unittest

from _support import (
    CLOSED, CONDITIONS, DAY, ERROR, EVIDENCE, FACTS, NOW, REFUSED, TEXT_MARKER, TIMEOUT,
    Env, FakePc, FakeUI, M, answered, consume_worker, envelope, flows, grants_mod,
    inner, mac, make_envelope, new_req_id, policy_dict, policy_mod, store_mod,
)

POSIX = os.name == "posix"


class Base(unittest.TestCase):
    mutate_policy = None

    def setUp(self):
        self.env = Env(self.mutate_policy)
        self.env.add_agent("codex", "whitelist")
        self.env.add_agent("kiro", "ask-first")

    def tearDown(self):
        self.env.close()

    def alert_exists(self, rid):
        return os.path.exists(self.env.bridge_file("alerts", rid + ".txt"))

    def assertRejected(self, res, code):
        self.assertEqual(res.route, "rejected", res)
        self.assertEqual(res.code, code, res)
        self.assertFalse(os.path.exists(self.env.bridge_file("inbox", res.req_id + ".json")), "rejected request reached the inbox")


# --------------------------------------------------------------- section 3/4
class RoutingAndVerification(Base):
    def test_valid_request_reaches_inbox_as_worker_request(self):
        path, rid = self.env.signed("codex", client={"display_name": "codex-legion"})
        res = self.env.route(path, rid)
        self.assertEqual((res.route, res.auth, res.agent_id, res.decision), ("inbox", "verified", "codex", "allow"))
        with open(self.env.bridge_file("inbox", rid + ".json"), encoding="utf-8") as f:
            w = json.load(f)
        self.assertEqual(w["from"], "codex")            # identity, never the self-declared name
        self.assertTrue(w["auth"]["verified"])
        self.assertEqual(w["auth"]["key_id"], "codex#1")
        self.assertEqual(w["reply_protocol"], 3)
        with open(path, "rb") as a, open(self.env.bridge_file("envelopes", rid + ".json"), "rb") as b:
            self.assertEqual(a.read(), b.read())         # original bytes kept
        self.assertTrue(os.path.exists(self.env.bridge_file("verdicts", rid + ".json")))
        self.assertEqual(os.listdir(self.env.bridge_file("inbox")), [rid + ".json"])  # nothing else in inbox/

    def test_ask_first_agent_task_is_ask_user(self):
        path, rid = self.env.signed("kiro", type_="task")
        res = self.env.route(path, rid)
        self.assertEqual((res.route, res.decision), ("inbox", "ask_user"))

    def test_tampered_signed_text_is_impersonation(self):
        rid = new_req_id("codex")
        kid, key = self.env.keys["codex"]
        good = json.dumps(inner("codex", rid), ensure_ascii=False)
        outer = make_envelope("codex", kid, key, good, rid)
        outer["signed"] = good.replace("hello", "HELLO")
        res = self.env.route(self.env.write_file(rid, outer), rid)
        self.assertRejected(res, "bad_signature")
        self.assertTrue(res.alert)
        self.assertEqual(res.reply_hint, "none")
        self.assertTrue(self.alert_exists(rid))

    def test_signed_with_another_agents_key(self):
        path, rid = self.env.signed("codex", key_from="kiro", key_id="codex#1")
        res = self.env.route(path, rid)
        self.assertRejected(res, "bad_signature")
        self.assertTrue(res.alert)

    def test_key_id_of_another_agent(self):
        path, rid = self.env.signed("codex", key_from="kiro", key_id="kiro#1")
        self.assertRejected(self.env.route(path, rid), "bad_key_id")

    def test_inner_agent_differs_from_outer(self):
        rid = new_req_id("codex")
        text = json.dumps(inner("kiro", rid), ensure_ascii=False)   # signed by codex, claims kiro inside
        path, rid = self.env.signed("codex", req_id=rid, signed_text=text)
        self.assertRejected(self.env.route(path, rid), "id_mismatch")

    def test_inner_req_id_differs(self):
        rid = new_req_id("codex")
        text = json.dumps(inner("codex", rid + "x"), ensure_ascii=False)
        path, rid = self.env.signed("codex", req_id=rid, signed_text=text)
        self.assertRejected(self.env.route(path, rid), "id_mismatch")

    def test_outer_req_id_must_equal_file_name(self):
        path, rid = self.env.signed("codex")
        other = new_req_id("codex")
        os.replace(path, os.path.join(self.env.pc_inbox, other + ".json"))
        res = self.env.route(os.path.join(self.env.pc_inbox, other + ".json"), other)
        self.assertEqual((res.route, res.code), ("rejected", "id_mismatch"))

    def test_unknown_agent_is_rejected_not_pending(self):
        self.env.keys["ghost"] = ("ghost#1", os.urandom(32))
        path, rid = self.env.signed("ghost")
        res = self.env.route(path, rid)
        self.assertRejected(res, "unknown_agent")
        self.assertFalse(res.alert)
        self.assertFalse(os.path.exists(self.env.bridge_file("pending", rid + ".json")))

    def test_unsigned_non_enroll_is_rejected(self):
        rid = new_req_id("codex")
        outer = {"v": 3, "req_id": rid, "agent_id": "codex", "signed": json.dumps(inner("codex", rid)), "auth": None}
        self.assertRejected(self.env.route(self.env.write_file(rid, outer), rid), "unsigned")

    def test_bad_alg_and_encodings(self):
        kid, key = self.env.keys["codex"]
        for alg, sig, code in (
            ("HMAC-SHA1", None, "bad_alg"),
            ("HMAC-SHA256", "@@@@", "bad_encoding"),
            ("HMAC-SHA256", mac(key, M.REQ_DOMAIN, "x")[:-4] + "AAA=", None),  # valid b64, wrong MAC
        ):
            rid = new_req_id("codex")
            text = json.dumps(inner("codex", rid))
            outer = make_envelope("codex", kid, key, text, rid, alg=alg, sig=sig)
            res = self.env.route(self.env.write_file(rid, outer), rid)
            self.assertRejected(res, code or "bad_signature")
        rid = new_req_id("codex")
        text = json.dumps(inner("codex", rid))
        short = base64.b64encode(b"\0" * 31).decode()
        outer = make_envelope("codex", kid, key, text, rid, sig=short)
        self.assertRejected(self.env.route(self.env.write_file(rid, outer), rid), "bad_encoding")

    def test_duplicate_json_keys_in_signed_are_rejected(self):
        rid = new_req_id("codex")
        base = json.dumps(inner("codex", rid))
        text = base[:-1] + ', "type": "publish-release"}'      # a second "type" key
        path, rid = self.env.signed("codex", req_id=rid, signed_text=text)
        self.assertRejected(self.env.route(path, rid), "bad_inner")

    def test_nan_and_wrong_types_are_rejected(self):
        cases = []
        rid = new_req_id("codex")
        cases.append((rid, json.dumps(inner("codex", rid)).replace(str(NOW), "NaN")))
        for bad in ({"ts": True}, {"payload": []}, {"reply_protocol": 2}, {"admin": True}, {"grant_id": "nope"}):
            rid = new_req_id("codex")
            d = inner("codex", rid)
            d.update(bad)
            cases.append((rid, json.dumps(d)))
        rid = new_req_id("codex")
        d = inner("codex", rid)
        del d["text"]
        cases.append((rid, json.dumps(d)))
        for rid, text in cases:
            path, rid = self.env.signed("codex", req_id=rid, signed_text=text)
            self.assertRejected(self.env.route(path, rid), "bad_inner")

    def test_unknown_outer_key_is_rejected(self):
        rid = new_req_id("codex")
        kid, key = self.env.keys["codex"]
        outer = make_envelope("codex", kid, key, json.dumps(inner("codex", rid)), rid, extra_outer={"trusted": True})
        self.assertRejected(self.env.route(self.env.write_file(rid, outer), rid), "bad_outer")

    def test_freshness_window(self):
        for ts, ok in ((NOW - 8 * DAY, "stale"), (NOW + 20 * 60, "future"), (NOW + 5 * 60, None), (NOW - 6 * DAY, None)):
            path, rid = self.env.signed("codex", ts=ts)
            res = self.env.route(path, rid)
            if ok:
                self.assertRejected(res, ok)
                self.assertEqual(res.reply_hint, "signed_error")
            else:
                self.assertEqual(res.route, "inbox", res)

    def test_oversize_file_is_rejected_before_parsing(self):
        path, rid = self.env.signed("codex", text="x" * (1 << 20))
        self.assertRejected(self.env.route(path, rid), "oversize")

    def test_utf8_bom_is_tolerated(self):
        rid = new_req_id("codex")
        kid, key = self.env.keys["codex"]
        outer = make_envelope("codex", kid, key, json.dumps(inner("codex", rid), ensure_ascii=False), rid)
        res = self.env.route(self.env.write_file(rid, outer, bom=True), rid)
        self.assertEqual(res.route, "inbox", res)

    def test_revoked_agent(self):
        self.env.registry.revoke("codex", "test", NOW)
        path, rid = self.env.signed("codex")
        self.assertRejected(self.env.route(path, rid), "agent_revoked")

    def test_old_key_is_dead_after_rekey(self):
        old = self.env.keys["codex"]
        new_kid, new_key = self.env.registry.rekey("codex", EVIDENCE, NOW)
        self.assertEqual(new_kid, "codex#2")
        path, rid = self.env.signed("codex")                  # still signs with the old key
        self.assertRejected(self.env.route(path, rid), "retired_key")
        self.env.keys["codex"] = (new_kid, new_key)
        path, rid = self.env.signed("codex")
        self.assertEqual(self.env.route(path, rid).route, "inbox")
        self.assertNotEqual(old[1], new_key)

    def test_hand_edited_registry_entry_is_unusable(self):
        p = os.path.join(self.env.id_home, "agents.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["agents"]["kiro"]["tier"] = "whitelist"            # promote without a card
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        path, rid = self.env.signed("kiro", type_="task")
        res = self.env.route(path, rid)
        self.assertRejected(res, "registry_tampered")
        self.assertTrue(res.alert)
        path, rid = self.env.signed("codex")                  # other agents keep working
        self.assertEqual(self.env.route(path, rid).route, "inbox")

    def test_registry_mutations_need_evidence(self):
        for bad in ({}, {"popup_req": "", "answer": "ok", "at": NOW}, {"popup_req": "x y", "answer": "ok", "at": NOW}):
            with self.assertRaises((ValueError, M.IdentityError)):
                self.env.registry.add("newbot", "whitelist", {}, bad, NOW)
        with self.assertRaises((ValueError, M.IdentityError)):
            self.env.registry.set_tier("kiro", "whitelist", {}, NOW)

    def test_reserved_and_malformed_agent_ids_cannot_be_added(self):
        for aid in ("muse", "admin", "Codex", "a", "x" * 33, "bad_id", "ko\u0434ex"):
            with self.assertRaises((ValueError, M.IdentityError)):
                self.env.registry.add(aid, "whitelist", {}, EVIDENCE, NOW)


# ------------------------------------------------------------------ section 4.9
class Replay(Base):
    def test_same_bytes_twice_is_duplicate_and_not_redelivered(self):
        path, rid = self.env.signed("codex")
        self.assertEqual(self.env.route(path, rid).route, "inbox")
        os.remove(self.env.bridge_file("inbox", rid + ".json"))     # the worker took it
        res = self.env.route(path, rid)
        self.assertEqual(res.route, "duplicate")
        self.assertFalse(os.path.exists(self.env.bridge_file("inbox", rid + ".json")))

    def test_same_req_id_new_content_is_conflict(self):
        path, rid = self.env.signed("codex")
        self.env.route(path, rid)
        path2, _ = self.env.signed("codex", req_id=rid, text="different")
        res = self.env.route(path2, rid)
        # (the first copy legitimately stays in the inbox, so no assertRejected here)
        self.assertEqual((res.route, res.code), ("rejected", "conflict"))
        self.assertTrue(res.alert)

    def test_replay_memory_is_persistent(self):
        path, rid = self.env.signed("codex")
        self.env.route(path, rid)
        e2 = self.env.reopen()
        self.assertEqual(self.env.route(path, rid, env=e2).route, "duplicate")

    def test_policy_requires_retention_longer_than_freshness(self):
        def bad(d):
            d["seen_retention_s"] = d["freshness"]["max_age_s"]
        with self.assertRaises(M.PolicyError):
            policy_mod.Policy.from_dict(policy_dict(bad))


# ---------------------------------------------------------------- legacy (3)
class Legacy(Base):
    def test_transition_keeps_todays_behaviour(self):
        path, rid = self.env.legacy("codex-legion")
        res = self.env.route(path, rid)
        self.assertEqual((res.route, res.auth, res.decision), ("inbox", "legacy_unsigned", "allow"))
        path, rid = self.env.legacy("codex-legion", type_="publish-release")
        self.assertEqual(self.env.route(path, rid).decision, "ask_user")

    def test_registered_name_cannot_downgrade(self):
        path, rid = self.env.legacy("codex")
        res = self.env.route(path, rid)
        self.assertRejected(res, "downgrade")
        self.assertTrue(res.alert)

    def test_legacy_never_uses_grants(self):
        gid = self.env.grant()
        d = policy_mod.decide(self.env.policy, auth="legacy_unsigned", agent=None, action="publish-release",
                              grant_id=gid, facts=FACTS, grants=self.env.grants, now=NOW)
        self.assertEqual(d.decision, "ask_user")


class LegacyRejectMode(Base):
    @staticmethod
    def mutate_policy(d):
        d["legacy_unsigned"] = "reject"

    def test_reject_mode(self):
        path, rid = self.env.legacy("codex-legion")
        self.assertRejected(self.env.route(path, rid), "legacy_disabled")


# ---------------------------------------------------------------- enrollment
class EnrollRouting(Base):
    def test_enroll_goes_pending(self):
        path, rid = self.env.enroll_request("newbot")
        res = self.env.route(path, rid)
        self.assertEqual((res.route, res.auth, res.agent_id), ("pending", "enroll", "newbot"))
        self.assertTrue(os.path.exists(self.env.bridge_file("pending", rid + ".json")))
        self.assertFalse(os.path.exists(self.env.bridge_file("inbox", rid + ".json")))

    def test_enroll_rate_limit(self):
        results = [self.env.route(*self.env.enroll_request(f"bot{i}")) for i in range(4)]
        self.assertEqual([r.route for r in results[:3]], ["pending"] * 3)
        self.assertEqual((results[3].route, results[3].code), ("rejected", "enroll_rate_limited"))

    def test_enroll_with_reserved_id(self):
        res = self.env.route(*self.env.enroll_request("muse"))
        self.assertEqual((res.route, res.code), ("rejected", "bad_agent_id"))


class EnrollFlow(Base):
    @staticmethod
    def mutate_policy(d):
        d["enroll_limits"]["per_hour"] = 100      # these tests enroll many agents at the same instant

    def pending(self, agent_id, display_name="New Bot"):
        path, rid = self.env.enroll_request(agent_id, display_name=display_name)
        self.assertEqual(self.env.route(path, rid).route, "pending")
        return self.env.bridge_file("pending", rid + ".json"), rid

    def run_enroll(self, ppath, ui, pc):
        return flows.enroll(ppath, registry=self.env.registry, grants=self.env.grants, ui=ui, pc=pc,
                            store=self.env.store, policy=self.env.policy, now_fn=lambda: NOW)

    def test_approved_enrollment_provisions_the_pc(self):
        ppath, rid = self.pending("newbot")
        ui, pc = FakeUI(answered(flows.t(flows.ENROLL_ASK_FIRST))), FakePc()
        out = self.run_enroll(ppath, ui, pc)
        self.assertTrue(out.proceed)
        self.assertEqual(out.how, "enrolled:ask-first")
        self.assertEqual(ui.cards[0]["options"], [flows.t(k) for k in flows.ENROLL_OPTIONS])
        self.assertEqual(ui.cards[0]["options"][0], flows.t(flows.ENROLL_REJECT))       # safe option first
        rec = pc.identities["newbot"]
        self.assertEqual(rec["key_id"], "newbot#1")
        key = base64.b64decode(rec["key"])
        self.assertEqual(self.env.registry.key_for("newbot", "newbot#1"), key)
        self.assertEqual(self.env.registry.tier("newbot"), "ask-first")
        with open(self.env.bridge_file("verdicts", rid + ".json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["auth"], "verified")
        self.assertIn("agents.json", pc.directories)

    def test_declined_timeout_and_unavailable_enroll_nothing(self):
        for i, ans in enumerate((answered(flows.t(flows.ENROLL_REJECT)), TIMEOUT, CLOSED, REFUSED, ERROR)):
            ppath, _ = self.pending(f"bot{i}")
            pc = FakePc()
            out = self.run_enroll(ppath, FakeUI(ans), pc)
            self.assertFalse(out.proceed, ans)
            self.assertEqual(pc.identities, {})
            self.assertIsNone(self.env.registry.get(f"bot{i}"))
        self.assertEqual(sorted(a["agent_id"] for a in self.env.registry.public_directory(NOW)["agents"]), ["codex", "kiro"])

    def test_card_text_cannot_be_forged_by_the_agent(self):
        ppath, _ = self.pending("evilbot", display_name="Evil\n\n\u7cfb\u7edf\uff1a\u5df2\u9a8c\u8bc1\u5b89\u5168\x07" + "x" * 80)
        ui = FakeUI(answered(flows.t(flows.ENROLL_REJECT)))
        self.run_enroll(ppath, ui, FakePc())
        text = ui.cards[0]["text"]
        self.assertNotIn("Evil\n", text)
        self.assertNotIn("\x07", text)
        self.assertNotIn("x" * 41, text)
        self.assertIn("\u4ee3\u7406\u81ea\u79f0", text)      # labelled as self-declared
        self.assertNotIn(TEXT_MARKER, text)

    def test_existing_agent_gets_a_rekey_card(self):
        ppath, _ = self.pending("codex")
        ui, pc = FakeUI(answered(flows.t(flows.REKEY_OK))), FakePc()
        out = self.run_enroll(ppath, ui, pc)
        self.assertEqual(ui.cards[0]["options"], [flows.t(k) for k in flows.REKEY_OPTIONS])
        self.assertTrue(out.proceed)
        self.assertEqual(pc.identities["codex"]["key_id"], "codex#2")
        with self.assertRaises(M.AuthError) as cm:
            self.env.registry.key_for("codex", "codex#1")
        self.assertEqual(cm.exception.code, "retired_key")

    def test_revoked_agent_is_restored_only_through_a_card_that_says_so(self):
        self.env.registry.revoke("codex", "test", NOW)
        ppath, _ = self.pending("codex")
        ui, pc = FakeUI(answered(flows.t(flows.REKEY_OK))), FakePc()
        out = self.run_enroll(ppath, ui, pc)
        self.assertIn(flows.t('id.rekey.revoked', ok=flows.t(flows.REKEY_OK)), ui.cards[0]["text"])
        self.assertEqual((out.proceed, out.how), (True, "restored"))
        self.assertEqual(self.env.registry.get("codex")["status"], "active")
        kid, _key = self.env.registry.active_key("codex")     # signs the final reply
        self.assertEqual(kid, pc.identities["codex"]["key_id"])
        # declined: still revoked, no key change
        self.env.registry.revoke("codex", "test", NOW)
        ppath, _ = self.pending("codex")
        out = self.run_enroll(ppath, FakeUI(answered(flows.t(flows.REKEY_NO))), FakePc())
        self.assertFalse(out.proceed)
        self.assertEqual(self.env.registry.get("codex")["status"], "revoked")
        with self.assertRaises(ValueError):
            self.env.registry.rekey("codex", EVIDENCE, NOW)    # a plain rekey never revives

    def test_pc_failure_is_reported(self):
        ppath, _ = self.pending("newbot")
        out = self.run_enroll(ppath, FakeUI(answered(flows.t(flows.ENROLL_WHITELIST))), FakePc(fail=True))
        self.assertEqual(out.how, "provision_failed")


# ------------------------------------------------------------------ section 7
class GrantLifecycle(Base):
    def consume(self, gid, rid, agent="codex", action="publish-release", facts=FACTS, now=NOW):
        return self.env.grants.consume(gid, agent, rid, action, facts, now)

    def test_uses_are_counted_and_capped(self):
        gid = self.env.grant(uses=2)
        r1, r2, r3 = (self.consume(gid, f"r{i}") for i in range(3))
        self.assertEqual((r1.ok, r1.uses_left), (True, 1))
        self.assertEqual((r2.ok, r2.uses_left), (True, 0))
        self.assertEqual((r3.ok, r3.reason), (False, "exhausted"))

    def test_consume_is_idempotent_per_request(self):
        gid = self.env.grant(uses=2)
        self.consume(gid, "r1")
        again = self.consume(gid, "r1")
        self.assertTrue(again.ok and again.already)
        self.assertEqual(again.uses_left, 1)

    def test_validity_window(self):
        gid = self.env.grant(days=1)
        self.assertEqual(self.consume(gid, "r1", now=NOW + DAY + 1).reason, "expired")
        gid2 = self.env.grant(not_before=NOW + 3600)
        self.assertEqual(self.consume(gid2, "r2").reason, "not_yet_valid")

    def test_bound_to_agent_and_action(self):
        gid = self.env.grant()
        self.assertEqual(self.consume(gid, "r1", agent="kiro").reason, "wrong_agent")
        self.assertEqual(self.consume(gid, "r2", action="task").reason, "wrong_action")
        self.assertTrue(self.consume(gid, "r3", action="publish-voicebridge").ok)   # retired name

    def test_conditions_are_enforced_on_facts(self):
        gid = self.env.grant()
        self.assertEqual(self.consume(gid, "r1", facts=dict(FACTS, version="2.0.0")).reason, "conditions_failed")
        self.assertEqual(self.consume(gid, "r2", facts=dict(FACTS, package_path="D:\\MyApp\\outside\\x.zip")).reason, "conditions_failed")
        facts = dict(FACTS)
        del facts["package_path"]
        self.assertEqual(self.consume(gid, "r3", facts=facts).reason, "conditions_failed")
        self.assertEqual(self.consume(gid, "r4", facts=dict(FACTS, package_path=None)).reason, "conditions_failed")

    def test_revocation_is_immediate_and_sticky(self):
        gid = self.env.grant()
        self.env.grants.revoke(gid, "test", NOW)
        self.assertEqual(self.consume(gid, "r1").reason, "revoked")
        p = os.path.join(self.env.id_home, "grants.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["grants"][gid]["status"] = "active"                  # un-revoke by hand
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        r = self.consume(gid, "r2")
        self.assertFalse(r.ok)
        self.assertIn(r.reason, ("tampered", "revoked"))

    def test_hand_edited_grant_is_unusable(self):
        gid = self.env.grant(uses=1)
        p = os.path.join(self.env.id_home, "grants.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["grants"][gid]["max_uses"] = 20
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.assertEqual(self.consume(gid, "r1").reason, "tampered")
        self.assertTrue(os.listdir(self.env.bridge_file("alerts")))

    def test_proposal_is_not_a_grant(self):
        g = self.env.grants.propose(self.env.spec(), "user-chat", NOW)
        self.assertEqual(self.consume(g["grant_id"], "r1").reason, "not_active")

    def test_activation_needs_evidence_and_unchanged_proposal(self):
        g = self.env.grants.propose(self.env.spec(), "user-chat", NOW)
        with self.assertRaises((ValueError, M.GrantError)):
            self.env.grants.activate(g["grant_id"], {}, NOW)
        p = os.path.join(self.env.id_home, "grants.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["grants"][g["grant_id"]]["max_uses"] = 20             # changed after the card text was made
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        with self.assertRaises(M.GrantError):
            self.env.grants.activate(g["grant_id"], EVIDENCE, NOW)

    def test_spec_validation_and_caps(self):
        bad_specs = [
            self.env.spec(days=31), self.env.spec(uses=21), self.env.spec(uses=0), self.env.spec(uses=True),
            self.env.spec(action="message"), self.env.spec(action="payment"), self.env.spec(action="task"),
            self.env.spec(conditions=[{"field": "version", "op": "regex", "value": ".*"}]),
            self.env.spec(conditions=[{"field": "package_size", "op": "max", "value": True}]),
            self.env.spec(conditions=[{"field": "version", "op": "prefix", "value": ""}]),
            self.env.spec(mode="silent"), self.env.spec(agent_id="Codex"),
        ]
        no_expiry = self.env.spec()
        del no_expiry["expires_at"]
        bad_specs.append(no_expiry)
        for s in bad_specs:
            with self.assertRaises(M.GrantError, msg=json.dumps(s)):
                grants_mod.validate_spec(s, self.env.policy, NOW)
        grants_mod.validate_spec(self.env.spec(days=30, uses=20), self.env.policy, NOW)

    def test_revoking_an_agent_kills_its_grants(self):
        gid = self.env.grant()
        self.assertEqual(self.env.grants.revoke_agent("codex", "test", NOW), 1)
        self.assertEqual(self.consume(gid, "r1").reason, "revoked")

    def test_grant_of_a_revoked_agent_is_unusable(self):
        gid = self.env.grant()
        self.env.registry.revoke("codex", "test", NOW)
        self.assertEqual(self.consume(gid, "r1").reason, "agent_not_active")

    def test_reservations(self):
        gid = self.env.grant(uses=1)
        g = self.env.grants
        self.assertTrue(g.reserve(gid, "codex", "r1", "publish-release", FACTS, NOW, 420).ok)
        self.assertEqual(self.consume(gid, "r2").reason, "exhausted")          # the reservation counts
        g.release(gid, "r1", NOW)
        self.assertTrue(self.consume(gid, "r2").ok)

    def test_expired_reservation_does_not_count(self):
        gid = self.env.grant(uses=1)
        g = self.env.grants
        g.reserve(gid, "codex", "r1", "publish-release", FACTS, NOW, 420)
        self.assertTrue(self.consume(gid, "r2", now=NOW + 421).ok)
        self.assertFalse(g.confirm(gid, "r1", NOW + 422).ok)

    def test_confirm_live_reservation(self):
        gid = self.env.grant(uses=1)
        g = self.env.grants
        g.reserve(gid, "codex", "r1", "publish-release", FACTS, NOW, 420)
        self.assertTrue(g.confirm(gid, "r1", NOW + 100).ok)
        self.assertEqual(self.consume(gid, "r2", now=NOW + 101).reason, "exhausted")

    def test_concurrent_consumers_cannot_overspend(self):
        gid = self.env.grant(uses=1)
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(6) as pool:
            results = pool.map(consume_worker, [(self.env.tmp, gid, f"c{i}") for i in range(12)])
        self.assertEqual(sum(results), 1, results)

    def test_auto_match_is_off_by_default(self):
        self.env.grant()
        self.assertIsNone(self.env.grants.find_auto("codex", "publish-release", FACTS, NOW))
        gid = self.env.grant(auto_match=True)
        self.assertEqual(self.env.grants.find_auto("codex", "publish-release", FACTS, NOW), gid)

    def test_card_text_comes_from_structured_fields(self):
        spec = self.env.spec()
        spec["note"] = "line1\nline2 \x1b[31m"
        g = self.env.grants.propose(spec, "user-chat", NOW)
        text = grants_mod.render(self.env.grants.get(g["grant_id"]))
        self.assertIn("D:\\MyApp\\out", text)
        self.assertIn("1.9.", text)
        self.assertIn("codex", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("line1\nline2", text)


# ------------------------------------------------------------- conditions (7)
class PathUnder(unittest.TestCase):
    def test_inside(self):
        for path, base in (
            ("D:\\VB\\out\\x.zip", "D:\\VB\\out"),
            ("d:\\vb\\OUT\\sub\\x.zip", "D:\\VB\\out"),
            ("D:/VB/out/x.zip", "D:\\VB\\out"),
            ("D:\\VB\\out\\x.zip", "D:\\VB\\out\\"),
            ("D:\\VB\\out", "D:\\VB\\out"),
        ):
            self.assertTrue(grants_mod.path_under(path, base), (path, base))

    def test_outside_or_ambiguous(self):
        for path, base in (
            ("D:\\VB\\outside\\x.zip", "D:\\VB\\out"),
            ("D:\\VB\\out\\..\\evil\\x.zip", "D:\\VB\\out"),
            ("D:\\VB\\out\\.\\x.zip", "D:\\VB\\out"),
            ("\\\\server\\share\\x.zip", "\\\\server\\share"),
            ("\\\\?\\D:\\VB\\out\\x.zip", "D:\\VB\\out"),
            ("\\\\.\\D:\\VB\\out\\x.zip", "D:\\VB\\out"),
            ("D:\\VB\\out\\x.zip:hidden", "D:\\VB\\out"),
            ("D:\\VB\\out.\\x.zip", "D:\\VB\\out"),
            ("D:\\VB\\out \\x.zip", "D:\\VB\\out"),
            ("D:\\VB\\\\out\\x.zip", "D:\\VB\\out"),
            ("VB\\out\\x.zip", "VB\\out"),
            ("E:\\VB\\out\\x.zip", "D:\\VB\\out"),
            ("D:\\VB\\out", "D:\\VB\\out\\x"),
            ("D:\\STRASSE\\x.zip", "D:\\Stra\u00dfe"),
            ("D:\\Strasse\\x.zip", "D:\\Stra\u00dfe"),
            ("", "D:\\VB\\out"),
        ):
            self.assertFalse(grants_mod.path_under(path, base), (path, base))


class Conditions(unittest.TestCase):
    def ok(self, cond, facts):
        return grants_mod.evaluate([cond], facts)[0]

    def test_type_strict_equals(self):
        c = {"field": "n", "op": "equals", "value": 1}
        self.assertTrue(self.ok(c, {"n": 1}))
        self.assertFalse(self.ok(c, {"n": "1"}))
        self.assertFalse(self.ok(c, {"n": True}))

    def test_numbers_exclude_bool(self):
        c = {"field": "n", "op": "max", "value": 5}
        self.assertTrue(self.ok(c, {"n": 3}))
        self.assertFalse(self.ok(c, {"n": 6}))
        self.assertFalse(self.ok(c, {"n": True}))
        self.assertFalse(self.ok({"field": "n", "op": "min", "value": 2}, {"n": "3"}))

    def test_in_iequals_prefix_dotted_missing(self):
        self.assertTrue(self.ok({"field": "s", "op": "in", "value": ["a", "b"]}, {"s": "a"}))
        self.assertFalse(self.ok({"field": "s", "op": "in", "value": ["a", "b"]}, {"s": "c"}))
        self.assertTrue(self.ok({"field": "s", "op": "iequals", "value": "abc"}, {"s": "ABC"}))
        self.assertFalse(self.ok({"field": "s", "op": "iequals", "value": "\u00e4bc"}, {"s": "\u00c4BC"}))  # ASCII-only fold
        self.assertTrue(self.ok({"field": "v", "op": "prefix", "value": "1.9."}, {"v": "1.9.2"}))
        self.assertFalse(self.ok({"field": "v", "op": "prefix", "value": "1.9."}, {"v": "1.90.0"}))
        self.assertTrue(self.ok({"field": "a.b", "op": "equals", "value": "x"}, {"a": {"b": "x"}}))
        self.assertFalse(self.ok({"field": "a.b", "op": "equals", "value": "x"}, {}))
        self.assertFalse(self.ok({"field": "a", "op": "equals", "value": None}, {"a": None}))

    def test_all_conditions_must_hold(self):
        ok, failures = grants_mod.evaluate(CONDITIONS, dict(FACTS, version="2.0.0"))
        self.assertFalse(ok)
        self.assertEqual(len(failures), 1)
        self.assertTrue(grants_mod.evaluate(CONDITIONS, FACTS)[0])
        self.assertTrue(grants_mod.evaluate([], FACTS)[0])


# ------------------------------------------------------------------ section 8
class PolicyFile(unittest.TestCase):
    def test_example_policy_loads(self):
        p = policy_mod.Policy.from_dict(policy_dict())   # the site template's policy section
        self.assertRegex(p.hash, r"^[0-9a-f]{64}$")
        self.assertEqual(p.classify("publish-release"), "privileged")
        self.assertEqual(p.classify("publish-voicebridge"), "privileged")   # retired name, mapped in code
        self.assertEqual(p.classify("ping"), "conversation")
        self.assertEqual(p.classify("dance"), "unknown")

    def test_invalid_policies_fail_closed(self):
        mutations = [
            lambda d: d.__setitem__("delay_window_s", 600),        # author's ceiling is 120 s
            lambda d: d.__setitem__("delay_window_s", 5),
            lambda d: d.__setitem__("surprise", 1),
            lambda d: d.__setitem__("legacy_unsigned", "maybe"),
            lambda d: d["tiers"]["whitelist"].__setitem__("task", "sure"),
            lambda d: d["classes"]["task"].append("ping"),          # one type in two classes
            lambda d: d.pop("tiers"),
        ]
        for m in mutations:
            with self.assertRaises(M.PolicyError):
                policy_mod.Policy.from_dict(policy_dict(m))
        policy_mod.Policy.from_dict(policy_dict(lambda d: d.__setitem__("_note", "ignored")))
        policy_mod.Policy.from_dict(policy_dict(lambda d: d.__setitem__("delay_window_s", 120)))


class Decide(Base):
    def decide(self, agent_id="codex", action="publish-release", grant_id=None, facts=FACTS, now=NOW, auth="verified"):
        agent = self.env.registry.get(agent_id) if agent_id else None
        return policy_mod.decide(self.env.policy, auth=auth, agent=agent, action=action, grant_id=grant_id,
                                 facts=facts, grants=self.env.grants, now=now)

    def test_tier_table(self):
        self.assertEqual(self.decide(action="message").decision, "allow")
        self.assertEqual(self.decide(action="task").decision, "allow")
        self.assertEqual(self.decide("kiro", action="message").decision, "allow")
        self.assertEqual(self.decide("kiro", action="task").decision, "ask_user")
        self.assertEqual(self.decide(action="dance").decision, "ask_user")
        self.assertEqual(self.decide(action="payment").decision, "ask_user")

    def test_privileged_needs_a_grant(self):
        d = self.decide()
        self.assertEqual((d.decision, d.reason), ("ask_user", "no_grant"))
        gid = self.env.grant()
        d = self.decide(grant_id=gid)
        self.assertEqual((d.decision, d.grant_id), ("allow_with_grant", gid))
        self.assertRegex(d.policy_hash, r"^[0-9a-f]{64}$")

    def test_delay_grant(self):
        gid = self.env.grant(mode="delay")
        d = self.decide(grant_id=gid)
        self.assertEqual((d.decision, d.window_s), ("delay", 120))

    def test_unusable_grant_falls_back_to_asking(self):
        gid = self.env.grant(uses=1)
        self.env.grants.consume(gid, "codex", "r0", "publish-release", FACTS, NOW)
        d = self.decide(grant_id=gid)
        self.assertEqual(d.decision, "ask_user")
        self.assertTrue(d.reason.startswith("grant_"), d.reason)

    def test_grants_never_unlock_red_line(self):
        gid = self.env.grant()
        self.assertEqual(self.decide(action="payment", grant_id=gid).decision, "ask_user")

    def test_grant_on_conversation_is_ignored_and_not_spent(self):
        gid = self.env.grant(uses=1)
        d = self.decide(action="message", grant_id=gid)
        self.assertEqual(d.decision, "allow")
        self.assertTrue(d.reason.endswith("+grant_ignored"), d.reason)
        self.assertTrue(self.env.grants.consume(gid, "codex", "r1", "publish-release", FACTS, NOW).ok)

    def test_decide_is_pure(self):
        gid = self.env.grant(uses=1)
        for _ in range(3):
            self.decide(grant_id=gid)
        self.assertTrue(self.env.grants.consume(gid, "codex", "r1", "publish-release", FACTS, NOW).ok)

    def test_inactive_agent_is_rejected(self):
        self.env.registry.revoke("kiro", "test", NOW)
        self.assertEqual(self.decide("kiro", action="message").decision, "reject")


class Authorize(Base):
    def run_auth(self, ui, grant_id=None, agent_id="codex", action="publish-release", facts=FACTS):
        ctx = {"auth": "verified", "agent": self.env.registry.get(agent_id), "agent_id": agent_id,
               "req_id": new_req_id(agent_id), "action": action, "facts": facts, "grant_id": grant_id}
        return flows.authorize(ctx, policy=self.env.policy, grants=self.env.grants, ui=ui,
                               store=self.env.store, now_fn=lambda: NOW)

    def spent(self, gid):
        return not self.env.grants.consume(gid, "codex", "probe", "publish-release", FACTS, NOW).ok

    def test_grant_allows_without_a_card(self):
        gid = self.env.grant(uses=1)
        ui = FakeUI()
        out = self.run_auth(ui, gid)
        self.assertEqual((out.proceed, out.how, out.grant_id), (True, "grant", gid))
        self.assertEqual(ui.cards, [])
        self.assertTrue(self.spent(gid))

    def test_delay_card_shape_and_timeout_proceeds(self):
        gid = self.env.grant(uses=1, mode="delay")
        ui = FakeUI(TIMEOUT)
        out = self.run_auth(ui, gid)
        self.assertEqual((out.proceed, out.how), (True, "delay_passed"))
        card = ui.cards[0]
        self.assertEqual(card["options"], [flows.t(flows.DELAY_NOW), flows.t(flows.DELAY_CANCEL)])
        self.assertEqual(card["timeout_s"], 120)
        self.assertTrue(card["expire"])
        self.assertIn(gid, card["text"])
        self.assertTrue(self.spent(gid))

    def test_delay_now(self):
        gid = self.env.grant(mode="delay")
        out = self.run_auth(FakeUI(answered(flows.t(flows.DELAY_NOW))), gid)
        self.assertEqual((out.proceed, out.how), (True, "delay_now"))

    def test_delay_cancel_close_and_unavailable_do_not_proceed_and_release(self):
        for ans, how in ((answered(flows.t(flows.DELAY_CANCEL)), "cancelled"), (CLOSED, "cancelled"),
                         (REFUSED, "card_unavailable"), (ERROR, "card_unavailable")):
            gid = self.env.grant(uses=1, mode="delay")
            out = self.run_auth(FakeUI(ans), gid)
            self.assertEqual((out.proceed, out.how), (False, how), ans)
            self.assertFalse(self.spent(gid), "the reservation was not released")

    def test_ask_card(self):
        ui = FakeUI(answered(flows.t(flows.ASK_ALLOW)))
        out = self.run_auth(ui)
        self.assertEqual((out.proceed, out.how), (True, "user_approved"))
        self.assertEqual(ui.cards[0]["options"][:2], [flows.t(flows.ASK_ALLOW), flows.t(flows.ASK_DENY)])
        self.assertEqual(out.evidence["popup_req"], "pop_20261112_101600_bbbbbb")

    def test_ask_anything_but_allow_is_no(self):
        for ans in (answered(flows.t(flows.ASK_DENY)), TIMEOUT, CLOSED, REFUSED, ERROR):
            self.assertFalse(self.run_auth(FakeUI(ans)).proceed, ans)

    def test_reject_shows_no_card(self):
        self.env.registry.revoke("kiro", "test", NOW)
        ui = FakeUI()
        out = self.run_auth(ui, agent_id="kiro", action="message")
        self.assertFalse(out.proceed)
        self.assertEqual(ui.cards, [])

    def test_exceptions_fail_closed_and_release(self):
        gid = self.env.grant(uses=1, mode="delay")
        out = self.run_auth(FakeUI(RuntimeError("boom")), gid)
        self.assertEqual((out.proceed, out.how), (False, "internal_error"))
        self.assertFalse(self.spent(gid))

    def test_audit_line_has_no_fact_values(self):
        gid = self.env.grant()
        self.run_auth(FakeUI(), gid)
        lines = [l for l in self.env.audit_lines() if l.get("event") == "authorize"]
        self.assertEqual(len(lines), 1)
        dumped = json.dumps(lines[0], ensure_ascii=False)
        self.assertNotIn("MyApp", dumped)
        self.assertIn(gid, dumped)


# ---------------------------------------------------------------- section 5
class Replies(Base):
    def test_round_trip_and_binding(self):
        kid, key = self.env.keys["codex"]
        inn = {"req_id": "codex_20261112_101500_000001", "seq": 2, "agent_id": "codex", "final": True,
               "status": "ok", "replied_at": "2026-11-12T09:16:03+00:00", "from": "muse", "reply_protocol": 3, "text": "done"}
        env = envelope.new_reply_envelope("codex", kid, key, inn)
        self.assertEqual(envelope.verify_reply(env, key, inn["req_id"], "codex", 2)["text"], "done")
        for args in ((inn["req_id"] + "x", "codex", 2), (inn["req_id"], "kiro", 2), (inn["req_id"], "codex", 3)):
            with self.assertRaises(M.AuthError):
                envelope.verify_reply(env, key, *args)
        with self.assertRaises(M.AuthError):
            envelope.verify_reply(env, self.env.keys["kiro"][1], inn["req_id"], "codex", 2)

    def test_request_signature_is_not_a_reply_signature(self):
        kid, key = self.env.keys["codex"]
        inn = {"req_id": "codex_20261112_101500_000001", "seq": 1, "agent_id": "codex", "final": True,
               "status": "ok", "replied_at": "x", "from": "muse", "reply_protocol": 3, "text": "forged"}
        text = json.dumps(inn)
        forged = {"v": 3, "req_id": inn["req_id"], "seq": 1, "agent_id": "codex", "signed": text,
                  "auth": {"alg": "HMAC-SHA256", "key_id": kid, "sig": mac(key, M.REQ_DOMAIN, text)}}
        with self.assertRaises(M.AuthError):
            envelope.verify_reply(forged, key, inn["req_id"], "codex", 1)


# ------------------------------------------------------------ secrets/audit
class NoLeaks(Base):
    def test_no_secret_or_text_in_logs_verdicts_alerts_or_mirror(self):
        path, rid = self.env.signed("codex")
        self.env.route(path, rid)
        rid2 = new_req_id("codex")
        kid, key = self.env.keys["codex"]
        outer = make_envelope("codex", kid, key, json.dumps(inner("codex", rid2)), rid2)
        outer["signed"] = outer["signed"].replace("hello", "HELLO")
        self.env.route(self.env.write_file(rid2, outer), rid2)
        self.env.grant()
        secrets = [base64.b64encode(k).decode() for _kid, k in self.env.keys.values()]
        with open(os.path.join(self.env.id_home, "seal.key"), "rb") as f:
            seal = f.read()
        # seal.key may be raw bytes or base64 text: forbid every representation
        secrets += [base64.b64encode(seal).decode(), seal.hex()]
        try:
            secrets.append(seal.decode("ascii").strip())
        except UnicodeDecodeError:
            pass
        secrets = [s for s in secrets if len(s) >= 16]
        blobs = [
            self.env.read_all_text_under("verdicts", "alerts"),
            self.env.read_all_text_under(os.path.join(self.env.id_home, "audit.jsonl")),
            json.dumps(self.env.registry.public_directory(NOW), ensure_ascii=False),
            json.dumps(self.env.grants.public_list(NOW), ensure_ascii=False),
        ]
        for blob in blobs:
            self.assertNotIn(TEXT_MARKER, blob)
            for s in secrets:
                self.assertNotIn(s, blob)
        mirror = self.env.registry.public_directory(NOW)
        for a in mirror["agents"]:
            self.assertFalse({"key", "keys", "seal"} & set(a))
        for g in self.env.grants.public_list(NOW):
            self.assertFalse({"seal", "approval"} & set(g))
        self.assertTrue(all("event" in l for l in self.env.audit_lines()))


@unittest.skipUnless(POSIX, "file permission semantics are POSIX-only")
class StorePermissions(Base):
    def test_files_are_private_from_the_start(self):
        for n in ("agents.json", "grants.json", "seal.key", "state.sqlite"):
            p = os.path.join(self.env.id_home, n)
            if n == "grants.json":
                self.env.grant()
            self.assertTrue(os.path.exists(p), n)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode) & 0o077, 0, n)

    def test_loose_permissions_are_refused(self):
        os.chmod(os.path.join(self.env.id_home, "agents.json"), 0o644)
        with self.assertRaises(M.InsecureStore):
            store_mod.Store(self.env.id_home, self.env.bridge).open()
        os.chmod(os.path.join(self.env.id_home, "agents.json"), 0o600)
        os.chmod(self.env.id_home, 0o755)
        with self.assertRaises(M.InsecureStore):
            store_mod.Store(self.env.id_home, self.env.bridge).open()
        os.chmod(self.env.id_home, 0o700)


if __name__ == "__main__":
    unittest.main()
