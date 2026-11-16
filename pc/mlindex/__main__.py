"""python -m mlindex <sync|rebuild|stats|threads|thread ID|search Q|verify [--sample N|--all]>

Requirements: bridge root from muselink.json bridge.root (default
<tool dir>\\muse-bridge), --root overrides (tests); one JSON line on stdout;
exit 0 ok, 1 verify mismatch, 2 usage, 3 writer busy, 30 internal error.
"""

import json
import os
import sys

from . import indexer, query, store, verify as verify_mod


def _tool_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _emit(value):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:  # pragma: no cover - Python 3.6 and older
        pass
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _fail(code, message):
    _emit({"ok": False, "error": message})
    return code


def _load_config(path):
    if not path:
        path = os.path.join(_tool_dir(), "muselink.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        value = json.load(fh)
    return value if isinstance(value, dict) else {}


def _section(config, name):
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def _take_option(args, names, takes_value=True):
    value = None
    rest = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in names:
            if takes_value:
                if i + 1 >= len(args):
                    raise ValueError("missing value for %s" % arg)
                value = args[i + 1]
                i += 2
                continue
            value = True
            i += 1
            continue
        rest.append(arg)
        i += 1
    return value, rest


def _resolve_paths(root, index, config_path):
    config = _load_config(config_path)
    bridge = _section(config, "bridge")
    if root is None:
        root = bridge.get("root") or ""
    if not root:
        root = os.path.join(_tool_dir(), "muse-bridge")
    root = os.path.abspath(root)

    if index is None:
        index_cfg = _section(config, "index")
        index = index_cfg.get("path") or ""
    if not index:
        index = os.path.join(root, "index", "threads.sqlite")
    elif not os.path.isabs(index):
        index = os.path.join(_tool_dir(), index)
    return root, os.path.abspath(index)


def _parse(argv):
    args = list(argv)
    if args and args[0] in ("--args-file", "-ArgsFile"):
        if len(args) < 2:
            raise ValueError("missing args file")
        with open(args[1], "r", encoding="utf-8-sig") as fh:
            args = fh.read().splitlines()
    root, args = _take_option(args, ("--root", "-Root"))
    index, args = _take_option(args, ("--index", "-Index"))
    config, args = _take_option(args, ("--config", "-Config"))
    if not args:
        raise ValueError("missing command")
    command = args[0]
    rest = args[1:]
    if command == "verify":
        check_all, rest = _take_option(rest, ("--all", "-All"), takes_value=False)
        sample, rest = _take_option(rest, ("--sample", "-Sample"))
        if rest:
            raise ValueError("unexpected arguments")
        try:
            sample_value = int(sample) if sample is not None else 5
        except ValueError:
            raise ValueError("--sample needs an integer")
        if sample_value < 0:
            raise ValueError("--sample needs a non-negative integer")
        return root, index, config, command, {
            "sample": sample_value, "all": bool(check_all)}
    if command in ("sync", "rebuild", "stats", "threads"):
        if rest:
            raise ValueError("unexpected arguments")
        return root, index, config, command, {}
    if command in ("thread", "search"):
        if not rest:
            raise ValueError("missing argument")
        return root, index, config, command, {"value": " ".join(rest)}
    raise ValueError("unknown command")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        root, index_path, config_path, command, options = _parse(argv)
        root, index_path = _resolve_paths(root, index_path, config_path)
    except ValueError as exc:
        return _fail(2, str(exc))
    except Exception as exc:
        return _fail(30, "%s: %s" % (type(exc).__name__, exc))

    try:
        if command in ("sync", "rebuild"):
            try:
                result = (indexer.sync(root, index_path)
                          if command == "sync" else indexer.rebuild(root, index_path))
            except store.WriterBusy:
                return _fail(3, "busy")
            result = dict(result)
            result["ok"] = True
            result["index"] = index_path
            _emit(result)
            return 0
        if command == "stats":
            result = query.stats(index_path)
            result["ok"] = True
            result["index"] = index_path
            _emit(result)
            return 0
        if command == "threads":
            _emit({"ok": True, "threads": query.list_threads(index_path)})
            return 0
        if command == "thread":
            thread_id = options["value"]
            value = query.thread(index_path, thread_id)
            _emit({"ok": True, "thread_id": thread_id, "thread": value})
            return 0
        if command == "search":
            value = options["value"]
            _emit({"ok": True, "query": value,
                   "results": query.search(index_path, value)})
            return 0
        if command == "verify":
            result = verify_mod.verify(
                root, index_path, sample=options["sample"],
                check_all=options["all"])
            result["ok"] = not result["mismatches"]
            _emit(result)
            return 0 if result["ok"] else 1
    except store.WriterBusy:
        return _fail(3, "busy")
    except FileNotFoundError as exc:
        return _fail(30, "index not found: %s" % exc)
    except Exception as exc:
        return _fail(30, "%s: %s" % (type(exc).__name__, exc))
    return _fail(2, "unknown command")


if __name__ == "__main__":
    sys.exit(main())
