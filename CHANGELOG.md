English | [中文](CHANGELOG.zh.md)

# Changelog
## 2026-11-16 Mailbox chat ergonomics, popup bolding and open-source readiness
- Desktop pet: frosted glass busy pill without protruding tails (centered below by default, smoothly clearing character and orbs when blocked below); zero-occlusion `bubbleRect()` placement for waiting asks; aligned `done` beside `talk` with 20 Hz hover leave verification; tightened DWM shadows, lifted talk card, top-layer `.pet-celebrate`, open-eyed white-background pet icon across tray/windows/avatars, left-click pet opening the `Muse` session directly, and a right-click `Set avatar` entry below `Exit`.
- Mailbox window: WeChat-style layout with Muse on the left and local agents + user on the right, official/classic AI icons (including white-background black-line Codex and official dark-charcoal OpenCode), safe pure-DOM Markdown bubble rendering, pinned `Muse` (`pet/chat`) without noisy section labels/message counts/passive status pills, chronological ask-before-answer popup logs with humanized authorization summaries, all-thread composer waking Muse in-thread with instant bottom scroll & poll scroll preservation, generic project-name topic badges & release follow-up consolidation, right-click conversation deletion to `trash/`, right-click user avatar `Set avatar` in chat, native taskbar minimize/restore with the MusePet icon, and retirement of legacy logon mailbox tasks/processes.
- Popup cards (`pc/popup.ps1`): normalized LF/CRLF paragraph wrapping with exact line-height sizing, and read-only `RichTextBox` rendering bold emphasis for `**...**` spans, lead labels and quoted targets while preserving selection and `Ctrl+C`.
- Experience library & open-source readiness: share prompts accumulate 5 verified local entries per prompt and stop permanently once declined; added `CONTRIBUTING.md` / `CONTRIBUTING.zh.md`, updated bilingual documentation, and verified full repository sanitization (`tools/check-public.py` 0 findings).

## 2026-11-14 Pet UI and the card stamp
- After the answer is saved, the card shows a stamp: a light veil, a green badge dropping in and a white check, then it closes 0.5 seconds after the animation; answers on the card and from the pet (pet_typed) look the same.
- A right-click card menu on the pet (hide, exit, language), with the chosen language also used by the card; renamed hover buttons with a retract animation; a new icon.
- The chat panel title is just Muse; the pet plays the waiting row while the user types; "Received" loops the note-taking row for about 3 seconds.
- `vm/phase.sh` lets Muse send its current stage to the pet's bubble; executor 4.2.0.
- Feedback on public experience is kept in a local overlay and never rewrites the public entry; an `ml_fix_owner` failure no longer aborts the calling script.

## 2026-11-14 Scale, browser channel and tool choice

- `pc/pet/ui/layout.json` separates UI scale 1 from sprite scale 0.7: the character keeps its bottom/center frame anchor while cards, panels, status marks and orbs use full design size in a 340×440 logical-pixel window.
- Browser control prefers the official Muse browser extension and asks before adding it.
- The semantic find budget defaults to 30 seconds, raised to 50 with `find_timeout_ms`; executor 4.1.0.
- The experience library keeps a tool-choice entry per application, looked up before acting; Kiro is the first.

## 2026-11-14 Alert confirmation and the session entry

- Bridge alerts are confirmed with `vm/alert-ack.sh`; an unconfirmed alert is woken again every 600 seconds, at most 3 times.
- The pet's Session button first launches the installed Muse web app, then the browser and profile from the site configuration, and finally the system default browser opening https://muse.ai/.

## 2026-11-14 Fixes from hands-on run §5

- The release upload task counts only non-null triggers, so a task with zero triggers passes the readiness check.
- The 30-minute consent answer is saved before the busy-desktop recheck.
- An unsigned "not approved" enrollment reply ends the `-Enroll` wait; a revoked agent is restored through a rekey card that states the revocation; a reply that cannot be sent is logged and alerted.
- The plugin installer writes only the configuration of the agent's own client, keeps Codex tables with backslashes, and creates the Kiro / Codex file when needed.
- Right-click hide on the pet is no longer blocked by the per-second refresh; "Received" waves once; recentering keeps the stacking order.
- `exp.sh confirm`; site configuration comparison treats an empty object as absent.

## 2026-11-13 Bridge service unit

- The unit carries `MUSELINK_HOME` and `MUSELINK_CONFIG`, so the service finds its configuration when User='s passwd home differs from the configured home.
- Install and self-heal render the unit through one script, `vm/bridge/render-unit.sh`.

## 2026-11-13 Repeatable installation

- The installer skips items that already match by existence, hash, configuration and task definition, and offers a force-redo parameter.
- A new tool directory on the system drive on the PC needs explicit permission; the preview lists non-system fixed disks and their free space.
- agent-desktop uses an existing location or installs inside the tool directory, and pip writes no cache.

## 2026-11-13 One site configuration and repackaging

- Five templates are merged into a single site configuration; both sides compare effective content and generate configuration from it.
- The release package is exported from one commit with a per-file SHA-256 manifest; installation keeps local state.
- Releases use a generic request name; the old name is normalized at the entrance.

## 2026-11-13 Experience library and local plugin

- Adds MCP/CLI adaptation, per-application consent, Muse priority and a structured trace.
- Adds experience drafting, confirmation, lookup, feedback, public sync, redaction and per-entry sharing.
- The share card shows the same snapshot field by field; an entry that cannot be shown in full is not offered for sharing, and a draft must be confirmed first.

## 2026-11-13 Recoverable enrollment

- The enrollment answer and the status line are parsed separately; a card reply can be collected again.
- One card owner is kept until the flow ends; a busy card slot does not consume enrollment retries.

## 2026-11-12 Mailbox window and desktop pet

- Adds a read-only mailbox window, background index sync, a tray entry and deep links.
- Adds a separate desktop pet process, work status, channel checks, replies from either side and a confirmation sound.
- Front end and back end are connected by a JSON contract and file events.

## 2026-11-12 Identity v3 and threads

- Adds per-agent signatures, enrollment, policy, sealed grants, replay checks and audit.
- Adds the thread index, full-text search, request status and a display of reply trust.
- Aligns the wake fields of the postman and the card hook, and adds an alert hook.

## 2026-11-12 Execution and transfer mechanisms

- The executor adds a token, a script allowlist, deadlines and input coordination, with shell off by default.
- Replies are stored by sequence number; the client waits for the final reply and keeps a read receipt for each one.
- Cards add focus protection and request binding; web notification adds token and origin checks.
- A release measures file facts before authorization, and verifies each chunk and the final download; adds maintenance and deployment scripts.
