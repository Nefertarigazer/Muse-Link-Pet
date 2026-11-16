# Pet shares the mailbox venv; dry-run by default, never enrolls an identity.
param([switch]$Apply, [switch]$AutoStart, [switch]$RequestIdentity, [switch]$Force)
$ErrorActionPreference = 'Stop'
$toolDir = Split-Path -Parent $PSScriptRoot
$python = Join-Path $toolDir 'mailbox\.venv\Scripts\python.exe'
$launcher = Join-Path $toolDir 'launcher.exe'
$entry = Join-Path $PSScriptRoot 'pet.ps1'
$iconLocation = (Join-Path $PSScriptRoot 'pet.ico') + ',0'
$wav = Join-Path $PSScriptRoot 'pet-confirm.wav'
if (Test-Path -LiteralPath $python) { "skip shared venv: already installed ($python)" }
else { "not found shared venv: $python (run mailbox\install.ps1 -Apply first)" }
$makeWav = $Force -or -not (Test-Path -LiteralPath $wav)
if ($makeWav) {
    $state = if (Test-Path -LiteralPath $wav) { 'change' } else { 'new' }
    "$state confirmation sound: $wav"
}
else { "skip confirmation sound: already installed ($wav)" }
$shell = New-Object -ComObject WScript.Shell
$shortcuts = @()
foreach ($directory in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $path = Join-Path $directory 'MuseLink Pet.lnk'
    $exists = Test-Path -LiteralPath $path
    $shortcut = $shell.CreateShortcut($path)
    if (-not $Force -and $exists -and $shortcut.TargetPath -eq $launcher -and
        $shortcut.Arguments -eq ('"' + $entry + '"') -and $shortcut.WorkingDirectory -eq $toolDir -and
        $shortcut.IconLocation -eq $iconLocation) {
        "skip $path`: already installed (target, arguments and icon match)"
    } else {
        $state = if ($exists) { 'change' } else { 'new' }
        "$state shortcut: $path -> $launcher `"$entry`""
        $shortcuts += $shortcut
    }
}
if ($AutoStart) { Write-Output 'PLAN: register only pet task, Interactive / Limited / AtLogOn' }
if ($RequestIdentity) { Write-Output 'PLAN: post pet identity enrollment; approval follows the existing enrollment flow' }
if (-not $Apply) {
    if ($AutoStart) { & (Join-Path $toolDir 'install\register-tasks.ps1') -ToolDir $toolDir -PetOnly -Force:$Force }
    Write-Output 'DRY RUN - nothing changed. Re-run with -Apply.'; exit 0
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run pet install as an ordinary user.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Run mailbox\install.ps1 -Apply first. Pet does not create a venv.' }
if (-not (Test-Path -LiteralPath $launcher)) { throw 'Build launcher\build-launcher.ps1 first.' }
if ($makeWav) {
    & $python (Join-Path $PSScriptRoot 'make_confirm_wav.py') --output $wav
    if ($LASTEXITCODE -ne 0) { throw 'Confirmation sound generation failed' }
}
foreach ($shortcut in $shortcuts) {
    $shortcut.TargetPath = $launcher
    $shortcut.Arguments = '"' + $entry + '"'
    $shortcut.WorkingDirectory = $toolDir
    $shortcut.Description = 'MuseLink Pet'
    $shortcut.IconLocation = $iconLocation
    $shortcut.Save()
}
if ($AutoStart) { & (Join-Path $toolDir 'install\register-tasks.ps1') -ToolDir $toolDir -PetOnly -Apply -Force:$Force }
if ($RequestIdentity) {
    . (Join-Path $toolDir 'muselink-common.ps1')
    . (Join-Path $toolDir 'i18n/i18n.ps1')
    Initialize-MLI18n (Join-Path $toolDir 'i18n')
    $petName = Get-MLSetting 'pet' 'display_name' 'Muse Pet'
    $display = T 'pet.identity_name' @{name=$petName}
    & (Join-Path $toolDir 'muse-call.ps1') -As pet -Enroll -DisplayName $display -Product 'MuseLink Pet' -Model 'local' -NoWait
    if ($LASTEXITCODE -ne 0) { throw 'Pet installed, but identity enrollment was not posted.' }
}
Write-Output 'Pet installed. Use -Apply -RequestIdentity to request the pet identity before signed bridge messages.'
