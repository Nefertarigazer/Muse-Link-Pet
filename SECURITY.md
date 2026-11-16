English | [中文](SECURITY.zh.md)

# Security mechanisms

## Assets and principals

Assets include the user's desktop and signed-in sessions, the clipboard and local files, the executor token, agent identity keys, the VM registry and grants, mailbox bodies, software waiting to be released, and shared entries. Principals are the user, Muse, enrolled local agents, unenrolled callers, and the Windows and VM accounts that run these programs.

Within the permissions the user's accounts already have, the system provides source verification, authorization decisions, input coordination and audit. The account itself protects the disk, sessions and keys; a process running under the same account can read the data that account can read, call tools and operate windows. The VM run account administers the identity store; seals reveal rewritten records, and the storage layer checks the identity directory permissions.

## Entrances and gates

| Entrance | Gate | What it guarantees | Code and contract |
| --- | --- | --- | --- |
| VM to PC | tailnet policy, SSH user keys, host fingerprint, connection parameters | The peer and the login account are fixed by the network and SSH configuration | `vm/lib/muselink-common.sh`, `vm/ts-connect.py`; [install section 1](docs/install.en.md#1-networking) |
| Local executor | Loopback-only listener, random token, token ACL, request deadline, input lock; shell is off by default and script is allowlisted | Calls through the normal interface hold the token, desktop input is coordinated by the lock, scripts follow the configuration | `pc/executor-host.py`, `executor-call.py`; [architecture section 2](docs/architecture.en.md#2-executor) |
| Mailbox requests | Full ID, size and strict JSON checks, identity v3 signature verification, freshness and replay table | Confirms the signing source and the byte integrity, and separates a duplicate delivery from a conflict on the same ID | `vm/muselink_id/envelope.py`, `inbound.py`; [protocol section 3](docs/protocol.en.md#3-identity-v3) |
| Enrollment and execution authorization | User enrollment card, tiered policy, sealed grants, conditions and usage, envelope re-verification at the execution point | A self-reported name is used for display only; a privileged action is decided from structured facts and a valid grant | `registry.py`, `policy.py`, `grants.py`, `flows.py`; [protocol section 4](docs/protocol.en.md#4-policy-and-authorization) |
| VM replies | Separate signature domain, checks the request, agent and sequence number; a reply that fails moves to quarantine | A reply is bound to its request; an unverified reply does not end the wait | `pc/muselink-identity.ps1`, `muse-call.ps1`; protocol section 3 |
| Cards and waiting | File hashes, ReqId, completion mutex, pending claim, expiry and owner | The displayed content matches the file that was passed in, a late answer cannot land on another request, and the first completion from either side takes effect | `popup-stage.ps1`, `Complete-MuseWait.ps1`; [protocol section 6](docs/protocol.en.md#6-popup-file-protocol) |
| Local plugin input | Enrollment ownership, agent × application consent, tier tightening, Muse priority, audit | Consent is obtained per application through the plugin interface and input yields; a timeout does not replay input | `pc/plugin/gate.py`, `tools.py`; [GUIDE](pc/plugin/GUIDE.en.md#tools-and-tiers) |
| Web completion notification | Loopback service, one-time token, Origin allowlist, ReqId and deduplication | Only the web origin and the token that the current wait recognized can submit the matching result | `pc/web-bridge.ps1`, `userscripts/muselink-web-ai-notifier.user.js` |
| Software release | Path validation, file facts on the target machine, authorization, remote hash after upload, public manifest read-back | The release flow checks the declared value, the measured value and the final downloaded value against each other | `vm/bridge/pipelines/publish-release.py`, `pc/file-facts.ps1`, `chunked-upload.ps1` |
| Experience sharing | schema, automatic redaction and literal word list, full-text batch card (per 5 verified entries), "Share" / "Decline (don't ask again)", outbound snapshot | No outbound entry is produced without consent for the batch; declining stops prompts permanently; submitted bytes match the card snapshot | `pc/explib/redact.py`, `share.py`; [architecture section 10](docs/architecture.en.md#10-experience-library) |

## Responsibility for data and authorization

An identity signature proves the key holder and the content; authorization is decided separately by policy. The request `text`, the self-reported `client`, the keyless directory mirror on the PC and what the index displays are all data. A deterministic execution point re-verifies the original envelope; a task handed to Muse is carried out according to the [bridge procedure](vm/bridge/PROTOCOL.en.md) and the user's private standing orders. For red lines and unknown types, policy accepts only ask or reject.

Run logs record only the structured fields each module defines: the identity audit records neither bodies nor keys, and the plugin audit and trace record neither input text, read-back text, answers, the clipboard nor screenshots. Selector names stay in the trace; the field ranges are in the plugin GUIDE. Experience redaction covers every field of an entry; an entry the card cannot display in full is not offered for sharing. The public content check is described in [RULES E2](RULES.en.md#2-engineering-rules-people-and-agents-changing-this-project).
