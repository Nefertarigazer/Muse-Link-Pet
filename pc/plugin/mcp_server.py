"""Line-delimited MCP stdio, with no dependencies or listening port."""
import argparse
import json
import sys

from i18n import doc_path
from . import __version__, gate, tools
from ._shared import agent_id, tool_dir

# This design deliberately uses the initialize lifecycle (legacy MCP).
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def serve(agent, base=None, stdin=None, stdout=None):
    base = tool_dir(base)
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    initialized = False
    ready = False
    for line in stdin:
        request = None
        try:
            try:
                request = json.loads(line)
            except ValueError:
                raise RpcError(-32700, "Parse error")
            if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or
                    not isinstance(request.get("method"), str) or
                    ("id" in request and (isinstance(request["id"], bool) or not isinstance(request["id"], (str, int, type(None)))))):
                raise RpcError(-32600, "Invalid Request")
            method, params = request["method"], request.get("params", {})
            if not isinstance(params, dict):
                raise RpcError(-32602, "Invalid params")
            if "id" not in request:
                if method == "notifications/initialized" and initialized:
                    ready = True
                continue
            if method == "initialize":
                if initialized or not isinstance(params.get("protocolVersion"), str):
                    raise RpcError(-32602, "Invalid initialization")
                offered = params["protocolVersion"]
                result = dict(protocolVersion=offered if offered in VERSIONS else VERSIONS[0],
                              capabilities={"tools": {"listChanged": False}},
                              serverInfo={"name": "muselink", "version": __version__},
                              instructions="Read " + str(doc_path(base / "plugin/GUIDE").relative_to(base)).replace("\\", "/") + ". Unknown: observe/verify before retrying.")
                initialized = True
            elif method == "ping":
                result = {}
            elif not ready:
                raise RpcError(-32002, "Server not initialized")
            elif method == "tools/list":
                known = gate.registered(base, agent)
                result = {"tools": [dict(name=t.name, description=t.description,
                          inputSchema=t.schema, annotations={"readOnlyHint": t.tier == "read"})
                          for t in tools.TOOLS.values() if known or t.tier == "read"]}
            elif method == "tools/call":
                name, arguments = params.get("name"), params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise RpcError(-32602, "Invalid tool call")
                # Hidden tools remain gated even if a client calls by name.
                if not gate.registered(base, agent) and name in tools.TOOLS and tools.TOOLS[name].tier != "read":
                    answer = {"status": "denied", "verb": name, "error": "UNREGISTERED_AGENT"}
                    gate.audit(base, agent, name, [], "", "deny", answer, 0)
                else:
                    answer = tools.invoke(name, arguments, agent, base)
                content = []
                image = answer.get("image") if isinstance(answer.get("image"), dict) else None
                if image and isinstance(image.get("base64"), str):
                    # A real image item, so the client model can look at it;
                    # the text item keeps the metadata, not the base64 again.
                    meta = image.get("metadata") or {}
                    mime = "image/jpeg" if str(meta.get("fmt", "")).lower() == "jpeg" else "image/png"
                    content.append({"type": "image", "data": image["base64"], "mimeType": mime})
                    answer = dict(answer, image={"metadata": meta})
                content.insert(0, {"type": "text", "text": json.dumps(answer, ensure_ascii=False)})
                result = dict(content=content, isError=answer["status"] != "ok")
            else:
                raise RpcError(-32601, "Method not found")
            response = dict(jsonrpc="2.0", id=request["id"], result=result)
        except RpcError as e:
            if isinstance(request, dict) and "method" in request and "id" not in request:
                continue
            response = dict(jsonrpc="2.0", id=request.get("id") if isinstance(request, dict) else None,
                            error={"code": e.code, "message": str(e)})
        except Exception:
            # Never put a Python traceback, arguments or secrets on stdout.
            response = dict(jsonrpc="2.0", id=request.get("id") if isinstance(request, dict) else None,
                            error={"code": -32603, "message": "Internal error"})
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, type=agent_id)
    parser.add_argument("--tool-dir", type=str)
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding="utf-8-sig")
    sys.stdout.reconfigure(encoding="utf-8")
    serve(args.agent, args.tool_dir)


if __name__ == "__main__":
    main()
