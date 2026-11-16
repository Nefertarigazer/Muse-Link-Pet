#!/usr/bin/env python3
"""Check paired documentation, level-two headings, relative links and anchors."""
from collections import Counter
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def english(path):
    name = path.name[:-6]
    return path.with_name(name + (".md" if name in ("README", "SECURITY", "CHANGELOG", "CONTRIBUTING") else ".en.md"))


def headings(text):
    in_fence = False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
        if not in_fence:
            match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if match:
                yield len(match[1]), match[2]


def anchors(text):
    seen = Counter()
    result = set(re.findall(r'<a\s+(?:id|name)=["\x27]([^"\x27]+)', text))
    for _, title in headings(text):
        title = re.sub(r"<[^>]+>", "", title).lower()
        slug = re.sub(r"[^\w\-\s]", "", title).replace(" ", "-")
        result.add(slug + ("-" + str(seen[slug]) if seen[slug] else ""))
        seen[slug] += 1
    return result


def check(root=ROOT):
    errors = []
    chinese = sorted(root.rglob("*.zh.md"))
    pairs = {english(path) for path in chinese}
    for path in list(root.rglob('*.en.md')) + [root / (name + '.md') for name in ('README', 'SECURITY', 'CHANGELOG', 'CONTRIBUTING')]:
        if not path.is_file():
            continue
        stem = path.name[:-6] if path.name.endswith('.en.md') else path.stem
        if not path.with_name(stem + '.zh.md').is_file():
            errors.append(('missing-chinese', str(path.with_name(stem + '.zh.md').relative_to(root))))
    for path in chinese:
        other = english(path)
        if not other.is_file():
            errors.append(("missing-english", str(other.relative_to(root))))
        else:
            counts = [sum(level == 2 for level, _ in headings(p.read_text(encoding="utf-8"))) for p in (path, other)]
            if counts[0] != counts[1]:
                errors.append(("heading-count", str(path.relative_to(root))))
    docs = chinese + [p for p in pairs if p.is_file()]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        # Strip fenced code blocks and inline code spans before scanning Markdown link targets.
        scannable = re.sub(r"(?ms)^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$", "", text)
        scannable = re.sub(r"`[^`\n]+`", "", scannable)
        # Inline Markdown links/images and reference-link definitions.
        targets = re.findall(r'!?\[[^\]]*\]\(<?([^\s)>]+)>?(?:\s+["\x27][^)]*)?\)', scannable)
        targets += re.findall(r'^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?', scannable, re.M)
        for raw in targets:
            url = urlsplit(raw)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if target in pairs and not target.exists():
                continue  # reported once as missing-english, including switch links
            bad_dot_seg = any(part not in (".", "..") and part.endswith(".") for part in Path(unquote(url.path)).parts)
            if bad_dot_seg or not target.exists():
                errors.append(("missing-target", str(path.relative_to(root)) + ": " + raw))
            elif url.fragment:
                try:
                    present = anchors(target.read_text(encoding="utf-8"))
                except (UnicodeError, OSError):
                    present = set()
                if unquote(url.fragment) not in present:
                    errors.append(("missing-anchor", str(path.relative_to(root)) + ": " + raw))
    return errors


def main():
    errors = check()
    for kind, detail in errors:
        print(kind + ": " + detail)
    print("check-docs: %d findings" % len(errors))
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
