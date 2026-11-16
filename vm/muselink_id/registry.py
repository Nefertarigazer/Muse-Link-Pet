"""registry - enrolled agents and their keys.

File: <id_home>/agents.json = {"v": 1, "agents": {<agent_id>: entry}}.
Entry:
  {"agent_id", "status": "active"|"revoked", "tier": "whitelist"|"ask-first",
   "client": {display_name, product, model, host, session_id} (self-declared),
   "enrolled_at", "updated_at",
   "keys": [{"key_id", "key" (base64), "created_at", "retired_at": int|None}],
   "approval": {"popup_req", "answer", "at"},     # the card that allowed the
                                                  # last security-relevant change
   "seal"}

Requirements
  1. Seal covers every field except "seal" itself, with each key's "key"
     replaced by its SHA-256 hex (so the seal input never holds a secret in
     a form that could leak through an error message). A seal mismatch on
     any entry -> that agent is unusable (AuthError "registry_tampered") and
     store.alert() is called once; other agents keep working.
  2. Exactly one key per agent has retired_at None (the active key).
     key_for(agent_id, key_id) raises: unknown_agent, agent_revoked,
     registry_tampered, unknown_key, retired_key - in that order of checks.
  3. Every mutating call takes `evidence` = {"popup_req", "answer", "at"}
     from a card the user answered (flows.py obtains it), validates its
     shape (popup_req matches REQ_ID_RX, answer non-empty str, at int) and
     stores it in "approval". No evidence -> ValueError. Mutations: add,
     rekey, set_tier. revoke() needs only a reason (it removes power).
  4. add(): agent_id must match AGENT_ID_RX, not be reserved, not exist.
     Generates the key with secrets.token_bytes(KEY_BYTES); key_id
     "<agent_id>#1". rekey(): new key_id n+1, the old key gets retired_at
     now (immediately invalid). Both return (key_id, key_bytes) so the
     caller can provision the PC; the key is never logged or audited.
  5. All writes under store.lock("registry"), read-modify-write, atomic.
  6. public_directory(): {"v":1, "generated_at", "agents": [{agent_id,
     status, tier, key_id (active), display_name, enrolled_at}]} - no key
     material, no seal. Written to the PC mirror by flows/ui.
  7. revoke(): status "revoked", re-seal, and the caller (flows) also
     revokes the agent's grants and removes the PC identity file.
"""

import base64
import copy
import hashlib
import re
import secrets

from . import (
    AGENT_ID_RX, KEY_BYTES, RESERVED_AGENT_IDS, AuthError, KEY_ID_RX,
    REQ_ID_RX, valid_id,
)

TIERS = ("whitelist", "ask-first")


def _valid_evidence(evidence):
    if not isinstance(evidence, dict):
        return False
    if set(evidence) != {"popup_req", "answer", "at"}:
        return False
    if not valid_id(REQ_ID_RX, evidence.get("popup_req")):
        return False
    if not isinstance(evidence.get("answer"), str) or not evidence["answer"]:
        return False
    if type(evidence.get("at")) is not int:
        return False
    return True


def _require_evidence(evidence):
    if not _valid_evidence(evidence):
        raise ValueError("approval evidence is missing or invalid")
    return dict(evidence)


def _sealable(entry):
    e = copy.deepcopy(entry)
    e.pop("seal", None)
    keys = []
    for k in e.get("keys", []):
        k = dict(k)
        raw = k.pop("key", "")
        try:
            data = base64.b64decode(raw, validate=True)
        except Exception:
            data = str(raw).encode("utf-8")
        k["key"] = hashlib.sha256(data).hexdigest()
        keys.append(k)
    e["keys"] = keys
    return e


def _clean_client(client):
    if not isinstance(client, dict):
        return {}
    out = {}
    for k in ("display_name", "product", "model", "host", "session_id"):
        v = client.get(k)
        if isinstance(v, str):
            out[k] = v[:200]
    return out


class Registry:
    def __init__(self, store):
        self.store = store

    def _load(self):
        d = self.store.read_json("agents.json", {"v": 1, "agents": {}})
        if not isinstance(d, dict) or not isinstance(d.get("agents"), dict):
            return {"v": 1, "agents": {}}
        return d

    def _check_entry(self, agent_id, entry):
        if not isinstance(entry, dict):
            self.store.alert("registry-" + str(agent_id), "registry entry is not an object")
            raise AuthError("registry_tampered", agent_id)
        seal = entry.get("seal")
        # The seal covers the entry, not the dict key it is filed under: a
        # sealed entry copied under another name must not count as valid.
        if entry.get("agent_id") != agent_id or not self.store.check_seal(_sealable(entry), seal):
            self.store.alert("registry-" + str(agent_id), "possible registry tampering for " + str(agent_id))
            raise AuthError("registry_tampered", agent_id)
        return entry

    def _audit(self, event, now, **fields):
        try:
            self.store.audit(event, now, **fields)
        except Exception:
            pass

    def get(self, agent_id):
        """-> entry dict (seal verified) or None if not registered."""
        d = self._load()
        entry = d["agents"].get(agent_id)
        if entry is None:
            return None
        return self._check_entry(agent_id, entry)

    def key_for(self, agent_id, key_id):
        """-> key bytes. Raises AuthError (requirement 2)."""
        entry = self.get(agent_id)
        if entry is None:
            raise AuthError("unknown_agent")
        if entry.get("status") != "active":
            raise AuthError("agent_revoked")
        for k in entry.get("keys", []):
            if k.get("key_id") == key_id:
                if k.get("retired_at") is not None:
                    raise AuthError("retired_key")
                try:
                    return base64.b64decode(k["key"], validate=True)
                except Exception:
                    raise AuthError("registry_tampered")
        raise AuthError("unknown_key")

    def active_key(self, agent_id):
        """-> (key_id, key bytes) of an active agent. Raises AuthError."""
        entry = self.get(agent_id)
        if entry is None:
            raise AuthError("unknown_agent")
        if entry.get("status") != "active":
            raise AuthError("agent_revoked")
        for k in entry.get("keys", []):
            if k.get("retired_at") is None:
                try:
                    return k["key_id"], base64.b64decode(k["key"], validate=True)
                except Exception:
                    raise AuthError("registry_tampered")
        raise AuthError("unknown_key")

    def tier(self, agent_id):
        entry = self.get(agent_id)
        if entry is None:
            raise AuthError("unknown_agent")
        return entry.get("tier")

    def add(self, agent_id, tier, client, evidence, now):
        """-> (key_id, key bytes)."""
        if not valid_id(AGENT_ID_RX, agent_id) or agent_id in RESERVED_AGENT_IDS:
            raise ValueError("bad agent_id")
        if tier not in TIERS:
            raise ValueError("bad tier")
        ev = _require_evidence(evidence)
        with self.store.lock("registry"):
            d = self._load()
            if agent_id in d["agents"]:
                raise ValueError("agent already exists")
            key = secrets.token_bytes(KEY_BYTES)
            key_id = agent_id + "#1"
            d["agents"][agent_id] = {
                "agent_id": agent_id,
                "status": "active",
                "tier": tier,
                "client": _clean_client(client),
                "enrolled_at": now,
                "updated_at": now,
                "keys": [{"key_id": key_id, "key": base64.b64encode(key).decode("ascii"),
                          "created_at": now, "retired_at": None}],
                "approval": ev,
            }
            entry = d["agents"][agent_id]
            entry["seal"] = self.store.seal(_sealable(entry))
            self.store.write_json("agents.json", d)
        self._audit("agent_added", now, agent_id=agent_id, tier=tier, key_id=key_id,
                    popup_req=ev["popup_req"])
        return key_id, key

    def rekey(self, agent_id, evidence, now, reactivate=False):
        """-> (key_id, key bytes). A revoked agent is rekeyed only with
        reactivate=True (its card said so); it is then active again. Its
        grants are not touched (revocation revoked them)."""
        ev = _require_evidence(evidence)
        with self.store.lock("registry"):
            d = self._load()
            entry = d["agents"].get(agent_id)
            if entry is None:
                raise ValueError("unknown agent")
            entry = self._check_entry(agent_id, entry)
            if entry.get("status") == "revoked":
                if not reactivate:
                    raise ValueError("agent revoked")
                entry["status"] = "active"
            elif reactivate:
                raise ValueError("agent not revoked")
            n = 1
            for k in entry.get("keys", []):
                m = re.fullmatch(KEY_ID_RX, str(k.get("key_id", "")))
                if m and m.group(1) == agent_id:
                    n = max(n, int(m.group(2)) + 1)
            for k in entry.get("keys", []):
                if k.get("retired_at") is None:
                    k["retired_at"] = now
            key = secrets.token_bytes(KEY_BYTES)
            key_id = "%s#%d" % (agent_id, n)
            entry.setdefault("keys", []).append({
                "key_id": key_id, "key": base64.b64encode(key).decode("ascii"),
                "created_at": now, "retired_at": None})
            entry["updated_at"] = now
            entry["approval"] = ev
            entry["seal"] = self.store.seal(_sealable(entry))
            self.store.write_json("agents.json", d)
        self._audit("agent_restored" if reactivate else "agent_rekeyed", now, agent_id=agent_id,
                    key_id=key_id, popup_req=ev["popup_req"])
        return key_id, key

    def set_tier(self, agent_id, tier, evidence, now):
        if tier not in TIERS:
            raise ValueError("bad tier")
        ev = _require_evidence(evidence)
        with self.store.lock("registry"):
            d = self._load()
            entry = d["agents"].get(agent_id)
            if entry is None:
                raise ValueError("unknown agent")
            entry = self._check_entry(agent_id, entry)
            entry["tier"] = tier
            entry["updated_at"] = now
            entry["approval"] = ev
            entry["seal"] = self.store.seal(_sealable(entry))
            self.store.write_json("agents.json", d)
        self._audit("agent_tier_set", now, agent_id=agent_id, tier=tier, popup_req=ev["popup_req"])

    def revoke(self, agent_id, reason, now):
        with self.store.lock("registry"):
            d = self._load()
            entry = d["agents"].get(agent_id)
            if entry is None:
                raise ValueError("unknown agent")
            entry = self._check_entry(agent_id, entry)
            entry["status"] = "revoked"
            entry["updated_at"] = now
            entry["seal"] = self.store.seal(_sealable(entry))
            self.store.write_json("agents.json", d)
        self._audit("agent_revoked", now, agent_id=agent_id, reason=str(reason)[:120])

    def public_directory(self, now):
        d = self._load()
        out = []
        for agent_id, entry in d["agents"].items():
            try:
                entry = self._check_entry(agent_id, entry)
            except AuthError:
                continue
            key_id = None
            for k in entry.get("keys", []):
                if k.get("retired_at") is None:
                    key_id = k.get("key_id")
                    break
            client = entry.get("client") or {}
            out.append({
                "agent_id": agent_id,
                "status": entry.get("status"),
                "tier": entry.get("tier"),
                "key_id": key_id,
                "display_name": client.get("display_name", ""),
                "enrolled_at": entry.get("enrolled_at"),
            })
        out.sort(key=lambda a: a["agent_id"])
        return {"v": 1, "generated_at": now, "agents": out}
