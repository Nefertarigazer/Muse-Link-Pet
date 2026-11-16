# Preview by default. Writes the muselink entry for -Agent into ONE client's
# configuration: -Client codex|kiro|claude (default: the agent id when it is
# one of these names). -Apply merges after a backup; the Codex/Kiro file is
# created when that client's own directory exists.
param([Parameter(Mandatory=$true)][string]$Agent,
      [ValidateSet('', 'codex', 'kiro', 'claude')][string]$Client = '',
      [switch]$Apply, [switch]$Force, [string]$Python = '',
      [string]$ConfigHome = $env:USERPROFILE)
$ErrorActionPreference = 'Stop'
$tool = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $tool 'mailbox\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command python.exe -ErrorAction Stop).Source }
}
# Use temporary PYTHONPATH to avoid PowerShell 5.1 native quote stripping.
$oldPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $tool
    $argv = @('-m', 'plugin._install', '--agent', $Agent, '--tool-dir', $tool, '--python', $Python, '--config-home', $ConfigHome)
    if ($Client) { $argv += @('--client', $Client) }
    if ($Apply) { $argv += '--apply' }
    if ($Force) { $argv += '--force' }
    & $Python @argv
    exit $LASTEXITCODE
} finally { $env:PYTHONPATH = $oldPath }
