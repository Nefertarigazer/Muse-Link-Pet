# smoke-executor.ps1 - run a sandbox executor host on a spare port and check
# the gates end to end: token required, shell disabled, script allow-list,
# argv and stdin transports, token file ACL, a read-only agent-desktop call.
# Does not touch the production host (different port, temp tool dir).
param([int]$Port = 18799)
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$t = Join-Path $env:TEMP ('ml-exec-' + $PID)
New-Item -ItemType Directory $t | Out-Null
foreach ($n in 'executor-host.py', 'executor-call.py') { Copy-Item (Join-Path $repo "pc\$n") $t }
$cfg = @{ executor = @{ port = $Port; allow_shell = $false; scripts = @('muse-shot.ps1') } } | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText((Join-Path $t 'muselink.json'), $cfg, (New-Object System.Text.UTF8Encoding($false)))
$fail = 0
function Check([string]$name, [bool]$ok, [string]$detail) { if ($ok) { "PASS $name" } else { "FAIL $name :: $detail"; $script:fail++ } }
function Call([string]$json) {
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
    (& python (Join-Path $t 'executor-call.py') $b64 2>&1 | Out-String).Trim()
}

$host_ = Start-Process python -ArgumentList ('"' + (Join-Path $t 'executor-host.py') + '"') -PassThru -WindowStyle Hidden
try {
    for ($i = 0; $i -lt 40 -and -not (Test-Path (Join-Path $t 'executor-token.txt')); $i++) { Start-Sleep -Milliseconds 250 }
    Start-Sleep -Milliseconds 500
    $o = Call '{"kind":"ping"}'
    Check 'ping with token' ($o -match '"pong"' -and $o -match '4\.0\.0') $o
    # raw client without the token (a file: PS 5.1 mangles quotes in native args)
    $raw = Join-Path $t 'raw.py'
    [IO.File]::WriteAllText($raw, "import socket,sys`ns=socket.create_connection(('127.0.0.1',int(sys.argv[1])))`ns.sendall(b'{""kind"":""ping""}\n')`nprint(s.recv(4096).decode())`n")
    $o = (& python $raw $Port 2>&1 | Out-String)
    Check 'no token -> denied' ($o -match 'AUTH_FAILED') $o
    $o = Call '{"kind":"shell","argv":["cmd","/c","echo hi"]}'
    Check 'shell disabled' ($o -match 'SHELL_DISABLED') $o
    $o = Call '{"kind":"script","name":"evil.ps1"}'
    Check 'script allow-list' ($o -match 'SCRIPT_NOT_ALLOWED') $o
    $o = ('{"kind":"info"}' | & python (Join-Path $t 'executor-call.py') - 2>&1 | Out-String).Trim()
    Check 'stdin transport' ($o -match '\\"version\\": \\"4\.0\.0\\"' -or $o -match 'version') $o
    $o = Call '{"kind":"ad","argv":["list-windows"],"timeout":30}'
    Check 'read-only ad call' ($o -match '"ok": true') $o.Substring(0, [Math]::Min(300, $o.Length))
    $acl = (& icacls (Join-Path $t 'executor-token.txt') | Out-String)
    $lines = @($acl -split "`n" | Where-Object { $_ -match ':\(' })
    Check 'token ACL: user + SYSTEM only' ($lines.Count -eq 2 -and $acl -match 'SYSTEM' -and $acl -match [regex]::Escape($env:USERNAME)) $acl
    $ev = Get-Content (Join-Path $t 'executor-events.jsonl') -Raw
    Check 'events carry a timezone' ($ev -match '"ts": "\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d"') $ev.Substring(0, [Math]::Min(200, $ev.Length))
    Check 'auth failure logged without secrets' ($ev -match 'auth_fail' -and $ev -notmatch (Get-Content (Join-Path $t 'executor-token.txt'))) 'see events'
} finally {
    Stop-Process -Id $host_.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $t -ErrorAction SilentlyContinue
}
if ($fail) { "FAILED $fail"; exit 1 } else { 'ALL PASS' }
