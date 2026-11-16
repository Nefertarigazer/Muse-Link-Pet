# check-ps-signing.ps1 - PowerShell half of the identity protocol, checked
# against the fixed vectors and against the Python verifier.
#   powershell -NoProfile -File tests\identity\check-ps-signing.ps1
# Prints one line per check and "OK all identity PS checks" at the end;
# exit 1 on any failure. Needs python on PATH (tests\identity\ps_roundtrip.py).
# Written by the reviewer with the skeleton (2026-11-12); ASCII-only source.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent (Split-Path -Parent $here)
. (Join-Path $root 'pc\muselink-common.ps1')
. (Join-Path $root 'pc\muselink-identity.ps1')
$utf8 = New-Object System.Text.UTF8Encoding($false)
$script:fail = 0
function Check([string]$name, [bool]$ok, [string]$detail = '') {
    if ($ok) { [Console]::WriteLine("ok   $name") }
    else { [Console]::WriteLine("FAIL $name $detail"); $script:fail++ }
}
function Sha256Hex([string]$s) {
    $h = [Security.Cryptography.SHA256]::Create()
    return (($h.ComputeHash($utf8.GetBytes($s)) | ForEach-Object { $_.ToString('x2') }) -join '')
}
function Invoke-Py([string[]]$argv) {
    # stderr of a native command becomes an error record; with 'Stop' that
    # would abort the whole script instead of failing one check
    $ErrorActionPreference = 'Continue'
    $out = & python (Join-Path $here 'ps_roundtrip.py') @argv 2>&1
    return @{ code = $LASTEXITCODE; out = (($out | ForEach-Object { [string]$_ }) -join "`n").Trim() }
}

# ---- 1. fixed vectors
$v = [IO.File]::ReadAllText((Join-Path $root 'spec\test-vectors\identity.json'), $utf8) | ConvertFrom-Json
$key = [Convert]::FromBase64String($v.key_b64)
foreach ($c in $v.cases) {
    $sig = $null
    try { $sig = Get-MLHmac $key ([string]$c.domain) ([string]$c.signed) } catch { $sig = "error: $($_.Exception.Message)" }
    Check "vector $($c.name)" ($sig -eq $c.sig_b64) "got=$sig want=$($c.sig_b64)"
}

# ---- 2. identity file in a temp dir
$tmp = Join-Path $env:TEMP ('mlid-ps-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $idFile = [ordered]@{ v = 1; agent_id = 'pstest'; key_id = 'pstest#1'; key = $v.key_b64; enrolled_at = 0 }
    [IO.File]::WriteAllText((Join-Path $tmp 'pstest.json'), ($idFile | ConvertTo-Json -Compress), $utf8)
    $id = $null
    try { $id = Get-MLIdentity 'pstest' $tmp } catch { Check 'Get-MLIdentity' $false $_.Exception.Message }
    Check 'Get-MLIdentity returns the identity' ($null -ne $id)
    $none = 'x'
    try { $none = Get-MLIdentity 'nobody' $tmp } catch { $none = 'threw' }
    Check 'Get-MLIdentity returns $null for a missing file' ($null -eq $none)

    # ---- 3. request ids
    $rid = $null
    try { $rid = New-MLReqId 'pstest' } catch { $rid = '' }
    Check 'New-MLReqId format' (($rid -like 'pstest_*') -and (Test-MLReqId $rid)) "got=$rid"

    if ($null -ne $id -and $rid) {
        # ---- 4. PS signs, Python verifies (tricky text, nested and empty payloads)
        $text = [regex]::Unescape('\u4f60\u597d Muse \"q\" back\\slash <t> & tab\tline\nline2 \ud83d\ude00')
        $payloads = @(
            [ordered]@{ a = [ordered]@{ b = @(1, 2) }; s = 'x' },
            [ordered]@{}
        )
        $n = 0
        foreach ($p in $payloads) {
            $n++
            $r = New-MLReqId 'pstest'
            $inner = [ordered]@{
                req_id = $r; agent_id = 'pstest'; ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
                created_at = [DateTimeOffset]::Now.ToString('o'); type = 'message'; text = $text
                payload = $p; grant_id = $null; reply_protocol = 3
            }
            $outer = New-MLSignedRequest $id $inner
            $f = Join-Path $tmp ($r + '.json')
            [IO.File]::WriteAllText($f, $outer, $utf8)
            $res = Invoke-Py @('verify', $f, $v.key_b64, 'pstest', 'pstest#1')
            Check "PS-signed request $n verifies in Python" ($res.code -eq 0 -and $res.out -eq ('OK message ' + (Sha256Hex $text))) $res.out
            # tamper with one character inside the signed text
            $bad = $outer.Replace('Muse', 'Mvse')
            $fb = Join-Path $tmp ($r + 'b.json')
            [IO.File]::WriteAllText($fb, $bad.Replace($r, $r + 'b'), $utf8)
            $res = Invoke-Py @('verify', $fb, $v.key_b64, 'pstest', 'pstest#1')
            Check "tampered request $n is refused" ($res.code -ne 0) $res.out
        }

        # ---- 5. Python signs a reply, PS verifies
        $rf = Join-Path $tmp 'reply.json'
        $res = Invoke-Py @('make-reply', $rf, $v.key_b64, 'pstest', 'pstest#1', $rid, '1')
        Check 'python made a reply' ($res.code -eq 0) $res.out
        if ($res.code -eq 0) {
            $rt = [IO.File]::ReadAllText($rf, $utf8)
            $t = Test-MLSignedReply $id $rt $rid 1
            Check 'reply verifies' ([bool]$t.ok) ([string]$t.reason)
            Check 'reply text intact' ($t.ok -and ([string]$t.inner.text).StartsWith([regex]::Unescape('\u5df2\u5b8c\u6210')))
            Check 'reply with the wrong seq is refused' (-not (Test-MLSignedReply $id $rt $rid 2).ok)
            Check 'reply for another request is refused' (-not (Test-MLSignedReply $id $rt ($rid + 'x') 1).ok)
            $tampered = $rt.Replace('"status\":\"ok\"', '"status\":\"error\"')
            if ($tampered -eq $rt) { $tampered = $rt.Replace('ok', 'OK') }
            Check 'tampered reply is refused' (-not (Test-MLSignedReply $id $tampered $rid 1).ok)
            $other = [ordered]@{ v = 1; agent_id = 'pstest'; key_id = 'pstest#1'; key = [Convert]::ToBase64String((New-Object byte[] 32)); enrolled_at = 0 }
            [IO.File]::WriteAllText((Join-Path $tmp 'pstest.json'), ($other | ConvertTo-Json -Compress), $utf8)
            $id2 = Get-MLIdentity 'pstest' $tmp
            Check 'reply under another key is refused' (-not (Test-MLSignedReply $id2 $rt $rid 1).ok)
            Check 'garbage never throws' (-not (Test-MLSignedReply $id 'not json' $rid 1).ok)
        }
    }
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:fail -gt 0) { [Console]::WriteLine("$($script:fail) identity PS check(s) FAILED"); exit 1 }
[Console]::WriteLine('OK all identity PS checks')
exit 0
