# muselink-identity.ps1 - identity protocol v3, PC side (dot-sourced by
# muse-call.ps1 after muselink-common.ps1).
# Conventions: docs/protocol.en.md section 3. Windows PowerShell 5.1,
# .NET Framework only, ASCII-only source (PS 5.1 reads BOM-less files as ANSI).
#
# Requirements
#  1. Identity file: <tool dir>\identities\<agent_id>.json =
#     {v:1, agent_id, key_id, key (base64, 32 bytes), enrolled_at}, written
#     by the VM (scp + icacls). Get-MLIdentity validates every field
#     (agent_id ^[a-z][a-z0-9-]{1,31}$, key_id "<agent_id>#<n>", key decodes
#     to exactly 32 bytes) and returns $null when the file is absent. It
#     warns on stderr (does not fail) when the file ACL grants read to
#     anyone besides the current user, SYSTEM and Administrators.
#  2. Get-MLHmac: base64(HMACSHA256(key, UTF8(domain + text))). Use
#     [Text.UTF8Encoding]::new($false) explicitly. Must reproduce every case
#     in spec\test-vectors\identity.json (tests\identity\check-ps-signing.ps1).
#  3. New-MLSignedRequest -Identity -Inner: Inner is an [ordered] hashtable
#     with the inner keys of docs/protocol.en.md section 3 (ts = [int64] epoch
#     seconds from [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()). Serialize
#     ONCE: $signed = $Inner | ConvertTo-Json -Depth 10 -Compress. The MAC
#     is computed over exactly that string, and that same string object is
#     put into the outer envelope (ConvertTo-Json of the outer object
#     escapes it; JSON string escaping round-trips exactly). ALWAYS pass
#     -Depth: the default depth 2 silently turns nested objects into
#     "System.Collections.Hashtable" text. Empty payload must serialize as
#     {} (use [ordered]@{} / New-Object PSObject), never as null or [].
#     grant_id: $null when not given. Returns the outer JSON text.
#  4. New-MLEnrollRequest -AgentId -Client: same inner shape, type
#     "enroll", outer auth = $null. No key involved.
#  5. Test-MLSignedReply -Identity -ReplyText -ReqId -Seq: returns
#     @{ ok; inner; reason }. ok only if: outer v == 3, keys exactly as
#     REPLY_OUTER_KEYS, alg HMAC-SHA256, key_id prefix == agent_id, MAC
#     matches (constant-time compare: XOR-accumulate over all bytes, no
#     early exit; .NET Framework has no FixedTimeEquals), inner parses,
#     inner req_id/agent_id/seq equal outer and the expected values. Any
#     exception -> ok $false with a reason, never a throw to the caller.
#  6. New-MLReqId -AgentId: "<agent_id>_<yyyyMMdd_HHmmss>_<6 hex>" (hex from
#     a RNGCryptoServiceProvider, not Get-Random); must match the req_id
#     charset in muselink-common.ps1 (Test-MLReqId).
#  7. Nothing in this file writes the key anywhere, prints it, or puts it
#     on a command line.
#  8. (review 2026-11-12) PowerShell's -ne / -match / -eq ignore case.
#     Every id comparison here uses -cne / -cnotmatch / -ceq.

$script:MLIdUtf8 = New-Object System.Text.UTF8Encoding($false)
$script:MLReplyOuter = @('v', 'req_id', 'seq', 'agent_id', 'signed', 'auth')
$script:MLReplyInner = @('req_id', 'seq', 'agent_id', 'final', 'status', 'replied_at', 'from', 'reply_protocol', 'text')

function Get-MLIdentityDir([string]$Dir) {
    if ($Dir) { return $Dir }
    $cmd = Get-Command Get-MLDir -ErrorAction SilentlyContinue
    if ($cmd) { return (Join-Path (Get-MLDir) 'identities') }
    return (Join-Path $PSScriptRoot 'identities')
}

function Test-MLIdentityAcl([string]$path) {
    try {
        $acl = Get-Acl -LiteralPath $path
        $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $allowed = @($me, 'NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators')
        foreach ($r in @($acl.Access)) {
            if ([string]$r.AccessControlType -ne 'Allow') { continue }
            $id = [string]$r.IdentityReference.Value
            $known = $false
            foreach ($a in $allowed) { if ($a -ieq $id) { $known = $true; break } }
            if (-not $known) {
                Write-Warning ("identity file ACL grants access to " + $id + ": " + $path)
                break
            }
        }
    } catch {}
}

function Get-MLIdentity([string]$AgentId, [string]$Dir = '') {
    if ($AgentId -cnotmatch '^[a-z][a-z0-9-]{1,31}$') { throw 'bad agent id' }
    $d = Get-MLIdentityDir $Dir
    $p = Join-Path $d ($AgentId + '.json')
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    $txt = [System.IO.File]::ReadAllText($p, $script:MLIdUtf8)
    $o = $txt | ConvertFrom-Json
    if ($null -eq $o) { throw 'identity file is empty' }
    if ([int]$o.v -ne 1) { throw 'identity version is not 1' }
    if ([string]$o.agent_id -cne $AgentId) { throw 'identity agent_id does not match the file name' }
    if ([string]$o.agent_id -cnotmatch '^[a-z][a-z0-9-]{1,31}$') { throw 'bad identity agent_id' }
    $kidRx = '^' + [regex]::Escape([string]$o.agent_id) + '#[1-9][0-9]{0,5}$'
    if ([string]$o.key_id -cnotmatch $kidRx) { throw 'bad identity key_id' }
    if (-not ($o.PSObject.Properties.Name -contains 'key')) { throw 'identity has no key' }
    $raw = $null
    try { $raw = [Convert]::FromBase64String([string]$o.key) } catch { throw 'identity key is not base64' }
    if ($raw.Length -ne 32) { throw 'identity key is not 32 bytes' }
    if (-not ($o.PSObject.Properties.Name -contains 'enrolled_at')) { throw 'identity has no enrolled_at' }
    $o | Add-Member -NotePropertyName KeyBytes -NotePropertyValue $raw -Force
    Test-MLIdentityAcl $p
    return $o
}

function Get-MLHmac([byte[]]$Key, [string]$Domain, [string]$Text) {
    $h = New-Object System.Security.Cryptography.HMACSHA256(, $Key)
    try {
        $bytes = $script:MLIdUtf8.GetBytes($Domain + $Text)
        return [Convert]::ToBase64String($h.ComputeHash($bytes))
    } finally {
        $h.Dispose()
    }
}

function Get-MLKeyBytes($Identity) {
    if ($Identity.PSObject.Properties.Name -contains 'KeyBytes') { return [byte[]]$Identity.KeyBytes }
    return [Convert]::FromBase64String([string]$Identity.key)
}

function New-MLReqId([string]$AgentId) {
    if ($AgentId -cnotmatch '^[a-z][a-z0-9-]{1,31}$') { throw 'bad agent id' }
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    try {
        $b = New-Object byte[] 3
        $rng.GetBytes($b)
    } finally {
        $rng.Dispose()
    }
    $hex = (($b | ForEach-Object { $_.ToString('x2') }) -join '')
    $stamp = [DateTime]::Now.ToString('yyyyMMdd_HHmmss')
    return ($AgentId + '_' + $stamp + '_' + $hex)
}

function Set-MLInnerDefaults($Inner, [string]$AgentId) {
    if ($Inner -is [System.Collections.IDictionary]) {
        if (-not $Inner.Contains('ts')) { $Inner['ts'] = [int64][DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
        if (-not $Inner.Contains('req_id')) { $Inner['req_id'] = (New-MLReqId $AgentId) }
        if (-not $Inner.Contains('agent_id')) { $Inner['agent_id'] = $AgentId }
        if (-not $Inner.Contains('reply_protocol')) { $Inner['reply_protocol'] = 3 }
        return $Inner
    }
    if (-not ($Inner.PSObject.Properties.Name -contains 'ts')) {
        $Inner | Add-Member -NotePropertyName ts -NotePropertyValue ([int64][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Force
    }
    if (-not ($Inner.PSObject.Properties.Name -contains 'req_id')) {
        $Inner | Add-Member -NotePropertyName req_id -NotePropertyValue (New-MLReqId $AgentId) -Force
    }
    if (-not ($Inner.PSObject.Properties.Name -contains 'agent_id')) {
        $Inner | Add-Member -NotePropertyName agent_id -NotePropertyValue $AgentId -Force
    }
    if (-not ($Inner.PSObject.Properties.Name -contains 'reply_protocol')) {
        $Inner | Add-Member -NotePropertyName reply_protocol -NotePropertyValue 3 -Force
    }
    return $Inner
}

function New-MLSignedRequest($Identity, $Inner) {
    $agentId = [string]$Identity.agent_id
    $Inner = Set-MLInnerDefaults $Inner $agentId
    $signed = $Inner | ConvertTo-Json -Depth 10 -Compress
    $sig = Get-MLHmac (Get-MLKeyBytes $Identity) "muselink-req-v3`n" $signed
    $outer = [ordered]@{
        v = 3
        req_id = [string]$Inner.req_id
        agent_id = $agentId
        signed = $signed
        auth = [ordered]@{ alg = 'HMAC-SHA256'; key_id = [string]$Identity.key_id; sig = $sig }
    }
    return ($outer | ConvertTo-Json -Depth 10 -Compress)
}

function New-MLEnrollRequest([string]$AgentId, $Inner) {
    if ($AgentId -cnotmatch '^[a-z][a-z0-9-]{1,31}$') { throw 'bad agent id' }
    $Inner = Set-MLInnerDefaults $Inner $AgentId
    if ($Inner -is [System.Collections.IDictionary]) {
        $Inner['type'] = 'enroll'
    } else {
        $Inner | Add-Member -NotePropertyName type -NotePropertyValue 'enroll' -Force
    }
    $signed = $Inner | ConvertTo-Json -Depth 10 -Compress
    $outer = [ordered]@{
        v = 3
        req_id = [string]$Inner.req_id
        agent_id = $AgentId
        signed = $signed
        auth = $null
    }
    return ($outer | ConvertTo-Json -Depth 10 -Compress)
}

function Test-MLByteEqual([byte[]]$a, [byte[]]$b) {
    if ($a.Length -ne $b.Length) { return $false }
    $d = 0
    for ($i = 0; $i -lt $a.Length; $i++) { $d = $d -bor ($a[$i] -bxor $b[$i]) }
    return ($d -eq 0)
}

function Test-MLExactKeys($Obj, [string[]]$Expected) {
    $names = @($Obj.PSObject.Properties | ForEach-Object { $_.Name })
    if ($names.Count -ne $Expected.Count) { return $false }
    foreach ($n in $Expected) { if ($names -notcontains $n) { return $false } }
    return $true
}

function Test-MLSignedReply($Identity, [string]$ReplyText, [string]$ReqId, [int]$Seq) {
    try {
        $outer = $ReplyText | ConvertFrom-Json
        if ($null -eq $outer) { return @{ ok = $false; inner = $null; reason = 'empty' } }
        if (-not (Test-MLExactKeys $outer $script:MLReplyOuter)) { return @{ ok = $false; inner = $null; reason = 'bad_outer_keys' } }
        if ([int]$outer.v -ne 3) { return @{ ok = $false; inner = $null; reason = 'bad_version' } }
        if ([string]$outer.agent_id -cne [string]$Identity.agent_id) { return @{ ok = $false; inner = $null; reason = 'wrong_agent' } }
        if (-not (Test-MLExactKeys $outer.auth @('alg', 'key_id', 'sig'))) { return @{ ok = $false; inner = $null; reason = 'bad_auth_keys' } }
        if ([string]$outer.auth.alg -ne 'HMAC-SHA256') { return @{ ok = $false; inner = $null; reason = 'bad_alg' } }
        $kidRx = '^' + [regex]::Escape([string]$Identity.agent_id) + '#[1-9][0-9]{0,5}$'
        if ([string]$outer.auth.key_id -cnotmatch $kidRx) { return @{ ok = $false; inner = $null; reason = 'bad_key_id' } }
        $want = Get-MLHmac (Get-MLKeyBytes $Identity) "muselink-reply-v3`n" ([string]$outer.signed)
        $gotBytes = $null
        $wantBytes = $null
        try {
            $gotBytes = [Convert]::FromBase64String([string]$outer.auth.sig)
            $wantBytes = [Convert]::FromBase64String($want)
        } catch { return @{ ok = $false; inner = $null; reason = 'bad_encoding' } }
        if (-not (Test-MLByteEqual $gotBytes $wantBytes)) { return @{ ok = $false; inner = $null; reason = 'bad_signature' } }
        $inner = [string]$outer.signed | ConvertFrom-Json
        if (-not (Test-MLExactKeys $inner $script:MLReplyInner)) { return @{ ok = $false; inner = $null; reason = 'bad_inner_keys' } }
        if ([string]$inner.req_id -cne [string]$outer.req_id) { return @{ ok = $false; inner = $null; reason = 'id_mismatch' } }
        if ([string]$inner.agent_id -cne [string]$outer.agent_id) { return @{ ok = $false; inner = $null; reason = 'id_mismatch' } }
        if ([int]$inner.seq -ne [int]$outer.seq) { return @{ ok = $false; inner = $null; reason = 'id_mismatch' } }
        if ([string]$outer.req_id -cne $ReqId) { return @{ ok = $false; inner = $null; reason = 'req_mismatch' } }
        if ([string]$outer.agent_id -cne [string]$Identity.agent_id) { return @{ ok = $false; inner = $null; reason = 'agent_mismatch' } }
        if ([int]$outer.seq -ne $Seq) { return @{ ok = $false; inner = $null; reason = 'seq_mismatch' } }
        return @{ ok = $true; inner = $inner; reason = '' }
    } catch {
        return @{ ok = $false; inner = $null; reason = $_.Exception.Message }
    }
}
