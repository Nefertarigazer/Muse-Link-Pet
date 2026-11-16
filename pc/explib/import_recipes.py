"""import_recipes - one-off: the user's existing ledgers -> local entries.

Requirements
  1. Input: a Markdown ledger in the format of docs/operating-guide.en.md section 5
     ("### <date> · <app>" headings, bullet fields 界面形态 / 目标 / 成功方法
     / 验证 / 状态 / 备注). Path given on the command line; nothing is read
     from the repository by default.
  2. Each section -> one entry, status valid ("有效") or review (anything
     else), works 1, source local, agent "import". Sections that do not
     parse are listed, not guessed.
  3. Writes only to the local library via store.put(); --dry-run prints the
     entries. Running twice does not duplicate (same app + goal + created
     -> skipped).
"""


from pathlib import Path
import re

from plugin._shared import mutex
from . import schema
from .store import Store


def parse(markdown_text):
    sections = re.split(r"(?m)^###\s+", markdown_text)[1:]
    entries, skipped = [], []
    for section in sections:
        heading, _, body = section.partition("\n")
        try:
            match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*·\s*(.+)", heading.strip())
            if not match:
                raise ValueError("heading")
            created, app = match.groups()
            fields = dict(re.findall(r"(?ms)^-\s*([^：:\n]+)[：:]\s*(.*?)(?=\n-\s|\Z)", body))
            for key in ("界面形态", "目标", "成功方法", "验证", "状态"):
                if not fields.get(key):
                    raise ValueError("missing field")
            entry = dict(id=schema.new_id(), schema=1, app=schema.normalize_app(app),
                         ui=fields["界面形态"].strip(), goal=fields["目标"].strip(),
                         method=[fields["成功方法"].strip()], verify=fields["验证"].strip(),
                         status="valid" if fields["状态"].strip().rstrip("。. ") == "有效" else "review",
                         created=created, last_verified=created, works=1, fails=0, source="local", agent="import", tags=[])
            entries.append(schema.validate(entry))
        except (ValueError, KeyError):
            skipped.append(heading.strip())
    return {"entries": entries, "skipped": skipped}


def run(path, base=None, dry_run=False):
    parsed = parse(Path(path).read_text(encoding="utf-8-sig"))
    store = Store(base)
    added, duplicates = [], []
    with mutex(store.base, "explib"):
        store.load()
        seen = {(e["app"], e["goal"], e["created"]) for e in store.entries.values() if e["source"] == "local"}
        for entry in parsed["entries"]:
            key = (entry["app"], entry["goal"], entry["created"])
            if key in seen:
                duplicates.append(entry["goal"])
                continue
            seen.add(key)
            added.append(entry if dry_run else store.put(entry))
    return dict(entries=added, added=len(added), duplicates=duplicates, skipped=parsed["skipped"], dry_run=bool(dry_run))
