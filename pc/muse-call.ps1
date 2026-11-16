# muse-call.ps1 v2 - local agent -> Muse bridge client (PC side).
#
# A local agent (Codex, Kiro, WorkBuddy, ...) runs ONE foreground command: the
# script drops a request into the bridge inbox and blocks until Muse's FINAL
# reply arrives. Interim replies (the "received, working on it" ACK and any
# progress notes) are printed as they come and the wait continues, so the
# caller's command output ends with the real answer.
#
# Reply protocol v2 (docs/protocol.en.md section 2): every reply is its
# own file outbox\<req_id>.<seq>.reply.json with "seq" and "final". v1 files
# (<req_id>.reply.json, no seq) are still read: status "info" = interim.
#
# Modes:
#   muse-call -Type ping -Text "hello"
#   muse-call -Type message|question|task -Text "..." | -TextFile <utf8 file>
#   muse-call -Type publish-release -Version 1.4.0 -PackagePath <zip> -Sha256 <hex>
#             [-SetupPath <exe>] [-Notes "..."]
#   muse-call -NoWait ...            post only, print the req id, exit
#   muse-call -Check <req_id>        read replies that arrived later
#   muse-call -ListPendingReplies    replies nobody has read yet
#
# Many agent hosts cap one command (e.g. a few minutes). If yours does, call
# with -NoWait, then poll with -Check; replies are never lost.
# Exit codes: 0 final reply received / posted ok, 2 wait timeout (fetch later
# with -Check), 3 local validation failed.
#
# Identity protocol v3 (docs/protocol.en.md section 3; helpers in
# muselink-identity.ps1), implemented and reviewed 2026-11-12:
#  - New params: -As <agent_id> (else $env:MUSELINK_AGENT, else
#    muselink.json bridge.agent), -Grant <grant_id>, -Enroll (with
#    -DisplayName -Product -Model; host = $env:COMPUTERNAME, session_id =
#    a new GUID per process), -ListGrants. Add bridge.agent and
#    bridge.require_identity (default false) to the site config template
#    (config\muselink.site.example.json, section "pc").
#  - When the identity file exists: build the inner request (req_id from
#    New-MLReqId, ts, type, text, payload, grant_id, reply_protocol 3,
#    client) and post New-MLSignedRequest output instead of the v1 object.
#    No identity: post v1 as today and print "UNSIGNED: not enrolled" to
#    stderr; with bridge.require_identity true, exit 3 instead.
#  - -Enroll posts New-MLEnrollRequest and waits like any request. The
#    final reply is signed with the NEW key that the VM writes into
#    identities\ during the flow: re-read the identity file before verifying.
#  - Replies to a v3 request: Test-MLSignedReply on every file. Not ok ->
#    print "===== UNVERIFIED REPLY (<reason>) =====" + raw text, move the file
#    to <bridge>\quarantine\, do NOT count it as final, keep waiting. A
#    forged "final" reply must never end the wait or close the request.
#  - -Check: new states STATE:REJECTED (file in rejected\) and
#    STATE:PENDING_ENROLL (the enroll card is waiting for the user).
#  - -ListGrants: read <bridge>\directory\grants.json (informational mirror)
#    and print this agent's active grants: id, action, conditions, expiry,
#    uses left, mode.
#  - Never print or log the key; never put it on a command line.
#
# Threaded mailbox (docs/protocol.en.md section 5): -Thread <id> and
# -ReplyTo <req_id> add thread_id / reply_to to the request (v3 inner and the
# v1/v2 object, so unsigned agents can use threads too).  thread_id must
# match the design-doc charset and reply_to must be a valid req id; exit 3
# when either is bad.  Without -Thread send nothing: the index derives the
# default thread itself.
param(
    # ping | message | question | task | publish-release (checked below,
    # after retired names are mapped)
    [string]$Type = 'message',
    [string]$Text = '',
    [string]$TextFile = '',
    [string]$Version = '',
    [string]$PackagePath = '',
    [string]$Sha256 = '',
    [string]$SetupPath = '',
    [string]$Notes = '',
    [string]$From = '',
    [switch]$Wait,
    [switch]$NoWait,
    [int]$TimeoutSec = 1800,
    [string]$Check = '',
    [switch]$ListPendingReplies,
    [string]$As = '',
    [switch]$Enroll,
    [string]$Grant = '',
    [switch]$ListGrants,
    [string]$DisplayName = '',
    [string]$Product = '',
    [string]$Model = '',
    [string]$Thread = '',
    [string]$ReplyTo = ''
)

$ErrorActionPreference = 'Stop'
# Force UTF-8 on stdout: with the default console code page (GBK on Chinese
# Windows) reply text would reach the calling agent as mojibake.
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {}

# Request types. A retired name (renamed for good: releasing is a generic
# action, not one app's) is still accepted from old callers and mapped here,
# BEFORE the request is built and signed; nothing is ever sent with it.
# Same table as vm/muselink_id RETIRED_TYPES.
$RetiredTypes = @{ 'publish-voicebridge' = 'publish-release' }
$Type = $Type.Trim().ToLowerInvariant()
if ($RetiredTypes.ContainsKey($Type)) {
    [Console]::Error.WriteLine("NOTE: type '$Type' is retired and was sent as '$($RetiredTypes[$Type])'; update the caller to use the new name.")
    $Type = $RetiredTypes[$Type]
}
if (@('ping', 'message', 'question', 'task', 'publish-release') -notcontains $Type) {
    [Console]::WriteLine("ERROR: unknown -Type '$Type' (ping, message, question, task, publish-release)")
    exit 3
}
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
$IdentityScript = Join-Path $PSScriptRoot 'muselink-identity.ps1'
if (Test-Path -LiteralPath $IdentityScript) { . $IdentityScript }

$BridgeRoot = Get-MLSetting 'bridge' 'root' (Get-MLPath 'muse-bridge')
$InboxDir   = Join-Path $BridgeRoot 'inbox'
$OutboxDir  = Join-Path $BridgeRoot 'outbox'
$ClaimedDir = Join-Path $BridgeRoot 'claimed'
$AckDir     = Join-Path $BridgeRoot 'ack'
$ArchiveDir = Join-Path $BridgeRoot 'archive'
$SentDir    = Join-Path $BridgeRoot 'sent'
$DoneDir    = Join-Path $BridgeRoot 'done'
$QuarantineDir = Join-Path $BridgeRoot 'quarantine'
foreach ($d in @($BridgeRoot,$InboxDir,$OutboxDir,$ClaimedDir,$AckDir,$ArchiveDir,$SentDir,$DoneDir,$QuarantineDir)) {
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

# All output goes through [Console]::WriteLine. Write-Output inside a function
# is captured into the function's return value (the 2026-11-07 swallowed-reply
# bug); never route reply text through the PowerShell output stream.
function Out-Line([string]$s) { [Console]::WriteLine($s) }

function Get-ReplyFiles([string]$dir, [string]$id) {
    $rx = '^' + [regex]::Escape($id) + '(\.(\d{3}))?\.reply\.json$'
    $list = @()
    foreach ($f in @(Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue)) {
        if ($f.Name -match $rx) {
            $seq = 0
            if ($matches[2]) { $seq = [int]$matches[2] }
            $list += [pscustomobject]@{ File = $f; Seq = $seq }
        }
    }
    return @($list | Sort-Object Seq)
}

function Read-ReplyObject([string]$path) {
    try { return ((Read-MLUtf8 $path) | ConvertFrom-Json) } catch { return $null }
}

function Get-RequestProto([string]$id) {
    foreach ($d in @($SentDir, $InboxDir, $ClaimedDir)) {
        $p = Join-Path $d ($id + '.json')
        if (Test-Path -LiteralPath $p) {
            try {
                $o = Read-ReplyObject $p
                if ($o -and ($o.PSObject.Properties.Name -contains 'v') -and [int]$o.v -eq 3) { return 3 }
            } catch {}
        }
    }
    return 1
}

function Get-RequestAgent([string]$id) {
    foreach ($d in @($SentDir, $InboxDir, $ClaimedDir)) {
        $p = Join-Path $d ($id + '.json')
        if (Test-Path -LiteralPath $p) {
            try {
                $o = Read-ReplyObject $p
                if ($o -and ($o.PSObject.Properties.Name -contains 'agent_id')) { return [string]$o.agent_id }
            } catch {}
        }
    }
    return ''
}

# An enrollment that was not approved leaves the agent without a key, so the
# VM can only answer unsigned. Such a reply may END the wait of -Enroll: a
# plain (not an envelope) final "error" for this req_id and seq. It grants
# nothing - approval still needs the identity file plus a reply signed with
# it - so a forged one can do no more than deleting the reply would. It stays
# in quarantine (the mailbox shows it as unverified).
function Test-MLEnrollDecline([string]$raw, [string]$id, [int]$seq) {
    try { $o = $raw | ConvertFrom-Json } catch { return $false }
    if ($null -eq $o -or $o -isnot [System.Management.Automation.PSCustomObject]) { return $false }
    $names = @($o.PSObject.Properties.Name)
    if ($names -contains 'auth' -or $names -contains 'signed' -or $names -contains 'v') { return $false }
    return ([string]$o.req_id -ceq $id) -and ([string]$o.status -eq 'error') -and
           ($o.final -eq $true) -and ($names -contains 'seq') -and ([int]$o.seq -eq $seq)
}
function Test-FinalReply($obj) {
    if ($null -eq $obj) { return $true }
    if ($obj.PSObject.Properties.Name -contains 'final') { return [bool]$obj.final }
    return ([string]$obj.status -ne 'info')
}

function Close-Request([string]$id) {
    # enrollment requests wait in pending\ (the postman files them there)
    foreach ($dir in @($ClaimedDir, (Join-Path $BridgeRoot 'pending'))) {
        $c = Join-Path $dir ($id + '.json')
        if (Test-Path -LiteralPath $c) {
            try { Move-Item -LiteralPath $c -Destination (Join-Path $DoneDir ($id + '.json')) -Force -ErrorAction Stop } catch {}
        }
    }
}

function Get-FreeArchiveName([string]$id, [string]$name) {
    $dest = Join-Path $ArchiveDir $name
    if (-not (Test-Path -LiteralPath $dest)) { return $dest }
    for ($s = 900; $s -le 999; $s++) {
        $dest = Join-Path $ArchiveDir ('{0}.{1:D3}.reply.json' -f $id, $s)
        if (-not (Test-Path -LiteralPath $dest)) { return $dest }
    }
    return (Join-Path $ArchiveDir ($id + '.' + [guid]::NewGuid().ToString('N') + '.dup.json'))
}

# Print every reply waiting in the outbox for $id (seq order), write its
# receipt, archive it. Returns @{ Count; Final }.
function Read-Replies([string]$id) {
    $count = 0
    $final = $false
    foreach ($r in (Get-ReplyFiles $OutboxDir $id)) {
        $raw = Read-MLUtf8 $r.File.FullName
        $obj = $null
        if ($script:V3) {
            if ($script:RefreshIdentity) { $script:Identity = Resolve-MLIdentity $script:AgentId }
            if ($null -eq $script:Identity) {
                $t = @{ ok = $false; inner = $null; reason = 'no_identity' }
            } else {
                $t = Test-MLSignedReply $script:Identity $raw $id $r.Seq
            }
            if (-not [bool]$t.ok) {
                $declined = $Enroll -and (Test-MLEnrollDecline $raw $id $r.Seq)
                if ($declined) {
                    Out-Line ('===== ENROLLMENT NOT APPROVED (unsigned reply) seq={0} final =====' -f $r.Seq)
                } else {
                    Out-Line ('===== UNVERIFIED REPLY ({0}) =====' -f [string]$t.reason)
                }
                Out-Line $raw
                Out-Line $(if ($declined) { '===== MUSE REPLY END =====' } else { '===== UNVERIFIED REPLY END =====' })
                $dest = Join-Path $QuarantineDir $r.File.Name
                if (Test-Path -LiteralPath $dest) {
                    $dest = Join-Path $QuarantineDir ($r.File.BaseName + '.' + [guid]::NewGuid().ToString('N').Substring(0, 6) + '.reply.json')
                }
                try { Move-Item -LiteralPath $r.File.FullName -Destination $dest -Force -ErrorAction Stop } catch {}
                if ($declined) { $count++; $final = $true }
                continue
            }
            $obj = $t.inner
            $raw = ($obj | ConvertTo-Json -Depth 10)
        } else {
            $obj = Read-ReplyObject $r.File.FullName
        }
        $isFinal = Test-FinalReply $obj
        $status = if ($obj) { [string]$obj.status } else { '?' }
        $label = if ($isFinal) { 'final' } else { 'interim' }
        Out-Line ('===== MUSE REPLY BEGIN seq={0} status={1} {2} =====' -f $r.Seq, $status, $label)
        Out-Line $raw
        Out-Line '===== MUSE REPLY END ====='
        $ackName = if ($r.Seq -gt 0) { '{0}.{1:D3}.ack' -f $id, $r.Seq } else { $id + '.ack' }
        Write-MLAtomic (Join-Path $AckDir $ackName) ([DateTimeOffset]::Now.ToString('o'))
        try { Move-Item -LiteralPath $r.File.FullName -Destination (Get-FreeArchiveName $id $r.File.Name) -ErrorAction Stop } catch {}
        $count++
        if ($isFinal) { $final = $true }
    }
    if ($final) { Close-Request $id }
    return @{ Count = $count; Final = $final }
}

function Get-ArchiveState([string]$id) {
    # @(...): a one-element function result is unrolled to a bare object, and
    # a PSCustomObject has no .Count in Windows PowerShell 5.1
    $files = @(Get-ReplyFiles $ArchiveDir $id)
    $final = $false
    foreach ($r in $files) {
        if ($script:V3 -and $null -ne $script:Identity) {
            $t = Test-MLSignedReply $script:Identity (Read-MLUtf8 $r.File.FullName) $id $r.Seq
            if ($t.ok -and (Test-FinalReply $t.inner)) { $final = $true }
        } elseif (Test-FinalReply (Read-ReplyObject $r.File.FullName)) { $final = $true }
    }
    return @{ Count = $files.Count; Final = $final }
}

# ---- identity selection ----
$AgentId = ''
if ($As -ne '') { $AgentId = $As }
elseif ($env:MUSELINK_AGENT) { $AgentId = $env:MUSELINK_AGENT }
else { $AgentId = [string](Get-MLSetting 'bridge' 'agent' '') }
$RequireIdentity = [bool](Get-MLSetting 'bridge' 'require_identity' $false)
$script:AgentId = $AgentId
$script:Identity = $null
$script:V3 = $false
$script:RefreshIdentity = $false
function Resolve-MLIdentity([string]$id) {
    if ($id -eq '') { return $null }
    if (-not (Get-Command Get-MLIdentity -ErrorAction SilentlyContinue)) { return $null }
    try { return (Get-MLIdentity $id) } catch { $script:IdentityError = $_.Exception.Message; return $null }
}
$script:IdentityError = ''
$script:Identity = Resolve-MLIdentity $AgentId
if ($null -ne $script:Identity) {
    $script:V3 = $true
    # the file's agent_id is canonical (-As KIRO would otherwise put "KIRO"
    # inside a request signed as "kiro" and be refused for id_mismatch)
    $AgentId = [string]$script:Identity.agent_id
    $script:AgentId = $AgentId
} elseif ($script:IdentityError -ne '' -and -not $Enroll) {
    # An identity file that exists but does not validate must not silently
    # downgrade to an unsigned request (review 2026-11-12).
    Out-Line ('ERROR: identity file for ' + $AgentId + ' is invalid: ' + $script:IdentityError)
    exit 3
}
if ($Enroll) { $script:V3 = $true; $script:RefreshIdentity = $true }
if ($Enroll -and $AgentId -eq '') { Out-Line 'ERROR: -Enroll needs -As <agent_id>'; exit 3 }
if (-not $script:V3 -and $RequireIdentity) { Out-Line 'ERROR: identity required but not enrolled'; exit 3 }

if ($ListGrants) {
    $gp = Join-Path $BridgeRoot 'directory\grants.json'
    if (-not (Test-Path -LiteralPath $gp)) { Out-Line 'NO_GRANTS_MIRROR'; exit 0 }
    try { $gd = (Read-MLUtf8 $gp) | ConvertFrom-Json } catch { Out-Line 'ERROR: grants mirror unreadable'; exit 3 }
    $items = @($gd.grants)
    if ($AgentId -ne '') { $items = @($items | Where-Object { [string]$_.agent_id -eq $AgentId }) }
    $items = @($items | Where-Object { [string]$_.status -eq 'active' })
    if ($items.Count -eq 0) { Out-Line 'NO_ACTIVE_GRANTS'; exit 0 }
    foreach ($g in $items) {
        $cond = ''
        try { $cond = ($g.conditions | ConvertTo-Json -Depth 5 -Compress) } catch {}
        Out-Line ('GRANT {0} action={1} expires={2} uses_left={3} mode={4} conditions={5}' -f $g.grant_id, $g.action, $g.expires_at, $g.uses_left, $g.mode, $cond)
    }
    exit 0
}

if ($ListPendingReplies) {
    $files = @(Get-ChildItem -LiteralPath $OutboxDir -Filter '*.reply.json' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime)
    if ($files.Count -eq 0) { Out-Line 'NO_PENDING_REPLIES'; exit 0 }
    foreach ($f in $files) {
        $obj = Read-ReplyObject $f.FullName
        $label = if (Test-FinalReply $obj) { 'final' } else { 'interim' }
        $st = if ($obj) { [string]$obj.status } else { '?' }
        Out-Line ('PENDING_REPLY {0} status={1} {2} at={3}' -f $f.Name, $st, $label, $f.LastWriteTime.ToString('o'))
    }
    Out-Line 'Fetch with: muse-call -Check <req_id>'
    exit 0
}

if ($Check -ne '') {
    if (-not (Test-MLReqId $Check)) { Out-Line 'ERROR: bad req id'; exit 3 }
    if ((Get-RequestProto $Check) -eq 3) {
        $script:V3 = $true
        if ($AgentId -eq '') { $AgentId = Get-RequestAgent $Check; $script:AgentId = $AgentId }
        $script:Identity = Resolve-MLIdentity $AgentId
    } else { $script:V3 = $false }
    $r = Read-Replies $Check
    if ($r.Count -gt 0) {
        if ($r.Final) { Out-Line "STATE:FINAL_READ req=$Check" }
        else { Out-Line "STATE:INTERIM_READ req=$Check (the final reply has not arrived yet; check again later)" }
        exit 0
    }
    $a = Get-ArchiveState $Check
    if ($a.Final -or (Test-Path -LiteralPath (Join-Path $DoneDir ($Check + '.json')))) { Out-Line "STATE:ALREADY_READ_FINAL req=$Check"; exit 0 }
    if ($a.Count -gt 0) { Out-Line "STATE:WAITING_FOR_FINAL req=$Check (interim replies already read)"; exit 0 }
    if (Test-Path -LiteralPath (Join-Path $InboxDir ($Check + '.json'))) { Out-Line "STATE:WAITING_TO_BE_PICKED_UP req=$Check"; exit 0 }
    if (Test-Path -LiteralPath (Join-Path $ClaimedDir ($Check + '.json'))) { Out-Line "STATE:MUSE_PROCESSING req=$Check"; exit 0 }
    if (Test-Path -LiteralPath (Join-Path $BridgeRoot ('rejected\' + $Check + '.json'))) { Out-Line "STATE:REJECTED req=$Check (see rejected\)"; exit 0 }
    if (Test-Path -LiteralPath (Join-Path $BridgeRoot ('pending\' + $Check + '.json'))) { Out-Line "STATE:PENDING_ENROLL req=$Check (the enrollment card is waiting for the user)"; exit 0 }
    Out-Line "STATE:UNKNOWN req=$Check (no such request on this PC)"
    exit 0
}

# ---- build a new request ----
# Validate the thread fields before anything is written (a bad id must not
# create a half-posted request).
if ($Thread -ne '' -and $Thread -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\z') {
    Out-Line 'ERROR: bad -Thread id (allowed: letters, digits, _ . : / -, max 128; must start with a letter or digit)'
    exit 3
}
if ($ReplyTo -ne '' -and -not (Test-MLReqId $ReplyTo)) { Out-Line 'ERROR: bad -ReplyTo req id'; exit 3 }
if ($Thread -eq '' -and $ReplyTo -ne '') {
    foreach ($dir in @($SentDir, $ClaimedDir, $DoneDir, $InboxDir)) {
        $rp = Join-Path $dir ($ReplyTo + '.json')
        if (Test-Path -LiteralPath $rp) {
            try {
                $ro = Read-ReplyObject $rp
                if ($ro -and ($ro.PSObject.Properties.Name -contains 'signed') -and $ro.signed) {
                    $ro = ($ro.signed | ConvertFrom-Json)
                }
                if ($ro) {
                    $rt = if ($ro.PSObject.Properties.Name -contains 'thread_id') { [string]$ro.thread_id } else { '' }
                    if ($rt -cmatch '^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\z') { $Thread = $rt; break }
                    $rType = if ($ro.PSObject.Properties.Name -contains 'type') { [string]$ro.type } else { '' }
                    $rAgent = if ($ro.PSObject.Properties.Name -contains 'agent_id' -and $ro.agent_id) { [string]$ro.agent_id } elseif ($ro.PSObject.Properties.Name -contains 'from') { [string]$ro.from } else { '' }
                    if ($rAgent -ne '' -and $rType -like 'publish-*') { $Thread = "$rAgent/release"; break }
                }
            } catch {}
        }
    }
}

if ($TextFile -ne '') {
    if (-not (Test-Path -LiteralPath $TextFile)) { Out-Line "ERROR: -TextFile not found: $TextFile"; exit 3 }
    $Text = Read-MLUtf8 $TextFile
}

function Get-FileFacts([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return @{
        sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        size   = (Get-Item -LiteralPath $path).Length
    }
}

$payload = [ordered]@{}
if ($Type -like 'publish-*') {
    if ($Version -eq '' -or $PackagePath -eq '' -or $Sha256 -eq '') {
        Out-Line "ERROR: $Type needs -Version, -PackagePath and -Sha256"; exit 3
    }
    $facts = Get-FileFacts $PackagePath
    if ($null -eq $facts) { Out-Line "ERROR: package not found: $PackagePath"; exit 3 }
    if ($facts.sha256 -ne $Sha256.ToLowerInvariant()) {
        Out-Line "ERROR: local sha256 mismatch. file=$($facts.sha256) given=$($Sha256.ToLowerInvariant()) - NOT sent to Muse."
        exit 3
    }
    $payload['version']      = $Version
    $payload['package_path'] = (Resolve-Path -LiteralPath $PackagePath).Path
    $payload['sha256']       = $facts.sha256
    $payload['package_size'] = $facts.size
    $payload['notes']        = $Notes
    if ($SetupPath -ne '') {
        $sf = Get-FileFacts $SetupPath
        if ($null -eq $sf) { Out-Line "ERROR: setup not found: $SetupPath"; exit 3 }
        $payload['setup_path']   = (Resolve-Path -LiteralPath $SetupPath).Path
        $payload['setup_sha256'] = $sf.sha256
        $payload['setup_size']   = $sf.size
    }
}

if ($script:V3) {
    if ($AgentId -eq '') { Out-Line 'ERROR: no agent id for a signed request'; exit 3 }
    $reqId = New-MLReqId $AgentId
    $client = [ordered]@{
        display_name = $DisplayName
        product = $Product
        model = $Model
        host = $env:COMPUTERNAME
        session_id = [guid]::NewGuid().ToString()
    }
    $inner = [ordered]@{
        req_id = $reqId
        agent_id = $AgentId
        ts = [int64][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        created_at = [DateTimeOffset]::Now.ToString('o')
        type = $Type
        text = $Text
        payload = $payload
        grant_id = $(if ($Grant -ne '') { $Grant } else { $null })
        reply_protocol = 3
        client = $client
    }
    if ($Thread -ne '') { $inner['thread_id'] = $Thread }
    if ($ReplyTo -ne '') { $inner['reply_to'] = $ReplyTo }
    if ($Enroll) {
        $json = New-MLEnrollRequest $AgentId $inner
    } else {
        if ($null -eq $script:Identity) { Out-Line 'ERROR: identity file not found'; exit 3 }
        $json = New-MLSignedRequest $script:Identity $inner
    }
} else {
    # an agent that named itself (-As) but is not enrolled says so in "from"
    # instead of borrowing the configured default sender label
    if ($From -eq '' -and $AgentId -ne '') { $From = $AgentId }
    if ($From -eq '') { $From = [string](Get-MLSetting 'bridge' 'from' 'local-agent') }
    $prefix = [string](Get-MLSetting 'bridge' 'req_prefix' 'codex')
    if ($prefix -notmatch '^[A-Za-z][A-Za-z0-9]{0,15}$') { $prefix = 'req' }
    $reqId = $prefix + '_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + ([guid]::NewGuid().ToString('N').Substring(0,6))
    $req = [ordered]@{
        req_id         = $reqId
        created_at     = [DateTimeOffset]::Now.ToString('o')
        from           = $From
        type           = $Type
        text           = $Text
        payload        = $payload
        reply_protocol = 2
    }
    if ($Thread -ne '') { $req['thread_id'] = $Thread }
    if ($ReplyTo -ne '') { $req['reply_to'] = $ReplyTo }
    $json = $req | ConvertTo-Json -Depth 6
    [Console]::Error.WriteLine('UNSIGNED: not enrolled')
}
$finPath = Join-Path $InboxDir ($reqId + '.json')
Write-MLAtomic $finPath $json
Copy-Item -LiteralPath $finPath -Destination (Join-Path $SentDir ($reqId + '.json')) -Force
Out-Line "MUSE_CALL_POSTED req=$reqId type=$Type"

if ($NoWait) {
    Out-Line "Posted without waiting. Read replies later with: muse-call -Check $reqId"
    exit 0
}

# ---- blocking wait until the FINAL reply. The script, not the model, does
# the waiting; our exit is the calling agent's wake-up event. ----
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$started = Get-Date
$nextBeat = Get-Date
$interim = 0
$watcher = New-Object System.IO.FileSystemWatcher $OutboxDir, ($reqId + '*.reply.json')
$watcher.NotifyFilter = [System.IO.NotifyFilters]'FileName, LastWrite'
$watcher.EnableRaisingEvents = $true
try {
    while ((Get-Date) -lt $deadline) {
        $r = Read-Replies $reqId
        $interim += $r.Count
        if ($r.Final) { Out-Line "MUSE_CALL_DONE req=$reqId"; exit 0 }
        if ((Get-Date) -ge $nextBeat) {
            Out-Line ('WAITING_FOR_MUSE req={0} elapsed={1}s timeout={2}s replies_so_far={3}' -f $reqId, [int]((Get-Date) - $started).TotalSeconds, $TimeoutSec, $interim)
            $nextBeat = (Get-Date).AddSeconds(30)
        }
        [void]$watcher.WaitForChanged([System.IO.WatcherChangeTypes]::All, 5000)
    }
} finally {
    $watcher.Dispose()
}
Out-Line "MUSE_CALL_TIMEOUT req=$reqId - no final reply within ${TimeoutSec}s (interim replies read: $interim). The request is NOT lost; read it later with: muse-call -Check $reqId"
exit 2
