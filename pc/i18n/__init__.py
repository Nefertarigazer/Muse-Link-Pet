"""MuseLink UI language packs. Standard library only; no regional-format input."""
import ctypes
from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import string
import tempfile

ROOT = Path(__file__).parent
_missing = set()
_formatter = string.Formatter()
# RFC 5646 language/script/region/variant/extensions/private use syntax.
_TAG = re.compile(r"^(?:[A-Za-z]{2,3}(?:-[A-Za-z]{3}){0,3}|[A-Za-z]{4}|[A-Za-z]{5,8})"
                  r"(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?"
                  r"(?:-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*"
                  r"(?:-[0-9A-WY-Za-wy-z](?:-[A-Za-z0-9]{2,8})+)*"
                  r"(?:-x(?:-[A-Za-z0-9]{1,8})+)?$", re.ASCII)


@dataclass(frozen=True)
class Locale:
    system: str
    ui: str
    docs: str
    font: str = "Segoe UI"
    dir: str = "ltr"


def resolve(system_tag, available):
    """Pure language selection; availability is filenames, never a registry."""
    original = system_tag if isinstance(system_tag, str) else ""
    tags = {s.lower(): s for s in available}
    ui, docs = "en", "en"
    if _TAG.fullmatch(original):
        parts = original.lower().split("-")
        if parts[0] == "zh":
            docs = "zh"
            # Only the script position counts, not a private-use substring.
            core = parts[1:next((i for i, p in enumerate(parts[1:], 1) if len(p) == 1), len(parts))]
            script = next((p for p in core if len(p) == 4 and p.isalpha()), "")
            region = next((p for p in core if len(p) == 2 and p.isalpha()), "")
            hant = script == "hant" or (script != "hans" and region in ("tw", "hk", "mo"))
            candidates = ["zh-hant", "zh-hans", "en"] if hant else ["zh-hans", "en"]
        elif parts[0] == "en":
            candidates = ["en"]
        else:
            candidates = []
            candidate = parts[:]
            while candidate:
                candidates.append("-".join(candidate))
                candidate.pop()
                if candidate and len(candidate[-1]) == 1:
                    candidate.pop()  # RFC 4647: remove trailing singleton too.
            candidates.append("en")
        ui = next((tags[c] for c in candidates if c in tags), "en")
    font = {"zh-Hans": "Microsoft YaHei UI", "zh-Hant": "Microsoft JhengHei UI",
            "ja": "Yu Gothic UI", "ko": "Malgun Gothic"}.get(ui, "Segoe UI")
    return Locale(original, ui, docs, font)


def system_tag():
    if "MUSELINK_LOCALE" in os.environ:
        return os.environ["MUSELINK_LOCALE"]
    try:
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return ""
    try:
        fn = kernel.GetUserPreferredUILanguages
        fn.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.ULONG),
                       wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]
        fn.restype = wintypes.BOOL
        count, size = wintypes.ULONG(), wintypes.ULONG()
        if fn(8, ctypes.byref(count), None, ctypes.byref(size)) and size.value:
            buf = ctypes.create_unicode_buffer(size.value)
            if fn(8, ctypes.byref(count), buf, ctypes.byref(size)) and buf.value:
                return buf.value
    except (AttributeError, OSError, ValueError):
        pass
    try:
        kernel.GetUserDefaultUILanguage.restype = wintypes.WORD
        kernel.LCIDToLocaleName.argtypes = [wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD]
        buf = ctypes.create_unicode_buffer(85)
        if kernel.LCIDToLocaleName(kernel.GetUserDefaultUILanguage(), buf, len(buf), 0):
            return buf.value
    except (AttributeError, OSError, ValueError):
        pass
    return ""


def available():
    return sorted(p.stem for p in ROOT.glob("*.json"))


@lru_cache(maxsize=None)
def _pack(tag):
    if tag not in available():
        return {}
    return json.loads((ROOT / (tag + ".json")).read_text(encoding="utf-8"))


def preference_path():
    # Installation-local, outside the read-only UI roots. Independent checkouts
    # and installed copies must never change each other's language.
    return ROOT.parent / "ui-language.txt"


def _preference():
    try:
        tag = preference_path().read_text(encoding="utf-8").strip()
        return tag if tag in available() else None
    except (OSError, UnicodeError):
        return None


def languages():
    return [dict(tag=tag, name=_pack(tag)["_meta"]["name"]) for tag in available()]


def set_language(tag):
    if not isinstance(tag, str) or tag not in available():
        raise ValueError("language")
    path = preference_path()
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="ui-language.", suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            stream.write(tag + "\n")
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return bundle()


@lru_cache(maxsize=None)
def _active(preference):
    system = system_tag()
    loc = resolve(preference if preference is not None else system, available())
    meta = _pack(loc.ui).get("_meta", {})
    return Locale(system, loc.ui, loc.docs, meta.get("font", loc.font), meta.get("dir", "ltr"))


def active():
    # Observe atomic preference changes in other processes, while retaining the
    # existing cached OS/environment fallback when no explicit choice exists.
    return _active(_preference())


active.cache_clear = _active.cache_clear


def format_message(message, args):
    """Named placeholders only; doubled braces are literals, never recursion."""
    result = []
    for literal, name, spec, conversion in _formatter.parse(message):
        result.append(literal)
        if name is not None:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or spec or conversion:
                raise ValueError("invalid named placeholder")
            result.append(str(args[name]) if name in args else "{" + name + "}")
    return "".join(result)


def _text(key, tag):
    pack = _pack(tag)
    if key in pack and isinstance(pack[key], str):
        return pack[key]
    if key not in _missing:
        _missing.add(key)
        logging.getLogger(__name__).warning("Missing i18n key: %s", key)
    return _pack("en").get(key, key)


def t(key, **args):
    return format_message(_text(key, active().ui), {"brand": "MuseLink", "muse": "Muse", "codex": "Codex", "kiro": "Kiro", "workbuddy": "WorkBuddy", **args})


def plural(key, n, **args):
    category = "one" if active().ui.split("-")[0] == "en" and n == 1 else "other"
    return t(key + "_" + category, **dict(args, n=n))


def labels(prefix, tag=None):
    tag = tag or active().ui
    return {key: _text(key, tag) for key in _pack("en") if key.startswith(prefix) and not key.startswith("_")}


def answer_key(answer, rendered):
    return next((key for key, value in rendered.items() if answer == value), None)


def bundle():
    loc = active()
    messages = {k: _text(k, loc.ui) for k in _pack("en") if not k.startswith("_")}
    messages.update({k: v for k, v in _pack(loc.ui).items() if not k.startswith("_")})
    return dict(tag=loc.ui, dir=loc.dir, font=loc.font, messages=messages)


def doc_path(base):
    """Return a language-specific Path, with the other edition as fallback."""
    base = Path(base)
    name = re.sub(r"(?:\.(?:zh|en))?\.md$", "", base.name)
    def edition(lang):
        suffix = ".md" if lang == "en" and name in ("README", "SECURITY", "CHANGELOG") else "." + lang + ".md"
        return base.with_name(name + suffix)
    selected = edition(active().docs)
    other = edition("en" if active().docs == "zh" else "zh")
    return selected if selected.exists() or not other.exists() else other
