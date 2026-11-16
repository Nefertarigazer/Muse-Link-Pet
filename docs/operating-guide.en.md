English | [中文](operating-guide.zh.md)

# Operating guide for a computer

## 1 Check the experience library first

Query with the target application's actual process name, for example lowercase `notepad.exe`. A local agent uses the plugin's `exp_lookup(app,goal,ui,tags)`; Muse uses `vm/exp.sh lookup <app> [goal]`. A hit includes the method, the verification steps, the status, the origin and the days since the last verification; choose according to the current UI. An entry tagged `tool-choice` records which tool works well for that application (agent-desktop, native UIA, winappCli and so on, with measured times and required parameters): when one exists, choose the route from it instead of failing once on the default channel first. Call parameters are in the [plugin GUIDE](../pc/plugin/GUIDE.en.md), the status mechanism is in architecture section 10, and the related rule is [O1](../RULES.en.md#1-operating-rules-agents-that-operate-the-computer).

## 2 Order of channel selection

For browser tasks, first select the official extension channel under O12 and the Chrome workflow below; use a browser fallback only when the user declines or cannot add the extension. For desktop applications, start with semantic observation, fill gaps with UIA, organize actions with seq, and localize from a current screenshot last:

```text
sem.sh observe <app> skeleton
sem.sh observe <app> digest
sem.sh resolve <app> <name> --exact
uia.sh locate <app> <name>
seq.sh <job.json>
shot.sh window <app>
```

In sem, resolved means an operable target has been located; not_operable means there is a match but no ref, so UIA can take over. UIA queries by name/controlType/class, and the current element description then decides between focus, invoke and read-only text. seq merges steps that are already clear, with conditional guards and post-verification; it organizes execution and does not replace target identification. When only a screenshot can express the target, take the physical pixels and the local coordinate mapping from the image just saved, and capture evidence again after the action. The mechanism is in architecture section 3, and the related rules are O2/O3.

## 3 Standard actions

### Screenshots

Muse: `shot.sh window <application name>` or `shot.sh screen`. Read the local image path and metadata on the last line and check the actual dimensions, format and saved byte count; the native PNG stays in the shots directory. When the window is minimized, the entrance restores it for the screenshot and minimizes it again. The compression gate for image transfer is in protocol section 8.

### Reading text

Use whatever data interface, DOM or UIA TextPattern the current application offers first. The return of the local plugin's `uia_read_text` is the read evidence; in a web page, locate the content within the current conversation node, and for a long generation combine the generation state, the completion marker and a fresh screenshot. For a keyboard copy, follow the clipboard gate in section 4 to save, focus, copy, read and restore. Look up selectors and methods for a specific application in the experience library instead of storing a second copy of the same recipe in this guide.

### Chrome

First check the target browser, actual profile and installation state of the official Muse browser control extension (Chrome extension). If installed, verify and reuse its official connection without reinstalling or asking to add it again. If absent, explicitly ask: "May I add the official Muse browser control extension (Chrome extension) to your browser?" Offer "Add it / Do not add it, use the fallback / Cannot add it, use the fallback" on one card and wait for a real answer; silence, dismissal and expiry select nothing automatically.

After consent, guide installation through the official entry provided by Muse and ask the user to confirm the target tab as the official channel requires. Verify the connection by reading the target page, then check the account, model tier, input box and conversation area before acting. The official extension is the first choice for browser control. On connection failure, check the current state and report it; do not interpret failure as refusal or switch channels on your own.

Only if the user declines or cannot add it (for example, unsupported browser or organization policy) explain why and use an available package-side fallback such as agent-browser / Playwright. If CDP is needed, check the current connection and permissions and obtain target-tab approval as the tool requires. Reuse a verified connection; do not open another browser or debugging port to skip the question. Installation checks and configuration boundaries are in install section 0.

To open a window for a specific profile, pass the launch arguments to Start-Process as one string, quoting a profile name that contains spaces in full:

```powershell
$chromeArgs = '--profile-directory="' + $profile + '" --new-window "' + $url + '"'
Start-Process -FilePath $chromeExe -ArgumentList $chromeArgs -WindowStyle Hidden
```

All three variables here come from the target machine and this task; the background launch form follows RULES O7. This launch example does not change the control-channel priority above. If a fallback needs CDP, a DevToolsActivePort file may be left over, so confirm it against the current process and a cheap liveness probe before connecting. Call tool dependencies according to what is actually installed in the target environment.

### Desktop applications

Use `executor.sh ad list-windows` to check the actual windows and apps, then observe. Resolve again once a menu surface or overlay appears; when a decorative image shares the name, decide from the operable candidates. Example actions: `sem.sh act <app> <name> expand --exact`, then `sem.sh verify <app> <menu item> present`. Once UIA focus returns focusVerified, a verified keyboard channel may follow; when a control supports only SelectionItem, focus it first and then use the application's own activation key.

### Files

Prefer a file API or the local index within the scope the task gives; when nothing is found, check the query scope and the permissions, then switch to a second route. File facts go through the argument-file interface of `file-facts.ps1`, called through an allowlisted script; complex paths and Chinese bodies use a UTF-8 JSON file. After a transfer, compute the size and SHA-256 on the target machine, and collect progress through short requests with a deadline. The tools do not infer a directory that does not exist on the user's behalf.

### Waiting for the user and for local tasks

Use the standard card entrance below for an interactive request; wait for a local pid/file/cmd/web with `start-wait.sh --kind ...` (the parameters are defined at the head of the script). Keep the ReqId after sending, and when the main path disconnects check the wait state and the target process first; collect an owned card again with popup-wait.sh. The result and exit-code semantics are in protocol section 6, and the related rules are O3/O8/O9.

### Telling the pet what you are doing

While working on the PC, send `phase.sh "<short phrase>"` at each new stage, for example "Packing the weekly report". The host appends one phase event, and the pet shows the newest phrase in its bubble while the PC is working, clipped to 60 characters. The phrase appears on screen: no secrets, accounts or user text; there is no need to send one for every step.

### Reaching the user with a card

Prepare a UTF-8 body and an optional one-option-per-line file:

```text
popup-send.sh --mode input --text-file <body file> --summary <one-line summary>
popup-send.sh --mode choice --text-file <body file> --options-file <options file>
popup-send.sh --mode info --text-file <body file> --no-wait
```

For a short question, use `popup-ask.sh <body> [options...]`. The script handles the file transfer, stage, trigger and wait, and returns this request's result once the user answers in the card or the desktop pet. A local agent uses the plugin's ask_user/notify_user instead. Display, expiry and the two-sided behavior are in architecture sections 4 and 8.

## 4 Cross-application techniques

### Controls that appear only on hover

Find a stable parent region or text anchor first and take its current bounds; prepare one screenshot and a fresh resolve. After moving the pointer onto the parent region, rescan the tree and the menu surface while the hover still holds; if the target appears, operate semantically. When the new tree still lacks the target but the new screenshot shows it, finish the geometric localization from that screenshot. After the action, check the submenu, the chip or the page state; relocate once the window shape changes.

### Enter sends

Identify the application's send key and the current editor state first, and prepare the full text, the guard and the verification method; use a verified input channel and send the terminating key immediately at the end. After sending, look for the original text in the user message node or the task state; when the result is unknown, read back the current state, separate a fully unsent input, a partial input and a delivered one, and only then decide the next step. The basis for this rule and the retry boundary are in O3/O6.

### Clipboard gate

Read the formats and a restorable snapshot and record the sequence number; focus the target and confirm it is in the foreground; write this run's payload, and read the source back at the moment before pasting; check the target body after the action. When restoring, compare the current sequence and the content written this run, confirm that new data the user copied later is not overwritten, and then restore the original content in the same desktop session. Set-Clipboard from an SSH session and the desktop application may belong to different sessions. A complex format that cannot be restored completely goes to the user as in O5.

### Anchor localization

Use the question the user just sent, a unique title, a region name or a hierarchy as the anchor; read the anchor back to confirm it too, so the check does not hit leftover editor content. For a conversation generation, check the message node and the generating indicator; record the body length, whether the node is present, the completion state and the sampling time. Take coordinate anchors from this run's original image, recompute them after a DPI, scroll or window-size change, and do not carry over an offset constant from one machine.

### Starting from analogy

Look up this application's existing entries and the interface structure verified for similar applications, form a hypothesis about where to start, and then confirm it with one current tree or screenshot. For example, an Electron editor's usual attachment entry, mention menu and task sidebar can help choose the first region to observe; the application's own returned result decides the next step, and an analogy is not execution evidence. Related rules O2/O4.

## 5 When to record experience, and how

After solving a new situation successfully, record one entry for the current app; cross-application methods stay in section 4 and rules stay in RULES. When an existing entry covers it, give feedback and revise the original method instead of creating a synonymous entry.

Local agent: `exp_record(app,goal,from_trace=true)` drafts from the structured trace; check the draft's method and verify, fill in the necessary input that the trace did not save, and call `exp_record(confirm=<id>,edits={...})`. Muse has no local plugin trace, so it supplies the method explicitly with `exp.sh record <app> <goal> <method-file> <verify>`, checks the draft it returns, and makes it valid with `exp.sh confirm <id>`; a draft is not found by lookup and takes no feedback. After use, send `exp.sh feedback <id> worked|failed`.

Tool choice gets one entry of its own: goal "which tool to use for locating or clicking controls", tags include `tool-choice`, method lists each tool in order with how to query, measured time and required parameters (such as the find budget), and verify states a single candidate and the read-back after an action; keep one such entry per application and revise it with new measurements. Example: `library/entries/kiro.exe/`. Each entry contains the application, the UI, the goal, the steps, the verification and the date, and its status must reflect the evidence. The entry schema and the storage rules are in architecture section 10; the CLI uses `python -m explib <command> --args-file <UTF-8 JSON>`, and drafts/confirm can be reviewed one entry at a time. A public entry is a reference for a method; executing tools still goes through the current authorization and target verification. Sharing goes through its own consent flow.
