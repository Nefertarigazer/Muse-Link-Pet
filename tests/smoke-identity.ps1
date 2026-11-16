# smoke-identity.ps1 - PC-side identity v3 smoke test (no production mailbox).
# Checks: a signed request is posted; a Python-signed reply verifies; a forged
# reply is quarantined and does not end the request; -Check reports REJECTED
# and PENDING_ENROLL. Runs in a temp tool dir.
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$t = Join-Path $env:TEMP ('ml-id-' + $PID)
New-Item -ItemType Directory $t | Out-Null
foreach ($n in 'muselink-common.ps1', 'muselink-identity.ps1', 'muse-call.ps1') { Copy-Item (Join-Path $repo "pc\$n") $t }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$v = [IO.File]::ReadAllText((Join-Path $repo 'spec\test-vectors\identity.json'), $utf8) | ConvertFrom-Json
New-Item -ItemType Directory (Join-Path $t 'identities') | Out-Null
$rec = [ordered]@{ v = 1; agent_id = 'codex'; key_id = 'codex#1'; key = $v.key_b64; enrolled_at = 0 }
[IO.File]::WriteAllText((Join-Path $t 'identities\codex.json'), ($rec | ConvertTo-Json -Compress), $utf8)
$cfg = @{ bridge = @{ root = (Join-Path $t 'muse-bridge'); agent = 'codex' } } | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText((Join-Path $t 'muselink.json'), $cfg, $utf8)
$fail = 0
function Check([string]$name, [bool]$ok, [string]$detail) { if ($ok) { "PASS $name" } else { "FAIL $name :: $detail"; $script:fail++ } }
function Call([string[]]$a) { (& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $t 'muse-call.ps1') @a 2>&1 | Out-String) }

$o = Call @('-As', 'codex', '-Type', 'ping', '-Text', 'hi', '-NoWait')
$id = [regex]::Match($o, 'req=(\S+)').Groups[1].Value
Check 'signed request posted' ([bool]$id) $o
$envFile = Join-Path $t "muse-bridge\inbox\$id.json"
$envObj = $null
try { $envObj = ([IO.File]::ReadAllText($envFile, $utf8) | ConvertFrom-Json) } catch {}
Check 'request is v3 signed' ($envObj -and [int]$envObj.v -eq 3 -and $null -ne $envObj.auth -and [string]$envObj.auth.key_id -eq 'codex#1') $o
Move-Item $envFile (Join-Path $t "muse-bridge\claimed\$id.json") -Force

$py = Join-Path $repo 'tests\identity\ps_roundtrip.py'
$rf = Join-Path $t 'reply.json'
& python $py make-reply $rf $v.key_b64 'codex' 'codex#1' $id '1' | Out-Null
Copy-Item $rf (Join-Path $t "muse-bridge\outbox\$id.001.reply.json")
$o = Call @('-As', 'codex', '-Check', $id)
Check 'signed reply verifies' ($o -match 'STATE:FINAL_READ' -and $o -match 'MUSE REPLY BEGIN') $o

$o = Call @('-As', 'codex', '-Type', 'ping', '-Text', 'hi2', '-NoWait')
$id2 = [regex]::Match($o, 'req=(\S+)').Groups[1].Value
Move-Item (Join-Path $t "muse-bridge\inbox\$id2.json") (Join-Path $t "muse-bridge\claimed\$id2.json") -Force
$inner = [ordered]@{ req_id = $id2; seq = 1; agent_id = 'codex'; final = $true; status = 'ok'; replied_at = 'x'; from = 'muse'; reply_protocol = 3; text = 'forged' }
$bad = [ordered]@{ v = 3; req_id = $id2; seq = 1; agent_id = 'codex'; signed = ($inner | ConvertTo-Json -Compress); auth = [ordered]@{ alg = 'HMAC-SHA256'; key_id = 'codex#1'; sig = ('A' * 43 + '=') } }
[IO.File]::WriteAllText((Join-Path $t "muse-bridge\outbox\$id2.001.reply.json"), ($bad | ConvertTo-Json -Compress), $utf8)
$o = Call @('-As', 'codex', '-Check', $id2)
Check 'forged reply is quarantined' ($o -match 'UNVERIFIED REPLY' -and -not ($o -match 'STATE:FINAL_READ')) $o
Check 'quarantine file moved' (@(Get-ChildItem (Join-Path $t 'muse-bridge\quarantine') -File).Count -ge 1) 'no quarantine file'

New-Item -ItemType Directory (Join-Path $t 'muse-bridge\rejected') -Force | Out-Null
New-Item -ItemType Directory (Join-Path $t 'muse-bridge\pending') -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $t 'muse-bridge\rejected\rej_req_1.json'), '{}', $utf8)
[IO.File]::WriteAllText((Join-Path $t 'muse-bridge\pending\pen_req_1.json'), '{}', $utf8)
$o = Call @('-As', 'codex', '-Check', 'rej_req_1')
Check 'check reports rejected' ($o -match 'STATE:REJECTED') $o
$o = Call @('-As', 'codex', '-Check', 'pen_req_1')
Check 'check reports pending enroll' ($o -match 'STATE:PENDING_ENROLL') $o

# ---- reviewer additions (2026-11-12)
$inboxDir = Join-Path $t 'muse-bridge\inbox'
[IO.File]::WriteAllText((Join-Path $t 'identities\broken.json'), '{"v":1,"agent_id":"broken","key_id":"broken#1","key":"AAAA","enrolled_at":0}', $utf8)
$before = @(Get-ChildItem $inboxDir -File).Count
$o = Call @('-As', 'broken', '-Type', 'ping', '-Text', 'x', '-NoWait')
$rc = $LASTEXITCODE
Check 'invalid identity file is an error, not a silent unsigned request' ($rc -eq 3 -and @(Get-ChildItem $inboxDir -File).Count -eq $before -and $o -match 'is invalid') "rc=$rc $o"
$o = Call @('-As', 'CODEX', '-Type', 'ping', '-Text', 'x', '-NoWait')
$rc = $LASTEXITCODE
Check 'agent ids are case-sensitive' ($rc -eq 3 -and @(Get-ChildItem $inboxDir -File).Count -eq $before) "rc=$rc $o"
$o = Call @('-As', 'notenrolled', '-Type', 'ping', '-Text', 'x', '-NoWait')
$id4 = [regex]::Match($o, 'req=(\S+)').Groups[1].Value
$e4 = $null; try { $e4 = [IO.File]::ReadAllText((Join-Path $inboxDir "$id4.json"), $utf8) | ConvertFrom-Json } catch {}
Check 'an unenrolled agent sends unsigned under its own name' ($e4 -and -not ($e4.PSObject.Properties.Name -contains 'v') -and [string]$e4.from -ceq 'notenrolled' -and $o -match 'UNSIGNED') $o
# reply for another request copied into this one's slot (outer req_id rewritten, inner signed) is refused
$o = Call @('-As', 'codex', '-Type', 'ping', '-Text', 'hi5', '-NoWait')
$id5 = [regex]::Match($o, 'req=(\S+)').Groups[1].Value
Move-Item (Join-Path $inboxDir "$id5.json") (Join-Path $t "muse-bridge\claimed\$id5.json") -Force
$rf2 = Join-Path $t 'reply2.json'
& python $py make-reply $rf2 $v.key_b64 'codex' 'codex#1' $id '1' | Out-Null
$moved = [IO.File]::ReadAllText($rf2, $utf8) | ConvertFrom-Json
$moved.req_id = $id5
[IO.File]::WriteAllText((Join-Path $t "muse-bridge\outbox\$id5.001.reply.json"), ($moved | ConvertTo-Json -Compress -Depth 5), $utf8)
$o = Call @('-As', 'codex', '-Check', $id5)
Check 'a reply re-addressed to another request is quarantined' ($o -match 'UNVERIFIED REPLY' -and -not ($o -match 'STATE:FINAL_READ')) $o

Remove-Item -Recurse -Force $t -ErrorAction SilentlyContinue
if ($fail) { "FAILED $fail"; exit 1 } else { 'ALL PASS' }
