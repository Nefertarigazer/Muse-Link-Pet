# file-facts.ps1 - report existence, size and SHA-256 of files named in a JSON
# arguments file. Callers on the VM never splice paths into a command line
# (the 2026-11-12 review found a quote in a declared path could break out of
# a PowerShell string); they upload {"paths": [...]} and run this fixed script.
#   powershell -File file-facts.ps1 -ArgsFile <tool dir>\<name>.json
# Output: one JSON line {"files":[{"path","exists","size","sha256"}]}.
#
# Identity protocol v3 (implemented 2026-11-12) (grant conditions use measured facts, docs/protocol.en.md
# section 4): add per file "full_path" = [IO.Path]::GetFullPath(path) and
# "reparse_in_path" = $true when the file or ANY ancestor directory has the
# ReparsePoint attribute (junction, symlink, mount point). The publish
# pipeline then sets the fact package_path to full_path, or to null when
# reparse_in_path is true, so a path_under condition cannot be satisfied
# through a link. Keep the existing fields unchanged (old callers).
param([Parameter(Mandatory=$true)][string]$ArgsFile)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
try {
    $a = (Read-MLUtf8 $ArgsFile) | ConvertFrom-Json
    $out = @()
    foreach ($p in @($a.paths)) {
        $path = [string]$p
        $rec = [ordered]@{ path = $path; exists = $false; size = $null; sha256 = $null; full_path = $null; reparse_in_path = $false }
        $full = $null
        try { if ($path) { $full = [IO.Path]::GetFullPath($path) } } catch { $full = $null }
        $rec.full_path = $full
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            $rec.exists = $true
            $rec.size = (Get-Item -LiteralPath $path).Length
            $rec.sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        if ($full) {
            try {
                $cur = $full
                while ($cur) {
                    $it = Get-Item -LiteralPath $cur -Force -ErrorAction SilentlyContinue
                    if ($it -and (($it.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { $rec.reparse_in_path = $true; break }
                    $parent = [IO.Path]::GetDirectoryName($cur)
                    if (-not $parent -or $parent -eq $cur) { break }
                    $cur = $parent
                }
            } catch { $rec.reparse_in_path = $true }
        }
        $out += [pscustomobject]$rec
    }
    [Console]::WriteLine((@{ ok = $true; files = $out } | ConvertTo-Json -Depth 4 -Compress))
} catch {
    [Console]::WriteLine((@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress))
    exit 1
}
