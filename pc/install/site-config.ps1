# site-config.ps1 - PC half of the one-file site configuration. Dot-sourced
# by deploy-pc.ps1; nothing runs on load.
#
# The site file (template config\muselink.site.example.json; the same file
# the VM uses) generates, in the tool dir:
#   muselink.json        section "pc", plus the values filled once elsewhere:
#                        executor.python <- site.pc_python,
#                        bridge.root     <- site.pc_bridge_dir,
#                        upload.*        <- publish (only when enabled)
#   plugin\policy.json   section "plugin_policy" (if it has any key)
# Rules: keys starting with _ are help and are dropped; "" means default
# (the key is left out); a section left empty is left out. Nothing secret.

function ConvertTo-MLPlain($v, [switch]$DropEmpty) {
    if ($null -eq $v) { return $null }
    if ($v -is [System.Management.Automation.PSCustomObject]) {
        $h = [ordered]@{}
        foreach ($p in $v.PSObject.Properties) {
            if ($p.Name.StartsWith('_')) { continue }
            if ($DropEmpty -and $p.Value -is [string] -and $p.Value -eq '') { continue }
            $h[$p.Name] = ConvertTo-MLPlain $p.Value -DropEmpty:$DropEmpty
        }
        return $h
    }
    if ($v -is [System.Array]) {
        $a = @(foreach ($x in $v) { ConvertTo-MLPlain $x -DropEmpty:$DropEmpty })
        return , $a
    }
    return $v
}

# Order-independent text form, for "same effective content" checks.
# An empty object counts as absent (the rule "a section left empty is left
# out"): {"tools": {}} and no "tools" key are the same effective content.
function Get-MLCanon($v) {
    if ($null -eq $v) { return 'null' }
    if ($v -is [System.Collections.IDictionary] -and $v.Count -eq 0) { return 'null' }
    if ($v -is [System.Collections.IDictionary]) {
        $parts = foreach ($k in (@($v.Keys) | Sort-Object)) {
            (ConvertTo-Json -InputObject ([string]$k) -Compress) + ':' + (Get-MLCanon $v[$k])
        }
        return '{' + (@($parts) -join ',') + '}'
    }
    if ($v -is [System.Array]) {
        return '[' + (@(foreach ($x in $v) { Get-MLCanon $x }) -join ',') + ']'
    }
    return (ConvertTo-Json -InputObject $v -Compress)
}

function Read-MLJsonFile([string]$path) {
    $text = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
    return ($text | ConvertFrom-Json)
}

function Get-MLFullPath([string]$p) {
    # relative to PowerShell's location, not the .NET current directory
    $abs = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($p)
    return ([System.IO.Path]::GetFullPath($abs)).TrimEnd('\')
}

# -> plain ordered dictionary of the whole site file; throws on any problem
function Read-MLSite([string]$path) {
    try { $raw = Read-MLJsonFile $path } catch { throw ('site file unreadable: ' + $path + ': ' + $_.Exception.Message) }
    $site = ConvertTo-MLPlain $raw
    if ($site['version'] -ne 1) { throw ('not a version 1 site file: ' + $path) }
    $s = $site['site']
    if ($s -isnot [System.Collections.IDictionary]) { throw 'site section missing' }
    foreach ($k in 'pc_host', 'pc_user', 'pc_tool_dir') {
        if (-not ($s[$k] -is [string]) -or $s[$k].Trim() -eq '') { throw ('site.' + $k + ' is required') }
    }
    if ($s['pc_tool_dir'] -notmatch '^[A-Za-z]:\\') { throw 'site.pc_tool_dir must be an absolute Windows path' }
    foreach ($sec in 'pc', 'plugin_policy', 'publish') {
        if ($null -ne $site[$sec] -and $site[$sec] -isnot [System.Collections.IDictionary]) { throw ($sec + ' section must be an object') }
    }
    return $site
}

function Get-MLSiteValue($dict, [string]$key) {
    if ($null -eq $dict -or -not $dict.Contains($key)) { return '' }
    $v = $dict[$key]
    if ($null -eq $v) { return '' }
    return [string]$v
}

# -> @( @{ Rel; Path; Object } ) the files this site file generates
function Get-MLPcTargets([string]$sitePath, $site, [string]$toolDir) {
    $raw = Read-MLJsonFile $sitePath
    $pc = ConvertTo-MLPlain $raw.pc -DropEmpty
    if ($null -eq $pc) { $pc = [ordered]@{} }
    $fills = @(
        @('executor', 'python', (Get-MLSiteValue $site['site'] 'pc_python'), 'site.pc_python'),
        @('bridge', 'root', (Get-MLSiteValue $site['site'] 'pc_bridge_dir'), 'site.pc_bridge_dir')
    )
    $pub = $site['publish']
    if ($null -ne $pub -and $pub['enabled'] -eq $true) {
        $dest = Get-MLSiteValue $pub 'UPLOAD_DEST_PC'
        if ($dest -eq '') { $dest = Get-MLSiteValue $pub 'RELEASE_SSH' }
        $fills += , @('upload', 'dest', $dest, 'publish.UPLOAD_DEST_PC / RELEASE_SSH')
        $fills += , @('upload', 'key', (Get-MLSiteValue $pub 'UPLOAD_KEY_PC'), 'publish.UPLOAD_KEY_PC')
        $fills += , @('upload', 'remote_dir', (Get-MLSiteValue $pub 'RELEASE_STAGE'), 'publish.RELEASE_STAGE')
    }
    foreach ($f in $fills) {
        $sec, $key, $val, $from = $f
        if ($pc.Contains($sec) -and $pc[$sec] -is [System.Collections.IDictionary] -and $pc[$sec].Contains($key)) {
            throw ('pc.' + $sec + '.' + $key + ' is filled from ' + $from + '; set it there only')
        }
        if ($val -eq '') { continue }
        if (-not $pc.Contains($sec)) { $pc[$sec] = [ordered]@{} }
        $pc[$sec][$key] = $val
    }
    foreach ($sec in @($pc.Keys)) {
        if ($pc[$sec] -is [System.Collections.IDictionary] -and $pc[$sec].Count -eq 0) { $pc.Remove($sec) }
    }
    $targets = @(@{ Rel = 'muselink.json'; Path = (Join-Path $toolDir 'muselink.json'); Object = $pc })
    $pp = ConvertTo-MLPlain $raw.plugin_policy -DropEmpty
    if ($null -ne $pp -and $pp.Count -gt 0) {
        $targets += @{ Rel = 'plugin\policy.json'; Path = (Join-Path $toolDir 'plugin\policy.json'); Object = $pp }
    }
    return $targets
}

# -> differences "section.key: old -> new" (empty = same effective content)
function Compare-MLConfig([string]$path, $new) {
    try { $old = ConvertTo-MLPlain (Read-MLJsonFile $path) -DropEmpty } catch { return @('current file unreadable: ' + $_.Exception.Message) }
    if ($old -isnot [System.Collections.IDictionary]) { return @('current file is not a JSON object') }
    $out = @()
    $short = { param($s) if ($s.Length -gt 100) { $s.Substring(0, 97) + '...' } else { $s } }
    foreach ($sec in (@($old.Keys) + @($new.Keys) | Sort-Object -Unique)) {
        $o = $old[$sec]; $n = $new[$sec]
        if ($o -is [System.Collections.IDictionary] -and $o.Count -eq 0) { $o = $null }
        if ($o -is [System.Collections.IDictionary] -and $n -is [System.Collections.IDictionary]) {
            foreach ($k in (@($o.Keys) + @($n.Keys) | Sort-Object -Unique)) {
                $a = Get-MLCanon $o[$k]; $b = Get-MLCanon $n[$k]
                if ($a -ne $b) { $out += ('{0}.{1}: {2} -> {3}' -f $sec, $k, (& $short $a), (& $short $b)) }
            }
        } else {
            $a = Get-MLCanon $o; $b = Get-MLCanon $n
            if ($a -ne $b) { $out += ('{0}: {1} -> {2}' -f $sec, (& $short $a), (& $short $b)) }
        }
    }
    return $out
}

# Pretty JSON. Containers are written here, never by ConvertTo-Json: Windows
# PowerShell 5.1 can serialize a function-returned array as {"value","Count"}.
function ConvertTo-MLJson($v, [string]$pad = '') {
    $in = $pad + '  '
    if ($null -eq $v) { return 'null' }
    if ($v -is [System.Collections.IDictionary]) {
        if ($v.Count -eq 0) { return '{}' }
        $parts = foreach ($k in @($v.Keys)) { $in + (ConvertTo-Json -InputObject ([string]$k) -Compress) + ': ' + (ConvertTo-MLJson $v[$k] $in) }
        return "{`r`n" + (@($parts) -join ",`r`n") + "`r`n" + $pad + '}'
    }
    if ($v -is [System.Array]) {
        if ($v.Count -eq 0) { return '[]' }
        $parts = foreach ($x in $v) { $in + (ConvertTo-MLJson $x $in) }
        return "[`r`n" + (@($parts) -join ",`r`n") + "`r`n" + $pad + ']'
    }
    return (ConvertTo-Json -InputObject $v -Compress)
}

function Write-MLConfigFile([string]$path, $obj) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
    $json = ConvertTo-MLJson $obj
    $tmp = $path + '.' + $PID + '.tmp'
    [System.IO.File]::WriteAllText($tmp, $json + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $path -Force
}
