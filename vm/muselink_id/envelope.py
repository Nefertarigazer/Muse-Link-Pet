"""envelope - build, parse and verify v3 request/reply envelopes.

Stateless: no files except the one being read, no clock except the `now`
argument, no replay store (inbound.py owns replay). Registry lookups go
through the object passed in (anything with key_for(agent_id, key_id)).

Requirements
  1. Signature = base64(HMAC-SHA256(key, UTF-8(domain + signed))), standard
     base64 with padding. Compare with hmac.compare_digest. The verifier
     NEVER re-serializes `signed`: it checks the MAC over the exact text,
     then parses that text. spec/test-vectors/identity.json must pass.
  2. strict_loads(text): json.loads with object_pairs_hook that raises on a
     duplicate key at any depth, and parse_constant that raises on
     NaN/Infinity/-Infinity. Duplicate keys are the classic way to make two
     parsers see two different requests behind one signature.
  3. b64decode_strict: base64.b64decode(s, validate=True); reject
     whitespace, missing padding and wrong decoded length -> "bad_encoding".
  4. read_envelope_file(path, file_req_id): size check BEFORE reading the
     whole file (os.stat, then read at most max_bytes + 1) -> "oversize";
     decode utf-8-sig; strict_loads -> "unparsable"; must be a dict.
     Returns (kind, outer) with kind in {"v3", "enroll", "legacy"}:
       legacy  = no "v" key (v1 shape; inbound applies the legacy policy)
       enroll  = v == 3, auth is None (inner type checked later)
       v3      = v == 3, auth present
     Outer shape errors -> "bad_outer"; outer req_id != file_req_id ->
     "id_mismatch". v other than 3 -> "bad_outer".
  5. verify_request(outer, registry, now, max_age_s, max_future_s,
     check_freshness=True) follows docs/protocol.en.md section 3 steps 4-8 exactly,
     in that order, and raises AuthError(code) at the first failure. Returns
     VerifiedRequest. Enforcement points that re-verify a stored envelope
     later (publish pipeline) pass check_freshness=False.
  6. Inner validation: keys = INNER_REQUIRED plus any of INNER_OPTIONAL and
     nothing else; ts is an int (not bool); type is a non-empty str of
     [a-z0-9-] up to 40 chars; text is str; payload is dict; reply_protocol
     == 3; grant_id is None or matches GRANT_ID_RX; client (if present) is a
     dict with only CLIENT_KEYS and str values up to 200 chars each.
  7. parse_enroll(outer, now, ...) validates an unsigned enroll envelope:
     same inner rules and freshness, type must be "enroll" (else
     "unsigned"), agent_id must match AGENT_ID_RX and not be reserved (else
     "bad_agent_id"). Returns the inner dict. No signature.
  8. Replies: new_reply_envelope signs with REPLY_DOMAIN. verify_reply
     checks outer keys (REPLY_OUTER_KEYS), alg, key_id prefix, MAC, inner
     keys (REPLY_INNER_KEYS), and that inner req_id/agent_id/seq equal both
     the outer values and the expected values passed by the caller.
  9. worker_request(vreq, tier): the dict written to the VM inbox for
     existing workers. v1-compatible keys: req_id, created_at, from
     (= agent_id), type, text, payload, reply_protocol (3); plus thread_id,
     reply_to, grant_id, and auth = {"verified": True, "agent_id",
     "key_id", "tier"}. Never copies `client` claims into `from`.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from collections import namedtuple

from . import (
    ALG, AUTH_KEYS, CLIENT_KEYS, GRANT_ID_RX, INNER_OPTIONAL, INNER_REQUIRED,
    KEY_ID_RX, OUTER_KEYS, PROTOCOL, REPLY_DOMAIN, REPLY_INNER_KEYS,
    REPLY_OUTER_KEYS, REQ_DOMAIN, REQ_ID_RX, SIG_BYTES, AGENT_ID_RX,
    RESERVED_AGENT_IDS, AuthError, valid_id,
)

TYPE_RX = r"^[a-z0-9-]{1,40}$"   # ASCII only: str.islower()/isdigit() accept "ä", "²"

VerifiedRequest = namedtuple("VerifiedRequest", "agent_id key_id req_id inner digest")


def b64encode(data):
    return base64.b64encode(data).decode("ascii")


def b64decode_strict(text, length=None):
    if not isinstance(text, str) or not text:
        raise AuthError("bad_encoding")
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise AuthError("bad_encoding")
    if length is not None and len(raw) != length:
        raise AuthError("bad_encoding")
    return raw


def sign(key, domain, signed):
    if isinstance(signed, str):
        signed = signed.encode("utf-8")
    if isinstance(domain, str):
        domain = domain.encode("utf-8")
    return b64encode(hmac.new(key, domain + signed, hashlib.sha256).digest())


def check_sig(key, domain, signed, sig_b64):
    try:
        got = b64decode_strict(sig_b64, SIG_BYTES)
    except AuthError:
        return False
    if isinstance(signed, str):
        signed = signed.encode("utf-8")
    if isinstance(domain, str):
        domain = domain.encode("utf-8")
    want = hmac.new(key, domain + signed, hashlib.sha256).digest()
    return hmac.compare_digest(got, want)


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def _no_constants(value):
    raise ValueError("non-finite number")


def strict_loads(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    return json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_no_constants)


def new_request_envelope(agent_id, key_id, key, inner):
    signed = json.dumps(inner, ensure_ascii=False)
    return {
        "v": PROTOCOL,
        "req_id": inner["req_id"],
        "agent_id": agent_id,
        "signed": signed,
        "auth": {"alg": ALG, "key_id": key_id, "sig": sign(key, REQ_DOMAIN, signed)},
    }


def read_envelope_file(path, file_req_id, max_bytes=1 << 20):
    try:
        st = os.stat(path)
    except OSError as e:
        raise AuthError("unparsable", str(e))
    if st.st_size > max_bytes:
        raise AuthError("oversize")
    with open(path, "rb") as f:
        raw = f.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise AuthError("oversize")
    try:
        text = raw.decode("utf-8-sig")
        outer = strict_loads(text)
    except Exception:
        raise AuthError("unparsable")
    if not isinstance(outer, dict):
        raise AuthError("unparsable")
    if "v" not in outer:
        rid = outer.get("req_id")
        if not valid_id(REQ_ID_RX, rid) or rid != file_req_id:
            raise AuthError("id_mismatch")
        return "legacy", outer
    if type(outer.get("v")) is not int or outer.get("v") != PROTOCOL:
        raise AuthError("bad_outer")
    if set(outer) != OUTER_KEYS:
        raise AuthError("bad_outer")
    rid = outer.get("req_id")
    aid = outer.get("agent_id")
    signed = outer.get("signed")
    auth = outer.get("auth")
    if not valid_id(REQ_ID_RX, rid):
        raise AuthError("bad_outer")
    if not isinstance(aid, str) or not isinstance(signed, str):
        raise AuthError("bad_outer")
    if auth is not None and not isinstance(auth, dict):
        raise AuthError("bad_outer")
    if rid != file_req_id:
        raise AuthError("id_mismatch")
    return ("enroll" if auth is None else "v3"), outer


def _validate_inner(inner):
    if not isinstance(inner, dict):
        raise AuthError("bad_inner")
    keys = set(inner)
    if not INNER_REQUIRED <= keys or not keys <= (INNER_REQUIRED | INNER_OPTIONAL):
        raise AuthError("bad_inner")
    if not isinstance(inner["req_id"], str) or not isinstance(inner["agent_id"], str):
        raise AuthError("bad_inner")
    ts = inner["ts"]
    if type(ts) is not int:
        raise AuthError("bad_inner")
    if not valid_id(TYPE_RX, inner["type"]):
        raise AuthError("bad_inner")
    if not isinstance(inner["text"], str):
        raise AuthError("bad_inner")
    if not isinstance(inner["payload"], dict):
        raise AuthError("bad_inner")
    if type(inner["reply_protocol"]) is not int or inner["reply_protocol"] != PROTOCOL:
        raise AuthError("bad_inner")
    gid = inner.get("grant_id")
    if gid is not None and not valid_id(GRANT_ID_RX, gid):
        raise AuthError("bad_inner")
    for name in ("created_at", "thread_id", "reply_to"):
        if name in inner and not isinstance(inner[name], str):
            raise AuthError("bad_inner")
    if "client" in inner:
        c = inner["client"]
        if not isinstance(c, dict) or not set(c) <= CLIENT_KEYS:
            raise AuthError("bad_inner")
        for v in c.values():
            if not isinstance(v, str) or len(v) > 200:
                raise AuthError("bad_inner")
    return inner


def _freshness(ts, now, max_age_s, max_future_s):
    if now - ts > max_age_s:
        raise AuthError("stale")
    if ts - now > max_future_s:
        raise AuthError("future")


def verify_request(outer, registry, now, max_age_s, max_future_s, check_freshness=True):
    auth = outer.get("auth")
    if not isinstance(auth, dict) or set(auth) != AUTH_KEYS:
        raise AuthError("bad_outer")
    if auth.get("alg") != ALG:
        raise AuthError("bad_alg")
    key_id = auth.get("key_id")
    if not isinstance(key_id, str):
        raise AuthError("bad_key_id")
    m = re.fullmatch(KEY_ID_RX, key_id)
    if not m or m.group(1) != outer.get("agent_id"):
        raise AuthError("bad_key_id")
    sig = auth.get("sig")
    b64decode_strict(sig, SIG_BYTES)
    key = registry.key_for(outer["agent_id"], key_id)
    signed = outer.get("signed")
    if not isinstance(signed, str) or not check_sig(key, REQ_DOMAIN, signed, sig):
        raise AuthError("bad_signature")
    try:
        inner = _validate_inner(strict_loads(signed))
    except AuthError:
        raise
    except Exception:
        raise AuthError("bad_inner")
    if inner["req_id"] != outer.get("req_id") or inner["agent_id"] != outer.get("agent_id"):
        raise AuthError("id_mismatch")
    if check_freshness:
        _freshness(inner["ts"], now, max_age_s, max_future_s)
    digest = hashlib.sha256(signed.encode("utf-8")).hexdigest()
    return VerifiedRequest(outer["agent_id"], key_id, outer["req_id"], inner, digest)


def parse_enroll(outer, now, max_age_s, max_future_s):
    if outer.get("auth") is not None:
        raise AuthError("unsigned")
    signed = outer.get("signed")
    if not isinstance(signed, str):
        raise AuthError("bad_inner")
    try:
        inner = _validate_inner(strict_loads(signed))
    except AuthError:
        raise
    except Exception:
        raise AuthError("bad_inner")
    if inner["type"] != "enroll":
        raise AuthError("unsigned")
    aid = inner["agent_id"]
    if aid != outer.get("agent_id") or inner["req_id"] != outer.get("req_id"):
        raise AuthError("id_mismatch")
    if not valid_id(AGENT_ID_RX, aid) or aid in RESERVED_AGENT_IDS:
        raise AuthError("bad_agent_id")
    _freshness(inner["ts"], now, max_age_s, max_future_s)
    return inner


def new_reply_envelope(agent_id, key_id, key, inner):
    signed = json.dumps(inner, ensure_ascii=False)
    return {
        "v": PROTOCOL,
        "req_id": inner["req_id"],
        "seq": inner["seq"],
        "agent_id": agent_id,
        "signed": signed,
        "auth": {"alg": ALG, "key_id": key_id, "sig": sign(key, REPLY_DOMAIN, signed)},
    }


def verify_reply(outer, key, expect_req_id, expect_agent_id, expect_seq):
    if not isinstance(outer, dict) or set(outer) != REPLY_OUTER_KEYS:
        raise AuthError("bad_outer")
    if type(outer.get("v")) is not int or outer.get("v") != PROTOCOL:
        raise AuthError("bad_outer")
    if not isinstance(outer.get("req_id"), str) or type(outer.get("seq")) is not int:
        raise AuthError("bad_outer")
    if not isinstance(outer.get("agent_id"), str) or not isinstance(outer.get("signed"), str):
        raise AuthError("bad_outer")
    auth = outer.get("auth")
    if not isinstance(auth, dict) or set(auth) != AUTH_KEYS:
        raise AuthError("bad_outer")
    if auth.get("alg") != ALG:
        raise AuthError("bad_alg")
    key_id = auth.get("key_id")
    if not isinstance(key_id, str):
        raise AuthError("bad_key_id")
    m = re.fullmatch(KEY_ID_RX, key_id)
    if not m or m.group(1) != outer["agent_id"]:
        raise AuthError("bad_key_id")
    sig = auth.get("sig")
    b64decode_strict(sig, SIG_BYTES)
    if not check_sig(key, REPLY_DOMAIN, outer["signed"], sig):
        raise AuthError("bad_signature")
    try:
        inner = strict_loads(outer["signed"])
    except Exception:
        raise AuthError("bad_inner")
    if not isinstance(inner, dict) or set(inner) != REPLY_INNER_KEYS:
        raise AuthError("bad_inner")
    if not isinstance(inner.get("req_id"), str) or not isinstance(inner.get("agent_id"), str):
        raise AuthError("bad_inner")
    if type(inner.get("seq")) is not int or type(inner.get("final")) is not bool:
        raise AuthError("bad_inner")
    if not isinstance(inner.get("status"), str) or not isinstance(inner.get("replied_at"), str):
        raise AuthError("bad_inner")
    if inner.get("from") != "muse":
        raise AuthError("bad_inner")
    if type(inner.get("reply_protocol")) is not int or inner.get("reply_protocol") != PROTOCOL:
        raise AuthError("bad_inner")
    if not isinstance(inner.get("text"), str):
        raise AuthError("bad_inner")
    if (inner["req_id"] != outer["req_id"] or inner["agent_id"] != outer["agent_id"]
            or inner["seq"] != outer["seq"]):
        raise AuthError("id_mismatch")
    if (outer["req_id"] != expect_req_id or outer["agent_id"] != expect_agent_id
            or outer["seq"] != expect_seq):
        raise AuthError("id_mismatch")
    return inner


def worker_request(vreq, tier):
    inn = vreq.inner
    req = {
        "req_id": vreq.req_id,
        "created_at": inn.get("created_at") or "",
        "from": vreq.agent_id,
        "type": inn["type"],
        "text": inn["text"],
        "payload": inn["payload"],
        "reply_protocol": PROTOCOL,
        "thread_id": inn.get("thread_id"),
        "reply_to": inn.get("reply_to"),
        "grant_id": inn.get("grant_id"),
        "auth": {"verified": True, "agent_id": vreq.agent_id, "key_id": vreq.key_id, "tier": tier},
    }
    return req
