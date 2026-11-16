# Complete-MuseWait.ps1 - generic mailbox completer (2026-11-03). Any terminal
# trigger (process exit, output-file release, web-AI completion) calls this to
# deliver its result through the SAME mailbox the popup uses:
# popup-result.txt + popup-kind.txt, then popup-done.txt LAST via an atomic
# rename, so wait-popup.ps1 (main path) and the hook fallback consume it like
# a popup answer. popup-done.txt carries "REQ:<id>" on line 2 (v2 format).
# Safety: does nothing unless a wait is armed (popup-pending.txt present) AND
# its content equals -ExpectedReqId, which is MANDATORY: a late trigger from
# an earlier wait can never deliver into a newer wait's mailbox.
#
# The parameter is -ReqId (alias -ExpectedReqId for old callers). Windows
# PowerShell 5.1 aborts with exit -1 when "-ExpectedReqId" appears on a
# powershell.exe -File command line (found 2026-11-12), so command-line callers
# must use -ReqId.
param(
    [Parameter(Mandatory=$true)][string]$Kind,
    [string]$Result = '',
    [string]$ResultFile = '',
    [Parameter(Mandatory=$true)][Alias('ExpectedReqId')][string]$ReqId
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
$ExpectedReqId = $ReqId

# Serialize every completer, including the popup. The identity includes this
# installation so sandbox tests cannot contend with a deployed installation.
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($PSScriptRoot.ToLowerInvariant()))).Replace('-', '') }
finally { $sha.Dispose() }
$mutex = New-Object Threading.Mutex($false, ('muselink-complete-' + $key))
$held = $false
try {
    try { $held = $mutex.WaitOne(10000) } catch [Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { exit 4 }
    if ((Get-MLDoneInfo (Get-MLPath 'popup-done.txt')).ReqId -eq $ExpectedReqId) { exit 3 }
    $pendingFile = Get-MLPath 'popup-pending.txt'
    if (-not (Test-Path -LiteralPath $pendingFile)) { exit 0 }
    $curReq = (Read-MLUtf8 $pendingFile).Trim()
    if ($curReq -ne $ExpectedReqId) { exit 0 }
    if ($ResultFile -ne '') { $Result = [IO.File]::ReadAllText($ResultFile, [Text.Encoding]::UTF8) }
    Write-MLUtf8 (Get-MLPath 'popup-result.txt') $Result
    Write-MLUtf8 (Get-MLPath 'popup-kind.txt') $Kind
    $nowIso = [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:sszzz')
    Write-MLAtomic (Get-MLPath 'popup-done.txt') ($nowIso + "`nREQ:" + $ExpectedReqId + "`n")
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
