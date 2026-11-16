"""VM UI language packs. Language comes exclusively from PC executor info."""
from dataclasses import dataclass
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
import string

ROOT = Path(__file__).parent
_missing = set()
_formatter = string.Formatter()
@dataclass(frozen=True)
class Locale:
    system: str
    ui: str
    docs: str
    font: str = "Segoe UI"
    dir: str = "ltr"


@lru_cache(maxsize=1)
def pc_locale():
    """Read PC executor info once. Never detect the VM's own language."""
    import subprocess
    try:
        result = subprocess.run([str(ROOT.parent / "executor.sh"), "info"],
                                capture_output=True, encoding="utf-8", timeout=15)
        response = json.loads(result.stdout)
        if result.returncode != 0 or response.get("ok") is not True:
            raise ValueError("PC info unavailable")
        info = response.get("out", {})
        if isinstance(info, str):
            info = json.loads(info)
        locale = response.get("locale") or info.get("locale") or {}
        if not isinstance(locale, dict):
            raise ValueError("locale")
        return locale
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return {"system": "", "ui": "en", "docs": "en"}


def available():
    return sorted(p.stem for p in ROOT.glob("*.json"))


@lru_cache(maxsize=None)
def _pack(tag):
    if tag not in available():
        return {}
    return json.loads((ROOT / (tag + ".json")).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def active():
    supplied = pc_locale()
    tag = supplied.get("ui", "en")
    tag = tag if tag in available() else "en"
    meta = _pack(tag).get("_meta", {})
    return Locale(supplied.get("system", ""), tag,
                  "zh" if supplied.get("docs") == "zh" else "en",
                  meta.get("font", "Segoe UI"), meta.get("dir", "ltr"))


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
