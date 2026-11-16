"""Configuration previews and conservative, backed-up merges."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys

from ._shared import agent_id, atomic, tool_dir


def configuration(base, python, agent):
    bootstrap = "import sys,runpy;sys.path.insert(0," + repr(str(tool_dir(base))) + ");runpy.run_module('plugin.mcp_server',run_name='__main__')"
    return dict(command=str(python), args=["-c", bootstrap, "--agent", agent_id(agent)])


CLIENTS = ("codex", "kiro", "claude")


def client_paths(home):
    """client -> (config file, is TOML, the client's own directory). The file
    is created only when that directory exists (the client is installed);
    ~/.claude.json is Claude Code's own state file and is never created."""
    home = Path(home)
    return {"codex": (home / ".codex/config.toml", True, home / ".codex"),
            "kiro": (home / ".kiro/settings/mcp.json", False, home / ".kiro"),
            "claude": (home / ".claude.json", False, None)}


def _toml_root(section):
    m = re.match(r"\s*([A-Za-z0-9_-]+)\s*(?:\.|$)", section)
    return m[1] if m else None


def _entry_agent(entry):
    args = entry.get("args") if isinstance(entry, dict) else None
    if isinstance(args, list) and "--agent" in args[:-1]:
        return str(args[args.index("--agent") + 1])
    return None


def merge(path, config, toml=False, *, apply=True, force=False, create_in=None):
    path = Path(path)
    if not path.is_file():
        if create_in is None or not Path(create_in).is_dir():
            return "skipped (file does not exist): " + str(path)
        original = ""
    else:
        original = path.read_text(encoding="utf-8-sig")
    def keep_existing(agent=None):
        # An existing MuseLink entry belongs to the user and is never
        # replaced, --force included; replacing it is a manual edit.
        note = " (--force does not replace it; edit or remove it by hand)" if force else ""
        who = "" if agent is None or agent == _entry_agent(config) else " for agent " + agent
        return "skip " + str(path) + ": already installed (existing muselink entry" + who + " kept)" + note

    if toml:
        # Existing MuseLink configuration belongs to the user; never replace
        # it (including nested env tables). All other TOML is kept verbatim.
        sections = re.findall(r"(?m)^\s*\[([^\[\]\n]+)\]\s*(?:#.*)?$", original)
        normalized = [re.sub(r"[\s\"']", "", s) for s in sections]
        if any(s == "mcp_servers.muselink" or s.startswith("mcp_servers.muselink.") for s in normalized):
            return keep_existing()
        # Escapes matter only where they could spell an MCP table: a section
        # under another bare root (Codex writes [projects.'D:\\work']) is kept
        # verbatim like any other table.
        escaped = [s for s in sections if "\\" in s and _toml_root(s) in (None, "mcp_servers")]
        if re.search(r"(?m)^\s*mcp_servers\s*=", original) or escaped:
            raise ValueError("inline/escaped MCP tables require a manual merge; file unchanged")
        if "mcp_servers" in normalized:
            table = re.search(r"(?ms)^\s*\[mcp_servers\]\s*\n(.*?)(?=^\s*\[|\Z)", original)
            if table and re.search(r"(?m)^\s*[\"']?muselink[\"']?\s*=", table[1]):
                return keep_existing()
        table = "[mcp_servers.muselink]\n" + "\n".join(
            k + " = " + json.dumps(v, ensure_ascii=False) for k, v in config.items()) + "\ntool_timeout_sec = 3620\n"
        updated = (original.rstrip() + "\n\n" if original.strip() else "") + table
    else:
        value = json.loads(original) if original.strip() else {}
        if not isinstance(value, dict) or not isinstance(value.get("mcpServers", {}), dict):
            raise ValueError("invalid mcpServers config")
        servers = value.setdefault("mcpServers", {})
        if "muselink" in servers:
            return keep_existing(_entry_agent(servers["muselink"]))
        servers["muselink"] = config
        updated = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if updated == original and not force:
        return "skip " + str(path) + ": already installed (merged content matches)"
    if not path.is_file():
        if not apply:
            return "new " + str(path) + ": create with the muselink entry"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic(path, updated)
        return "created: " + str(path)
    if not apply:
        return "change " + str(path) + ": merge muselink entry (backup on --apply)"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    shutil.copy2(path, str(path) + ".muselink-" + stamp + ".bak")
    atomic(path, updated)
    return "merged (backup saved): " + str(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, type=agent_id)
    parser.add_argument("--tool-dir", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--config-home", default=str(Path.home()))
    parser.add_argument("--client", choices=CLIENTS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    # One client configuration file holds one muselink entry, i.e. one agent:
    # the entry is written only into the client this agent runs in.
    client = args.client or (args.agent if args.agent in CLIENTS else None)
    if client is None:
        parser.error("--client codex|kiro|claude is required when the agent id is not a client name")
    config = configuration(args.tool_dir, args.python, args.agent)
    path, is_toml, create_in = client_paths(args.config_home)[client]
    print(client + ": " + str(path))
    if is_toml:
        print("[mcp_servers.muselink]")
        for k, v in config.items():
            print(k + " = " + json.dumps(v, ensure_ascii=False))
        print("tool_timeout_sec = 3620")
    else:
        print(json.dumps({"mcpServers": {"muselink": config}}, ensure_ascii=False, indent=2))
    if client == "claude":
        print("Claude Code command (alternative to the merge, not executed by -Apply):")
        # PowerShell single quotes, with literal quotes doubled.
        quote = lambda s: "'" + s.replace("'", "''") + "'"
        print("claude mcp add --scope user --transport stdio muselink -- " + " ".join(map(quote, [config["command"], *config["args"]])))
    print(merge(path, config, is_toml, apply=args.apply, force=args.force, create_in=create_in))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
