# Only the legacy mailbox belonging to this installation is retired.
function Remove-MLStandaloneMailbox {
    param([string]$ToolDir, [switch]$Apply,
        [string]$Desktop = [Environment]::GetFolderPath('Desktop'),
        [string]$Programs = [Environment]::GetFolderPath('Programs'),
        [string]$Startup = [Environment]::GetFolderPath('Startup'))
    $entry = Join-Path $ToolDir 'mailbox\mailbox.ps1'
    $app = Join-Path $ToolDir 'mailbox\app.py'
    $matchesMailbox = {
        param([string]$Text)
        $normalized = $Text.Replace('/', '\')
        return ($normalized -match ('(?i)' + [regex]::Escape($entry) + '(?=$|[\s"])') -or
                $normalized -match ('(?i)' + [regex]::Escape($app) + '(?=$|[\s"])'))
    }
    foreach ($directory in @($Desktop, $Programs)) {
        $path = Join-Path $directory 'MuseLink Mailbox.lnk'
        if (Test-Path -LiteralPath $path) {
            "remove legacy shortcut: $path"
            if ($Apply) { Remove-Item -LiteralPath $path -Force }
        } else { "skip absent legacy shortcut: $path" }
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($link in @(Get-ChildItem -LiteralPath $Startup -Filter '*.lnk' -ErrorAction SilentlyContinue)) {
        $shortcut = $shell.CreateShortcut($link.FullName)
        if (& $matchesMailbox ($shortcut.TargetPath + ' ' + $shortcut.Arguments)) {
            "remove mailbox startup shortcut: $($link.FullName)"
            if ($Apply) { Remove-Item -LiteralPath $link.FullName -Force }
        }
    }
    foreach ($task in @(Get-ScheduledTask -ErrorAction Stop)) {
        $ours = @($task.Actions | Where-Object { & $matchesMailbox ($_.Execute + ' ' + $_.Arguments) })
        if ($ours.Count) {
            "remove mailbox autostart task: $($task.TaskPath)$($task.TaskName)"
            if ($Apply) {
                Stop-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction SilentlyContinue
                Unregister-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -Confirm:$false
            }
        }
    }
    foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
                       'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce')) {
        if (-not (Test-Path -LiteralPath $key)) { continue }
        foreach ($property in (Get-ItemProperty -LiteralPath $key).PSObject.Properties) {
            if ($property.Name -notlike 'PS*' -and $property.Value -is [string] -and
                (& $matchesMailbox $property.Value)) {
                "remove mailbox autostart value: $key\$($property.Name)"
                if ($Apply) { Remove-ItemProperty -LiteralPath $key -Name $property.Name }
            }
        }
    }
    foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
        if ($process.ProcessId -ne $PID -and $process.Name -match '^(pythonw?|powershell|pwsh|launcher)\.exe$' -and
            (& $matchesMailbox $process.CommandLine)) {
            "stop legacy mailbox process: $($process.ProcessId)"
            if ($Apply) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
}
