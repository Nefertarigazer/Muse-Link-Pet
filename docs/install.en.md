English | [中文](install.zh.md)

# Install

The interface language follows the user's Windows display language automatically and needs no configuration.

## 0 Prerequisites

The target is a Windows 10/11 desktop used for everyday sign-in, a Linux Muse environment, an SSH client and Python. Windows UI modules use Python 3.10+ and WebView2; VM standard-library modules use Python 3.8+. Check on each target machine first, and keep an existing installation. Location rules follow [RULES E4–E6](../RULES.en.md#2-engineering-rules-people-and-agents-changing-this-project).

On Windows, list the local fixed disks and their free space with these commands, then choose the tool directory; `$env:SystemDrive` is the system drive, and no drive letter or path is prefilled:

```powershell
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
  Select-Object DeviceID,@{n='FreeGiB';e={[math]::Round($_.FreeSpace/1GB,2)}}
Get-Volume
Get-PSDrive -PSProvider FileSystem
$env:SystemDrive
```

| Component | Check on the target PC; skip the install when it is satisfied | When it is not installed |
| --- | --- | --- |
| Python | `Get-Command python.exe -All`; `python -c "import sys; print(sys.executable); print(sys.version)"`, excluding the Store placeholder entry | Run the Python installer and choose a directory on a non-system disk; check the actual interpreter again |
| Node.js/npm | `Get-Command node.exe`; `node --version`; `npm.cmd --version` | Where the Node installer offers a directory, choose a non-system disk, and do not change global npm settings |
| agent-desktop | `Get-Command agent-desktop -All`; `npm.cmd root -g`; search the package root you found and the tool directory recursively for `agent-desktop*.exe`, and run `--help` on the actual exe | Install locally per section 4; when no exe is found, do not fill in a guessed location |
| OpenSSH server | As administrator, `Get-WindowsCapability -Online -Name OpenSSH.Server*`; `Get-Service sshd` | Only when State is not Installed, run `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`, then set sshd to automatic and start it; Windows components may use the system drive |
| Tailscale | `Get-Command tailscale.exe`; `tailscale status` | Run the Tailscale installer; where it offers a directory, choose a non-system disk; signing in and approving devices are in section 1 |
| WebView2 | Check the installed Edge WebView2 Runtime entry; the back end also checks the runtime when a window starts | Install Microsoft WebView2 Runtime when it is missing; where the directory can be chosen, apply the same location rule |

The PC install scripts do not install these prerequisites. When something is not found, complete that item first and then continue, so the runtime does not fall back to an unverified built-in. On the VM, check `python3 --version` and `command -v ssh scp bash systemctl`; install what is missing with that environment's package manager, and do not reinstall components that already exist.

### Browser control extension (when needed)

The official Muse browser extension does not ship with the package: when browser control is needed, check under O12 whether it is installed, and if not, the user adds it after agreeing; steps are in [operating guide section 3](operating-guide.en.md#3-standard-actions). The package-side fallback browser stack (agent-browser / Playwright and similar) is used only when the user declines or cannot add it, and is not reinstalled when present. No configuration key selects the control channel; `pc.pet.session_browser/session_profile` only serve the pet's session button.

## 1 Networking

The user performs network sign-in, device approval and policy editing. Muse walks through one operation at a time, states the state to expect, and waits for the user to finish before continuing; when something is stuck it explains the next step from the current screen and never confirms an account for the user.

### Tailscale step-by-step reference (PC, Muse VM, and optional extra servers)

1. **Windows PC**: Muse checks `Get-Command tailscale.exe -ErrorAction SilentlyContinue` and `tailscale status`. If missing, with the user's consent it guides the user to install Tailscale (`https://tailscale.com/download/windows`) and sign in via the system tray icon, then runs `tailscale ip -4` to record the PC's tailnet IPv4 (`100.x.y.z` -> `site.pc_host`) and `tailscale status` (first line -> `pc.tailscale.device_name`).
2. **Muse Linux environment (and any extra servers the user wants linked)**: Muse checks `command -v tailscale` and `tailscale status`. If missing, with the user's consent it installs Tailscale (for standard Linux: `curl -fsSL https://tailscale.com/install.sh | sh`; in containers without `/dev/net/tun`, run userspace `tailscaled --tun=userspace-networking` and set `vm.USE_TUNNEL` accordingly) and runs `sudo tailscale up` (or `tailscale up`), handing the auth URL to the user to sign in and approve the new machine at `https://login.tailscale.com/admin/machines`. With the user's consent, repeat the same join flow for any additional server devices the user wants Muse and their PC to connect to.
3. **Confirm both devices online**: The user confirms in the Tailscale Machines admin page that both devices show `Connected`, and Muse verifies connectivity with `tailscale status` on both sides.
4. **Windows OpenSSH (`sshd`) & keys**: Check `sshd` on the PC and enable it. On the VM, Muse inspects existing SSH keys and public key fingerprints read-only and reuses a usable key; the user puts the matching public key into `authorized_keys` of the target Windows account (`%USERPROFILE%\.ssh\authorized_keys`, or `C:\ProgramData\ssh\administrators_authorized_keys` with tightened ACLs if the Windows account is an administrator).
5. **Known hosts**: The user verifies the PC host key fingerprint through a trusted channel and the VM builds `known_hosts`. Host checking (`HOST_KEY_CHECK=yes`) is strict by default; `accept-new` on first connect is used only for an initial connection whose fingerprint has already been verified.
6. **Tailscale ACL least-privilege tags**: After filling in the site configuration, run `python3 vm/site-config.py acl --site <site file>`. It outputs a minimal ACL fragment granting `tag:muse` access to `tag:muselink-pc` on `tcp:22` (SSH) only:

   ```json
   {
     "tagOwners": { "tag:muse": ["autogroup:admin"], "tag:muselink-pc": ["autogroup:admin"] },
     "grants": [
       { "src": ["autogroup:member"], "dst": ["*"], "ip": ["*"] },
       { "src": ["tag:muse"], "dst": ["tag:muselink-pc"], "ip": ["tcp:22"] }
     ]
   }
   ```

   Muse explains the two tags, direction, and port; the user pastes them into Tailscale **Access controls** (`https://login.tailscale.com/admin/acls`) and applies `tag:muse` to the Muse device and `tag:muselink-pc` to the Windows PC (skipping replacement when an existing policy already satisfies this).
7. **End-to-end SSH test**: On the VM run `ssh <Windows account looked up>@<PC address looked up> whoami` and confirm the returned identity matches the target Windows user. If the Muse environment uses userspace networking / a CONNECT proxy (`vm.USE_TUNNEL=1`, `vm.TUNNEL_PORT=3130`), verify with `vm/pc.sh whoami`.

Key files and passwords never travel in plain text through chat. The related gates are in [SECURITY](../SECURITY.md).

## 2 Site configuration

### 2.1 Filling in values and looking them up on site

Copy the site file from `config/muselink.site.example.json`. The single site file lives at `<tool directory>/muselink.site.json` on the PC and at `~/.config/muselink/muselink.site.json` on the VM, with identical content on both. Address values in the example are all empty, and `_fields` give the lookup method.

For an existing installation, read the original runtime files first: on the PC, muselink.json→pc and plugin/policy.json→plugin_policy; on the VM, muselink.env→vm, policy.json→policy and publish.env→publish. The shared PC address, user, tool directory, Python and mailbox go into site; upload information goes into publish and is no longer defined twice in pc.upload or vm. When the original settings differ, do not overwrite one side with the other's value; verify what each side actually calls and look at the preview differences first.

| Address-type fields (all start empty) | How to look it up and confirm it on the target machine |
| --- | --- |
| site.pc_host / pc_user | On the PC, `tailscale ip -4` / `$env:USERNAME`; verify the actual login account with SSH whoami |
| site.pc_tool_dir | Existing install: check the executor-host task action, the running process command line and its directory; new install: list the disks and space as in section 0 and choose a directory on a non-system local fixed disk, in the form `<drive>:\MuseLink` |
| site.pc_python | On the PC, `Get-Command python.exe -All` and `python -c "import sys; print(sys.executable)"`; for an existing installation, check that executor.python is executable |
| site.pc_bridge_dir | Empty means `<tool directory>\muse-bridge` (use that for a new install); for an existing installation read the current bridge.root and `Test-Path` it, and fill this in only when it is not the default location |
| pc.executor.agent_desktop_exe | Find the actual package and exe per section 4, verify with --help, then fill this in |
| pc.executor.agent_desktop_home | Existing install: taken from the `state_root` that agent-desktop `status` returns. New install: choose a path on a non-system disk outside the user profile (form `<drive>:\agent-desktop-home`), create only the parent directory and let agent-desktop create the directory itself. The executor host, the screenshot script and the owner repair script share this value; see section 4, step 1 |
| pc.pet.session_url | Empty means https://muse.ai/; for a specific session the user opens it in a browser on the target machine and copies the https URL from the address bar |
| pc.pet.session_browser / session_profile | Personal values, may stay empty. Find the browser exe with `Get-Command chrome.exe` or the target of a Start menu shortcut; for the profile, open chrome://version (edge://version for Edge) in that profile and take the last part of "Profile Path" |
| pc.index.path | Read the current index.path, or check index/threads.sqlite under the mailbox you confirmed; leave empty on a new install that has not generated it and let the index create it |
| pc.tailscale.exe_dir / device_name | Split-Path the result of `(Get-Command tailscale.exe).Source`; check the device name with `tailscale status` |
| pc.web_bridge.allowed_origins | Not a local address but the public origin of a web AI; the template shows the code default. To add or remove one, use the origin from the browser address bar (scheme + host) |
| pc.explib.public.index_url / repo | Look up the index.json HTTPS address and the repository identifier wherever the public library is actually hosted; leave both empty while the public library does not exist |
| vm.SSH_KEY / SSH_CONFIG / KNOWN_HOSTS | On the VM, `ls -la ~/.ssh` and `ssh -G <PC target>` to check identityfile, the configuration and userknownhostsfile; verify the files and check the fingerprint |
| vm.ML_STATE_DIR / ML_BRIDGE_HOME | On the VM, check the current muselink.env, the hook wrapper scripts and `systemctl cat muse-bridge`, and verify the directories; on a new install, confirm the chosen VM layout and then fill these in |
| vm.PROXY_FALLBACK | On the VM, check the proxy the runtime environment provides and the service environment, and verify it works in a clean systemd/cron environment; never print an environment that contains credentials |
| publish.RELEASE_SSH / RELEASE_SSH_KEY / RELEASE_KNOWN_HOSTS | On the VM, check user/hostname/identityfile/userknownhostsfile from `ssh -G <target>` for the update target, and verify connectivity and the files; without them, releases stay disabled |
| publish.RELEASE_WEB_DIR / RELEASE_TEST_DIR / RELEASE_STAGE | Look at the site configuration, staging directory and test directory on the actual update server using `pwd`, `test -d` and a permission check; fill in only directories that have been created and confirmed |
| publish.RELEASE_BASE_URL / RELEASE_LATEST_URL | Check the actual site routes and update manifest, and verify the public address by an HTTP read-back; leave empty with a note while nothing is published |
| publish.RELEASE_ALLOWED_DIRS | On the PC, use `Resolve-Path` to check the directories the user approved for packaging, separated by semicolons; leave empty when none are specified |
| publish.UPLOAD_DEST_PC / UPLOAD_KEY_PC | On the PC, `ssh -G <upload target>` to check user/hostname/identityfile, and `Test-Path` to verify the key file; it may be relative to the tool directory, and the file name is not guessed |
| publish.RELEASE_ZIP_NAME / RELEASE_SETUP_NAME | Look up the release file names in the target project's actual build output and in the updater configuration; they may contain `{version}`; leave the setup name empty when there is no installer |

Keep accounts, directories, servers and URLs empty, with a pending note, when they are not installed or cannot be found. The installer rejects a site file that lacks any of its three required items (`pc_host`, `pc_user`, `pc_tool_dir`); other empty strings are omitted by the generator, and the reader may still apply a built-in default, so an empty value does not prove the feature is disabled at runtime. Confirm the actual path during installation before enabling the related module, and do not take a fallback value for an on-site check result.

Other settings follow the template mechanism: `_` keys are help text and are dropped; keys and tokens stay in their own files and the site file holds only paths. `publish.enabled=false` generates no publish.env and does not delete an existing file. Sensitive words go only into pc.explib.redact.literals; protocol constants are in protocol section 8.

### 2.2 Generate, compare and replace

| Generated file | Input | Generator |
| --- | --- | --- |
| PC muselink.json | pc + Python/mailbox from site + publish upload fields | pc/install/deploy-pc.ps1, site-config.ps1 |
| PC plugin/policy.json | plugin_policy | Same as above |
| VM muselink.env | site + vm | vm/site-config.py, called through install.sh |
| VM policy.json | policy | Same as above, validated with the identity module |
| VM publish.env | publish, when enabled | Same as above |
| tailnet policy snippet | tailnet | site-config.py acl, printed only and applied by the user |

A missing file is new; identical effective content prints skip; a difference lists the key differences and stops. Only PC `-ReplaceConfig` and VM `--replace-config` replace a differing configuration, and the old files are kept as a backup. For an existing installation, preview until it matches, then decide the values the user explicitly wants changed. Engineering conventions are in RULES E7.

## 3 Packaging and verification

Commit in the development checkout first, then run:

```text
python tools/make-release.py <empty output directory> <commit ID>
```

The release package contains pc/, vm/ and RELEASE.txt; each side has a RELEASE-MANIFEST.sha256 and the site template, and the VM also carries the standing orders template. It is exported from the commit only and contains no uncommitted working-tree state; check the manifest hashes and the file count once before and once after copying.

On the PC, split each manifest line into the 64-character hash and the relative path and compare with `Get-FileHash -LiteralPath ... -Algorithm SHA256`; on the VM, run `sha256sum -c RELEASE-MANIFEST.sha256` inside the tree. The acceptance record cites the commit ID in RELEASE.txt. During the repackaging window that migrates the old release type, pause releases first and resume once the PC client, the VM service and the hooks have all switched.

## 4 PC side

`$ToolDir`, `$SiteFile` and `<release package>` below are values looked up or chosen on the target PC. Deployment scripts preview by default; an existing directory is kept even on the system drive, a new directory on the system drive is refused and needs an explicit `-AllowSystemDrive`. That parameter is also required when the system drive is the only disk.

1. **agent-desktop, skip if already installed.** Check the launch entry on PATH, the existing npm package root and the tool directory:

   ```powershell
   Get-Command agent-desktop -All -ErrorAction SilentlyContinue
   npm.cmd root -g
   Get-ChildItem "$ToolDir\tools\agent-desktop" -Recurse -Filter agent-desktop*.exe -ErrorAction SilentlyContinue
   ```

   Search the package directory you actually found recursively for the exe and run --help. Keep an existing one and write `pc.executor.agent_desktop_exe`. A new installation first completes the next deployment step so that the tool directory passes the system drive check, then does a local install, and after finding the exe reruns configuration generation. The cache also stays in the tool directory, and the original session environment is restored:

   ```powershell
   $oldNpmCache = $env:npm_config_cache
   try {
     $env:npm_config_cache = Join-Path $ToolDir 'tools\npm-cache'
     npm.cmd install --prefix "$ToolDir\tools\agent-desktop" agent-desktop
     if ($LASTEXITCODE -ne 0) { throw 'agent-desktop install failed' }
   } finally { $env:npm_config_cache = $oldNpmCache }
   Get-ChildItem "$ToolDir\tools\agent-desktop" -Recurse -Filter agent-desktop*.exe
   ```

   If no exe is found, stop at this item. This step does not change global npm settings. The package location and the state directory are configured separately; during installation put the confirmed state directory into `pc.executor.agent_desktop_home` in the single site file, let deployment generate the runtime configuration, and the executor host sets `AGENT_DESKTOP_HOME` when it starts.

   **Keep the state directory outside the user profile.** agent-desktop puts its state in `%USERPROFILE%\.agent-desktop` by default. Before every operation it self-checks that directory: if the owner is not the current user, or the list contains SYSTEM, Administrators or a principal other than the current user, it refuses to work. The Codex elevated sandbox on the same machine adds `CodexSandboxUsers` read permission to every first-level folder under the user profile each time it initializes (recorded in `%USERPROFILE%\.codex\.sandbox\sandbox.<date>.log`), so the default location fails again and again. On a new install choose a path on a non-system disk outside the user profile (form `<drive>:\agent-desktop-home`) and put it into `pc.executor.agent_desktop_home`. **Do not create this directory in advance**: the parent directory existing is enough, and agent-desktop creates it itself when it first writes state, with only three principals, no permissions inherited from above, and the current user as owner. The host and the screenshot script set `AGENT_DESKTOP_HOME` from this value at startup, and the state location is whatever `status` returns as `state_root`.

   Path priority: the repair script's `-StateDir`, the runtime configuration `executor.agent_desktop_home`, the process, user and system environment variables, and only then the default location. A user-level variable is read from the registry, so it is found even when a scheduled task did not inherit the new variable. Set the same variable when running agent-desktop in a terminal on its own.

   An existing installation keeps its state in place; to move it: stop the host first; copy the old state to the new path (skipping `refstore.lock`) without permissions; run `status` once as the target run identity and confirm `state_root` and the three-principal list; after switching over and verifying the host, rename the old directory and keep it. Files copied or created under an elevated identity are owned by Administrators, so run `install\fix-agent-desktop-owner.ps1` (step 4) before running at normal privilege.

2. **Deploy code and generate configuration.** `powershell -File <release package>\pc\install\deploy-pc.ps1 -SiteFile <site file>`; the preview lists the disk, new, change and skip. Add `-Apply` once confirmed. Code skips identical files by SHA-256, backs up changed files first and verifies the hash after copying; files unique to the machine are not deleted. `-Force` recopies code, and differences in configuration still go through ReplaceConfig.
3. **launcher.** `powershell -File <tool directory>\launcher\build-launcher.ps1 -OutDir <tool directory> -Preview` shows the plan; drop Preview to compile. It skips when the exe exists and `launcher.source.sha256` equals the current launcher.cs hash, and reports an error when csc is not found. `-Force` forces a compile.
4. **Task definitions.** `powershell -File <tool directory>\install\register-tasks.ps1 -ToolDir <tool directory>` previews, and `-Apply` is added only when something changes. It skips when action, arguments, triggers, principal, run level and settings match, and lists old→new when they differ. Ordinary tasks are interactive Limited; tailscale-watchdog needs an administrator and `-IncludeWatchdog`. An existing Highest executor is listed explicitly as changing to Limited; run the owner repair as the user decides first, then apply the change.
5. **Executor.** After code changes, run restart-host.ps1 in an elevated terminal, previewing first and then `-Apply`; it disposes of the old instance and starts the task. A new installation registers the tasks first, then starts once. It skips the restart when the correct version is already running with exactly one listener; verify liveness with the executor ping, the host_start log entry and the listener evidence.
6. **Web waiting.** Install `userscripts/muselink-web-ai-notifier.user.js` in the target browser as needed. Check the existing script content and the site origin; skip when it is identical and replace it on an update; fill in the origin per section 2.

Dependencies, the launcher, the venv, logs, the mailbox, the index, identity and experience data stay in the original install directory. Deployment does not overwrite the local venv, launcher, mailbox, identity, experience or run state; the source hash record is maintained by the builder.

## 5 VM side

1. Check the existing service with `systemctl cat muse-bridge`, the hook script_paths and the process command lines to establish the current tree and state locations. Upgrade an existing installation in place; seal or back up the code tree first and keep identity, mailbox, SSH and hook state.
2. Put the verified vm/ tree into the target directory; before replacing the code wholesale, confirm that no enrollment or card handover is in progress, and keep the files that hold state. Do not copy the identity directory from the code package.
3. `bash <VM tree>/install.sh --site <site file>` previews; check that nothing reports FAIL, then add `--apply`. Configuration comparison is as in section 2: the three hook wrapper scripts and service.rendered are skipped when identical and written when they differ; `--force` forces all four files to be rewritten. The script also verifies the release manifest, clears pycache inside the tree, sets executable bits, and checks the local identity and bridge directories.
4. Compare the rendered service with the existing unit using `systemctl cat muse-bridge`; skip the install when the content matches and the service is running. `bridge/render-unit.sh` renders the unit for both install and self-heal; its `MUSELINK_HOME` and `MUSELINK_CONFIG` lines point at the home and muselink.env that install.sh uses, because the service starts with the passwd home of User=. When it is missing or changed, apply the sudo install, daemon-reload and enable/restart commands that install.sh prints, then read back status and the service log. The script prints root operations and does not run them.
5. Platform hooks run every 60 seconds: codex-bridge-watch→ml-bridge-postman.sh, popup-wake-fallback→ml-popup-fallback.sh, bridge-alert-watch→ml-bridge-alert-watch.sh. Read the platform's existing definitions and compare script_path, schedule and wake fields one by one; skip identical ones, register missing ones and change only differing ones. Before the first switch, use a dry run to inspect the decisions, then enable them one at a time. The contract is in protocol section 7.
6. Keep standing orders when they already exist; when they do not, install.sh warns, and the user fills in `standing-orders.example.md` and places it at `~/.config/muselink/standing-orders.md`. The deployer does not extend authorization on the user's behalf.

Legacy command forwarding lives in vm/compat and is deleted once the old callers have migrated and repackaging acceptance has passed; the transitional exclusion is in tools/public-exclude.txt.

## 6 Optional modules

| Module | Installation, and the basis for skip if already installed |
| --- | --- |
| Mailbox dependencies | Run mailbox/install.ps1 as a standard user, preview then `-Apply`; reuse the shared venv and matching dependency hash. Creates no shortcuts. Upgrade removes the legacy MuseLink Mailbox.lnk from Desktop/Programs and matching Startup links, stops the installation's legacy standalone mailbox processes, and removes its scheduled/Run/RunOnce autostart entries. `-AutoStart` registers only the pet task; legacy register-tasks mailbox switches also map to pet. It does not start mailbox/tray.py. |
| Desktop pet | Complete the mailbox venv first; run pet/install.ps1 as a standard user, previewing and then `-Apply -AutoStart`; skips generating the confirmation sound when it exists, and skips the shortcut and the pet autostart definition when they match. `-RequestIdentity` is an explicit enrollment request and is decided by the user through the existing flow. |
| Local plugin | plugin/install.ps1 -Agent <enrolled ID> [-Client codex\|kiro\|claude], preview and then `-Apply`; writes only the configuration of the client this agent runs in (-Client may be left out when the ID is the client name), keeps other entries and existing muselink entries, and makes no backup when it matches; creates the Codex or Kiro file when that client is installed (a .codex / .kiro directory exists) and the file is missing. Use -ConfigHome to test against temporary configuration. Client details are in the GUIDE. |
| Experience library | The code ships with the package, and at runtime it reads experience/local and public automatically. Copy private entries into local/<app>/; fill in the configuration for wherever the public library is actually hosted, then sync. An identical JSON that already exists is not copied again. The daily share task uses register-tasks.ps1 -ExplibOnly, previewing and then Apply. |
| Release | Only for the user's own update site. Fill in publish on site and enable it, with the upload target filled in only after a connection to it has been verified on the target machine; skip when the existing service, task and configuration match. Run with test:true first, and release normally only after authorization, the remote directory, the hash and the public manifest have all been verified. |

A module installer's `-Force` is an explicit force-redo; it does not replace identity consent. Enroll each agent separately with `muse-call.ps1 -As <id> -Enroll`, and finish one agent's card before starting the next. Once all are enrolled, the user may decide to enable legacy_unsigned=reject and bridge.require_identity=true in the site configuration, and then regenerate the runtime files.

## 7 Acceptance checklist

Record the commit, command, result, log or screenshot for each item. When something fails, fix it in the commit, rebuild the package and start again from consistency; operations and authorization follow RULES.

| Item | What it verifies |
| --- | --- |
| Consistency | The PC and VM manifests all match, the install preview shows matching configuration, and every process command line comes from the tool directory and the chosen VM tree |
| Channel | pc-master.sh ensure, executor.sh ping and ad list-windows complete a round trip |
| Operation layer | sem observe/resolve, uia locate, one inspected seq job, and a shot screenshot |
| Cards and bus | A choice card's answer reaches the main path, a no-wait card is delivered by the fallback, a short-expiry card closes and reports expired; the local task wait completes, and the web channel completes as needed |
| Bridge | A signed ping from an enrolled agent gets an interim and a final reply, and Check collects them again; a test release is handled by the resident service alone, and the legacy-name compatibility entrance outputs the normalized type |
| Hooks and self-heal | A temporary test alert is detected and cleaned up afterwards; stop the service in a controlled way and observe self-heal. The comparison tooling is private acceptance material and is not installed with the public package |
| Enrollment | After a simulated wait interruption the card can be resumed, and a busy slot loses no request; the user clicks pet once for a real enrollment, and the reply and key installation complete |
| Mailbox window | Threads, release cards and unread replies are correct; open/close/reopen from the pet reuses one in-process window; minimizing shows the pet.ico taskbar icon and restores on click, closing hides it, unread badge follows the pet mailbox button, and pet exit leaves no child. |
| Desktop pet | Starts at sign-in, the hover button, hide and recall, the four two-sided reply combinations, one confirmation sound, and the entrance opening the mailbox; the final appearance is confirmed against the asset list |
| Plugin | observe, the consent card on the first act, continued use in the same application after a timed approval, and no execution after a denial; an ask_user answer can be collected |
| Experience library | record a draft, review and confirm, lookup, feedback after real use; also exercise the Muse CLI path once; verify accumulated-5 batch sharing and permanent decline with synthetic entries, and do not test submission with real personal entries |

## 8 Upgrading an existing installation

Follow the same flow as sections 2–7: check the current state, carry over the current site values, preview, and apply only missing items and differences. An existing tool directory is not moved, including an old one on the system drive; every package comes from one commit, and old code loaded by a running process needs a separate restart per section 4. The difference between installed source and production runs is in RULES E1/E8.

The PC deployment backup directory holds the overwritten code, the original configuration, the executor-host task XML and manifest.json. To roll back, stop the related processes first, restore the backed-up files per the manifest, restore the tasks that need restoring from the backed-up XML, return the site file to a version that matches the old runtime files, then verify against the old release package and restart to check liveness. Added code can be identified from the manifest; the mailbox, identity, token and experience data are not deleted.

A VM rollback restores the complete code tree of the previous commit, the original service unit and the hook script_paths; generated configuration uses the timestamped .bak backup taken at installation. The identity store and the mailbox ledger stay in place and are not overwritten with an old identity copy. After the rollback, re-check the channel, a signed ping, waiting and the service status, and confirm that the final reply can still be collected.
