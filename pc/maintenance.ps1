# maintenance.ps1 - daily housekeeping for the tool dir (task muselink-maintenance).
# Deletes only regenerable leftovers; mailbox history is NEVER deleted here
# (the files are the source of truth for threads).
#   - executor-events.jsonl: rotated by the host itself (> 5 MB -> .1)
#   - shots\*: older than 14 days
#   - upload-work\ and the legacy vb-up-work\: chunk files older than 1 day
#   - uia-req-*/uia-out-* temp files older than 1 day
#   - popup-claim-*.tmp older than 1 day (claims are removed right after use)
# -WhatIf lists instead of deleting.
param([switch]$WhatIf)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
$log = Get-MLPath 'maintenance.log'
$now = Get-Date
$freed = 0L
$count = 0

function Remove-Old([string]$dir, [string]$filter, [int]$days) {
    if (-not (Test-Path -LiteralPath $dir)) { return }
    foreach ($f in @(Get-ChildItem -LiteralPath $dir -Filter $filter -File -ErrorAction SilentlyContinue)) {
        if (($now - $f.LastWriteTime).TotalDays -lt $days) { continue }
        $script:freed += $f.Length
        $script:count++
        if ($WhatIf) { Write-Output ("would delete {0}" -f $f.FullName) }
        else { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue }
    }
}

Remove-Old (Get-MLPath 'shots') '*' 14
Remove-Old (Get-MLPath 'upload-work') '*.part*' 1
Remove-Old (Get-MLPath 'vb-up-work') '*.part*' 1
Remove-Old (Get-MLDir) 'uia-req-*.json' 1
Remove-Old (Get-MLDir) 'uia-out-*.json' 1
Remove-Old (Get-MLDir) 'popup-claim-*.tmp' 1

$line = '{0} files={1} bytes={2} whatif={3}' -f $now.ToString('yyyy-MM-ddTHH:mm:sszzz'), $count, $freed, [bool]$WhatIf
Add-Content -Path $log -Value $line -Encoding UTF8
Write-Output $line
