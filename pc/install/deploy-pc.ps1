# deploy-pc.ps1 - copy the PC package (this repo's pc\ folder) into the live
# tool directory and generate its configuration from the site file. Without
# -Apply it only prints the plan.
#   powershell -File deploy-pc.ps1 [-SiteFile <muselink.site.json>] [-ToolDir <dir>]
#                                  [-Apply] [-ReplaceConfig] [-BackupDir <dir>]
#
# Site file (one file for the whole installation, the same one the VM uses;
# template muselink.site.example.json, guide docs/install.en.md section 2): -SiteFile,
# else <ToolDir>\muselink.site.json if it exists. The tool dir defaults to
# its site.pc_tool_dir; a different -ToolDir is refused. It generates
# muselink.json and plugin\policy.json (site-config.ps1): a missing file is
# written; one with the same effective content is left untouched; one that
# differs is listed and the run stops, unless -ReplaceConfig (the old copy
# goes into -BackupDir). Without a site file the existing muselink.json is
# kept as it is.
#
# Rules (2026-11-12 phase-1 rollout):
# - Never deletes anything in the tool dir. Files that exist only there
#   (launcher.exe, keys\, old pc-to-a1-chunked-upload.ps1, ...) are left
#   alone; muselink.json and plugin\policy.json change only as described
#   above.
# - Before overwriting, every file that differs is copied into -BackupDir
#   (same relative path) together with the executor-host task XML and a
#   manifest.json (old/new SHA-256 per file).
# - After copying, every file is re-hashed against the package; any mismatch
#   ends the script with exit 1.
# - Running processes keep the code they already loaded. Restarting the
#   executor host is a separate, elevated step (restart-host.ps1).
param(
    [string]$ToolDir = '',
    [string]$SiteFile = '',
    [switch]$Apply,
    [switch]$ReplaceConfig,
    [string]$BackupDir = '',
    [switch]$Force,
    [switch]$AllowSystemDrive
)
$ErrorActionPreference = 'Stop'
$src = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'site-config.ps1')

if ($SiteFile -eq '' -and $ToolDir -ne '' -and (Test-Path -LiteralPath (Join-Path $ToolDir 'muselink.site.json'))) {
    $SiteFile = Join-Path $ToolDir 'muselink.site.json'
}
$site = $null
if ($SiteFile -ne '') {
    $SiteFile = Get-MLFullPath $SiteFile
    $site = Read-MLSite $SiteFile
    $siteDir = Get-MLFullPath $site['site']['pc_tool_dir']
    if ($ToolDir -eq '') { $ToolDir = $siteDir }
    elseif ((Get-MLFullPath $ToolDir) -ne $siteDir) { throw ('-ToolDir ' + $ToolDir + ' is not site.pc_tool_dir ' + $siteDir) }
}
if ($ToolDir -eq '') { throw 'give -SiteFile (or -ToolDir for a code-only deploy)' }
$ToolDir = Get-MLFullPath $ToolDir
if (-not $Apply) {
    '== non-system local fixed drives (choose on this machine)'
    Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Where-Object {
        $_.DeviceID -ne $env:SystemDrive
    } | ForEach-Object { '{0} free={1:N2} GiB' -f $_.DeviceID, ($_.FreeSpace / 1GB) }
}
if (-not (Test-Path -LiteralPath $ToolDir -PathType Container)) {
    if ([IO.Path]::GetPathRoot($ToolDir).TrimEnd('\') -eq $env:SystemDrive -and -not $AllowSystemDrive) {
        throw 'New tool directory is on the system drive. Choose a non-system local fixed drive, or explicitly pass -AllowSystemDrive.'
    }
    if (-not $Apply) { "tool dir $ToolDir does not exist yet; -Apply creates it" }
    else { New-Item -ItemType Directory -Force -Path $ToolDir | Out-Null }
}
$ToolDir = Get-MLFullPath $ToolDir
if ($BackupDir -eq '') { $BackupDir = Join-Path $ToolDir ('backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }

$plan = @()
# Only the package goes over: never this checkout's own runtime state, local
# venv, locally built launcher or raw art sources (they would overwrite the
# live tool dir's own copies). Each installer builds its venv / WAV on site.
$skip = '\\__pycache__\\|\\\.venv\\|\\smoke\\|\\packs\\[^\\]+\\source\\|^launcher\.exe$|^launcher\.source\.sha256$|^muselink(\.site)?\.json$|^muse-bridge\\|^identities\\|^experience\\|^plugin\\(trace\\|audit\.jsonl|state\.json|policy\.json)|' +
        '^mailbox\\(window\.json|user-avatar\.png|seen\.json|pet-history\.jsonl)$|^pet\\(pet-(presence\.json|presence\.tmp|position\.json|confirm\.wav)|user-avatar\.png)$|\.tmp$|\.pyc$'
Get-ChildItem -LiteralPath $src -Recurse -File | Where-Object {
    $_.FullName.Substring($src.Length + 1) -notmatch $skip -and $_.FullName -notmatch '\\__pycache__\\|\\\.venv\\'
} | ForEach-Object {
    $rel = $_.FullName.Substring($src.Length + 1)
    $dst = Join-Path $ToolDir $rel
    $newHash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    $oldHash = $null
    if (Test-Path -LiteralPath $dst) { $oldHash = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash }
    $state = if ($null -eq $oldHash) { 'new' } elseif ($oldHash -eq $newHash -and -not $Force) { 'same' } else { 'changed' }
    $plan += [pscustomobject]@{ rel = $rel; state = $state; old = $oldHash; new = $newHash; src = $_.FullName; dst = $dst }
}

$plan | Sort-Object state, rel | ForEach-Object {
    if ($_.state -eq 'same') { 'skip {0}: already installed (sha256={1})' -f $_.rel, $_.new }
    else { $label = if ($_.state -eq 'changed') { 'change' } else { $_.state }; '{0,-8} {1}' -f $label, $_.rel }
}
$todo = @($plan | Where-Object { $_.state -ne 'same' })
'{0} files: {1} new, {2} changed, {3} unchanged' -f $plan.Count, @($plan | Where-Object state -eq 'new').Count, @($plan | Where-Object state -eq 'changed').Count, @($plan | Where-Object state -eq 'same').Count

# ---- configuration generated from the site file
$cfgTodo = @()
$cfgFail = 0
if ($null -eq $site) {
    'no site file: muselink.json is left as it is'
} else {
    "== site configuration from $SiteFile"
    foreach ($t in @(Get-MLPcTargets $SiteFile $site $ToolDir)) {
        if (-not (Test-Path -LiteralPath $t.Path)) { "new      $($t.Rel) (generated)"; $cfgTodo += $t; continue }
        $diff = @(Compare-MLConfig $t.Path $t.Object)
        if ($diff.Count -eq 0) { "skip $($t.Rel): already installed (matches the site file)"; continue }
        if ($ReplaceConfig) { "replace  $($t.Rel) differs from the site file:"; $cfgTodo += $t }
        else { "FAIL     $($t.Rel) differs from the site file:"; $cfgFail = 1 }
        $diff | ForEach-Object { '           ' + $_ }
    }
    if ($cfgFail) { '         fix the site file, or rerun with -ReplaceConfig (the old copy goes into the backup dir)' }
}
if (-not $Apply) { 'preview only; rerun with -Apply'; exit $cfgFail }
if ($cfgFail) { 'NOT applied: the configuration differs (see FAIL above)'; exit 1 }
if ($todo.Count -eq 0 -and $cfgTodo.Count -eq 0) { 'nothing to do'; exit 0 }

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
foreach ($p in @($todo | Where-Object state -eq 'changed')) {
    $b = Join-Path $BackupDir $p.rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $b) | Out-Null
    Copy-Item -LiteralPath $p.dst -Destination $b -Force
    if ((Get-FileHash -LiteralPath $b -Algorithm SHA256).Hash -ne $p.old) { throw ('backup mismatch: ' + $p.rel) }
}
foreach ($t in $cfgTodo) {
    if (Test-Path -LiteralPath $t.Path) {
        $b = Join-Path $BackupDir $t.Rel
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $b) | Out-Null
        Copy-Item -LiteralPath $t.Path -Destination $b -Force
    }
    Write-MLConfigFile $t.Path $t.Object
    "config   wrote $($t.Rel)"
}
$xml = & schtasks.exe /query /tn executor-host /xml 2>$null
if ($LASTEXITCODE -eq 0 -and $xml) {
    [IO.File]::WriteAllLines((Join-Path $BackupDir 'executor-host.task.xml'), [string[]]$xml, (New-Object Text.UnicodeEncoding($false, $true)))
} else { 'warning: could not export the executor-host task XML' }
$manifest = [pscustomobject]@{ created = (Get-Date).ToString('o'); source = $src; tool_dir = $ToolDir; site_file = $SiteFile;
                              config = @($cfgTodo | ForEach-Object { $_.Rel }); files = @($todo | Select-Object rel, state, old, new) }
[IO.File]::WriteAllText((Join-Path $BackupDir 'manifest.json'), ($manifest | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
"backup: $BackupDir"

$bad = 0
foreach ($p in $todo) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $p.dst) | Out-Null
    Copy-Item -LiteralPath $p.src -Destination $p.dst -Force
    if ((Get-FileHash -LiteralPath $p.dst -Algorithm SHA256).Hash -ne $p.new) { "MISMATCH $($p.rel)"; $bad++ }
}
if ($bad -gt 0) { "deploy finished with $bad mismatches; restore from $BackupDir"; exit 1 }
"deployed $($todo.Count) files; all hashes match the package"
