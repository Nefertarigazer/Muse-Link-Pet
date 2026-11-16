# check-syntax.ps1 - static checks for every repository script (including
# new, non-ignored files; excluding ignored private references/build outputs):
#   PowerShell: parses, and is ASCII-only unless it starts with a UTF-8 BOM
#               (Windows PowerShell 5.1 reads BOM-less files as ANSI)
#   Python:     py_compile
#   bash:       bash -n (Git Bash on Windows), and no CRLF line endings
#   .cmd:       ASCII + CRLF
# Exit 1 on any finding.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$bad = 0
function Fail([string]$m) { Write-Output "FAIL $m"; $script:bad++ }
$skip = '\\(\.git|private)\\'
$names = & git -c ("safe.directory=" + ($root -replace '\\', '/')) -c core.autocrlf=false `
    -C $root ls-files --cached --others --exclude-standard -- '*.ps1' '*.py' '*.sh' '*.cmd'
if ($LASTEXITCODE -ne 0) { throw 'Unable to enumerate repository scripts' }
$scripts = @($names | Sort-Object -Unique | ForEach-Object { Get-Item -LiteralPath (Join-Path $root $_) } |
    Where-Object { $_.FullName -notmatch $skip })

foreach ($f in $scripts | Where-Object { $_.Extension -eq '.ps1' }) {
    $errs = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$errs)
    if ($errs.Count) { Fail ("{0}: {1}" -f $f.FullName, $errs[0].Message) }
    $b = [IO.File]::ReadAllBytes($f.FullName)
    if ([Text.Encoding]::UTF8.GetString($b) -match "[^`r]`n") { Fail ("{0}: LF without CR" -f $f.FullName) }
    $bom = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
    if (-not $bom -and ($b | Where-Object { $_ -gt 127 } | Select-Object -First 1)) { Fail ("{0}: non-ASCII without BOM" -f $f.FullName) }
}
$py = $scripts | Where-Object { $_.Extension -eq '.py' }
foreach ($f in $py) {
    $out = & python -m py_compile $f.FullName 2>&1
    if ($LASTEXITCODE -ne 0) { Fail ("{0}: {1}" -f $f.FullName, ($out | Out-String).Trim()) }
}
$bash = (Get-Command bash -ErrorAction SilentlyContinue).Source
foreach ($f in $scripts | Where-Object { $_.Extension -eq '.sh' }) {
    if ([IO.File]::ReadAllText($f.FullName).Contains("`r")) { Fail ("{0}: CRLF line endings" -f $f.FullName) }
    if ($bash) {
        $out = & $bash -n ($f.FullName -replace '\\', '/') 2>&1
        if ($LASTEXITCODE -ne 0) { Fail ("{0}: {1}" -f $f.FullName, ($out | Out-String).Trim()) }
    }
}
foreach ($f in $scripts | Where-Object { $_.Extension -eq '.cmd' }) {
    $t = [IO.File]::ReadAllText($f.FullName)
    if (([IO.File]::ReadAllBytes($f.FullName) | Where-Object { $_ -gt 127 } | Select-Object -First 1)) { Fail ("{0}: non-ASCII" -f $f.FullName) }
    if ($t -match "[^`r]`n") { Fail ("{0}: LF without CR" -f $f.FullName) }
}
if ($bad -eq 0) { Write-Output 'OK all scripts pass' }
# Localization checks are part of the existing repository check entry point.
& python (Join-Path $root 'tools/check-i18n.py')
if ($LASTEXITCODE -ne 0) { Fail 'language packs' }
try { & (Join-Path $root 'tests/i18n/vectors.ps1') } catch { Fail ("i18n PS vectors: " + $_.Exception.Message) }
& node (Join-Path $root 'tests/i18n/i18n.test.mjs')
if ($LASTEXITCODE -ne 0) { Fail 'i18n JS vectors' }
& python (Join-Path $root 'tools/check-docs.py')
if ($LASTEXITCODE -ne 0) { Fail 'paired documentation (see findings above)' }
if ($bad -eq 0) { Write-Output 'OK all repository checks pass' } else { Write-Output "FOUND $bad problem(s)"; exit 1 }
