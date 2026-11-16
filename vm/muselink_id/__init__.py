"""muselink_id - identity protocol v3 and pre-authorization grants (VM side).

Skeleton 2026-11-12 (Kiro), implemented by DS1017, reviewed by Kiro. Global conventions live in
docs/protocol.en.md sections 3-4; each module states its own requirements
at the top. The constants below ARE the convention: do not change them
without updating docs/protocol.en.md and spec/test-vectors/identity.json.

Module map:
  store     paths, permissions, atomic writes, locks, sqlite state, audit
  envelope  request/reply envelopes: build, parse, verify (stateless)
  registry  enrolled agents and their keys (sealed entries)
  grants    grant model, conditions, consume/reserve/confirm/release, revoke
  policy    policy file + the pure decide() function
  inbound   routing one inbox file: verify, replay check, decide, write files
  flows     authorize() (cards, delay window), enrollment, grant ceremony
  ui        adapters to the real popup (popup-ask.sh) and the PC (pc.sh/scp)
  cli       the muselink-id command (wrapper: vm/muselink-id.sh)

Hard rules for every module:
  * Python 3.8+ standard library only (the VM has no pip packages for this).
  * Fail closed: any exception on an authorization path means "do not
    proceed" (CLI exit 30), never "allow".
  * Never write a key, a seal, request text or payload values into logs,
    audit lines, alerts, verdict files or the PC mirror. key_id is fine.
  * Time is injected (a `now` argument, epoch seconds) wherever a decision
    depends on it, so tests can control it.
  * Windows (the developer PC) must be able to import every module and run
    tests/identity: guard fcntl with a fallback (store.py).
"""

PROTOCOL = 3
ALG = "HMAC-SHA256"
REQ_DOMAIN = "muselink-req-v3\n"
REPLY_DOMAIN = "muselink-reply-v3\n"
SEAL_DOMAIN = "muselink-seal-v1\n"
KEY_BYTES = 32
SIG_BYTES = 32

AGENT_ID_RX = r"^[a-z][a-z0-9-]{1,31}$"
KEY_ID_RX = r"^([a-z][a-z0-9-]{1,31})#([1-9][0-9]{0,5})$"
REQ_ID_RX = r"^[A-Za-z0-9][A-Za-z0-9_-]{2,95}$"
GRANT_ID_RX = r"^g-[0-9a-f]{12}$"
RESERVED_AGENT_IDS = frozenset({
    "muse", "user", "system", "admin", "root", "legacy", "unknown",
    "anonymous", "all", "any", "none",
})

OUTER_KEYS = frozenset({"v", "req_id", "agent_id", "signed", "auth"})
AUTH_KEYS = frozenset({"alg", "key_id", "sig"})
INNER_REQUIRED = frozenset({"req_id", "agent_id", "ts", "type", "text", "payload", "reply_protocol"})
INNER_OPTIONAL = frozenset({"created_at", "grant_id", "thread_id", "reply_to", "client"})
CLIENT_KEYS = frozenset({"display_name", "product", "model", "host", "session_id"})
REPLY_OUTER_KEYS = frozenset({"v", "req_id", "seq", "agent_id", "signed", "auth"})
REPLY_INNER_KEYS = frozenset({"req_id", "seq", "agent_id", "final", "status", "replied_at", "from", "reply_protocol", "text"})

# Error codes (docs/protocol.en.md section 3). AuthError.code is always one of these.
AUTH_CODES = frozenset({
    "oversize", "unparsable", "bad_outer", "id_mismatch", "legacy_disabled",
    "downgrade", "unsigned", "bad_alg", "bad_key_id", "bad_encoding",
    "unknown_agent", "agent_revoked", "registry_tampered", "unknown_key",
    "retired_key", "bad_signature", "bad_inner", "stale", "future",
    "duplicate", "conflict", "bad_agent_id",
})
# RouteResult.code (inbound.py) is one of these or "".
ROUTE_CODES = AUTH_CODES | frozenset({"enroll_rate_limited", "internal"})
# Codes that mean "someone may be impersonating a registered agent": alert,
# no reply (steps 4-6 of the verification order).
IMPERSONATION_CODES = frozenset({
    "bad_alg", "bad_key_id", "bad_encoding", "unknown_key", "retired_key",
    "bad_signature", "registry_tampered",
})

DECISIONS = ("allow", "allow_with_grant", "delay", "ask_user", "reject")

# Request types renamed for good (2026-11-13: releasing is a generic action,
# the first app that used it does not name it). An old name is accepted on
# input only and mapped where each side first sees a request: muse-call.ps1
# before signing, inbound routing and Policy.canonical() here. Nothing new is
# written with it. Delete an entry once no client, prompt or queued request
# still uses it.
RETIRED_TYPES = {"publish-voicebridge": "publish-release"}


def current_type(name):
    """Lower-cased request type with retired names mapped to the current one."""
    n = str(name or "").strip().lower()
    return RETIRED_TYPES.get(n, n)

# muselink-id exit codes (docs/protocol.en.md section 4)
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ASK = 10
EXIT_DELAY = 11
EXIT_DENY = 20
EXIT_INTERNAL = 30


class IdentityError(Exception):
    """Base class. `code` is a short machine-readable reason."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class AuthError(IdentityError):
    """A request or reply failed authentication / validation (AUTH_CODES)."""


class PolicyError(IdentityError):
    """The policy file is invalid. Loading fails; nothing is decided."""


class GrantError(IdentityError):
    """A grant spec is invalid, or a grant record failed its seal check."""


class InsecureStore(IdentityError):
    """The identity directory or a file in it is readable by others, or is
    owned by another user (POSIX). The store refuses to operate."""


def valid_id(rx, value):
    """True only if the WHOLE string matches. Use this, never re.match, for
    every id check: with re.match a pattern ending in "$" also accepts a
    trailing newline ("codex\\n"), which then flows into file names, card
    text and PowerShell code (review 2026-11-12)."""
    import re
    return isinstance(value, str) and re.fullmatch(rx, value) is not None
