"""verify - prove the index matches the files (task book acceptance).

Requirements
  * verify(sample=N, seed=None): pick N random messages, re-read their source
    files, compare field by field (req_id, seq, role, type, text, payload,
    created_at/replied_at, state inputs, source_sha256). Report every
    mismatch with the field name; exit non-zero on any mismatch.
  * --all checks every message (used by the tests).
"""

import json
import random

from . import sources, store


def _json(value):
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def _compare(mismatches, message_id, field, expected, actual):
    if expected != actual:
        mismatches.append({
            "message_id": message_id,
            "field": field,
            "expected": _json(expected),
            "actual": _json(actual),
        })


def _check_row(row):
    mismatches = []
    message_id = row["message_id"]
    rec = sources.parse_file(row["source_path"], row["dir"])
    if rec is None:
        mismatches.append({
            "message_id": message_id,
            "field": "source_missing",
            "expected": row["source_path"],
            "actual": None,
        })
        return mismatches
    for field in ("req_id", "seq", "role", "type", "text",
                  "created_at", "replied_at"):
        _compare(mismatches, message_id, field, rec.get(field), row[field])
    try:
        stored_payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        stored_payload = None
    _compare(mismatches, message_id, "payload", rec.get("payload"), stored_payload)
    _compare(mismatches, message_id, "final", bool(rec.get("final")),
             bool(row["final"]))
    _compare(mismatches, message_id, "status", rec.get("status") or "",
             row["status"] or "")
    _compare(mismatches, message_id, "dir", rec.get("dir") or "",
             row["dir"] or "")
    _compare(mismatches, message_id, "parse_error", rec.get("parse_error"),
             row["parse_error"])
    _compare(mismatches, message_id, "source_sha256", rec.get("sha256"),
             row["source_sha256"])
    expected_id = rec["req_id"]
    if rec.get("kind") == "reply":
        expected_id = "%s#%d" % (rec["req_id"], int(rec.get("seq") or 0))
    _compare(mismatches, message_id, "message_id", expected_id, message_id)
    return mismatches


def verify(bridge_root, index_path, sample=5, seed=None, check_all=False):
    conn = store.open_reader(index_path)
    try:
        rows = conn.execute(
            """SELECT * FROM messages
               ORDER BY message_id ASC""").fetchall()
        if check_all:
            chosen = rows
        else:
            count = min(max(int(sample), 0), len(rows))
            chosen = random.Random(seed).sample(list(rows), count)
            chosen.sort(key=lambda row: row["message_id"])
        mismatches = []
        for row in chosen:
            mismatches.extend(_check_row(row))
        return {
            "ok": not mismatches,
            "checked": len(chosen),
            "total": len(rows),
            "mode": "all" if check_all else "sample",
            "seed": seed,
            "mismatches": mismatches,
        }
    finally:
        conn.close()
