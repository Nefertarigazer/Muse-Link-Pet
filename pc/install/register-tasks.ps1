# register-tasks.ps1 - (re)register the MuseLink scheduled tasks for the
# current user. Without -Apply it only prints the plan.
#   powershell -File register-tasks.ps1 [-ToolDir <dir>] [-Apply] [-IncludeWatchdog] [-IncludeExplib]
#   ... -ExplibOnly | -PetOnly | -MailboxOnly   register only that one task
#
# Every task starts through launcher.exe (no console window) and runs in the
# user's interactive session with the LIMITED run level. Trigger-type tasks
# (popup, muse-watch, bridge-publish-upload) have NO trigger at all and are
# only started on demand with schtasks /Run: a "one-time" trigger such as
# "/sc once /st 23:59" really fires once that night (2026-11-12 review).
# The Tailscale watchdog needs the highest run level to start a service, so it
# is registered only with -IncludeWatchdog from an elevated PowerShell.
param(
    [string]$ToolDir = '',
    [switch]$Apply,
    [switch]$IncludeWatchdog,
    [switch]$IncludeMailbox,
    [switch]$MailboxOnly,
    [switch]$IncludePet,
    [switch]$PetOnly,
    [switch]$IncludeExplib,
    [switch]$ExplibOnly,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
if ($ToolDir -eq '') { $ToolDir = Split-Path -Parent $PSScriptRoot }
$launcher = Join-Path $ToolDir 'launcher.exe'
$user = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }

function New-MLSettings([switch]$Long) {
    $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -StartWhenAvailable
    if ($Long) { $s.ExecutionTimeLimit = 'PT0S' }
    return $s
}

function Get-Action([string]$script, [string]$extra = '') {
    $arg = '"' + (Join-Path $ToolDir $script) + '"'
    if ($extra) { $arg += ' ' + $extra }
    return New-ScheduledTaskAction -Execute $launcher -Argument $arg -WorkingDirectory $ToolDir
}

$limited = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$plan = @(
    @{ Name = 'executor-host'; Action = (Get-Action 'executor-host-run.ps1'); Trigger = (New-ScheduledTaskTrigger -AtLogOn -User $user); Principal = $limited; Settings = (New-MLSettings -Long) },
    @{ Name = 'popup'; Action = (Get-Action 'popup.ps1'); Trigger = $null; Principal = $limited; Settings = (New-MLSettings -Long) },
    @{ Name = 'muse-watch'; Action = (Get-Action 'watch-start.ps1'); Trigger = $null; Principal = $limited; Settings = (New-MLSettings -Long) },
    @{ Name = 'bridge-publish-upload'; Action = (Get-Action 'chunked-upload.ps1' ('-ArgsFile "' + (Join-Path $ToolDir 'upload-args.json') + '"')); Trigger = $null; Principal = $limited; Settings = (New-MLSettings -Long) },
    @{ Name = 'muselink-maintenance'; Action = (Get-Action 'maintenance.ps1'); Trigger = (New-ScheduledTaskTrigger -Daily -At '04:30'); Principal = $limited; Settings = (New-MLSettings) }
)
# *Only: register just that one task. Without it the base plan above is
# re-registered too, which moves executor-host to the Limited run level.
if ($MailboxOnly -or $PetOnly -or $ExplibOnly) { $plan = @() }
# Retired mailbox switches are compatibility aliases for the pet task.
if ($IncludePet -or $PetOnly -or $IncludeMailbox -or $MailboxOnly) {
    $plan += @{ Name = 'pet'; Action = (Get-Action 'pet\pet.ps1');
                Trigger = (New-ScheduledTaskTrigger -AtLogOn -User $user);
                Principal = $limited; Settings = (New-MLSettings -Long) }
    # Remove any legacy logon 'mailbox' task (which popped up the mailbox window at startup)
    # and stop any leftover standalone mailbox process (which owns the old envelope tray icon).
    $legacyMailbox = @(Get-ScheduledTask -TaskName 'mailbox' -TaskPath '\' -ErrorAction SilentlyContinue)
    foreach ($lt in $legacyMailbox) {
        $acts = @($lt.Actions | Where-Object { ($_.Execute + ' ' + $_.Arguments).Replace('/', '\') -match '(?i)\\mailbox\\(mailbox\.ps1|app\.py)(?=$|[\s"])' })
        if ($acts.Count) {
            "remove legacy mailbox logon task: $($lt.TaskPath)$($lt.TaskName)"
            if ($Apply) {
                Stop-ScheduledTask -TaskName $lt.TaskName -TaskPath $lt.TaskPath -ErrorAction SilentlyContinue
                Unregister-ScheduledTask -TaskName $lt.TaskName -TaskPath $lt.TaskPath -Confirm:$false
            }
        }
    }
    foreach ($lp in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($lp.ProcessId -ne $PID -and $lp.Name -match '^(pythonw?|powershell|pwsh|launcher)\.exe$' -and
            $lp.CommandLine -and $lp.CommandLine.Replace('/', '\') -match '(?i)\\mailbox\\(mailbox\.ps1|app\.py)(?=$|[\s"])') {
            "stop legacy standalone mailbox process: $($lp.ProcessId)"
            if ($Apply) { Stop-Process -Id $lp.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
}
if ($IncludeExplib -or $ExplibOnly) {
    $plan += @{ Name = 'explib-share';
                Action = (Get-Action 'explib\share-check.ps1');
                Trigger = (New-ScheduledTaskTrigger -Daily -At '12:30');
                Principal = $limited; Settings = (New-MLSettings) }
}
if ($IncludeWatchdog -and -not $MailboxOnly -and -not $PetOnly -and -not $ExplibOnly) {
    $t = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 3)
    $plan += @{ Name = 'tailscale-watchdog'; Action = (Get-Action 'tailscale-watchdog.ps1'); Trigger = $t;
                Principal = (New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest); Settings = (New-MLSettings) }
}

if (-not (Test-Path -LiteralPath $launcher)) { Write-Output "WARN launcher.exe not found in $ToolDir (run launcher\build-launcher.ps1 first)" }
# Flatten only definition properties, excluding CIM transport metadata and IDs.
function Add-MLTaskFields($value, [string]$prefix, $fields) {
    if ($null -eq $value) { $fields[$prefix] = ''; return }
    if ($value -is [Microsoft.Management.Infrastructure.CimInstance]) {
        $fields[$prefix + '.class'] = $value.CimClass.CimClassName
        foreach ($prop in $value.CimInstanceProperties) {
            if ($prop.Name -eq 'Id') { continue }
            $v = $prop.Value
            # Scheduler may return a logon trigger's account as a SID.
            if ($prop.Name -eq 'UserId' -and $v) {
                try { $v = (New-Object Security.Principal.NTAccount([string]$v)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { }
            }
            # Calendar dates are incidental; the daily time is the schedule.
            if ($prop.Name -eq 'StartBoundary' -and $value.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger' -and $v) {
                $v = ([datetime]$v).ToString('HH:mm:ss')
            }
            Add-MLTaskFields $v ($prefix + '.' + $prop.Name) $fields
        }
    } elseif ($value -is [Array]) {
        $fields[$prefix + '.count'] = [string]$value.Count
        for ($i = 0; $i -lt $value.Count; $i++) { Add-MLTaskFields $value[$i] ($prefix + "[$i]") $fields }
    } else { $fields[$prefix] = [string]$value }
}
function Get-MLTaskFields($task) {
    $f = @{}
    Add-MLTaskFields @($task.Actions) 'action' $f
    Add-MLTaskFields @($task.Triggers | Where-Object { $null -ne $_ }) 'trigger' $f
    Add-MLTaskFields $task.Settings 'settings' $f
    foreach ($key in 'UserId', 'GroupId', 'LogonType', 'RunLevel') {
        $v = [string]$task.Principal.$key
        if ($key -eq 'UserId' -and $v) {
            try { $v = (New-Object Security.Principal.NTAccount($v)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { }
        }
        $f['principal.' + $key] = $v
    }
    return $f
}
foreach ($p in $plan) {
    # Query failures are errors, not evidence that a task is absent.
    $existing = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq $p.Name -and $_.TaskPath -eq '\' })
    $back = if ($existing.Count) { $existing[0] } else { $null }
    $task = New-ScheduledTask -Action $p.Action -Principal $p.Principal -Settings $p.Settings
    if ($p.Trigger) { $task.Triggers = @($p.Trigger) }
    # A repeating watchdog keeps its original start epoch on later installs.
    # @(...) of a task without triggers is @($null), Count 1: filter nulls
    if ($back -and $p.Name -eq 'tailscale-watchdog' -and @($back.Triggers | Where-Object { $null -ne $_ }).Count -eq 1 -and
        $back.Triggers[0].CimClass.CimClassName -eq 'MSFT_TaskTimeTrigger' -and $back.Triggers[0].StartBoundary) {
        $task.Triggers[0].StartBoundary = $back.Triggers[0].StartBoundary
    }
    $diff = @()
    if ($back) {
        $old = Get-MLTaskFields $back
        $new = Get-MLTaskFields $task
        foreach ($key in (@($old.Keys) + @($new.Keys) | Sort-Object -Unique)) {
            if ($old[$key] -cne $new[$key]) { $diff += ('{0}: {1} -> {2}' -f $key, $old[$key], $new[$key]) }
        }
    }
    if ($back -and $diff.Count -eq 0 -and -not $Force) {
        "skip $($p.Name): already installed (action, triggers, principal and settings match)"; continue
    }
    $state = if ($back) { 'change' } else { 'new' }
    "$state $($p.Name): $($p.Action.Execute) $($p.Action.Arguments) run=$($p.Principal.RunLevel)"
    $diff | ForEach-Object { '  ' + $_ }
    if ($Apply) {
        if (-not (Test-Path -LiteralPath $launcher)) { throw 'launcher.exe not found; build it first' }
        Register-ScheduledTask -TaskName $p.Name -InputObject $task -Force | Out-Null
        $back = Get-ScheduledTask -TaskName $p.Name -TaskPath '\'
        Write-Output ("  registered: state={0} runlevel={1} triggers={2}" -f $back.State, $back.Principal.RunLevel, @($back.Triggers | Where-Object { $null -ne $_ }).Count)
    }
}
if (-not $Apply) { Write-Output 'DRY RUN - nothing registered. Re-run with -Apply.' }
