"""Closed tool table shared by MCP/CLI. No raw executor entry point."""
from dataclasses import dataclass
import copy
from i18n import t
import json
import math
import time

from . import ask, executor_client, gate
from ._shared import agent_id, tool_dir


def _typed(node):
    # Gemini/Vertex function-calling rejects property schemas whose "enum"
    # lacks a "type". All enums in this table are string enums; declare it.
    if isinstance(node, dict):
        if "enum" in node and "type" not in node and all(isinstance(v, str) for v in node["enum"]):
            node["type"] = "string"
        for v in node.values():
            _typed(v)
    elif isinstance(node, list):
        for v in node:
            _typed(v)
    return node


def obj(properties=None, required=()):
    return _typed(dict(type="object", properties=properties or {}, required=list(required), additionalProperties=False))


S = {"type": "string"}
B = {"type": "boolean"}
APP = {"type": "string", "minLength": 1, "maxLength": 200}
TIMEOUT = {"type": "number", "minimum": 1, "maximum": 3600}
QUERY = obj(dict(name=S, exact=B, role=S, path_contains=S, path_excludes=S), ("name",))
UIQUERY = obj(dict(name=S, exact=B, controlType=S, classContains=S))
EXPECT = obj(dict(query=QUERY, should={"enum": ["present", "absent"]}, text=S))
SEM_PROPS = dict(app=APP, query=QUERY, surface=S, action=S, key=S,
                 nth={"type": "integer", "minimum": 0}, expect=EXPECT,
                 should={"enum": ["present", "absent"]}, mode={"enum": ["skeleton", "digest", "full"]},
                 find_timeout_ms={"type": "integer", "minimum": 1000, "maximum": 50000})


def validate(value, schema, path="args", depth=0):
    """Validate the small JSON Schema vocabulary used by this closed table."""
    if depth > 16:
        raise ValueError("arguments nested too deeply")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(path + ": invalid value")
    kind = schema.get("type")
    classes = {"object": dict, "array": list, "string": str, "boolean": bool,
               "integer": int, "number": (int, float)}
    if kind and (not isinstance(value, classes[kind]) or
                 (kind in ("integer", "number") and (isinstance(value, bool) or not math.isfinite(value)))):
        raise ValueError(path + ": expected " + kind)
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise ValueError(path + ": missing required fields")
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            raise ValueError(path + ": unexpected fields")
        for k, v in value.items():
            if k in props:
                validate(v, props[k], path + "." + k, depth + 1)
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError(path + ": invalid array length")
        for v in value:
            validate(v, schema.get("items", {}), path + "[]", depth + 1)
    if kind == "string" and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 32768):
        raise ValueError(path + ": invalid text length")
    if kind in ("number", "integer") and not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
        raise ValueError(path + ": number outside range")


@dataclass(frozen=True)
class Tool:
    name: str
    tier: str
    schema: dict
    handler: object
    description: str


TOOLS = {}


def register(tool):
    """Extension point for extension 5; callers still go through invoke/gate."""
    if not isinstance(tool, Tool) or tool.name in TOOLS or tool.tier not in ("read", "input", "ask", "local"):
        raise ValueError("invalid or duplicate tool")
    TOOLS[tool.name] = tool


def _sem(verb, args):
    req = {"kind": "sem", "verb": verb, "app": args["app"], "timeout": 60}
    if "surface" in args:
        req["surface"] = args["surface"]
    if verb == "observe":
        req["mode"] = args.get("mode", "skeleton")
    else:
        if "find_timeout_ms" in args:
            req["find_timeout_ms"] = args["find_timeout_ms"]
        req["query"] = {"name": args["query"]["name"], "exact": False, **args["query"]}
    if verb == "verify":
        req["should"] = args.get("should", "present")
    if verb == "act":
        for key in ("action", "key", "nth", "expect"):
            if key in args:
                req[key] = copy.deepcopy(args[key])
        if "query" in req.get("expect", {}):
            req["expect"]["query"].setdefault("exact", req["query"]["exact"])
            req["expect"].setdefault("should", "present")
    return req


def _uia(op, args):
    return dict(kind="uia", op=op, app=args["app"], timeout=25,
                query={"exact": False, **args.get("query", {})})


def _steps(steps, depth=0):
    if depth > 4:
        raise ValueError("too many nested alternatives")
    apps = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("step must be object")
        clean = {k: v for k, v in step.items() if k != "alternatives"}
        if clean.get("verb") == "sleep":
            schema = obj(dict(verb={"enum": ["sleep"]}, ms={"type": "integer", "minimum": 0, "maximum": 60000}), ("verb", "ms"))
        elif clean.get("kind") == "uia":
            schema = obj(dict(kind={"enum": ["uia"]}, op={"enum": ["locate", "read_text", "focused", "focus", "invoke"]},
                              app=APP, query=UIQUERY, timeout=TIMEOUT), ("kind", "op", "app"))
        else:
            schema = obj(dict(SEM_PROPS, kind={"enum": ["sem"]}, verb={"enum": ["observe", "resolve", "verify", "act", "act_if"]},
                              unless=QUERY, timeout=TIMEOUT), ("verb", "app"))
        validate(clean, schema, "step")
        if "app" in step:
            apps.add(step["app"])
        if "alternatives" in step:
            validate(step["alternatives"], dict(type="array", maxItems=10))
            apps.update(_steps(step["alternatives"], depth + 1))
    return apps


def build_request(name, args, agent):
    validate(args, TOOLS[name].schema)
    if name in ("observe", "resolve", "verify", "act"):
        req = _sem(name, args)
    elif name.startswith("uia_"):
        req = _uia(name[4:], args)
    elif name == "seq":
        _steps(args["steps"])
        req = dict(kind="seq", timeout=float(args.get("timeout", 150)), steps=copy.deepcopy(args["steps"]))
    elif name == "shot":
        if args.get("mode", "window") == "window" and not args.get("app"):
            raise ValueError("window shot requires app")
        req = dict(kind="script", name="muse-shot.ps1", args=["-Mode", args.get("mode", "window")])
        if args.get("mode", "window") == "window":
            req["args"] += ["-App", args["app"]]
    elif name == "ping":
        req = dict(kind="ping")
    else:
        raise ValueError("not an executor tool")
    req["job"] = f"plugin:{agent_id(agent)}:{name}"
    return req


def _has_unknown(value):
    if isinstance(value, dict):
        return value.get("status") == "unknown" or any(_has_unknown(v) for v in value.values())
    return isinstance(value, list) and any(_has_unknown(v) for v in value)


def normalize(reply, verb):
    inner = reply.get("out")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError:
            inner = None
    payload = inner if isinstance(inner, dict) else reply
    status = payload.get("status", reply.get("status"))
    if _has_unknown(payload) or reply.get("status") == "unknown":
        status = "unknown"
    elif status in ("ok", "resolved"):
        status = "ok"
    elif status is None and verb in ("ping", "shot"):
        status = "ok" if reply.get("ok") else "fail"
    elif status is None:
        status = "unknown"
    elif status != "unknown":
        status = "fail"
    result = {"status": status, "verb": verb}
    # The result can contain readback for the calling agent; audit/trace never
    # serialize it. Preserve semantic evidence rather than just outer ok.
    result.update({k: v for k, v in payload.items() if k not in ("status", "verb", "token", "out", "ok")})
    if verb == "shot" and status == "ok":
        lines = reply.get("out", "").strip().splitlines()
        try:
            result["image"] = dict(metadata=json.loads(lines[0]), base64=lines[1])
        except (ValueError, IndexError):
            result.update(status="unknown", error="INVALID_SCREENSHOT_RESPONSE")
    if result["status"] == "unknown":
        if payload.get("status") is None and reply.get("status") is None:
            result.setdefault("error", "INVALID_HOST_RESULT")
        result["guidance"] = "先 observe/verify 再决定是否重试"
    if result["status"] == "fail":
        result.setdefault("error", "HOST_FAILED")
    return result


def _executor(base, agent, name, args):
    return normalize(executor_client.call(build_request(name, args, agent), base), name)


def invoke(name, args, agent, base=None):
    base = tool_dir(base)
    agent_id(agent)
    start = time.monotonic()
    decision, apps, target, query = "invalid", [], "", {}
    result = {"status": "fail", "verb": name, "error": "INVALID_ARGUMENTS"}
    try:
        tool = TOOLS.get(name)
        if tool is None:
            result["error"] = "UNKNOWN_TOOL"
        else:
            validate(args, tool.schema)
            query = args.get("query", {})
            target = query.get("name", "")
            apps = sorted(_steps(args["steps"])) if name == "seq" else [args["app"]] if args.get("app") else []
            if tool.tier != "read" and not gate.registered(base, agent):
                decision = "deny"
                result = {"status": "denied", "verb": name, "error": "UNREGISTERED_AGENT"}
            elif tool.tier == "ask":
                decision = "ask"
                result = {"verb": name, **tool.handler(base, agent, name, args)}
            else:
                action = args.get("action", "click") if name == "act" else name
                intent = {"click": t("plugin.intent.click"), "focus": t("plugin.intent.focus"), "press": t("plugin.intent.press"), "uia_focus": t("plugin.intent.focus"),
                          "uia_invoke": t("plugin.intent.uia_invoke"), "seq": t("plugin.intent.seq")}.get(action, action)
                verdict = gate.enforce(base, agent, name, tool.tier, apps, target, intent=intent)
                decision = verdict["decision"]
                if decision == "allow":
                    result = tool.handler(base, agent, name, args)
                else:
                    result = {"verb": name, **{k: v for k, v in verdict.items() if k != "decision"}}
    except (ValueError, TypeError, KeyError):
        result = {"status": "fail", "verb": name, "error": "INVALID_ARGUMENTS_OR_POLICY"}
    except (OSError, TimeoutError):
        result = {"status": "unknown", "verb": name, "error": "LOCAL_IO"}
    if result["status"] == "unknown":
        result["guidance"] = "先 observe/verify 再决定是否重试"
    try:
        gate.audit(base, agent, name, apps, target, decision, result, int((time.monotonic() - start) * 1000), query)
    except OSError:
        # A post-dispatch audit failure cannot turn a delivered input into a
        # retryable failure. Keep the actual outcome and flag the missing log.
        result["audit_error"] = "AUDIT_UNAVAILABLE"
    return result


for _verb in ("observe", "resolve", "verify", "act"):
    _keys = {"observe": ("app", "surface", "mode"), "resolve": ("app", "query", "surface", "find_timeout_ms"),
             "verify": ("app", "query", "surface", "should", "find_timeout_ms"),
             "act": ("app", "query", "surface", "action", "key", "nth", "expect", "find_timeout_ms")}[_verb]
    register(Tool(_verb, "input" if _verb == "act" else "read",
                  obj({k: SEM_PROPS[k] for k in _keys}, ("app",) if _verb == "observe" else ("app", "query")),
                  _executor, "Semantic " + _verb + "; unknown requires observe/verify before retry."))
for _op in ("locate", "read_text", "focused", "focus", "invoke"):
    register(Tool("uia_" + _op, "input" if _op in ("focus", "invoke") else "read",
                  obj(dict(app=APP, query=UIQUERY), ("app",)), _executor, "Native UIA " + _op))
register(Tool("seq", "input", obj(dict(steps=dict(type="array", items={"type": "object"}, minItems=1, maxItems=100), timeout=TIMEOUT), ("steps",)), _executor,
              "Semantic/UIA batch; consent required for every referenced app, including alternatives."))
register(Tool("shot", "read", obj(dict(mode={"enum": ["window", "screen"]}, app=APP)), _executor, "Capture screenshot through muse-shot.ps1."))
register(Tool("ping", "read", obj(), _executor, "Ping the local executor."))
_CARD = dict(text={"type": "string", "minLength": 1}, expires_in={"type": "integer", "minimum": 10, "maximum": 3600})
register(Tool("ask_user", "ask", obj(dict(_CARD, mode={"enum": ["input", "choice", "info"]},
              options=dict(type="array", items=S, maxItems=4)), ("text",)),
              lambda b, a, n, x: ask.ask_user(a, base=b, **x), "Ask one question on the shared card bus; wait for the answer."))
register(Tool("notify_user", "ask", obj(_CARD, ("text",)),
              lambda b, a, n, x: ask.notify_user(a, base=b, **x), "Silent info card; return without waiting."))

# Optional extension: absence is supported for standalone plugin installs.
try:
    from explib.tools import register_all
except ModuleNotFoundError as exc:
    if exc.name not in ("explib", "explib.tools"):
        raise
else:
    register_all()
