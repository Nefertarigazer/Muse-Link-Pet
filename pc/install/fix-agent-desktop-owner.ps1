# fix-agent-desktop-owner.ps1 - prerequisite for running the executor host
# WITHOUT elevation.
#
# agent-desktop refuses private state files whose owner is "foreign". Files
# created while the host ran elevated are owned by BUILTIN\Administrators, so a
# non-elevated host gets PERM_DENIED on snapshot (seen 2026-11-10 with the
# ad-driver task). This script makes the current user the owner of
# the resolved agent-desktop state directory and everything in it. It does NOT touch the
# DACLs (the 2026-11-08 incident: a recursive ACL edit left 76 files with an
# empty DACL while reporting success).
#
#   powershell -File fix-agent-desktop-owner.ps1            # report only
#   powershell -File fix-agent-desktop-owner.ps1 -Apply     # back up, then fix
# Verification after -Apply: every item's owner is the current user AND its
# DACL still has entries. Then run one `executor.sh ad snapshot --app <app>`
# from a non-elevated host before calling the migration done.
param([switch]$Apply, [string]$StateDir = '')
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'muselink-common.ps1')
$StateDir = Get-MLAgentDesktopHome $StateDir
Write-Output "State directory: $StateDir"
if (-not (Test-Path -LiteralPath $StateDir)) { Write-Output "NOTHING_TO_DO no $StateDir"; exit 0 }
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

function Get-Report {
    $items = @(Get-Item -LiteralPath $StateDir) + @(Get-ChildItem -LiteralPath $StateDir -Recurse -Force)
    foreach ($it in $items) {
        if ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw ('State contains a reparse point; inspect its target first: ' + $it.FullName)
        }
        $acl = Get-Acl -LiteralPath $it.FullName
        [pscustomobject]@{
            Path = $it.FullName
            Owner = $acl.Owner
            Aces = $acl.Access.Count
            Dacl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)
        }
    }
}

# @(...): a one-element result would otherwise be a bare object without .Count
$before = @(Get-Report)
$foreign = @($before | Where-Object { $_.Owner -ne $me })
$empty = @($before | Where-Object { $_.Aces -eq 0 })
Write-Output ("items={0} not_owned_by_me={1} empty_dacl={2} me={3}" -f $before.Count, $foreign.Count, $empty.Count, $me)
$foreign | Select-Object -First 10 | ForEach-Object { Write-Output ("  foreign owner: {0}  {1}" -f $_.Owner, $_.Path) }
if ($empty.Count -gt 0) { Write-Output 'STOP: some items have an EMPTY DACL - repair those first (see the playbook DACL section).'; exit 2 }
if (-not $Apply) { Write-Output 'REPORT ONLY. Re-run with -Apply to take ownership.'; exit 0 }
if ($foreign.Count -eq 0) { Write-Output 'OK nothing to change'; exit 0 }

$backup = Join-Path $env:TEMP ('agent-desktop-acl-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
& icacls $StateDir /save $backup /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw "ACL backup failed; ownership was not changed: $StateDir" }
Write-Output "ACL backup: $backup (restore: icacls <parent dir> /restore <file>)"
foreach ($row in $foreign) {
    & icacls $row.Path /setowner $me /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ('Setting owner failed: ' + $row.Path) }
}
$after = @(Get-Report)
$bad = @($after | Where-Object { $_.Owner -ne $me -or $_.Aces -eq 0 })
$originalDacls = @{}
foreach ($row in $before) { $originalDacls[$row.Path] = $row.Dacl }
$changedDacls = @($after | Where-Object { $_.Dacl -ne $originalDacls[$_.Path] })
if ($changedDacls.Count -gt 0) { throw 'DACL changed during ownership repair; inspect the backup before continuing.' }
if ($bad.Count -gt 0) {
    $bad | Select-Object -First 10 | ForEach-Object { Write-Output ("  NOT FIXED: owner={0} aces={1} {2}" -f $_.Owner, $_.Aces, $_.Path) }
    Write-Output "FAILED items=$($bad.Count)"
    exit 1
}
Write-Output "FIXED items=$($foreign.Count); every item now owned by $me with a non-empty DACL"
