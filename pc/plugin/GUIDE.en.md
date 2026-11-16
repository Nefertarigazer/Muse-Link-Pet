English | [中文](GUIDE.zh.md)

# Local agent guide

This file is read from the tool directory at runtime. The MCP entrance is `python -m plugin.mcp_server --agent <enrolled ID>` and the CLI entrance is `python -m plugin <tool> --agent <ID> --args-file <UTF-8 JSON>`; both share validation, the consent gate, ownership and audit. Use args-file for Chinese arguments, to avoid the stdin encoding conversion in Windows PowerShell 5.1. Operating rules are collected in [RULES](../../RULES.en.md), and cross-application methods are in the [operating guide](../../docs/operating-guide.en.md).

## Tools and tiers

| Tier | Tool | Parameter summary |
| --- | --- | --- |
| read | observe | app; optional mode=skeleton/digest/full, surface |
| read | resolve, verify | app, query; verify takes optional should=present/absent, surface |
| read | uia_locate, uia_read_text, uia_focused | app; optional query |
| read | shot, ping | mode=window/screen for shot, window needs app; ping takes an empty object |
| input | act | app, query; optional action/key/nth/surface/expect |
| input | uia_focus, uia_invoke | app; optional query |
| input | seq | steps; timeout 150 seconds by default |
| ask | ask_user | text; mode=input/choice/info; choice carries 1–4 options; optional expires_in |
| ask | notify_user | text; optional expires_in, silent and does not wait for an answer |
| read | exp_lookup | app; optional goal/ui/tags, at most three |
| local | exp_record, exp_feedback | draft or confirm one experience entry; feedback takes id, worked |

A semantic query supports name/exact/role/path_contains/path_excludes; a UIA query supports name/exact/controlType/classContains. expect supports query/should/text. seq accepts semantics, UIA, sleep, act_if/unless and alternatives; every application it references needs consent. Step-level job/token/deadline and raw shell/ad/script do not pass plugin validation.

`--agent` refers to an enrollment file under identities in the tool directory. Without enrollment only read tools are listed; enrollment establishes local ownership, while the signing mailbox identity is handled by another entrance. On the first input the plugin asks per agent × application, "Allow once / Allow for 30 minutes / Deny", and only the first two authorize; closing the card, a timeout or the desktop pet's generic check does not equal permission. A policy can disable a tool or tighten it to input, but cannot downgrade input to read-only. The defaults are consent_seconds=1800 and muse_priority_seconds=15.

When non-plugin work happened in the recent window, input returns busy and retry_after_s; the plugin has no queue service, and the final input is still coordinated by the executor lock and by yielding to the user. jobs are recorded as `plugin:<agent>:<tool>`. Tier and consent code is in gate.py/tools.py, and the related rule is O8.

## Result discipline

Every tool returns `{status,verb,...}`; ok/resolved count as ok, fail keeps the error code, and unknown keeps the uncertainty plus effect/verification. Handling after a timeout, a disconnect or an unclear delivery is in RULES O3: observe/verify first, then decide the next step.

CLI exit codes: 0 success, 1 failure or the card expired or was superseded, 4 unknown, 5 busy, 10 denied or not enrolled. MCP puts the JSON in content[0].text and sets isError when it is not a success. Screenshot data goes only to the caller and is not written to audit/trace. The MCP initialize negotiation code is in mcp_server.py, and that file is the authority on the version set currently supported.

audit.jsonl records the time, agent, tool, application, selector, gate, status and duration; trace/<agent>.jsonl keeps the structural queries and status of the latest 50 calls. Neither records input text, expected text, read-back, the clipboard, card text, options, answers or screenshots; selector names are kept. The rules for storing logs and local state are in RULES E3.

## Asking etiquette

Per RULES O4/O8, look up what can be looked up first, then ask the missing item with one card. ask_user defaults to 600 seconds and accepts 10–3600 seconds; it returns kind/result/req_id. A busy card slot returns busy; Muse can supersede a local card, and the original call reports preempted. notify_user is an information card that does not ring and returns immediately, has a bounded subprocess for reclaiming slots, and is not used to approve actions.

Browser control follows RULES O12: check for the official Muse browser extension first; when it is absent, ask with an `ask_user` choice card whether it may be added; no answer, closing or expiry is not consent, and `notify_user` cannot replace the question. Steps: operating guide section 3, Chrome.

## Contacting Muse

Run muse-call.ps1 or muse-call.cmd in the tool directory; write long text to a UTF-8 file:

```text
muse-call.cmd -As <agent_id> -Enroll -DisplayName <display name> -Product <product> -Model <model>
muse-call.cmd -As <agent_id> -Type question -TextFile <body file>
muse-call.cmd -As <agent_id> -Type task -TextFile <body file> -NoWait
muse-call.cmd -Check <req_id>
muse-call.cmd -ListPendingReplies
muse-call.cmd -As <agent_id> -ListGrants
```

Enrollment waits for the user's real choice, and a request is signed only after the identity file arrives. Keep the req_id for one request; the WAITING_FOR_MUSE heartbeat means it is still waiting, and after the MUSE_CALL_TIMEOUT timeout collect again with Check. `-NoWait` changes only the call's wait and does not cancel the request; after reading an interim reply, the final reply is still awaited. `-Thread` / `-ReplyTo` group requests and `-Grant` presents a grant. For release follow-ups (verification, manifest fixes, confirmations), pass `-Thread <agent>/release` or `-ReplyTo <release_req_id>` so they stay in the release thread instead of opening a separate chat. The specific fields, release parameters, identity verification and error codes are in [protocol](../../docs/protocol.en.md), and the general waiting conventions are in RULES O9.

## When to record experience

The call order and the draft confirmation steps are in [operating guide section 5](../../docs/operating-guide.en.md#5-when-to-record-experience-and-how). The local trace can draft a structural method, and the agent explicitly fills in the missing text; the local tier calls no executor and raises no input consent card, and only enrolled agents may write the local library. Methods are never executed automatically, and sharing goes through its own per-entry silent consent card. The mechanism is in architecture section 10, and the rules are O1/E11.

## Installation

`plugin/install.ps1 -Agent <id> [-Client codex|kiro|claude]` previews the configuration of the client this agent runs in (the Codex TOML, the Kiro JSON, or Claude Code's ~/.claude.json and command) and the handling of existing configuration; one client configuration holds one muselink entry, that is one agent, and -Client may be left out when the ID is the client name. `-Apply` merges into the user configuration file, keeps other entries and existing muselink entries (naming the agent when the entry belongs to another one), and writes no backup when the content does not change. Tables under other roots whose names contain backslashes in the Codex file (such as project paths) are kept as they are. A missing Codex or Kiro file is created when that client's directory exists, and any other missing file is skipped; -Force explicitly backs up and rewrites; -ConfigHome allows verification against a temporary directory. It prefers the mailbox venv and then looks for the local Python, and reports an error when neither is found.

The Codex entry carries tool_timeout_sec=3620, and the call budget of other clients must also cover the card lifetime of a request. The client's own tool approval and the MuseLink consent card each apply, and the installer keeps the client's approval policy. The overall install flow and the basis for skip if already installed are in install section 6; development testing follows RULES E1.
