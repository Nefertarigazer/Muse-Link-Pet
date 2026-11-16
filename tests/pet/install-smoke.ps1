# Real .lnk files in a sandbox; scheduler, processes and registry are mocked.
$ErrorActionPreference = 'Stop'
$taskFixtureRepo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$taskFixtureRoot = Join-Path $taskFixtureRepo ('build\r11-install-' + [guid]::NewGuid().ToString('N'))
$taskFixtureTool = Join-Path $taskFixtureRoot 'tool'
$taskFixtureDesktop = Join-Path $taskFixtureRoot 'Desktop'
$taskFixturePrograms = Join-Path $taskFixtureRoot 'Programs'
$taskFixtureStartup = Join-Path $taskFixtureRoot 'Startup'
$taskFixtureCalls = New-Object Collections.Generic.List[string]
foreach ($directory in @($taskFixtureDesktop,$taskFixturePrograms,$taskFixtureStartup,
    "$taskFixtureTool\pet","$taskFixtureTool\mailbox\.venv\Scripts","$taskFixtureTool\install")) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
# Redirect only the three shell folders in copies of the installers.
foreach ($relative in @('pet\install.ps1','mailbox\install.ps1','mailbox\retire-standalone.ps1')) {
    $text = [IO.File]::ReadAllText((Join-Path $taskFixtureRepo ('pc\' + $relative)))
    foreach ($pair in @(@('Desktop',$taskFixtureDesktop),@('Programs',$taskFixturePrograms),@('Startup',$taskFixtureStartup))) {
        $text = $text.Replace("[Environment]::GetFolderPath('$($pair[0])')",("'" + $pair[1] + "'"))
    }
    [IO.File]::WriteAllText((Join-Path $taskFixtureTool $relative),$text,[Text.Encoding]::ASCII)
}
foreach ($relative in @('launcher.exe','pet\pet.ps1','pet\pet-confirm.wav','mailbox\mailbox.ps1',
    'mailbox\.venv\Scripts\python.exe')) {
    [IO.File]::WriteAllText((Join-Path $taskFixtureTool $relative),'fixture')
}
Copy-Item -LiteralPath (Join-Path $taskFixtureRepo 'pc\pet\pet.ico') -Destination "$taskFixtureTool\pet\pet.ico"
[IO.File]::WriteAllText("$taskFixtureTool\mailbox\requirements.txt",'fixture requirements')
$taskFixtureHash = (Get-FileHash "$taskFixtureTool\mailbox\requirements.txt").Hash
[IO.File]::WriteAllText("$taskFixtureTool\mailbox\.venv\requirements.sha256",$taskFixtureHash)
[IO.File]::WriteAllText("$taskFixtureTool\install\register-tasks.ps1",'param($ToolDir,[switch]$PetOnly,[switch]$Apply,[switch]$Force) if(-not $PetOnly){throw "Wrong autostart owner"}; $taskFixtureCalls.Add("pet-autostart:$Apply")')
$taskFixtureLegacy = $false
function Get-ScheduledTask {
    if ($taskFixtureLegacy) {
        [pscustomobject]@{TaskName='mailbox';TaskPath='\';Actions=@([pscustomobject]@{Execute='launcher.exe';Arguments=('"' + $taskFixtureTool + '\mailbox\mailbox.ps1"')})}
    }
}
function Stop-ScheduledTask { param($TaskName,$TaskPath,$ErrorAction) $taskFixtureCalls.Add('stop-task:'+$TaskName) }
function Unregister-ScheduledTask { param($TaskName,$TaskPath,$Confirm) $taskFixtureCalls.Add('remove-task:'+$TaskName) }
function Get-CimInstance {
    if ($taskFixtureLegacy) {
        [pscustomobject]@{ProcessId=123456;Name='pythonw.exe';CommandLine=('pythonw.exe "'+$taskFixtureTool+'\mailbox\app.py"')}
        [pscustomobject]@{ProcessId=123457;Name='pythonw.exe';CommandLine='pythonw.exe D:\other\mailbox\app.py'}
        [pscustomobject]@{ProcessId=123458;Name='pythonw.exe';CommandLine=('pythonw.exe "'+$taskFixtureTool+'\mailbox\app.py.backup"')}
    }
}
function Stop-Process { param($Id,[switch]$Force,$ErrorAction) $taskFixtureCalls.Add('stop-process:'+$Id) }
function Test-Path {
    param($LiteralPath,$Path)
    $value = if ($LiteralPath) { $LiteralPath } else { $Path }
    if ($value -like 'HKCU:*') { return $taskFixtureLegacy }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $value
}
function Get-FileHash {
    param($LiteralPath,$Path,$Algorithm)
    $value = if($LiteralPath){$LiteralPath}else{$Path}
    $sha=[Security.Cryptography.SHA256]::Create()
    try { [pscustomobject]@{Hash=([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($value)))).Replace('-','')} }
    finally { $sha.Dispose() }
}
function Get-ItemProperty {
    param($LiteralPath)
    [pscustomobject]@{OldMailbox=('"'+$taskFixtureTool+'\mailbox\mailbox.ps1"');Other='unrelated.exe'}
}
function Remove-ItemProperty { param($LiteralPath,$Name) $taskFixtureCalls.Add('remove-run:'+$Name) }
function Assert-MLFixture($Condition,$Message) { if (-not $Condition) { throw $Message } }
$taskFixtureShell = New-Object -ComObject WScript.Shell
# Fresh install creates only the pet shortcuts with the pet icon.
& "$taskFixtureTool\mailbox\install.ps1" -Apply -AutoStart
Assert-MLFixture (-not (Test-Path "$taskFixtureDesktop\MuseLink Mailbox.lnk")) 'Fresh mailbox created a shortcut'
& "$taskFixtureTool\pet\install.ps1" -Apply -AutoStart | Out-Null
foreach ($directory in @($taskFixtureDesktop,$taskFixturePrograms)) {
    $link=$taskFixtureShell.CreateShortcut("$directory\MuseLink Pet.lnk")
    Assert-MLFixture ($link.TargetPath -eq "$taskFixtureTool\launcher.exe") 'Wrong pet shortcut target'
    Assert-MLFixture ($link.Arguments -eq ('"'+$taskFixtureTool+'\pet\pet.ps1"')) 'Wrong pet shortcut arguments'
    Assert-MLFixture ($link.IconLocation -eq "$taskFixtureTool\pet\pet.ico,0") 'Wrong pet shortcut icon'
}
# Upgrade preview preserves legacy state; apply removes only this mailbox.
foreach ($directory in @($taskFixtureDesktop,$taskFixturePrograms,$taskFixtureStartup)) {
    $link=$taskFixtureShell.CreateShortcut("$directory\MuseLink Mailbox.lnk")
    $link.TargetPath="$taskFixtureTool\launcher.exe";$link.Arguments='"'+$taskFixtureTool+'\mailbox\mailbox.ps1"';$link.Save()
}
$taskFixtureLegacy=$true
. "$taskFixtureTool\mailbox\retire-standalone.ps1"
$taskFixtureBefore=$taskFixtureCalls.Count
Remove-MLStandaloneMailbox -ToolDir $taskFixtureTool | Out-Null
Assert-MLFixture ($taskFixtureCalls.Count -eq $taskFixtureBefore) 'Dry run mutated legacy state'
Assert-MLFixture (Test-Path "$taskFixtureDesktop\MuseLink Mailbox.lnk") 'Dry run removed shortcut'
& "$taskFixtureTool\mailbox\install.ps1" -Apply -AutoStart
foreach ($directory in @($taskFixtureDesktop,$taskFixturePrograms,$taskFixtureStartup)) {
    Assert-MLFixture (-not (Test-Path "$directory\MuseLink Mailbox.lnk")) 'Upgrade left a legacy shortcut'
}
Assert-MLFixture ($taskFixtureCalls.Contains('stop-process:123456')) 'Legacy process not retired'
Assert-MLFixture (-not $taskFixtureCalls.Contains('stop-process:123457')) 'Touched unrelated process'
Assert-MLFixture (-not $taskFixtureCalls.Contains('stop-process:123458')) 'Matched a path prefix'
Assert-MLFixture ($taskFixtureCalls.Contains('remove-task:mailbox')) 'Legacy task not retired'
Assert-MLFixture ($taskFixtureCalls.Contains('remove-run:OldMailbox')) 'Legacy Run entry not retired'
Assert-MLFixture (-not $taskFixtureCalls.Contains('remove-run:Other')) 'Touched unrelated Run entry'
$taskFixtureRepeat = & "$taskFixtureTool\pet\install.ps1" -Apply
Assert-MLFixture (@($taskFixtureRepeat | Where-Object { $_ -like 'skip *MuseLink Pet.lnk*' }).Count -eq 2) 'Pet install not idempotent'
@{ok=$true;root=$taskFixtureRoot;checks=@('fresh mailbox: no shortcuts','pet: two shortcuts + pet.ico',
    'upgrade preview unchanged','upgrade removes Desktop/Programs/Startup legacy links',
    'legacy process/task/Run retired with unrelated entries preserved','pet autostart only','pet reinstall idempotent');calls=$taskFixtureCalls.ToArray()} | ConvertTo-Json -Depth 5
