"""schema - the entry format, validation and the status machine.

Requirements
  1. Fields exactly as spec/experience.schema.json (docs/architecture.en.md section 10):
     id "x-<12 hex>", schema 1, app, app_version, ui, goal, method (1..12
     steps; a step is a string <= 160 chars or {"step": str<=160, "tool":
     plugin tool name, "args": object}), verify, status, created,
     last_verified (YYYY-MM-DD), works, fails (int >= 0), source
     ("local"|"public"), agent, tags (<= 8, each <= 24 chars). Unknown keys,
     wrong types, over-long text -> ValueError naming the field (fail closed).
  2. app is normalized: lower-case process name ("MiniMax Code.exe" ->
     "minimax code.exe"); the same function is used by store and lookup.
  3. Status machine (pure functions, `now` passed in):
       draft  --confirm--> valid
       valid  --fail x2 in a row--> review
       review --fail--> stale ; review/valid untouched 90 days --> stale
       any but draft --works--> valid (fails reset to 0); feedback on a
       draft is refused (only confirm() leaves draft)
     feedback(entry, worked, today) -> new entry dict (never mutates input).
  4. new_id() uses secrets; ids never derive from content.
  5. Card budget: render(entry) -> the plain text shown on a share card;
     a valid entry must render to <= 1200 characters (enforced here).
"""

import copy
from datetime import date
import json
import re
import secrets

STATUSES = ("draft", "valid", "review", "stale")
SOURCES = ("local", "public")
MAX_STEPS = 12
STALE_DAYS = 90


def new_id():
    # A purely numeric suffix would look like a telephone number to the
    # whole-entry privacy detector. Resample that rare case; IDs remain
    # random, content-independent twelve-digit hex strings.
    while True:
        suffix = secrets.token_hex(6)
        if not suffix.isdigit():
            return "x-" + suffix


def normalize_app(name):
    if not isinstance(name, str):
        raise ValueError("app: expected string")
    name = name.strip().lower()
    if (not 1 <= len(name) <= 80 or name in (".", "..") or
            re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")) or
            name.split(".")[0] in {"con", "prn", "aux", "nul", *["com" + str(i) for i in range(1, 10)],
                                  *["lpt" + str(i) for i in range(1, 10)]}):
        raise ValueError("app: unsafe process name")
    return name


def _day(value, field="date"):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(field + ": invalid date") from None


def _text(value, field, maximum, minimum=0):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ValueError(field + ": invalid text length/type")


def _json(value, field, depth=0):
    if depth > 12:
        raise ValueError(field + ": nesting too deep")
    if isinstance(value, dict):
        for k, v in value.items():
            _text(k, field, 160, 1)
            _json(v, field, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _json(v, field, depth + 1)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError(field + ": not JSON")


def validate(entry):
    """-> entry (unchanged) or raise ValueError("<field>: <why>")."""
    required = {"id", "schema", "app", "goal", "method", "verify", "status", "created",
                "last_verified", "works", "fails", "source"}
    optional = {"app_version", "ui", "agent", "tags"}
    if not isinstance(entry, dict):
        raise ValueError("entry: expected object")
    if entry.keys() - required - optional:
        raise ValueError(sorted(entry.keys() - required - optional)[0] + ": unknown field")
    if required - entry.keys():
        raise ValueError(sorted(required - entry.keys())[0] + ": required")
    if not isinstance(entry["id"], str) or not re.fullmatch(r"x-[0-9a-f]{12}", entry["id"]):
        raise ValueError("id: invalid")
    if type(entry["schema"]) is not int or entry["schema"] != 1:
        raise ValueError("schema: must be 1")
    if normalize_app(entry["app"]) != entry["app"]:
        raise ValueError("app: must be normalized")
    for k, maximum, minimum in (("goal", 160, 1), ("verify", 240, 1), ("ui", 160, 0),
                                ("app_version", 40, 0), ("agent", 32, 0)):
        if k in entry:
            _text(entry[k], k, maximum, minimum)
    for k, choices in (("status", STATUSES), ("source", SOURCES)):
        if entry[k] not in choices:
            raise ValueError(k + ": invalid value")
    for k in ("created", "last_verified"):
        _day(entry[k], k)
    for k in ("works", "fails"):
        if type(entry[k]) is not int or entry[k] < 0:
            raise ValueError(k + ": expected nonnegative integer")
    tags = entry.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 8:
        raise ValueError("tags: invalid array")
    for tag in tags:
        _text(tag, "tags", 24, 1)
    steps = entry["method"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError("method: requires 1..12 steps")
    for step in steps:
        if isinstance(step, str):
            _text(step, "method", 160, 1)
        elif isinstance(step, dict):
            if "step" not in step or step.keys() - {"step", "tool", "args"}:
                raise ValueError("method: invalid step keys")
            _text(step["step"], "method.step", 160, 1)
            if "tool" in step and (not isinstance(step["tool"], str) or not re.fullmatch(r"[a-z_]{2,32}", step["tool"])):
                raise ValueError("method.tool: invalid tool name")
            if "args" in step:
                if not isinstance(step["args"], dict):
                    raise ValueError("method.args: expected object")
                _json(step["args"], "method.args")
        else:
            raise ValueError("method: invalid step")
    try:
        body = render(entry)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("method.args: invalid JSON") from None
    if len(body) > 1200:
        raise ValueError("render: exceeds 1200 character card budget")
    return entry


def confirm(entry, today):
    validate(entry)
    if entry["status"] != "draft":
        raise ValueError("status: only drafts can be confirmed")
    result = copy.deepcopy(entry)
    result.update(status="valid", last_verified=_day(today).isoformat())
    return validate(result)


def feedback(entry, worked, today):
    validate(entry)
    if type(worked) is not bool:
        raise ValueError("worked: expected boolean")
    if entry["status"] == "draft":
        # A draft becomes valid only through confirm() by the agent that
        # reviewed it; a "worked" report must not skip that review.
        raise ValueError("status: confirm the draft first")
    result = copy.deepcopy(entry)
    if worked:
        result.update(status="valid", works=entry["works"] + 1, fails=0,
                      last_verified=_day(today).isoformat())
    else:
        _day(today)
        result["fails"] += 1
        if entry["status"] == "review":
            result["status"] = "stale"
        elif entry["status"] == "valid" and result["fails"] >= 2:
            result["status"] = "review"
    return validate(result)


def age_out(entry, today):
    """valid/review untouched STALE_DAYS -> stale; otherwise unchanged."""
    validate(entry)
    result = copy.deepcopy(entry)
    if entry["status"] in ("valid", "review") and (_day(today) - _day(entry["last_verified"])).days >= STALE_DAYS:
        result["status"] = "stale"
    return result


def render(entry):
    # Canonical JSON is both the full card text and the exact upload payload.
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
