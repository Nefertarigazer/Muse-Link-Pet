English | [中文](troubleshooting.zh.md)

# Troubleshooting

Each entry lists the symptom, the cause, the handling and the related rules. Code locations are in the linked mechanism sections; actual directories and addresses are looked up on the target machine.

## Networking and SSH

| Symptom | Cause | Handling | Related rules |
| --- | --- | --- | --- |
| Host key verification failed | The actual HOME, SSH configuration or known_hosts does not match expectations, or the fingerprint changed | Use ssh -v to see which files are loaded and check the fingerprint through a trusted channel; regenerate the configuration after fixing the site fields | O3, E4, E7 |
| banner timeout, PC offline | The local proxy path, the tailnet or a PC service is offline | Check the local proxy and tailscale status first, then verify through an authorized second route; on the PC check the Tailscale service, and where a watchdog exists read back its run result | O3, O9 |
| No proxy environment under the service, while an interactive shell works | The systemd/cron environment lacks the interactive proxy variables | Confirm a working PROXY_FALLBACK on the target VM, write it into the site configuration and regenerate; then re-check the service Environment | E4, E7 |
| The long SSH session ends and the task does not return | The session or the call budget for this run ended, and the target process may still be executing | Check the target process, the task log and the result file, switch to a detached task with short polling, and keep the original request | O3, O7, O9 |

## Executor and operation layer

| Symptom | Cause | Handling | Related rules |
| --- | --- | --- | --- |
| AUTH_FAILED | The executor token is missing or does not match, or the client origin is wrong | Check the token file and ACL in the target tool directory and do not copy the token into a report; confirm the bridge and the host belong to the same installation | O3, E3 |
| Unreachable, exit 2 | No listener, the task did not start, or it is not in the interactive session | Check the task action, the run account, the listener and the logs; restart and verify liveness per install section 4 | O7, O10 |
| Old and new behavior alternate | Several hosts, or a process still loading old code | Preview with restart-host and restart in a controlled way, verify zero listeners before a single start, and check the version fingerprint | O10, E8 |
| snapshot, status report PERM_DENIED: `private file DACL grants an untrusted principal` | The state directory's list gained SYSTEM, Administrators or a principal other than the current user. When Codex runs on the same machine, it is usually the `CodexSandboxUsers` read permission that the elevated sandbox initialization adds to every first-level folder under the user profile (the log `%USERPROFILE%\.codex\.sandbox\sandbox.<date>.log` contains `granting read ACE to ...`). Commands that do not read state, such as list-windows, still work | Locate the directory from the `state_root` that `status` returns. Immediate remedy: after `icacls <directory> /save <backup>`, run `icacls <directory> /remove:g <the extra group>` on the directory itself only, without /T. Permanent fix: move the state directory outside the user profile per install section 4, step 1 | O10, E5 |
| snapshot, status report PERM_DENIED: `private file is owned by a foreign principal` | The state file's owner is not the current run identity, which commonly follows creating or copying it under an elevated identity and then running at normal privilege | Run `install\fix-agent-desktop-owner.ps1` to report first, then `-Apply`; it changes only the owner and not the list, and backs up the permissions before it runs | O10 |
| sem not_operable, NOT_OPERABLE | The element found has no operable ref | Locate with the current UIA semantics and check the focus or the TextPattern; localize from a new screenshot when necessary | O2 |
| act AMBIGUOUS or several UIA matches | Overlapping names, decorative elements, several windows | Add exact, role, surface, path or an explicit index, then look at the current candidates | O2, O3 |
| Clicked, but without the expected effect | The control's role needs a different Pattern, or the window is in the background or the element changed | observe/verify again, try expand on a menu and toggle on a switch; decide the action from the current evidence | O2, O3 |
| UWP shows no content, a list cannot find an off-screen item | Background suspension withdraws the tree, a frame owns the root, or the list is virtualized | Bring the window to the front, check the actual window root, scroll until the target is visible and then relocate | O2 |
| busy, INPUT_LOCK_TIMEOUT, expired | Muse priority, the input lock is held, or the request is past its deadline | Wait per retry_after_s, check the state and submit the new action that is needed now; do not replay input whose result is unclear | O3, O8 |
| resolve, act or verify returns TIMEOUT on a large window (such as Kiro) | agent-desktop's live find reads the whole window before it calls a match unique, and a deep Chromium tree does not finish within the budget | First look up the app's tool-choice experience (operating guide section 2); the find budget defaults to 30 seconds and `sem.sh --find-timeout-ms` or the plugin's `find_timeout_ms` raise it to 50; or use `uia.sh locate`. Do not switch to `--first` or a cut depth, which give up the uniqueness proof. Stage timing: `tools/diagnose-sem.py` (development tree) | O1, O2 |

## Cards and waiting

| Symptom | Cause | Handling | Related rules |
| --- | --- | --- | --- |
| STAGE_FAIL or garbled text | The staging bytes or hash do not match, or the script encoding does not follow the convention | Prepare the input again through the popup-send file channel, check the UTF-8 and the hash manifest, and establish the current request's ownership first | O8, E10 |
| The card appears but typing does not go straight in | It is not activated by default and the keyboard guard is still up | Click the card to give it focus; when the default has to change, change popup.activate_on_show in the site file and regenerate | E7 |
| REQ_MISMATCH, an old card's answer mixes in | The result belongs to another ReqId | Check the request IDs in pending/armed/done and do not claim a wrong one; send only after the old flow is closed out | O8, O9 |
| Answered, but the main wait does not return | The connection ended, pending was consumed, or an owned wait needs collecting again | Check this request's done, owner and inflight; the owning program collects again with popup-wait, and for an ordinary card check the fallback heartbeat | O3, O8, O9 |
| The probe always says the card is still alive | The liveness condition matches the probe itself or a other scripts whose command line also contains popup (check-popup, wait-popup, popup-stage) | Check the target condition and the self-exclusion in Get-MLPopupProcess; verify with the case where the target does not exist | O3 |
| The card slot is busy and the enrollment card does not appear | Another request holds the slot, or the owner keeps a resumable card | Check the current owner and collect again after the original request finishes; a busy slot is neither a rejection nor a consumed retry | O8 |
| Whitelist clicked on the enrollment card but the flow did not finish | The old parsing merged the status line into the answer, or the wait was interrupted | Check the label-split parsing in the packaged ui.py and the enrollment log, and resume the existing request after the upgrade; do not edit authorization by hand in the mailbox | O8, E8 |
| The desktop pet reports completion but the input card does not close | The two-sided rules keep an input-capable card open to receive supplementary text | Check this request's result and the supplementary message path per architecture section 8 | O3, O8 |

## Mailbox and releases

| Symptom | Cause | Handling | Related rules |
| --- | --- | --- | --- |
| Only "received" appears, with no final result | An interim reply only, or the call's wait ended | Keep the req_id and use muse-call -Check; on the VM check where bridge-reply-status reports the final reply | O9 |
| Reported as read, but no final reply is visible | The whole request's state was inferred from the ACK location | Check outbox/ack/archive/done per the per-reply and final semantics of protocol section 2 | O3, O9 |
| UNVERIFIED, quarantine | The signature, the key, or the request or sequence number does not match | Check the matching installed identity and the update record, keep the original for verification, and keep waiting for a valid final reply | O3, O8 |
| STATE:REJECTED/PENDING_ENROLL | Routing rejected it, or it still waits on the enrollment card | Look at the state with Check, and on the VM judge from verdict.reason and the identity audit; collect again through the existing enrollment flow | O8, O9 |
| Muse inbox is empty while a PC request is unhandled | The PC is offline, the postman has not taken it, or the file is still in another stage | Check PC inbox/claimed/pending against the VM ledger and the hook heartbeat, and do not resend the same request | O9 |
| The reply file exists but the command prints nothing | PowerShell output was captured as the function's return value | Check the print path in muse-call that uses Console.WriteLine and collect again with Check | O3, O9 |
| The service disappears after the environment was replaced | The systemd unit has to be restored from the saved template | Check the self-heal log and the service master copy in the workspace, restore the unit per install section 5, and read back the run state | E8, O7 |
| The bridge service exits on every start, logging `config not found` | The unit lacks `MUSELINK_HOME` / `MUSELINK_CONFIG`, so the service looks for its configuration in the passwd home of User= | Check those two lines with `systemctl cat muse-bridge`; replace the unit with the one install.sh renders, per install section 5, and read back the status | E8, O7 |
| The release hash, size or version does not match | The declaration, the build file or the transferred artifact differs | Give the requester the measured and declared values, and check the original PC package, the remote assembly and the public manifest; once corrected, the authorization flow continues | O3, O8 |
| SHELL_DISABLED | An old action still calls the shell that is off by default | Move screenshots and file facts onto the existing allowlisted scripts and look up the specific action in the experience library; the user decides on any legacy compatibility that is still needed | O2, E7 |

## Windows, experience library and environment

| Symptom | Cause | Handling | Related rules |
| --- | --- | --- | --- |
| The mailbox window is blank | WebView2 is missing, the JS MIME type is wrong, or the front-end resources do not match | Check the runtime, the package manifest and the MIME table in shell.py; debug the front end in a temporary directory only | E1, E8 |
| The index does not match the files | The cache is not synced, or a file was skipped while it was moving | Run mlindex sync, rebuild when necessary, then verify --sample N; the original letters are not modified | O3 |
| An experience lookup returns no hit | A different app, a draft entry, or a path, source or schema mismatch | Check the normalized process name, drafts and Store.skipped; fix the entry per the schema and confirm it again | O1, E11 |
| No share card appears for an experience | Fewer than 5 valid entries have accumulated, the card is too long, redaction matched, an expired prompt is within its 24h cooldown, or the user declined (`never`) | Check the share-blocked/share state; the conditions are in architecture section 10, and consent is not bypassed by editing state by hand | O1, O8 |
| Zero bytes or a hash mismatch, as if data was lost in transfer | The target has no staging space, or the local write failed | Check the free space on both sides, on the VM check `df -h /tmp` and the working directory, and confirm the file was written before transferring | O3, E5 |
| Chinese text in a PS1 file turns into question marks or garbage | PS5.1 reads a script without a BOM as ANSI, and stdin may go through the default encoding | Verify the original by reading it as UTF-8, add a BOM to a non-ASCII PS1, pass parameters through UTF-8 files, and keep CMD at ASCII/CRLF | E10 |
| The program does not start after the task is registered | The action arguments, the session, the permissions or the launcher location do not match | Compare the task definition and the command line, use absolute script paths, and read back the process and the listener after starting | O7, O10 |
| git authentication fails in an SSH session | The credential is bound to the desktop sign-in session | Perform the authentication action in an authorized desktop session and read the result back from a file; do not print credentials in a report | O3, O7 |

The IDs map to [RULES](../RULES.en.md). Installation differences and recovery methods are in install section 8.
