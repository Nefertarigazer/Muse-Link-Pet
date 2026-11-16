# mlindex.ps1 - run the thread indexer (pc\mlindex) from the tool dir.
#   powershell -File mlindex.ps1 sync | rebuild | stats | threads | thread <id> | search <q> | verify --sample 5
# Requirements: python from muselink.json executor.python (default
# 'python'); PYTHONPATH = this script's directory; pass arguments through a
# temp args file or -m arguments without re-quoting (PS 5.1 mangles embedded
# quotes); forward stdout (UTF-8) and the exit code unchanged. ASCII only.
$ErrorActionPreference = 'Stop'

try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {}

$python = 'python'
$configPath = Join-Path $PSScriptRoot 'muselink.json'
if (Test-Path -LiteralPath $configPath) {
    try {
        $cfg = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($null -ne $cfg -and ($cfg.PSObject.Properties.Name -contains 'executor')) {
            $ex = $cfg.executor
            if ($null -ne $ex -and ($ex.PSObject.Properties.Name -contains 'python')) {
                $value = [string]$ex.python
                if ($value -ne '') { $python = $value }
            }
        }
    } catch {
        [Console]::Error.WriteLine('muselink.json unreadable: ' + $_.Exception.Message)
    }
}

$env:PYTHONPATH = $PSScriptRoot
# PS 5.1 converts native-command arguments through the ANSI code page, which
# mangles Chinese search words and embedded quotes.  Write the arguments as
# UTF-8 lines and let Python read them back.
$utf8 = New-Object System.Text.UTF8Encoding($false)
$argFile = Join-Path ([System.IO.Path]::GetTempPath()) ('mlindex-args-' + $PID + '-' + [guid]::NewGuid().ToString('N') + '.txt')
$lines = @()
foreach ($a in $args) { $lines += [string]$a }
$text = ''
if ($lines.Count -gt 0) { $text = ($lines -join "`n") + "`n" }
[System.IO.File]::WriteAllText($argFile, $text, $utf8)
try {
    & $python -m mlindex --args-file $argFile
} catch {
    [Console]::Error.WriteLine('mlindex: ' + $_.Exception.Message)
    exit 30
} finally {
    Remove-Item -LiteralPath $argFile -Force -ErrorAction SilentlyContinue
}
exit $LASTEXITCODE
