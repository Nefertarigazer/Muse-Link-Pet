# smoke-muse-call.ps1 - sandbox test of the v2 reply semantics of muse-call:
# the blocking call must print the interim ACK and keep waiting for the final
# reply; -Check must report interim vs final correctly; v1 file names still
# work. Runs in a temp tool dir; nothing touches the real mailbox.
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$t = Join-Path $env:TEMP ('ml-call-' + $PID)
New-Item -ItemType Directory $t | Out-Null
foreach ($n in 'muselink-common.ps1', 'muse-call.ps1') { Copy-Item (Join-Path $repo "pc\$n") $t }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$fail = 0
function Check([string]$name, [bool]$ok, [string]$detail) { if ($ok) { "PASS $name" } else { "FAIL $name :: $detail"; $script:fail++ } }
function Call([string[]]$a) { (& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $t 'muse-call.ps1') @a 2>&1 | Out-String) }
function Reply([string]$id, [string]$name, [int]$seq, [bool]$final, [string]$status, [string]$text) {
    $o = [ordered]@{ req_id = $id; seq = $seq; final = $final; status = $status; text = $text }
    if ($seq -eq 0) { $o.Remove('seq'); $o.Remove('final') }
    $p = Join-Path $t "muse-bridge\outbox\$name"
    [IO.File]::WriteAllText($p + '.tmp', ($o | ConvertTo-Json), $utf8)
    Move-Item ($p + '.tmp') $p -Force
}

# blocking call: ACK after 2 s, final after 5 s
$job = Start-Job -ScriptBlock { param($s) & powershell -NoProfile -ExecutionPolicy Bypass -File $s -Type ping -Text hi -TimeoutSec 40 2>&1 | Out-String } -ArgumentList (Join-Path $t 'muse-call.ps1')
$id = $null
for ($i = 0; $i -lt 40 -and -not $id; $i++) {
    Start-Sleep -Milliseconds 250
    $f = Get-ChildItem (Join-Path $t 'muse-bridge\inbox') -Filter *.json -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($f) { $id = $f.BaseName }
}
Check 'request posted' ([bool]$id) 'no inbox file'
Move-Item (Join-Path $t "muse-bridge\inbox\$id.json") (Join-Path $t "muse-bridge\claimed\$id.json")
Start-Sleep 2; Reply $id "$id.001.reply.json" 1 $false 'info' 'ACK working'
Start-Sleep 3; Reply $id "$id.002.reply.json" 2 $true 'ok' 'FINAL answer'
$out = Receive-Job (Wait-Job $job -Timeout 60)
Check 'blocking call printed ACK' ($out -match 'ACK working' -and $out -match 'interim') $out
Check 'blocking call waited for the final reply' ($out -match 'FINAL answer' -and $out -match 'MUSE_CALL_DONE') $out
Check 'request closed' (Test-Path (Join-Path $t "muse-bridge\done\$id.json")) 'claimed not moved to done'
$o = Call @('-Check', $id)
Check 'check after final' ($o -match 'STATE:ALREADY_READ_FINAL') $o

# -NoWait + late replies + -Check
$o = Call @('-Type', 'question', '-Text', 'q', '-NoWait')
$id2 = [regex]::Match($o, 'req=(\S+)').Groups[1].Value
Reply $id2 "$id2.001.reply.json" 1 $false 'info' 'ACK2'
$o = Call @('-Check', $id2)
Check 'check reports interim only' ($o -match 'ACK2' -and $o -match 'STATE:INTERIM_READ') $o
$o = Call @('-Check', $id2)
Check 'check with nothing new waits for final' ($o -match 'STATE:WAITING_FOR_FINAL') $o
Reply $id2 "$id2.002.reply.json" 2 $true 'error' 'mismatch details'
$o = Call @('-Check', $id2)
Check 'check reads final' ($o -match 'mismatch details' -and $o -match 'STATE:FINAL_READ') $o

# v1 names (old VM side): info first, final later under the same name
$o = Call @('-Type', 'ping', '-Text', 'v1', '-NoWait')
$id3 = [regex]::Match($o, 'req=(\S+)').Groups[1].Value
Reply $id3 "$id3.reply.json" 0 $false 'info' 'v1 ack'
$o = Call @('-Check', $id3)
Check 'v1 info is interim' ($o -match 'STATE:INTERIM_READ') $o
Reply $id3 "$id3.reply.json" 0 $true 'ok' 'v1 final'
$o = Call @('-Check', $id3)
Check 'v1 final read and archived without overwriting' ($o -match 'STATE:FINAL_READ' -and @(Get-ChildItem (Join-Path $t 'muse-bridge\archive') -Filter "$id3*").Count -eq 2) $o

Remove-Item -Recurse -Force $t
if ($fail) { "FAILED $fail"; exit 1 } else { 'ALL PASS' }
