# check-popup.ps1 - one-shot probe used by Muse's hook fallback
# (vm/hooks/popup-fallback.sh). Serves both wait types sharing the mailbox:
# 'popup' (a card is on screen) and 'task' (a local task / web-AI trigger
# delivers popup-done.txt itself; the probe then only claims finished
# results and never relaunches anything). -WaitType defaults to 'popup'.
#   done present      -> atomically claim pending and print the result
#   popup still open  -> report only (STATE:STILL_OPEN)
#   popup gone        -> relaunch the popup task (input files are still on
#                        disk), report STATE:RELAUNCHED
#   -ForceExpire      -> delete pending and close the popup if it is open
# -ProbeOnly reports state without claiming, relaunching or expiring (hook
# dry runs can never consume a real result).
# Popup liveness = process command lines (a Local\ mutex is invisible across
# sessions: the card runs in session 1, this probe in the SSH session).
param(
    [Parameter(Mandatory=$true)][string]$ReqId,
    [string]$WaitType = 'popup',
    [switch]$ForceExpire,
    [switch]$ProbeOnly
)
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
. (Join-Path $PSScriptRoot 'muselink-common.ps1')

if (-not (Test-MLReqId $ReqId)) { Write-Output 'STATE:BAD_REQID'; exit 1 }

$pendFile  = Get-MLPath 'popup-pending.txt'
$doneFile  = Get-MLPath 'popup-done.txt'
$kindFile  = Get-MLPath 'popup-kind.txt'
$resFile   = Get-MLPath 'popup-result.txt'
$claimFile = Get-MLPath ("popup-claim-hook-" + $ReqId + ".tmp")

function Write-DoneBlock([string]$at, [string]$claimTag) {
    Write-Output 'STATE:DONE'
    Write-Output ('REQ_ID:' + $ReqId)
    Write-Output ('DONE_AT:' + $at)
    Write-Output ('KIND:' + (Read-MLUtf8 $kindFile).Trim())
    if ($claimTag) { Write-Output ('CLAIM:' + $claimTag) }
    Write-Output '---RESULT---'
    Write-Output (Read-MLUtf8 $resFile)
}

function Get-TriggerProcess {
    # Leftover triggers of an expired task wait (watch-start.ps1 runs
    # Watch-LocalTask in-process; web-bridge.ps1 runs directly). Same
    # self-match care as Get-MLPopupProcess.
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and
            $_.CommandLine -match 'watch-start\.ps1|web-bridge\.ps1' -and
            $_.CommandLine -notmatch 'check-popup|wait-popup'
        }
}

if (-not (Test-Path -LiteralPath $pendFile)) {
    # Recovery of a consumed-but-undelivered answer (2026-11-05 post-mortem):
    # the main path may claim the pending, finish here and print into a dead
    # SSH channel. Consumers never delete done/result/kind, so the answer is
    # still on disk. v2 done files carry the ReqId, which makes the decision
    # exact; the 60 s age keeps a healthy main path (which claims within
    # seconds) from ever racing this branch. Legacy done files without a ReqId
    # fall back to the old single-flight rule with a 120 s age.
    if (Test-Path -LiteralPath $doneFile) {
        $info = Get-MLDoneInfo $doneFile
        $age = ([DateTime]::Now - (Get-Item -LiteralPath $doneFile).LastWriteTime).TotalSeconds
        $mine = $false
        if ($info.ReqId -ne '') { $mine = ($info.ReqId -eq $ReqId -and $age -ge 60) }
        else { $mine = ($age -ge 120) }
        if ($mine) {
            Write-DoneBlock $info.At 'RECOVERED_UNCLAIMED'
            exit 0
        }
    }
    Write-Output 'STATE:NO_PENDING'
    exit 0
}
$curReq = (Read-MLUtf8 $pendFile).Trim()
if ($curReq -ne $ReqId) {
    Write-Output 'STATE:MISMATCH'
    exit 0
}

if ($ForceExpire) {
    if (-not $ProbeOnly) {
        Remove-Item -LiteralPath $pendFile -Force -ErrorAction SilentlyContinue
        if ($WaitType -eq 'popup') {
            Get-MLPopupProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
        if ($WaitType -eq 'task') {
            # a web bridge would keep holding its port and block the next one
            Get-TriggerProcess | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
    Write-Output 'STATE:EXPIRED_CLEANED'
    exit 0
}

if (Test-Path -LiteralPath $doneFile) {
    $info = Get-MLDoneInfo $doneFile
    if ($info.ReqId -ne '' -and $info.ReqId -ne $ReqId) {
        Write-Output ('STATE:MISMATCH done=' + $info.ReqId)
        exit 0
    }
    if ($ProbeOnly) {
        Write-Output 'STATE:DONE'
        Write-Output ('REQ_ID:' + $ReqId)
        exit 0
    }
    try {
        Move-Item -LiteralPath $pendFile -Destination $claimFile -Force -ErrorAction Stop
    } catch {
        Write-Output 'STATE:ALREADY_CLAIMED'
        exit 0
    }
    Write-DoneBlock $info.At ''
    Remove-Item -LiteralPath $claimFile -Force -ErrorAction SilentlyContinue
    exit 0
}

if ($WaitType -eq 'task') {
    Write-Output 'STATE:STILL_WAITING'
    exit 0
}

if (@(Get-MLPopupProcess).Count -gt 0) {
    Write-Output 'STATE:STILL_OPEN'
    exit 0
}
if ($ProbeOnly) {
    Write-Output 'STATE:DEAD'
    exit 0
}
schtasks /run /tn popup | Out-Null
Write-Output 'STATE:RELAUNCHED'
exit 0
