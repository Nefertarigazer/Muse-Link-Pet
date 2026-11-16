"""policy - the policy file and the pure decide() function.

Requirements
  1. Policy.from_file(path) / Policy.from_dict(d): keys starting with "_"
     are ignored at every level; any other unknown key, a missing key or a
     wrong type -> PolicyError (fail closed: nothing is decided with a
     half-understood policy). Template: the "policy" section of
     config/muselink.site.example.json, written out by vm/install.sh (the
     tests load it, so it must stay valid).
  2. Validation beyond types: delay_window_s in 10..120 (author's decision
     2026-11-12: at most 2 minutes); legacy_unsigned in {"transition",
     "reject"}; every tier maps every class to one of allow | ask |
     grant_or_ask | reject, except red_line, which must be ask | reject in
     every tier, and unknown_type, which must be ask | reject (author's
     boundary, review 2026-11-12); a type may appear in at most one class; alias
     targets must be a known type; seen_retention_s > freshness.max_age_s
     (otherwise a replay could slip in after its seen row expired).
  3. policy.hash = SHA-256 hex of the file bytes (from_dict: of
     canonical_json). Every Decision carries it so audits show which policy
     decided.
  4. canonical(type_or_action): lower-case, retired type names mapped
     (muselink_id.RETIRED_TYPES), then aliases applied.
     classify(name) -> "conversation" | "task" | "privileged" | "red_line" |
     "unknown", after canonical().
  5. decide(...) is PURE: no writes, no cards. It may call
     grants.check() / grants.find_auto() (read-only). Table: docs/protocol.en.md
     section 4. Precisely:
       auth "failed"                       -> reject (reason = auth code)
       auth "legacy_unsigned":
          legacy_unsigned == "reject"      -> reject "legacy_disabled"
          else use tier "legacy"; grants are never honoured for legacy
       auth "verified": agent entry must be active, else reject
          "agent_not_active"; tier = entry tier
       class rule "allow"                  -> allow
       class rule "ask"                    -> ask_user ("tier_requires_ask"
                                              or "red_line" / "unknown_type")
       class rule "reject"                 -> reject "class_rejected"
       class rule "grant_or_ask":
          grant cited  -> grants.check(); ok -> allow_with_grant (mode
                          allow) or delay (mode delay, window_s =
                          delay_window_s); not ok -> ask_user
                          "grant_<reason>"
          none cited   -> find_auto(); found -> as above; else ask_user
                          "no_grant"
       a grant cited on a non-privileged action is ignored and the reason
       gets the suffix "+grant_ignored" (never an error, never a use).
       unknown class uses policy.unknown_type ("ask" -> ask_user
       "unknown_type").
"""

import hashlib
import json
from collections import namedtuple

from . import PolicyError, current_type
from .store import canonical_json

Decision = namedtuple("Decision", "decision reason grant_id window_s policy_hash")

CLASSES = ("conversation", "task", "privileged", "red_line")
RULES = frozenset({"allow", "ask", "grant_or_ask", "reject"})


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check_keys(d, allowed, where):
    if not isinstance(d, dict):
        raise PolicyError(where, "not an object")
    for k in d:
        if k.startswith("_"):
            continue
        if k not in allowed:
            raise PolicyError(where, "unknown key " + str(k))


class Policy:
    def __init__(self, d, digest):
        self.hash = digest
        self.raw = d
        self.version = d["version"]
        self.legacy_unsigned = d["legacy_unsigned"]
        self.freshness = dict(d["freshness"])
        self.seen_retention_s = d["seen_retention_s"]
        self.max_envelope_bytes = d["max_envelope_bytes"]
        self.delay_window_s = d["delay_window_s"]
        self.grant_caps = dict(d["grant_caps"])
        self.enroll_limits = dict(d["enroll_limits"])
        self.ask_card_timeout_s = d["ask_card_timeout_s"]
        self.classes = {k: list(v) for k, v in d["classes"].items()}
        self.aliases = dict(d["aliases"])
        self.tiers = {k: dict(v) for k, v in d["tiers"].items()}
        self.unknown_type = d["unknown_type"]
        self.grant_templates = {k: dict(v) for k, v in (d.get("grant_templates") or {}).items()
                                if not k.startswith("_")}
        self.type_to_class = {}
        for cls, names in self.classes.items():
            for name in names:
                self.type_to_class[name] = cls

    @classmethod
    def from_file(cls, path):
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            raise PolicyError("policy_unreadable", str(e))
        digest = hashlib.sha256(raw).hexdigest()
        try:
            from .envelope import strict_loads
            d = strict_loads(raw.decode("utf-8-sig"))
        except Exception as e:
            raise PolicyError("policy_unparsable", str(e))
        return cls.from_dict(d, digest=digest)

    @classmethod
    def from_dict(cls, d, digest=None):
        if digest is None:
            digest = hashlib.sha256(canonical_json(d).encode("utf-8")).hexdigest()
        _check_keys(d, {"version", "legacy_unsigned", "freshness", "seen_retention_s",
                        "max_envelope_bytes", "delay_window_s", "grant_caps",
                        "enroll_limits", "ask_card_timeout_s", "classes", "aliases",
                        "tiers", "unknown_type", "grant_templates"}, "policy")
        for k in ("version", "legacy_unsigned", "freshness", "seen_retention_s",
                  "max_envelope_bytes", "delay_window_s", "grant_caps", "enroll_limits",
                  "ask_card_timeout_s", "classes", "aliases", "tiers", "unknown_type"):
            if k not in d:
                raise PolicyError("missing", k)
        if not _is_int(d["version"]) or d["version"] != 1:
            raise PolicyError("bad_version")
        if d["legacy_unsigned"] not in ("transition", "reject"):
            raise PolicyError("bad_legacy_unsigned")
        _check_keys(d["freshness"], {"max_age_s", "max_future_s"}, "freshness")
        for k in ("max_age_s", "max_future_s"):
            if not _is_int(d["freshness"].get(k)) or d["freshness"][k] <= 0:
                raise PolicyError("bad_freshness", k)
        if not _is_int(d["seen_retention_s"]) or d["seen_retention_s"] <= d["freshness"]["max_age_s"]:
            raise PolicyError("bad_seen_retention")
        if not _is_int(d["max_envelope_bytes"]) or d["max_envelope_bytes"] <= 0:
            raise PolicyError("bad_max_envelope_bytes")
        if not _is_int(d["delay_window_s"]) or not (10 <= d["delay_window_s"] <= 120):
            raise PolicyError("bad_delay_window")
        _check_keys(d["grant_caps"], {"max_days", "max_uses"}, "grant_caps")
        for k in ("max_days", "max_uses"):
            if not _is_int(d["grant_caps"].get(k)) or d["grant_caps"][k] <= 0:
                raise PolicyError("bad_grant_caps", k)
        _check_keys(d["enroll_limits"], {"per_hour", "card_timeout_s"}, "enroll_limits")
        for k in ("per_hour", "card_timeout_s"):
            if not _is_int(d["enroll_limits"].get(k)) or d["enroll_limits"][k] <= 0:
                raise PolicyError("bad_enroll_limits", k)
        if not _is_int(d["ask_card_timeout_s"]) or d["ask_card_timeout_s"] <= 0:
            raise PolicyError("bad_ask_card_timeout")
        _check_keys(d["classes"], set(CLASSES), "classes")
        seen_types = set()
        for cname in CLASSES:
            names = d["classes"].get(cname)
            if not isinstance(names, list) or not names:
                raise PolicyError("missing_class", cname)
            for name in names:
                if not isinstance(name, str) or not name:
                    raise PolicyError("bad_type_name")
                if name in seen_types:
                    raise PolicyError("type_in_two_classes", name)
                seen_types.add(name)
        if not isinstance(d["aliases"], dict):
            raise PolicyError("bad_aliases")
        for alias, target in d["aliases"].items():
            if not isinstance(alias, str) or not isinstance(target, str) or target not in seen_types:
                raise PolicyError("bad_alias", str(alias))
        if not isinstance(d["tiers"], dict) or not d["tiers"]:
            raise PolicyError("bad_tiers")
        for tier, rules in d["tiers"].items():
            if not isinstance(tier, str):
                raise PolicyError("bad_tier")
            _check_keys(rules, set(CLASSES), "tier " + tier)
            for cname in CLASSES:
                if rules.get(cname) not in RULES:
                    raise PolicyError("bad_tier_rule", tier + "." + cname)
            # Author's boundary (confirmed 2026-11-12): red-line actions always
            # go to a card; no tier may let them through, with or without a grant.
            if rules.get("red_line") not in ("ask", "reject"):
                raise PolicyError("bad_tier_rule", tier + ".red_line must be ask or reject")
        # docs/protocol.en.md section 4: an unknown type is never allowed silently
        if d["unknown_type"] not in ("ask", "reject"):
            raise PolicyError("bad_unknown_type")
        gt = d.get("grant_templates")
        if gt is not None:
            if not isinstance(gt, dict):
                raise PolicyError("bad_grant_templates")
            for action, tmpl in gt.items():
                if action.startswith("_"):
                    continue
                if not isinstance(tmpl, dict):
                    raise PolicyError("bad_grant_template")
                _check_keys(tmpl, {"conditions", "days", "uses", "mode"}, "grant_template " + action)
                if not isinstance(tmpl.get("conditions", []), list):
                    raise PolicyError("bad_grant_template")
                for k in ("days", "uses"):
                    if k in tmpl and (not _is_int(tmpl[k]) or tmpl[k] <= 0):
                        raise PolicyError("bad_grant_template", k)
                if "mode" in tmpl and tmpl["mode"] not in ("allow", "delay"):
                    raise PolicyError("bad_grant_template", "mode")
        return cls(d, digest)

    def canonical(self, name):
        if not isinstance(name, str):
            return ""
        n = current_type(name)
        seen = set()
        while n in self.aliases and n not in seen:
            seen.add(n)
            n = self.aliases[n]
        return n

    def classify(self, name):
        return self.type_to_class.get(self.canonical(name), "unknown")


def _decision(policy, decision, reason, grant_id=None, window_s=None):
    return Decision(decision, reason, grant_id, window_s, policy.hash)


def _grant_mode(grants, grant_id):
    try:
        g = grants.get(grant_id)
    except Exception:
        return None
    return g.get("mode") if g else None


def decide(policy, *, auth, agent, action, grant_id, facts, grants, now):
    """auth: "verified" | "legacy_unsigned" | "failed:<code>".
    agent: registry entry (verified) or None. -> Decision."""
    if isinstance(auth, str) and auth.startswith("failed"):
        reason = auth.split(":", 1)[1] if ":" in auth else "auth_failed"
        return _decision(policy, "reject", reason)
    if auth == "legacy_unsigned":
        if policy.legacy_unsigned == "reject":
            return _decision(policy, "reject", "legacy_disabled")
        tier = "legacy"
        cls = policy.classify(action)
        rule = policy.tiers.get(tier, {}).get(cls, policy.unknown_type)
        if cls == "unknown":
            rule = policy.unknown_type
        if rule == "allow":
            return _decision(policy, "allow", "legacy_allowed")
        if rule == "reject":
            return _decision(policy, "reject", "class_rejected")
        return _decision(policy, "ask_user", "legacy_requires_ask")
    if auth != "verified":
        return _decision(policy, "reject", "auth_failed")
    if agent is None or agent.get("status") != "active":
        return _decision(policy, "reject", "agent_not_active")
    agent_id = agent.get("agent_id")
    tier = agent.get("tier")
    cls = policy.classify(action)
    rule = policy.tiers.get(tier, {}).get(cls, policy.unknown_type)
    if cls == "unknown":
        rule = policy.unknown_type
    cited = grant_id if (grant_id and cls == "privileged") else None
    ignored = bool(grant_id and cls != "privileged")
    suffix = "+grant_ignored" if ignored else ""

    if rule == "allow":
        return _decision(policy, "allow", "tier_allows" + suffix)
    if rule == "reject":
        return _decision(policy, "reject", "class_rejected" + suffix)
    if rule == "ask":
        base = "red_line" if cls == "red_line" else ("unknown_type" if cls == "unknown" else "tier_requires_ask")
        return _decision(policy, "ask_user", base + suffix)
    if rule == "grant_or_ask":
        if cited:
            ok, reason = grants.check(cited, agent_id, action, facts, now)
            if ok:
                mode = _grant_mode(grants, cited)
                if mode == "delay":
                    return _decision(policy, "delay", "grant_delay", cited, policy.delay_window_s)
                return _decision(policy, "allow_with_grant", "grant_ok", cited)
            return _decision(policy, "ask_user", "grant_" + reason + suffix)
        auto = grants.find_auto(agent_id, action, facts, now) if cls == "privileged" else None
        if auto:
            mode = _grant_mode(grants, auto)
            if mode == "delay":
                return _decision(policy, "delay", "grant_delay", auto, policy.delay_window_s)
            return _decision(policy, "allow_with_grant", "grant_auto", auto)
        return _decision(policy, "ask_user", "no_grant" + suffix)
    return _decision(policy, "ask_user", "unknown_type" + suffix)
