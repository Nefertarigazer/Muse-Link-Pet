"""redact - make an entry safe to publish, then prove it.

Requirements (docs/architecture.en.md section 10, three passes + a re-check)
  1. Literals: every value in muselink.json -> explib.redact.literals, plus
     the current USERNAME, USERDOMAIN, COMPUTERNAME, the tool dir and
     bridge root paths and the Tailscale device name if configured. Each is
     replaced (case-insensitive, longest first) by a placeholder such as
     <USER>, <HOST>, <PATH>.
  2. Patterns: user profile paths -> %USERPROFILE%\\..., e-mail, phone
     numbers, IPv4/IPv6, URLs with a query string (query dropped), long
     random-looking tokens (>= 20 chars of [A-Za-z0-9+/_-] with mixed
     classes) -> <TOKEN>.
  3. Content fields: step args named text, value, clipboard, title, file,
     path, expect_text and any arg longer than 80 chars are removed; agent
     is replaced by "community"; app_version kept.
  4. check(entry) runs passes 1-2 as detectors over the WHOLE serialized
     entry and returns the hits. redact() returns (clean, hits_after);
     a caller may share only when hits_after == [].
  5. Pure: no I/O except reading config/env in literals(); deterministic.
"""


import copy
import ipaddress
import json
import os
import re

from plugin._shared import read_json, tool_dir


def config(base):
    return (read_json(tool_dir(base) / "muselink.json", {}) or {}).get("explib", {})


def literals(base):
    base = tool_dir(base)
    cfg = read_json(base / "muselink.json", {}) or {}
    values = config(base).get("redact", {}).get("literals", [])
    result = dict(values) if isinstance(values, dict) else {v: "<PRIVATE>" for v in values if v}
    for name, placeholder in (("USERNAME", "<USER>"), ("USERDOMAIN", "<HOST>"), ("COMPUTERNAME", "<HOST>")):
        if os.environ.get(name):
            result[os.environ[name]] = placeholder
    for value, placeholder in ((str(base), "<PATH>"), (cfg.get("bridge", {}).get("root"), "<PATH>"),
                               (cfg.get("tailscale", {}).get("device_name"), "<HOST>")):
        if value:
            result[value] = placeholder
            if "\\" in value:
                result[value.replace("\\", "/")] = placeholder
    return {k: v for k, v in result.items() if isinstance(k, str) and k}


PROFILE = re.compile(r"(?i)(?:[a-z]:[\\/]Users[\\/][^\\/\s\"<>]+|/home/[^/\s\"<>]+)")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w.])(?:\+\d[\d ()-]{7,}\d|\d{10,15})(?![\w.])")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]{0,39}(?![\w:])")
URL = re.compile(r"https?://[^\s\"]+", re.I)
TOKEN = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{20,}(?![A-Za-z0-9+/_-])")
CONTENT = {"text", "value", "clipboard", "title", "file", "path", "expect_text"}


def _replace(text, lits):
    hits = []
    def sub(pattern, label, replace):
        nonlocal text
        def found(m):
            value = replace(m)
            if value != m[0]:
                hits.append(label)
            return value
        text = pattern.sub(found, text)
    for value in sorted(lits, key=len, reverse=True):
        sub(re.compile(re.escape(value), re.I), "literal", lambda m, v=value: lits[v])
    sub(PROFILE, "profile", lambda m: "%USERPROFILE%")
    sub(EMAIL, "email", lambda m: "<EMAIL>")
    sub(PHONE, "phone", lambda m: "<PHONE>")
    def ip(m):
        try:
            ipaddress.ip_address(m[0])
            return "<IP>"
        except ValueError:
            return m[0]
    sub(IPV4, "ip", ip)
    sub(IPV6, "ip", ip)
    sub(URL, "url-query", lambda m: m[0].split("?", 1)[0] if "?" in m[0] else m[0])
    def token(m):
        value = m[0]
        classes = sum((bool(re.search(r"[a-z]", value)), bool(re.search(r"[A-Z]", value)),
                       bool(re.search(r"[0-9]", value)), bool(re.search(r"[+/_-]", value))))
        return "<TOKEN>" if classes >= 2 else value
    sub(TOKEN, "token", token)
    return text, hits


def redact(entry, lits):
    """-> (clean entry, remaining hits)."""
    clean = copy.deepcopy(entry)
    # Structural values (IDs, tags, tool names, argument keys) are not rewritten:
    # changing them could change the method. The whole-object recheck blocks them.
    for field in ("app", "app_version", "ui", "goal", "verify"):
        if field in clean:
            clean[field] = _replace(clean[field], lits)[0]
    def args(value):
        if isinstance(value, dict):
            return {k: args(v) for k, v in value.items() if k.lower() not in CONTENT and
                    len(json.dumps(v, ensure_ascii=False)) <= 82}
        if isinstance(value, list):
            return [args(v) for v in value if not isinstance(v, str) or len(v) <= 80]
        return _replace(value, lits)[0] if isinstance(value, str) else value
    clean["method"] = [(_replace(s, lits)[0] if isinstance(s, str) else
                        dict(s, step=_replace(s["step"], lits)[0], **({"args": args(s["args"])} if "args" in s else {})))
                       for s in clean["method"]]
    clean["agent"] = "community"
    return clean, check(clean, lits)


def check(entry, lits):
    # Include every key and every value, even fields the rewriting pass skips.
    hits = []
    def visit(value):
        if isinstance(value, dict):
            for k, v in value.items():
                visit(k)
                visit(v)
        elif isinstance(value, list):
            for v in value:
                visit(v)
        elif isinstance(value, str):
            hits.extend(_replace(value, lits)[1])
        elif type(value) in (int, float):
            hits.extend(_replace(json.dumps(value, allow_nan=False), lits)[1])
    visit(entry)
    return hits
