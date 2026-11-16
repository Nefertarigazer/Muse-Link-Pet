"""make-release.py - build the copy-ready release folders from ONE commit.

    python tools/make-release.py <out dir> [commit, default HEAD]

Exports the commit with `git archive` (so only tracked files, nothing from a
working tree), puts the site template into both sides and the standing-order
template into vm/ (installation guide: docs/install.en.md section 2), then writes
  <out>/pc/RELEASE-MANIFEST.sha256   for the PC tool dir
  <out>/vm/RELEASE-MANIFEST.sha256   checked by vm/install.sh
  <out>/RELEASE.txt                  commit, date, file counts
Muse copies <out>/pc/* into the tool dir and <out>/vm/ to
~/workspace/tools/muselink/ exactly as they are.
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile

SITE_FILES = ("config/muselink.site.example.json", "config/standing-orders.example.zh.md", "config/standing-orders.example.en.md")
from datetime import datetime, timezone

GIT = ["git", "-c", "safe.directory=*", "-c", "core.autocrlf=false"]


def main():
    out = os.path.abspath(sys.argv[1])
    commit = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    if os.path.exists(out) and os.listdir(out):
        sys.exit("output dir must be empty: " + out)
    full = subprocess.run(GIT + ["rev-parse", commit], capture_output=True, text=True, check=True).stdout.strip()
    data = subprocess.run(GIT + ["archive", "--format=tar", full, "pc", "vm"] + list(SITE_FILES),
                          capture_output=True, check=True).stdout
    counts = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(out)
    for rel in SITE_FILES:
        for side in (("vm",) if "standing-orders.example." in rel else ("pc", "vm")):
            shutil.copyfile(os.path.join(out, rel), os.path.join(out, side, os.path.basename(rel)))
    shutil.rmtree(os.path.join(out, "config"))
    for side in ("pc", "vm"):
        root = os.path.join(out, side)
        lines = []
        for d, _, files in os.walk(root):
            for name in sorted(files):
                p = os.path.join(d, name)
                rel = os.path.relpath(p, root).replace(os.sep, "/")
                with open(p, "rb") as f:
                    lines.append(hashlib.sha256(f.read()).hexdigest() + "  " + rel)
        lines.sort(key=lambda s: s.split("  ", 1)[1])
        with open(os.path.join(root, "RELEASE-MANIFEST.sha256"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        counts[side] = len(lines)
    with open(os.path.join(out, "RELEASE.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("commit %s\nbuilt %s\npc files %d\nvm files %d\n" % (
            full, datetime.now(timezone.utc).isoformat(timespec="seconds"), counts["pc"], counts["vm"]))
    print("release %s -> %s (pc %d, vm %d files)" % (full[:12], out, counts["pc"], counts["vm"]))


if __name__ == "__main__":
    main()
