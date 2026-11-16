English | [中文](protocol.zh.md)

# Protocol

## 1 Mailbox file layout

Files are the source of truth, and writes use a temporary file plus an atomic replace. The PC root directory is set by the site configuration; the subdirectories are used as follows.

| PC subdirectory | Contents and writer |
| --- | --- |
| `inbox/`, `sent/` | New requests delivered by the client, and the client's own copies |
| `claimed/` | Requests the postman has taken |
| `pending/`, `rejected/` | Requests awaiting enrollment confirmation or rejected, moved by the postman |
| `outbox/` | Replies Muse has delivered that the client has not read |
| `archive/`, `ack/` | Replies the client has read, and a per-reply `<req_id>.<seq>.ack` |
| `done/` | Requests whose final reply has been read |
| `directory/` | Keyless mirror of agents and grants, for lookup only |
| `quarantine/` | Replies the client could not verify |
| `index/` | Rebuildable SQLite cache and single-writer lock |

The VM root is `$ML_BRIDGE_HOME`: `inbox/working/processed/rejected` are the handling stages, `replies/` keeps the reply ledger and `alerts/` holds alerts; `envelopes/<id>.json` holds the original envelope, `verdicts/<id>.json` the verdict and `pending/<id>.json` an enrollment awaiting confirmation. Original envelopes and verdicts are not mixed into the working inbox. Implementation: `pc/muse-call.ps1`, `vm/hooks/bridge-postman.sh`, `vm/muselink_id/inbound.py`, `vm/bridge/muse-bridge-reply.sh`.

## 2 Requests and replies

An unsigned request uses `req_id`, `created_at`, `from`, `type`, `text`, an object `payload` and `reply_protocol`. `text` is request data; structured parameters go in payload. The inner fields of an identity envelope are in section 3.

| Type | Meaning |
| --- | --- |
| `ping` | Channel round-trip confirmation |
| `message` | Carries a message |
| `question` | Asks a question |
| `task` | Asks for a task to be handled; the specific action is still decided by authorization |
| `publish-release` | Handed by the resident service to the release pipeline |

Release payload: `version`, `package_path`, `sha256`, `package_size`, `notes`; optional `setup_path`, `setup_sha256`, `setup_size`, `test`. Facts are measured by the execution point on the target machine. `pc/muse-call.ps1` provides the matching parameters; the pipeline is in `vm/bridge/pipelines/publish-release.py`.

The retired name `publish-voicebridge` maps to `publish-release`. The PC entrance normalizes before signing; the VM's `RETIRED_TYPES` / `current_type()`, the policy entrance and the places that read queued requests accept the old input. The original signed text keeps its bytes, and business handling uses the normalized type. New requests send only the current name; the condition for removing the compatibility is in [RULES E9](../RULES.en.md#2-engineering-rules-people-and-agents-changing-this-project).

Reply fields: `req_id`, an incrementing `seq`, a boolean `final`, `status`, `replied_at`, `from:"muse"`, `reply_protocol` and `text`. `seq` starts at 1, and each reply is stored separately in the VM ledger. An explicit final takes priority; without final, `info` is an interim reply and `ok/error/needs-user` are final. After outputting an interim reply the client keeps waiting for the final one.

v1 has no version envelope and names the reply `<req_id>.reply.json`; when a request declares reply_protocol ≥ 2, the PC uses `<req_id>.<seq:03>.reply.json`. v2 introduced numbering and final-reply semantics; v3 keeps the numbering and adds identity signatures. Reading the old versions still works, and an enrolled agent cannot fall back to an unsigned request.

`muse-call` waits 1800 seconds by default, `-NoWait` only delivers, and `-Check <id>` collects again. `bridge-reply-status.sh <id>` reports `FINAL_READ`, `FINAL_DELIVERED_UNREAD`, `INTERIM_ONLY`, `FINAL_MISSING_ON_PC`, `NOT_SENT` or `PC_PROBE_FAILED` from the ledger and the PC location. The waiting discipline is in [RULES O9](../RULES.en.md#1-operating-rules-agents-that-operate-the-computer).

## 3 Identity v3

The outer request has exactly five keys:

```json
{"v":3,"req_id":"agent_20261113_120000_ab12cd","agent_id":"agent","signed":"<inner JSON text>","auth":{"alg":"HMAC-SHA256","key_id":"agent#1","sig":"<base64>"}}
```

The inner layer requires `req_id/agent_id/ts/type/text/payload/reply_protocol`; reply_protocol is 3 and ts is an integer in epoch seconds. Optional: `created_at/grant_id/thread_id/reply_to/client`; client contains only the strings `display_name/product/model/host/session_id`, each at most 200 characters. Self-reported information is for display. Unknown fields, duplicate JSON keys and non-finite numbers are refused; input is read as UTF-8 and a BOM is tolerated.

The signature is `base64(HMAC-SHA256(key, UTF-8(domain + signed)))` in standard base64 with padding. The key is a random 32 bytes and the signature is 32 bytes. The request domain is `muselink-req-v3\n`, the reply domain `muselink-reply-v3\n` and the record seal domain `muselink-seal-v1\n`. Verification uses the original signed text and does not re-serialize it, so the two sides need no agreement on JSON whitespace or key order. Vectors: `spec/test-vectors/identity.json`.

The check order is implemented by `envelope.py` and `inbound.py`:

| Order | Check | Error code |
| --- | --- | --- |
| 1 | File size, UTF-8 JSON object | `oversize/unparsable` |
| 2 | Outer keys and types, file name equals req_id | `bad_outer/id_mismatch` |
| 3 | legacy, enrollment and signature branches | `legacy_disabled/downgrade/unsigned` |
| 4 | Algorithm, key_id prefix, strict base64 and length | `bad_alg/bad_key_id/bad_encoding` |
| 5 | Enrollment state, seal, current key | `unknown_agent/agent_revoked/registry_tampered/unknown_key/retired_key` |
| 6 | Constant-time MAC comparison | `bad_signature` |
| 7 | Parse the inner layer, field types, inner and outer IDs agree | `bad_inner/id_mismatch` |
| 8 | Freshness | `stale/future` |
| 9 | seen table deduplicates by ID and digest | `duplicate` for the same digest, `conflict` for a different one |

For an enrolled principal, a suspected impersonation error (`bad_alg`, `bad_key_id`, `bad_encoding`, `unknown_key`, `retired_key`, `bad_signature`, `registry_tampered`) writes an alert and gets no reply; an inner, freshness or conflict error on an already verified request gets a signed error reply. A duplicate is not delivered again. The enrollment rate-limit error is `enroll_rate_limited` and an internal failure is `internal`. The complete error set is in `vm/muselink_id/__init__.py`.

Enrollment is the only v3 request with auth=null; its inner type is enroll, and the key is issued only after the user confirms the card. An agent whose enrollment is not approved has no key, so the VM can only reply unsigned; `muse-call -Enroll` ends its wait as "not approved" on an unsigned, non-envelope, final error reply with this request's ID and sequence number, which stays in quarantine and grants nothing. A revoked agent that enrolls again gets the rekey card, which states that it was revoked; if the user agrees it gets a new key and is active again, while its earlier grants stay revoked. An ordinary request without a version is handled by legacy_unsigned as transition or reject, and a from that is already enrolled is refused as a downgrade.

A v3 reply has the outer fields `v/req_id/seq/agent_id/signed/auth` and the inner fields `req_id/seq/agent_id/final/status/replied_at/from/reply_protocol/text`. The client checks the algorithm, key_id, MAC, key set and the expected ID and sequence number; a failure shows UNVERIFIED and moves the reply to quarantine, and does not count as final. Implementation: `pc/muselink-identity.ps1`.

The identity store is in `$MUSELINK_ID_HOME` (default `~/.config/muselink/identity/`): `agents.json` and `grants.json` carry seal records; `seal.key`; `state.sqlite` holds seen, usage, reservations and revocations; `audit.jsonl` holds the structural audit. POSIX directories are 700 and files 600, and it refuses to run when the account or the permissions do not meet the requirement. The PC identity file `<tool directory>/identities/<agent_id>.json` is `{v:1,agent_id,key_id,key,enrolled_at}`, transferred as a file with a restricted ACL; keys never enter the mailbox or a command line. The mirror holds no keys and is not used for authorization decisions.

## 4 Policy and authorization

Policy is generated from the site configuration, and `policy.py` validates every key; an unknown key or an illegal value makes loading fail. Types are normalized first and then classified. The classes are conversation, task, privileged and red_line; agent tiers are whitelist, ask-first and legacy.

| Class | whitelist | ask-first | legacy |
| --- | --- | --- | --- |
| conversation | allow | allow | allow |
| task | allow | ask | allow |
| privileged | grant_or_ask | grant_or_ask | ask |
| red_line | ask | ask | ask |

The table is the template policy; an actual installation follows the policy it generated. Red lines include payment, delete-data, credential-change, third-party-message and network-change; the loader accepts only ask/reject for them, and only ask/reject for unknown types. Verdict names are `allow/allow_with_grant/delay/ask_user/reject`. An execution point re-verifies the retained original envelope and cannot pass on the verdict alone or on the auth mark in the request.

Grant fields include `grant_id/agent_id/action/conditions/not_before/expires_at/max_uses/mode/auto_match/note/status/seal` plus the proposal and approval records. Grants are issued only for privileged actions, and conditions are all ANDed; the operators are `equals/iequals/prefix/in/min/max/path_under`, a missing fact does not match, and booleans are not treated as numbers. On Windows, path_under works on separator boundaries, folds ASCII case only, and refuses relative paths, UNC and device paths, parent traversal, data streams and ambiguous trailing characters. Facts come from measurements at the execution point.

A grant is proposed with `muselink-id.sh grants propose` and created through a structured card. The normal CLI offers no direct activation path. Usage is deduplicated by grant × req_id; the same ID with the same action and the same facts may be retried, and a different action or different facts reports `request_reused`. A delay reserves first and confirms validity, revocation and agent state before execution; cancelling releases the usage, and an expired reservation reports `reservation_expired`. Either the JSON state or a database revocation alone blocks use, and revoking an agent also revokes its grants. Code: `grants.py/flows.py/registry.py`.

An ask card allows only through an explicit allow option; closing it, a timeout or a card that never appeared does not allow. Once a delay card has appeared, the action may be allowed immediately or cancelled within the window, and a timeout allows it through the delay mechanism; a busy card slot or an unreachable PC does not start a delayed allow. The enrollment card offers "Deny / Ask first / Whitelist", and self-reported text has control characters removed, is length-limited and is labeled with its source. Enrollment is handled one request at a time, and an owned card can be resumed; a busy card slot does not consume attempts. The optional exemption for similar requests is created by grant_templates as a structured grant.

Default values and formats are collected in section 8. Command exit codes: 0 success or allowed, 2 usage error, 10 ask the user, 11 delayed, 20 rejected, 30 internal error. The specific content of the standing orders is read from the user's private file, and the general handling is in `vm/bridge/PROTOCOL.en.md`.

## 5 Threads

A request's thread_id decides grouping, and reply_to points at the req_id being answered; in v3 both are inside signed, and in an unsigned request they are at the top level. When a thread ID is invalid, the index treats it as absent while the client refuses to send. The default key is `<agent>/<topic>`: agent is the identity agent_id or the old from, topic is release for publish-* and chat for the rest; a reply follows its request.

A message's identity is req_id and a reply's is req_id#seq (0 for v1, which has no sequence number). Copies in multiple directories count once, and the request body comes from sent first. Request state is computed from the files: inbox=delivered, claimed/pending=claimed, an interim reply=acked, a final reply=replied or needs_user, rejected=rejected. done proves the final reply was read even after it has been removed, and final_read can also be proven by an archive final. A thread with an item that needs the user is waiting_user; otherwise a thread with an item that is not final is in progress, and the rest are complete.

The index only reads files and does not verify signatures; an archive reply is marked read, outbox is marked unread_unverified and quarantine is marked rejected_by_client. The index is not a basis for authorization. Code: `pc/mlindex/model.py/sources.py/indexer.py`; the window mechanism is in architecture section 7.

## 6 Popup file protocol

In the tool directory, `popup-text.txt/mode.txt/options.txt/expected-hash.txt` are staging input, and mode is info/choice/input; `popup-expires-in.txt` is an optional relative expiry in seconds. stage checks the file SHA-256 before preparing the card. `popup-reqid.txt` and `popup-pending.txt` bind this request's ReqId; `popup-armed.json` gives the desktop pet `{req_id,mode,summary,at}` and is distinguished from a local task wait.

Completion writes `popup-result.txt` and `popup-kind.txt`, and `popup-done.txt` last: a timestamp on the first line and `REQ:<id>` on the second. Text is UTF-8, and the completion text has no BOM. The completer checks the ReqId and applies first come, first served under a mutex; an existing done for this request is not rewritten and the exit code is 3. The main wait and the fallback read under the atomic pending claim mutex. The result output contains STATUS, RESULT, KIND and other lines, parsing is split by label, and the DELIVERED status that follows is not the answer body.

kind distinguishes sources such as info/choice/typed/input, pet_done/pet_typed, expired, web_ai_done and task results. A generic desktop pet completion is not misread by the authorizer as an explicit authorization option. The two-sided display response is in architecture section 8. Code: `Complete-MuseWait.ps1/wait-popup.ps1/check-popup.ps1`, `vm/popup-send.sh/popup-wait.sh`.

The VM inflight marker lives in the state directory, is parsed by field and is not sourced; it contains the request, the time, the mode, the summary and an optional owner. An owned card is awaited again by its owner, and the fallback does not take in its answer. When the main path has not delivered an ordinary card, the fallback claims it or reports it expired.

## 7 Hook contract

One terminal decision per tick, and one wake per request or per alert. A platform adapter declares `ML_HOOK_ADAPTER_CONTRACT=2`; with a platform runtime it calls silent/wake/log, otherwise it prints `SILENT: ...` / `WAKE: ...`. `HATCH_HOOK_DRY_RUN=1` or `MUSELINK_HOOK_DRY_RUN=1` only reports: it writes no state, claims nothing, moves nothing, re-pulls nothing and cleans up nothing.

| Detector → platform hook | Wake reason | payload |
| --- | --- | --- |
| bridge-postman → codex-bridge-watch | codex_message | req_id, local_path, type, created_at, the first 400 characters of text, payload or parse_error; the identity extension adds agent_id/auth/decision/reason/verdict_path |
| popup-fallback → popup-wake-fallback | popup_recovered | req_id/mode/summary/kind/done_at/result |
| Same as above | popup_expired | req_id/mode/summary |
| bridge-alert-watch → bridge-alert-watch | bridge_alert | alert_id/path/the first 600 characters of text/written_at/more/attempt/max_attempts/ack |
| bridge-selfheal (when deployed separately) | selfheal_failed | log |

The postman stays silent when there is no letter, the PC is unreachable, a pull fails, a name is bad, routing fails, a request is rejected or duplicated, it awaits enrollment, or the pipeline has taken over; the card hook stays silent when a marker is missing or bad, during the grace period, when the card is consumed, still waiting or owned, or when the probe fails; the alert hook stays silent on the first baseline, with nothing new, while waiting for a confirmation, or when the scan fails. Alerts must be confirmed: after reporting to the user, the handler runs the ack command from the payload (`vm/alert-ack.sh <alert_id>`; `--list` shows unconfirmed alerts), and only then is the alert done. An unconfirmed alert is woken again every 600 seconds, at most 3 times in all; if it is still unconfirmed 600 seconds after the last wake it is recorded as done and the hook log gets gave_up, while the alert file stays as it is. The state is in `$ML_STATE_DIR/bridge-alert-watch.seen` and `.pending.json`, under one lock shared by both scripts; `MUSELINK_ALERT_RETRY_S` and `MUSELINK_ALERT_MAX_WAKES` change the interval and the count. Wake fields stay compatible with existing platform prompts, and new fields may only be appended. Wrappers, detectors and adapter functions are in `vm/hooks/`, and the matching installation is in install section 5.

## 8 Protocol constants

| Item | Value and single code source |
| --- | --- |
| agent_id | `^[a-z][a-z0-9-]{1,31}$`; reserved names are in `muselink_id/__init__.py` |
| key_id / grant_id | `<agent_id>#<1..6-digit positive integer>` / `g-<12 hex digits>`, same as above |
| req_id | `^[A-Za-z0-9][A-Za-z0-9_-]{2,95}$`, matched in full on both sides |
| thread_id | `^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$`, `mlindex/model.py` |
| Envelope and freshness defaults | 1 MiB; maximum age 604800 seconds, future offset 600 seconds; seen is kept for 691200 seconds, `policy.py` |
| Authorization default limits | Grant 30 days / 20 uses; enrollment 3 per hour; enrollment and ask card 1800 seconds, template policy |
| Delay window | 120 seconds by default, the loader allows 10–120; a reservation is kept for an extra 300 seconds, `policy.py/grants.py` |
| Executor kind | ping/info/phase/ad/sem/uia/seq/script/shell, `executor-host.py` |
| Screenshot gate | Above 400 KB, convert to JPEG, quality from 80 down to 50, `muse-shot.ps1` |

Site fields are installation configuration; protocol fields and file names are the contract shared by both sides, and a change updates the implementation, the vectors and the contract tests together. The engineering process is in RULES.
