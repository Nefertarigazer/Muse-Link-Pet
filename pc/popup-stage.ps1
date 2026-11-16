# popup-stage.ps1 - PC-side staging for the popup template (vm/popup-send.sh).
# 1. Verifies every staged input file against popup-expected-hash.txt BEFORE
#    anything is shown (text, mode and options: the option text is also the
#    answer that travels back). Mismatch -> STAGE_FAIL, nothing shown.
# 2. Closes any popup still on screen. The popup task runs with IgnoreNew, so
#    a stale card would otherwise swallow the trigger and its answer would be
#    filed under the new ReqId.
# 3. Clears stale outputs, writes popup-reqid.txt (echoed into popup-done.txt
#    by the card) and popup-pending.txt (ReqId only), triggers the task.
#
# popup-expected-hash.txt: one "<file> <sha256>" per line. A single bare hash
# (the 2026-11-03 format) is accepted and means popup-text.txt.
#
# Identity protocol v3 (implemented 2026-11-12): accept popup-expires-in.txt in the manifest (the
# allowed-name pattern becomes popup-(text|mode|options|expires-in)\.txt), and
# delete a stale popup-expires-in.txt when the manifest does not list it, so an
# old deadline never leaks into the next card.
param(
    [Parameter(Mandatory = $true)][string]$ReqId
)
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'muselink-common.ps1')

if (-not (Test-MLReqId $ReqId)) { Write-Output 'STAGE_FAIL bad_reqid'; exit 3 }

$hashFile = Get-MLPath 'popup-expected-hash.txt'
if (-not (Test-Path -LiteralPath $hashFile)) { Write-Output 'STAGE_FAIL missing_hash_manifest'; exit 3 }
$expected = @{}
foreach ($line in ((Read-MLUtf8 $hashFile) -split "`r?`n")) {
    $t = $line.Trim()
    if ($t -eq '') { continue }
    $parts = $t -split '\s+'
    if ($parts.Count -eq 1) { $expected['popup-text.txt'] = $parts[0].ToLowerInvariant() }
    else { $expected[$parts[0]] = $parts[1].ToLowerInvariant() }
}
if (-not $expected.ContainsKey('popup-text.txt')) { Write-Output 'STAGE_FAIL manifest_without_text'; exit 3 }
foreach ($name in $expected.Keys) {
    if ($name -notmatch '^popup-(text|mode|options|expires-in)\.txt$') { Write-Output "STAGE_FAIL unexpected_file $name"; exit 3 }
    $p = Get-MLPath $name
    if (-not (Test-Path -LiteralPath $p)) { Write-Output "STAGE_FAIL missing_file $name"; exit 3 }
    $actual = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$name]) {
        Write-Output "STAGE_FAIL hash_mismatch file=$name expected=$($expected[$name]) actual=$actual"
        exit 3
    }
}

# close a card that is still open (stale or superseded), then wait it out
$stale = @(Get-MLPopupProcess)
foreach ($p in $stale) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
for ($i = 0; $i -lt 30 -and @(Get-MLPopupProcess).Count -gt 0; $i++) { Start-Sleep -Milliseconds 100 }

# Use the completer's install-scoped lock so replacing a card cannot interleave
# with publishing an answer for its predecessor.
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($PSScriptRoot.ToLowerInvariant()))).Replace('-', '') }
finally { $sha.Dispose() }
$mutex = New-Object Threading.Mutex($false, ('muselink-complete-' + $key))
$held = $false
try {
    try { $held = $mutex.WaitOne(10000) } catch [Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { Write-Output 'STAGE_FAIL completer_busy'; exit 3 }
    foreach ($n in 'popup-done.txt', 'popup-result.txt', 'popup-kind.txt') {
        Remove-Item -LiteralPath (Get-MLPath $n) -Force -ErrorAction SilentlyContinue
    }
    if (-not $expected.ContainsKey('popup-expires-in.txt')) {
        Remove-Item -LiteralPath (Get-MLPath 'popup-expires-in.txt') -Force -ErrorAction SilentlyContinue
    }
    Write-MLAtomic (Get-MLPath 'popup-reqid.txt') $ReqId
    $mode = (Read-MLUtf8 (Get-MLPath 'popup-mode.txt')).Trim().ToLowerInvariant()
    $options = @((Read-MLUtf8 (Get-MLPath 'popup-options.txt')) -split '\r?\n' | Where-Object { $_.Trim() })
    if ($mode -notin @('info', 'choice', 'input')) { $mode = if ($options.Count) { 'choice' } else { 'info' } }
    if ($mode -eq 'choice' -and -not $options.Count) { $mode = 'input' }
    $summary = ((Read-MLUtf8 (Get-MLPath 'popup-text.txt')) -split '\r?\n')[0]
    $summary = [regex]::Replace($summary, '[\p{Cc}\p{Cf}]', '')
    if ($summary.Length -gt 120) {
        $summary = $summary.Substring(0, 120)
        if ([char]::IsHighSurrogate($summary[$summary.Length - 1])) { $summary = $summary.Substring(0, $summary.Length - 1) }
    }
    $armed = @{ req_id = $ReqId; mode = $mode; summary = $summary; at = [DateTimeOffset]::Now.ToString('o') }
    Write-MLAtomic (Get-MLPath 'popup-armed.json') ($armed | ConvertTo-Json -Compress)
    Write-MLAtomic (Get-MLPath 'popup-pending.txt') $ReqId
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
schtasks /Run /TN popup | Out-Null
Write-Output ("STAGED req=$ReqId files=" + ($expected.Keys -join ',') + " closed_stale=" + $stale.Count)
