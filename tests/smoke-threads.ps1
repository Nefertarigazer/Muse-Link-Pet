# smoke-threads.ps1 - PC-side smoke test for pc\mlindex.ps1.
# What: in a temp tool dir with a synthetic mailbox, run mlindex.ps1 sync,
# stats, threads, search (a Chinese word), verify --sample 5; then muse-call
# -Thread t1 -NoWait and check the request lands in thread t1 after sync.
# Pass: every command exits 0 with one JSON line; 'ALL PASS' at the end.
# Never touches the production tool dir. ASCII only.
$ErrorActionPreference = 'Continue'
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {}
$repo = Split-Path -Parent $PSScriptRoot
$t = Join-Path $env:TEMP ('ml-threads-' + $PID)
if (Test-Path -LiteralPath $t) { Remove-Item -Recurse -Force $t }
New-Item -ItemType Directory $t | Out-Null
Copy-Item (Join-Path $repo 'pc\mlindex.ps1') $t
Copy-Item (Join-Path $repo 'pc\mlindex') (Join-Path $t 'mlindex') -Recurse
Copy-Item (Join-Path $repo 'pc\muse-call.ps1') $t
Copy-Item (Join-Path $repo 'pc\muselink-common.ps1') $t

$bridge = Join-Path $t 'muse-bridge'
$python = (Get-Command python).Source
$utf8 = New-Object System.Text.UTF8Encoding($false)
$cfg = [ordered]@{
    executor = [ordered]@{ python = $python }
    bridge = [ordered]@{ root = $bridge }
    index = [ordered]@{ path = '' }
}
[System.IO.File]::WriteAllText((Join-Path $t 'muselink.json'), ($cfg | ConvertTo-Json -Depth 5), $utf8)

$fail = 0
function Check([string]$name, [bool]$ok, [string]$detail) {
    if ($ok) { [Console]::WriteLine('PASS ' + $name) }
    else { [Console]::WriteLine('FAIL ' + $name + ' :: ' + $detail); $script:fail++ }
}
function RunIndex([string[]]$a) {
    return ((& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $t 'mlindex.ps1') @a 2>&1 | Out-String).Trim())
}
function OneJson([string]$name, [string]$out) {
    $lines = @($out -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
    Check ($name + ' one JSON line') ($lines.Count -eq 1) $out
    if ($lines.Count -ne 1) { return $null }
    try { return ($lines[0] | ConvertFrom-Json) }
    catch { Check ($name + ' parses as JSON') $false $out; return $null }
}

# Synthetic mailbox, built by tests\threads\_mailbox.py (never production).
$seed = Join-Path $t 'seed.py'
$seedCode = @'
import os, sys
sys.path.insert(0, os.path.join(os.environ['ML_REPO'], 'tests', 'threads'))
from _mailbox import Mailbox
m = Mailbox(os.environ['ML_BRIDGE'])
m.write_request('codex_20261112_101500_aaaaaa', 'sent', v=2, from_='codex',
                type_='message', text='hello thread', thread_id='t1')
m.write_request('kiro_20261112_101500_bbbbbb', 'claimed', v=2, from_='kiro',
                type_='publish-release', text='release notes',
                payload={'version': '1.0.0', 'package_path': 'D:/out/a.zip',
                         'package_size': 10, 'sha256': 'a' * 64})
m.write_reply('kiro_20261112_101500_bbbbbb', 1, 'archive', status='ok',
              final=True, text='\u4e2d\u6587\u53d1\u5e03\u8bf4\u660e')
'@
[System.IO.File]::WriteAllText($seed, $seedCode, $utf8)
$env:ML_REPO = $repo
$env:ML_BRIDGE = $bridge
& $python $seed
Check 'synthetic mailbox seeded' ($LASTEXITCODE -eq 0) 'seed.py failed'

$out = RunIndex @('sync')
$j = OneJson 'sync' $out
Check 'sync ok' ($null -ne $j -and $j.ok -eq $true) $out
Check 'sync saw files' ($null -ne $j -and [int]$j.files -ge 3) $out

$out = RunIndex @('stats')
$j = OneJson 'stats' $out
Check 'stats fts true' ($null -ne $j -and $j.fts -eq $true) $out
Check 'stats messages' ($null -ne $j -and [int]$j.messages -ge 3) $out

$out = RunIndex @('threads')
$j = OneJson 'threads' $out
$ids = @()
if ($null -ne $j) { $ids = @($j.threads | ForEach-Object { [string]$_.id }) }
Check 'thread t1 listed' ($ids -contains 't1') $out
Check 'default release thread listed' ($ids -contains 'kiro/release') $out

$q = [string][char]0x4E2D + [string][char]0x6587 + [string][char]0x53D1
$out = RunIndex @('search', $q)
$j = OneJson 'search' $out
$hits = 0
if ($null -ne $j) { $hits = @($j.results).Count }
Check 'search finds Chinese substring' ($hits -ge 1) $out

$out = RunIndex @('verify', '--sample', '5')
$j = OneJson 'verify' $out
Check 'verify sample ok' ($null -ne $j -and $j.ok -eq $true) $out

# muse-call -Thread on an unsigned (v1) request: the index must see t1.
$call = Join-Path $t 'muse-call.ps1'
$out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $call -Type message -Text 'thread smoke' -Thread t1 -NoWait 2>&1 | Out-String)
Check 'muse-call posted with -Thread' ($out -match 'MUSE_CALL_POSTED') $out
$firstId = [regex]::Match($out, 'req=(\S+)').Groups[1].Value
Check 'posted request id parsed' ($firstId -ne '') $out
$out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $call -Type message -Text 'reply link' -Thread t1 -ReplyTo $firstId -NoWait 2>&1 | Out-String)
Check 'muse-call posted with -ReplyTo' ($out -match 'MUSE_CALL_POSTED') $out
$out = (& powershell -NoProfile -ExecutionPolicy Bypass -File $call -Type message -Text 'bad' -Thread 'bad id' -NoWait 2>&1 | Out-String)
Check 'muse-call refuses a bad -Thread' ($LASTEXITCODE -eq 3) $out

$out = RunIndex @('sync')
$j = OneJson 'sync after muse-call' $out
Check 'second sync ok' ($null -ne $j -and $j.ok -eq $true) $out

$out = RunIndex @('thread', 't1')
$j = OneJson 'thread t1' $out
$texts = @()
if ($null -ne $j -and $null -ne $j.thread) { $texts = @($j.thread.messages | ForEach-Object { [string]$_.text }) }
Check 'new request landed in t1' ($texts -contains 'thread smoke') $out
Check 'reply link request landed in t1' ($texts -contains 'reply link') $out

$replyCheck = Join-Path $t 'replycheck.py'
$replyCode = @'
import os, sqlite3
path = os.path.join(os.environ['ML_BRIDGE'], 'index', 'threads.sqlite')
conn = sqlite3.connect(path)
row = conn.execute("SELECT reply_to FROM requests WHERE reply_to=?", (os.environ['ML_REPLY_TO'],)).fetchone()
conn.close()
print('REPLY_TO_OK' if row else 'REPLY_TO_MISSING')
'@
[System.IO.File]::WriteAllText($replyCheck, $replyCode, $utf8)
$env:ML_REPLY_TO = $firstId
$out = (& $python $replyCheck 2>&1 | Out-String).Trim()
Check 'reply_to reached the index' ($out -ceq 'REPLY_TO_OK') $out

Remove-Item -Recurse -Force $t
if ($fail) { 'FAILED ' + $fail; exit 1 } else { 'ALL PASS' }
