# launcher.exe accepts a PowerShell script, so this wrapper runs the daily CLI.
$ErrorActionPreference = 'Stop'
$explibToolDir = Split-Path -Parent $PSScriptRoot
$explibPython = Join-Path $explibToolDir 'mailbox\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $explibPython)) { $explibPython = 'python.exe' }
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $explibToolDir
& $explibPython -m explib share-check --tool-dir $explibToolDir
exit $LASTEXITCODE
