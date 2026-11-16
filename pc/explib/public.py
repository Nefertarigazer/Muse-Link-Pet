"""public - read-only sync of the community library.

Requirements
  1. Source: muselink.json -> explib.public.index_url (HTTPS only). The
     index is {"v": 1, "entries": [{"path": "entries/<app>/<id>.json",
     "sha256": "..."}]}; entry URLs are resolved relative to the index URL
     and must stay on the same host.
  2. Limits: index <= 1 MiB, entry <= 32 KiB, <= 5000 entries; timeout 20 s
     per request; ETag / If-None-Match on the index.
  3. Every entry: sha256 must match, schema.validate() must pass, source is
     forced to "public"; anything else is dropped and counted.
  4. Replace public/ atomically (build in public.new/, swap); local/ is never
     touched. Returns {"added", "kept", "dropped", "not_modified"}.
  5. urllib only (no new dependency); proxy settings from the environment.
"""


import hashlib
import json
from pathlib import Path
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

from plugin._shared import atomic, mutex, read_json, tool_dir
from . import redact, schema

INDEX_LIMIT = 1024 * 1024
ENTRY_LIMIT = 32768


def _fetch(url, headers, limit):
    # Redirects are refused: an HTTPS URL must never redirect to HTTP or a
    # different host before validation gets a chance to examine the response.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            return response.status, dict(response.headers), response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, dict(exc.headers), b""
        raise


def sync(base, fetch=None):
    base = tool_dir(base)
    url = redact.config(base).get("public", {}).get("index_url", "")
    origin = urllib.parse.urlsplit(url)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.fragment:
        raise ValueError("public.index_url: HTTPS URL required")
    fetch = fetch or _fetch
    root = base / "experience"
    with mutex(base, "explib"):
        previous, staging, backup = root / "public", root / "public.new", root / "public.old"
        # Validate fixed targets before any recursive cleanup or directory move.
        for path in (staging, backup, previous):
            if path.is_symlink() or path.resolve().parent != root.resolve() or path.resolve() != path.absolute():
                raise ValueError("public: unsafe directory")
        if backup.exists() and not previous.exists():
            backup.rename(previous)
        state = read_json(root / "public-sync.json", {})
        headers = {"If-None-Match": state["etag"]} if previous.exists() and state.get("url") == url and state.get("etag") else {}
        status, response_headers, body = fetch(url, headers, INDEX_LIMIT)
        old_count = len(list(previous.glob("*/*.json")))
        if status == 304:
            return dict(added=0, kept=old_count, dropped=0, not_modified=True)
        if status != 200 or len(body) > INDEX_LIMIT:
            raise ValueError("index: HTTP failure or size limit")
        index = json.loads(body.decode("utf-8"))
        if (not isinstance(index, dict) or type(index.get("v")) is not int or index["v"] != 1 or
                not isinstance(index.get("entries"), list) or len(index["entries"]) > 5000):
            raise ValueError("index: invalid format or entry count")
        for path in (staging, backup):
            if path.exists():
                shutil.rmtree(path)
        staging.mkdir(parents=True)
        added = kept = dropped = 0
        seen = set()
        swapped = False
        try:
            for item in index["entries"]:
                try:
                    path, digest = item["path"], item["sha256"]
                    if (not isinstance(path, str) or not re.fullmatch(r"entries/[^/]+/x-[0-9a-f]{12}\.json", path) or
                            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                        raise ValueError("path/hash")
                    target = urllib.parse.urljoin(url, urllib.parse.quote(path, safe="/"))
                    dest = urllib.parse.urlsplit(target)
                    if (dest.scheme != "https" or dest.netloc != origin.netloc or
                            ".." in path.split("/") or "\\" in path or "%" in path or "?" in path or "#" in path):
                        raise ValueError("external/unsafe path")
                    code, _, payload = fetch(target, {}, ENTRY_LIMIT)
                    if code != 200 or len(payload) > ENTRY_LIMIT or hashlib.sha256(payload).hexdigest() != digest:
                        raise ValueError("entry HTTP/size/hash")
                    entry = json.loads(payload.decode("utf-8"))
                    schema.validate(entry)
                    if path != "entries/" + entry["app"] + "/" + entry["id"] + ".json" or entry["id"] in seen:
                        raise ValueError("entry path/duplicate")
                    entry["source"] = "public"
                    schema.validate(entry)
                    data = schema.render(entry)
                    relative = Path(entry["app"]) / (entry["id"] + ".json")
                    old = previous / relative
                    if old.is_file() and old.read_text(encoding="utf-8") == data:
                        kept += 1
                    else:
                        added += 1
                except (OSError, ValueError, TypeError, KeyError, RecursionError):
                    dropped += 1
                    continue
                # A disk failure is a failed sync, not an invalid community
                # entry. Abort before replacing the previous complete tree.
                atomic(staging / relative, data)
                seen.add(entry["id"])
            if previous.exists():
                previous.rename(backup)
            try:
                staging.rename(previous)
                swapped = True
            except OSError:
                if backup.exists():
                    backup.rename(previous)
                raise
            atomic(root / "public-sync.json", {"url": url, "etag": next((v for k, v in response_headers.items() if k.lower() == "etag"), "")})
        except BaseException:
            if swapped:
                shutil.rmtree(previous)
                if backup.exists():
                    backup.rename(previous)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        if backup.exists():
            shutil.rmtree(backup)
        return dict(added=added, kept=kept, dropped=dropped, not_modified=False)
