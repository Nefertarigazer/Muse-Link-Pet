"""Shared fixtures for the identity acceptance tests (not a test module).

Envelopes are built HERE with the standard library (hmac/hashlib/json), not
with muselink_id.envelope, so a symmetric bug in the code under test (e.g.
the wrong domain on both sides) cannot make the tests pass.
"""
import base64
import copy
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "vm"))

import muselink_id as M  # noqa: E402
from muselink_id import envelope, flows, inbound  # noqa: E402
from muselink_id import grants as grants_mod  # noqa: E402
from muselink_id import policy as policy_mod  # noqa: E402
from muselink_id import registry as registry_mod  # noqa: E402
from muselink_id import store as store_mod  # noqa: E402
from muselink_id.ui import UiAnswer  # noqa: E402
from muselink_id import i18n as locale_mod  # noqa: E402
from unittest.mock import patch  # noqa: E402

# Synthetic acceptance cards use the original language explicitly. Never query
# a live PC while testing a fixture store.
with patch.object(locale_mod, 'pc_locale', return_value={'system': 'zh-CN', 'ui': 'zh-Hans', 'docs': 'zh'}):
    locale_mod.active.cache_clear()
    locale_mod.active()

SITE_EXAMPLE = os.path.join(ROOT, "config", "muselink.site.example.json")   # its "policy" section
VECTORS = os.path.join(ROOT, "spec", "test-vectors", "identity.json")
NOW = 1794564900                      # 2026-11-12T09:15:00Z
DAY = 86400
EVIDENCE = {"popup_req": "pop_20261112_101500_aaaaaa", "answer": "ok", "at": NOW}
TEXT_MARKER = "SECRET-TEXT-MARKER-7f3a"
FACTS = {
    "version": "1.9.2",
    "package_path": "D:\\MyApp\\out\\VB-1.9.2.zip",
    "package_size": 91234567,
    "setup_present": False,
    "test": False,
}
CONDITIONS = [
    {"field": "package_path", "op": "path_under", "value": "D:\\MyApp\\out"},
    {"field": "version", "op": "prefix", "value": "1.9."},
]


def b64(data):
    return base64.b64encode(data).decode("ascii")


def mac(key, domain, text):
    return b64(hmac.new(key, (domain + text).encode("utf-8"), hashlib.sha256).digest())


def policy_dict(mutate=None):
    with open(SITE_EXAMPLE, encoding="utf-8") as f:
        d = json.load(f)["policy"]
    if mutate:
        mutate(d)
    return d


def load_policy(mutate=None):
    return policy_mod.Policy.from_dict(policy_dict(mutate))


_counter = [0]


def new_req_id(agent_id):
    _counter[0] += 1
    return f"{agent_id}_20261112_101500_{_counter[0]:06x}"


def inner(agent_id, req_id, type_="message", ts=NOW, text="hello " + TEXT_MARKER, payload=None, **extra):
    d = {
        "req_id": req_id,
        "agent_id": agent_id,
        "ts": ts,
        "type": type_,
        "text": text,
        "payload": {} if payload is None else payload,
        "reply_protocol": 3,
    }
    d.update(extra)
    return d


def make_envelope(agent_id, key_id, key, signed_text, req_id, *, outer_agent=None, alg="HMAC-SHA256", sig=None, extra_outer=None):
    outer = {
        "v": 3,
        "req_id": req_id,
        "agent_id": outer_agent or agent_id,
        "signed": signed_text,
        "auth": {"alg": alg, "key_id": key_id, "sig": sig if sig is not None else mac(key, M.REQ_DOMAIN, signed_text)},
    }
    if extra_outer:
        outer.update(extra_outer)
    return outer


class FakeUI:
    """Records every card; answers from a queue (UiAnswer or Exception)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.cards = []

    def choice(self, text, options, timeout_s, expire=False):
        self.cards.append({"text": text, "options": list(options), "timeout_s": timeout_s, "expire": expire})
        a = self.answers.pop(0) if self.answers else UiAnswer("error", "", "")
        if isinstance(a, Exception):
            raise a
        return a

    def notify(self, text):
        self.cards.append({"text": text, "options": [], "notify": True})


def answered(label):
    return UiAnswer("answered", label, "pop_20261112_101600_bbbbbb")


TIMEOUT = UiAnswer("timeout", "", "pop_20261112_101600_cccccc")
CLOSED = UiAnswer("closed", "", "pop_20261112_101600_dddddd")
REFUSED = UiAnswer("refused", "", "")
ERROR = UiAnswer("error", "", "")


class FakePc:
    def __init__(self, fail=False):
        self.fail = fail
        self.identities = {}
        self.removed = []
        self.directories = {}

    def write_identity(self, agent_id, record):
        if self.fail:
            raise OSError("PC unreachable (fake)")
        self.identities[agent_id] = copy.deepcopy(record)

    def remove_identity(self, agent_id):
        self.removed.append(agent_id)
        self.identities.pop(agent_id, None)

    def write_directory(self, name, obj):
        self.directories[name] = copy.deepcopy(obj)


class Env:
    """A private identity home + bridge home in a temp dir."""

    def __init__(self, mutate_policy=None, tmp=None):
        self.owns_tmp = tmp is None
        self.tmp = tmp or tempfile.mkdtemp(prefix="mlid-")
        self.id_home = os.path.join(self.tmp, "identity")
        self.bridge = os.path.join(self.tmp, "bridge")
        self.pc_inbox = os.path.join(self.tmp, "pc-inbox")
        for d in (self.id_home, self.bridge, self.pc_inbox):
            os.makedirs(d, mode=0o700, exist_ok=True)
        self.policy_d = policy_dict(mutate_policy)
        self.policy = policy_mod.Policy.from_dict(self.policy_d)
        self.store = store_mod.Store(self.id_home, self.bridge).open()
        self.registry = registry_mod.Registry(self.store)
        self.grants = grants_mod.Grants(self.store, self.policy, self.registry)
        self.keys = {}  # agent_id -> (key_id, key) as returned by the registry

    def reopen(self):
        """Fresh objects on the same directories (persistence checks)."""
        return Env(tmp=self.tmp, mutate_policy=None)

    def close(self):
        if self.owns_tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- agents and requests
    def add_agent(self, agent_id, tier="whitelist"):
        key_id, key = self.registry.add(agent_id, tier, {"display_name": agent_id}, EVIDENCE, NOW)
        self.keys[agent_id] = (key_id, key)
        return key_id, key

    def write_file(self, req_id, obj=None, raw=None, bom=False):
        data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        path = os.path.join(self.pc_inbox, req_id + ".json")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def signed(self, agent_id, *, key_from=None, req_id=None, signed_text=None, outer_agent=None, key_id=None, **inner_kw):
        """Write a signed request; returns (path, req_id)."""
        rid = req_id or new_req_id(agent_id)
        kid, key = self.keys[key_from or agent_id]
        text = signed_text if signed_text is not None else json.dumps(inner(agent_id, rid, **inner_kw), ensure_ascii=False)
        outer = make_envelope(agent_id, key_id or kid, key, text, rid, outer_agent=outer_agent)
        return self.write_file(rid, outer), rid

    def enroll_request(self, agent_id, display_name="New Bot", ts=NOW):
        rid = new_req_id(agent_id)
        inn = inner(agent_id, rid, type_="enroll", ts=ts, text="",
                    client={"display_name": display_name, "product": "test", "model": "m", "host": "PC", "session_id": "s1"})
        outer = {"v": 3, "req_id": rid, "agent_id": agent_id, "signed": json.dumps(inn, ensure_ascii=False), "auth": None}
        return self.write_file(rid, outer), rid

    def legacy(self, from_, type_="message", payload=None):
        rid = new_req_id("codex")
        req = {"req_id": rid, "created_at": "2026-11-12T10:15:00+01:00", "from": from_, "type": type_,
               "text": "legacy " + TEXT_MARKER, "payload": payload or {}, "reply_protocol": 2}
        return self.write_file(rid, req), rid

    def route(self, path, req_id, now=NOW, env=None):
        e = env or self
        return inbound.route_request(path, req_id, store=e.store, registry=e.registry, policy=e.policy,
                                     grants=e.grants, now=now)

    # ---- grants
    def spec(self, agent_id="codex", action="publish-release", conditions=None, days=7, uses=3,
             mode="allow", auto_match=False, not_before=NOW, now=NOW):
        return {
            "agent_id": agent_id, "action": action,
            "conditions": copy.deepcopy(CONDITIONS if conditions is None else conditions),
            "not_before": not_before, "expires_at": now + days * DAY, "max_uses": uses,
            "mode": mode, "auto_match": auto_match, "note": "test grant",
        }

    def grant(self, now=NOW, **kw):
        g = self.grants.propose(self.spec(now=now, **kw), "user-chat", now)
        self.grants.activate(g["grant_id"], EVIDENCE, now)
        return g["grant_id"]

    # ---- files
    def bridge_file(self, *parts):
        return os.path.join(self.bridge, *parts)

    def read_all_text_under(self, *dirs):
        out = []
        for d in dirs:
            p = os.path.join(self.bridge, d) if not os.path.isabs(d) else d
            if os.path.isfile(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    out.append(f.read())
                continue
            for root, _dirs, files in os.walk(p):
                for n in files:
                    with open(os.path.join(root, n), encoding="utf-8", errors="replace") as f:
                        out.append(f.read())
        return "\n".join(out)

    def audit_lines(self):
        p = os.path.join(self.id_home, "audit.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def consume_worker(args):
    """Top-level so multiprocessing (spawn) can import it."""
    tmp, grant_id, req_id = args
    e = Env(tmp=tmp)
    r = e.grants.consume(grant_id, "codex", req_id, "publish-release", FACTS, NOW)
    return bool(r.ok)
