# restart-host.ps1 - swap the running executor host for the version on disk.
#   powershell -File restart-host.ps1 [-ToolDir <dir>] [-Apply]
# Without -Apply it only shows what is running and what it would stop.
#
# Must run elevated while the "executor-host" task runs at the highest run
# level (current production): a medium-integrity shell cannot stop it. Once
# register-tasks.ps1 has moved the task to the Limited run level, a normal
# shell is enough. Do NOT use the executor's own "shell" kind to restart it.
#
# Steps (the order matters, see docs/install.en.md section 4):
#   1. end the task instance (the task is IgnoreNew: a lingering instance
#      would swallow the next /run);
#   2. stop every python process whose command line runs executor-host.py
#      (listener or not: a stray old host still holds the singleton mutex);
#   3. wait for 0 listeners on the port;
#   4. schtasks /run once;
#   5. accept only: exactly 1 listener AND a fresh host_start line in
#      executor-events.jsonl carrying the version from executor-host.py.
# Exit codes: 0 ok, 1 verification failed, 2 not elevated / bad state.
param(
    [string]$ToolDir = '',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
if ($ToolDir -eq '') { $ToolDir = Split-Path -Parent $PSScriptRoot }
. (Join-Path $ToolDir 'muselink-common.ps1')
$port = [int](Get-MLSetting 'executor' 'port' 18744)
$hostPy = Join-Path $ToolDir 'executor-host.py'
$events = Join-Path $ToolDir 'executor-events.jsonl'
$want = (Select-String -LiteralPath $hostPy -Pattern '^VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1).Matches[0].Groups[1].Value

function Get-Listeners { @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique) }
function Get-HostProcs { @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -and $_.CommandLine -match 'executor-host\.py' }) }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$procs = Get-HostProcs
$lis = Get-Listeners
"elevated=$admin port=$port listeners=$($lis -join ',') host_procs=$(@($procs | ForEach-Object { $_.ProcessId }) -join ',') target_version=$want"
foreach ($l in $lis) {
    if (-not (@($procs | ForEach-Object { $_.ProcessId }) -contains $l)) {
        "port $port is held by pid $l, which is not a visible executor-host.py process"
        if (-not $admin) { 'not elevated: cannot see the command line of an elevated host; rerun from an elevated PowerShell'; exit 2 }
        'refusing to stop an unknown process'; exit 2
    }
}
if (-not $Apply) { 'preview only; rerun with -Apply'; exit 0 }

$t0 = Get-Date
& schtasks.exe /end /tn executor-host 2>&1 | Out-Null
foreach ($p in $procs) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped pid $($p.ProcessId)" }
    catch { "could not stop pid $($p.ProcessId): $($_.Exception.Message)"; exit 2 }
}
$deadline = (Get-Date).AddSeconds(15)
while (((Get-Listeners).Count -gt 0 -or (Get-HostProcs).Count -gt 0) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
if ((Get-Listeners).Count -gt 0) { "still listening on $port after 15 s; not starting a second host"; exit 2 }

& schtasks.exe /run /tn executor-host | Out-Null
$deadline = (Get-Date).AddSeconds(40)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    $lis = Get-Listeners
    if ($lis.Count -ne 1) { continue }
    $fresh = @(Get-Content -LiteralPath $events -Tail 50 -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
        try { $_ | ConvertFrom-Json } catch { $null }
    } | Where-Object { $_ -and $_.type -eq 'host_start' -and ([string]$_.detail) -match ('v' + [regex]::Escape($want)) -and ([datetime]$_.ts) -ge $t0.AddSeconds(-2) })
    if ($fresh.Count -ge 1) { $ok = $true; break }
}
$lis = Get-Listeners
"listeners after start: $($lis.Count) ($($lis -join ','))"
if ($ok -and $lis.Count -eq 1) { "OK: executor host v$want is the only listener"; exit 0 }
"FAILED: expected 1 listener and a fresh host_start v$want in $events"
exit 1
