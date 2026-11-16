# wait-popup.ps1 v3 - wait (up to -TimeoutSeconds) for popup-done.txt, then
# claim the result atomically and print it. Muse's VM runs this in a loop of
# short waits (vm/lib/muselink-common.sh: ml_wait_loop): each call holds the
# SSH session for at most ~25 s, under the time limits observed on the Muse
# tunnel proxy, and a broken call is simply retried. The loop's exit is what
# wakes Muse (platform background-task handoff).
#
# Claiming: popup-pending.txt (content = ReqId) is atomically renamed to a
# claim file; only the consumer that wins the rename may read the result.
# The hook fallback (check-popup.ps1) uses the same claim, so the main path
# and the fallback can never both consume one result. popup-done.txt line 2
# ("REQ:<id>") must match too: an answer is never filed under another ReqId.
# (-TimeoutSeconds defaults to 600 for callers that predate the short-wait
# loop; the loop passes 25.)
param(
    [Parameter(Mandatory=$true)][string]$ReqId,
    [int]$TimeoutSeconds = 600
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
. (Join-Path $PSScriptRoot 'muselink-common.ps1')

$doneFile  = Get-MLPath 'popup-done.txt'
$kindFile  = Get-MLPath 'popup-kind.txt'
$resFile   = Get-MLPath 'popup-result.txt'
$pendFile  = Get-MLPath 'popup-pending.txt'
$claimFile = Get-MLPath ("popup-claim-" + $ReqId + ".tmp")

Write-Output ('WAIT_STARTED:' + [DateTime]::Now.ToString('HH:mm:ss.fff'))

# Everything below runs inside a catch-all: any failure surfaces as
# STATUS:ERROR, never as a silent exit (2026-11-03 chain-test anomaly).
try {
    if (-not (Test-MLReqId $ReqId)) { Write-Output 'STATUS:ERROR bad_reqid'; exit 1 }
    $watcher = New-Object System.IO.FileSystemWatcher (Get-MLDir), 'popup-done.txt'
    $watcher.NotifyFilter = [System.IO.NotifyFilters]'FileName, LastWrite, Size'
    $watcher.EnableRaisingEvents = $true
    try {
        $deadline = [DateTime]::Now.AddSeconds($TimeoutSeconds)
        $found = $false
        while ([DateTime]::Now -lt $deadline) {
            if (Test-Path -LiteralPath $doneFile) { $found = $true; break }
            $res = $watcher.WaitForChanged([System.IO.WatcherChangeTypes]::All, 1000)
            if (-not $res.TimedOut) { $found = $true; break }
        }
        if (-not $found -and (Test-Path -LiteralPath $doneFile)) { $found = $true }
        if (-not $found) {
            Write-Output 'STATUS:TIMEOUT'
            exit 124
        }
    } finally {
        $watcher.Dispose()
    }

    if (-not (Test-Path -LiteralPath $pendFile)) {
        Write-Output 'STATUS:ALREADY_CLAIMED'
        exit 0
    }
    $curReq = (Read-MLUtf8 $pendFile).Trim()
    if ($curReq -ne $ReqId) {
        Write-Output 'STATUS:REQ_MISMATCH pending'
        exit 0
    }
    # the done file may still be landing (watcher fired on create): give the
    # REQ line a moment, then insist it matches when present
    $info = Get-MLDoneInfo $doneFile
    for ($i = 0; $i -lt 10 -and $info.At -eq ''; $i++) { Start-Sleep -Milliseconds 100; $info = Get-MLDoneInfo $doneFile }
    if ($info.ReqId -ne '' -and $info.ReqId -ne $ReqId) {
        Write-Output ('STATUS:REQ_MISMATCH done=' + $info.ReqId)
        exit 0
    }
    try {
        Move-Item -LiteralPath $pendFile -Destination $claimFile -Force -ErrorAction Stop
    } catch {
        Write-Output 'STATUS:ALREADY_CLAIMED'
        exit 0
    }

    $kind   = (Read-MLUtf8 $kindFile).Trim()
    $result = Read-MLUtf8 $resFile
    Remove-Item -LiteralPath $claimFile -Force -ErrorAction SilentlyContinue

    Write-Output ('WAIT_ENDED:' + [DateTime]::Now.ToString('HH:mm:ss.fff'))
    Write-Output 'STATUS:DONE'
    Write-Output ('REQ_ID:' + $ReqId)
    Write-Output ('DONE_AT:' + $info.At)
    Write-Output ('KIND:' + $kind)
    Write-Output '---RESULT---'
    Write-Output $result
    exit 0
} catch {
    Write-Output ('STATUS:ERROR ' + $_.Exception.Message)
    exit 1
}
