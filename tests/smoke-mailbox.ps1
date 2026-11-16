# smoke-mailbox.ps1 - sandbox smoke test of the popup/wait mailbox (no tasks,
# no popup windows): normal claim, stale trigger, foreign done, stranded-answer
# recovery. Runs in a temp copy of the scripts.
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$t = Join-Path $env:TEMP ('ml-mailbox-' + $PID)
New-Item -ItemType Directory $t | Out-Null
foreach ($n in 'muselink-common.ps1', 'Complete-MuseWait.ps1', 'wait-popup.ps1', 'check-popup.ps1') { Copy-Item (Join-Path $repo "pc\$n") $t }
function Run([string]$f, [string[]]$a) { (& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $t $f) @a 2>&1 | Out-String).Trim() }
function P([string]$n) { Join-Path $t $n }
$fail = 0
function Check([string]$name, [bool]$ok, [string]$detail) { if ($ok) { "PASS $name" } else { "FAIL $name :: $detail"; $script:fail++ } }

# 1 normal delivery and claim
Set-Content (P 'popup-pending.txt') 'req_A_1' -NoNewline
Run 'Complete-MuseWait.ps1' @('-Kind', 'cmd_done', '-Result', 'hello', '-ReqId', 'req_A_1') | Out-Null
$o = Run 'wait-popup.ps1' @('-ReqId', 'req_A_1', '-TimeoutSeconds', '3')
Check 'claim' ($o -match 'STATUS:DONE' -and $o -match 'hello' -and $o -match 'REQ_ID:req_A_1') $o
$o = Run 'check-popup.ps1' @('-ReqId', 'req_A_1')
Check 'no double consume' ($o -match 'STATE:NO_PENDING') $o

# 2 a late trigger of an older wait cannot deliver into a newer one
Set-Content (P 'popup-pending.txt') 'req_B_2' -NoNewline
Remove-Item (P 'popup-done.txt')
Run 'Complete-MuseWait.ps1' @('-Kind', 'cmd_done', '-Result', 'stale', '-ReqId', 'req_A_1') | Out-Null
Check 'stale trigger ignored' (-not (Test-Path (P 'popup-done.txt'))) 'done file was written'

# 3 an answer bound to another request is never claimed
Set-Content (P 'popup-done.txt') "2026-11-12T10:00:00+01:00`nREQ:req_A_1`n" -NoNewline
$o = Run 'wait-popup.ps1' @('-ReqId', 'req_B_2', '-TimeoutSeconds', '2')
Check 'foreign done refused' ($o -match 'STATUS:REQ_MISMATCH' -and (Test-Path (P 'popup-pending.txt'))) $o

# 4 stranded answer: no pending, done bound to B, 90 s old
Remove-Item (P 'popup-pending.txt')
Set-Content (P 'popup-done.txt') "2026-11-12T10:00:00+01:00`nREQ:req_B_2`n" -NoNewline
Set-Content (P 'popup-result.txt') 'answer-B' -NoNewline
(Get-Item (P 'popup-done.txt')).LastWriteTime = (Get-Date).AddSeconds(-90)
$o = Run 'check-popup.ps1' @('-ReqId', 'req_B_2')
Check 'recovery for owner' ($o -match 'STATE:DONE' -and $o -match 'RECOVERED_UNCLAIMED' -and $o -match 'answer-B') $o
$o = Run 'check-popup.ps1' @('-ReqId', 'req_C_3')
Check 'no recovery for others' ($o -match 'STATE:NO_PENDING') $o
(Get-Item (P 'popup-done.txt')).LastWriteTime = Get-Date
$o = Run 'check-popup.ps1' @('-ReqId', 'req_B_2')
Check 'fresh done left to the main path' ($o -match 'STATE:NO_PENDING') $o

Remove-Item -Recurse -Force $t
if ($fail) { "FAILED $fail"; exit 1 } else { 'ALL PASS' }
