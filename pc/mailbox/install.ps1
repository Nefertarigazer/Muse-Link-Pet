# install.ps1 - dry-run by default; no production Python package changes.
param(
    [switch]$Apply,
    [switch]$AutoStart,
    [switch]$Force,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$toolDir = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $toolDir 'launcher.exe'
$venv = Join-Path $PSScriptRoot '.venv'
$req = Join-Path $PSScriptRoot 'requirements.txt'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$record = Join-Path $venv 'requirements.sha256'
$hash = (Get-FileHash -LiteralPath $req -Algorithm SHA256).Hash
$create = $Force -or -not (Test-Path -LiteralPath $venvPython)
$install = $Force -or $create -or -not (Test-Path -LiteralPath $record)
if (-not $install) { $install = [IO.File]::ReadAllText($record).Trim() -ne $hash }
if ($create) {
    $state = if (Test-Path -LiteralPath $venvPython) { 'change' } else { 'new' }
    "$state venv: $Python -m venv $venv"
}
else { "skip venv: already installed ($venvPython)" }
if ($install) {
    $state = if (Test-Path -LiteralPath $record) { 'change' } else { 'new' }
    "$state dependencies: $venvPython -m pip install --no-cache-dir --require-hashes -r $req"
}
else { "skip dependencies: already installed (requirements.txt sha256=$hash)" }
. (Join-Path $PSScriptRoot 'retire-standalone.ps1')
if (-not $Apply) { Remove-MLStandaloneMailbox -ToolDir $toolDir }
if ($AutoStart) { Write-Output 'PLAN: mailbox is pet-owned; register pet autostart only' }
if (-not $Apply) {
    if ($AutoStart) { & (Join-Path $toolDir 'install\register-tasks.ps1') -ToolDir $toolDir -PetOnly -Force:$Force }
    Write-Output 'DRY RUN - nothing changed. Re-run with -Apply.'; exit 0
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run mailbox install as an ordinary user.' }
if (-not (Test-Path -LiteralPath $launcher)) { throw 'Build launcher\build-launcher.ps1 first (ordinary user).' }
Remove-MLStandaloneMailbox -ToolDir $toolDir -Apply
if ($create) {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
if ($install) {
    & $venvPython -m pip install --no-cache-dir --require-hashes -r $req
    if ($LASTEXITCODE -ne 0) { throw 'dependency installation failed' }
    [IO.File]::WriteAllText($record, $hash + "`n", [Text.Encoding]::ASCII)
}
if ($AutoStart) {
    & (Join-Path $toolDir 'install\register-tasks.ps1') -ToolDir $toolDir -PetOnly -Apply -Force:$Force
}
Write-Output 'Shared mailbox dependencies installed; open the mailbox from MuseLink Pet.'
