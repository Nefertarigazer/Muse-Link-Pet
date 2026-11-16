# tailscale-watchdog.ps1 - local self-heal for Tailscale on the PC.
# Runs every 3 min via scheduled task (launcher.exe, no console window). The
# task needs the highest run level: starting a service requires it.
# Healthy: exit silently (heartbeat line once per hour).
# Unhealthy: start the service and/or the tray app, re-check, log the outcome.
# Never runs "tailscale up": a user who disconnected on purpose stays down.
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
$log = Get-MLPath 'tailscale-watchdog.log'

function Write-Log($msg) {
  $line = ('{0} {1}' -f (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz'), $msg)
  Add-Content -Path $log -Value $line -Encoding UTF8
  try {
    if ((Get-Item $log).Length -gt 262144) {
      Get-Content $log -Tail 400 | Set-Content $log -Encoding UTF8
    }
  } catch {}
}

# Locate the binaries: muselink.json -> tailscale.exe_dir, else the directory
# of the installed service binary, else the default install location.
function Get-TailscaleDir {
  $d = [string](Get-MLSetting 'tailscale' 'exe_dir' '')
  if ($d -and (Test-Path -LiteralPath (Join-Path $d 'tailscale.exe'))) { return $d }
  try {
    $svc = Get-CimInstance Win32_Service -Filter "Name='Tailscale'" -ErrorAction Stop
    if ($svc -and $svc.PathName) {
      $exe = ($svc.PathName -replace '^"([^"]+)".*$', '$1') -replace '^(\S+\.exe).*$', '$1'
      $d = Split-Path -Parent $exe
      if (Test-Path -LiteralPath (Join-Path $d 'tailscale.exe')) { return $d }
    }
  } catch {}
  return (Join-Path $env:ProgramFiles 'Tailscale')
}
$tsDir = Get-TailscaleDir
$tsExe = Join-Path $tsDir 'tailscale.exe'
$guiExe = Join-Path $tsDir 'tailscale-ipn.exe'

function Get-BackendState {
  if (-not (Test-Path $tsExe)) { return 'no-cli' }
  try {
    $j = & $tsExe status --json 2>$null | ConvertFrom-Json
    return $j.BackendState
  } catch { return 'error' }
}

$state = Get-BackendState
$svc = Get-Service -Name 'Tailscale' -ErrorAction SilentlyContinue
$gui = Get-Process -Name 'tailscale-ipn' -ErrorAction SilentlyContinue

if ($state -eq 'Running' -and $gui) {
  if ((Get-Date).Minute -lt 3) { Write-Log 'ok (hourly heartbeat)' }
  exit 0
}

Write-Log ("unhealthy: backend=$state service=" + $(if ($svc) { $svc.Status } else { 'missing' }) + ' gui=' + $(if ($gui) { 'up' } else { 'down' }) + " dir=$tsDir")

if ($svc -and $svc.Status -ne 'Running') {
  try { Start-Service -Name 'Tailscale' -ErrorAction Stop; Write-Log 'service started' } catch { Write-Log ('service start failed: ' + $_.Exception.Message) }
}
if (-not $gui -and (Test-Path $guiExe)) {
  try { Start-Process $guiExe; Write-Log 'gui launched' } catch { Write-Log ('gui launch failed: ' + $_.Exception.Message) }
}

Start-Sleep -Seconds 8
$state2 = Get-BackendState
$gui2 = Get-Process -Name 'tailscale-ipn' -ErrorAction SilentlyContinue
if ($state2 -eq 'Running') { Write-Log 'healed: backend Running' } else { Write-Log ("still down: backend=$state2 gui=" + $(if ($gui2) { 'up' } else { 'down' }) + ' (will retry next cycle)') }
exit 0
