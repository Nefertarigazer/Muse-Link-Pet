#!/usr/bin/env python3
"""Validate all PC/VM language packs; optionally generate an untracked pseudo pack."""
import argparse
import json
from pathlib import Path
import re
import string
import sys

ROOT = Path(__file__).resolve().parents[1]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def placeholders(message):
    if not isinstance(message, str):
        raise ValueError("message must be a string")
    names = set()
    for _, name, spec, conversion in string.Formatter().parse(message):
        if name is not None:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or spec or conversion:
                raise ValueError("only named placeholders are permitted")
            names.add(name)
    return names


def check(directory):
    errors = []
    packs = {}
    for path in sorted(directory.glob("*.json")):
        try:
            pack = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
            if not isinstance(pack, dict):
                raise ValueError("pack must be an object")
            meta = pack.get("_meta")
            if (not isinstance(meta, dict) or meta.get("tag") != path.stem or
                    not isinstance(meta.get("name"), str) or not meta["name"].strip() or
                    meta.get("dir", "ltr") not in ("ltr", "rtl") or
                    "font" in meta and not isinstance(meta["font"], str)):
                raise ValueError("invalid _meta")
            if "_desc" in pack and (path.stem != "en" or not isinstance(pack["_desc"], dict)):
                raise ValueError("_desc belongs only to en")
            packs[path.stem] = pack
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append(path.name + ": " + str(exc))
    if "en" not in packs:
        return errors + ["missing English base pack"]
    keys = {k for k in packs["en"] if k not in ("_meta", "_desc")}
    for tag, pack in packs.items():
        actual = {k for k in pack if k not in ("_meta", "_desc")}
        if actual != keys:
            errors.append(tag + ": key set differs from en (missing=" + str(sorted(keys-actual)) + ", extra=" + str(sorted(actual-keys)) + ")")
        groups = {}
        for key in sorted(actual):
            if ".opt." in key and isinstance(pack[key], str):
                groups.setdefault(key.rsplit(".opt.", 1)[0], []).append(pack[key])
            try:
                if not re.fullmatch(r"(?:popup|tray|mailbox|pet|plugin|explib|id|release)\.[A-Za-z0-9_.]+", key):
                    raise ValueError("invalid key")
                names = placeholders(pack[key])
                if key in keys and names != placeholders(packs["en"][key]):
                    raise ValueError("placeholder set differs from en")
                if key.endswith("_one") and key[:-4] + "_other" not in actual:
                    raise ValueError("missing _other")
            except ValueError as exc:
                errors.append(tag + ": " + key + ": " + str(exc))
        for prefix, values in groups.items():
            if len(values) != len(set(values)):
                errors.append(tag + ": " + prefix + ": duplicate option labels")
        if tag == "en" and (set(pack.get("_desc", {})) - keys or
                            any(not isinstance(v, str) for v in pack.get("_desc", {}).values())):
            errors.append("en: invalid translator descriptions")
    return errors


def pseudo(pack):
    """Accent and expand visible text ~40%, preserving named placeholders."""
    accents = str.maketrans("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                           "áƀçďéƒğĥíĵķľḿñóƥɋŕšţúṽŵẋýžÁɃÇĎÉƑĞĤÍĴĶĽḾÑÓƤɊŔŠŢÚṼŴẊÝŽ")
    result = {"_meta": dict(tag="qps-ploc", name="Pseudo", dir="ltr", font="Segoe UI")}
    for key, value in pack.items():
        if key.startswith("_"):
            continue
        chunks = re.split(r"(\{\{|\}\}|\{[A-Za-z_][A-Za-z0-9_]*\})", value)
        visible = sum(len(c) for c in chunks[::2])
        result[key] = "[" + "".join(c.translate(accents) if i % 2 == 0 else c for i, c in enumerate(chunks)) + "~" * max(0, round(visible * .4) - 2) + "]"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pseudo-dir", type=Path, help="write qps-ploc.json here for temporary UI checks")
    args = parser.parse_args()
    errors = []
    for side in ("pc", "vm"):
        directory = ROOT / side / "i18n"
        found = check(directory)
        errors.extend(side + "/i18n/" + e for e in found)
        if not found:
            count = len([k for k in json.loads((directory / "en.json").read_text(encoding="utf-8")) if not k.startswith("_")])
            print(side + ": " + str(count) + " keys per pack")
    if args.pseudo_dir:
        args.pseudo_dir.mkdir(parents=True, exist_ok=True)
        pack = json.loads((ROOT / "pc/i18n/en.json").read_text(encoding="utf-8"))
        (args.pseudo_dir / "qps-ploc.json").write_text(json.dumps(pseudo(pack), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for error in errors:
        print(error)
    print("check-i18n: %d findings" % len(errors))
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
