# Local launch wrapper. No scheduled task and no changes to popup.ps1.
param([string]$ToolDir, [string]$ReqId, [string]$Silent = 'false', [string]$PopupScript)
$ErrorActionPreference = 'Stop'
if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') { exit 1 }
. (Join-Path $ToolDir 'muselink-common.ps1')
# Mirror the production mutex, so a Muse card that won before startup is
# never accidentally opened as this local card. Release as soon as the
# popup has loaded its request snapshot (the first Read-Utf8 calls).
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($ToolDir.ToLowerInvariant()))).Replace('-', '') }
finally { $sha.Dispose() }
$script:launchMutex = New-Object Threading.Mutex($false, ('muselink-complete-' + $key))
$script:launchHeld = $false
try {
    try { $script:launchHeld = $script:launchMutex.WaitOne(10000) } catch [Threading.AbandonedMutexException] { $script:launchHeld = $true }
    if (-not $script:launchHeld) { exit 1 }
    if ((Read-MLUtf8 (Join-Path $ToolDir 'popup-reqid.txt')).Trim() -ne $ReqId) { exit 0 }
    # Test-Path's process-local shim hides ONLY the chime configuration for
    # notify_user. All production files, including sound settings, are intact.
    $script:quiet = $Silent -eq 'true'
    $script:soundPath = Join-Path $ToolDir 'popup-sound.txt'
    function Test-Path {
        [CmdletBinding(DefaultParameterSetName='Path')]
        param([Parameter(Position=0,ParameterSetName='Path')][string[]]$Path,
              [Parameter(ParameterSetName='LiteralPath')][string[]]$LiteralPath)
        $p = @(if ($LiteralPath) { $LiteralPath } else { $Path })
        if ($script:quiet -and $p.Count -eq 1 -and $p[0] -eq $script:soundPath) { return $false }
        Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
    }
    # popup's first configuration read follows its card snapshot. A command
    # breakpoint releases the staging lock before WinForms begins waiting.
    $popupPath = Join-Path $ToolDir 'popup.ps1'
    $lines = [IO.File]::ReadAllLines($popupPath)
    $runLine = 0
    for ($i = 0; $i -lt $lines.Length; $i++) {
        if ($lines[$i] -match '^\$script:Text\s*=') { $runLine = $i + 1; break }
    }
    # Release after snapshot/options have been read, before UI setup. The
    # next function declaration is stable across the current popup version.
    for ($i = $runLine; $i -lt $lines.Length; $i++) {
        if ($lines[$i] -match '^function Clr\(') { $runLine = $i + 1; break }
    }
    $script:releaseBreak = Set-PSBreakpoint -Script $popupPath -Line $runLine -Action {
        if ($script:launchHeld) {
            $script:launchMutex.ReleaseMutex(); $script:launchHeld = $false
            Remove-PSBreakpoint $script:releaseBreak
            $script:releaseBreak = $null
        }
    }
    . $popupPath
} finally {
    if ($script:releaseBreak) { Remove-PSBreakpoint $script:releaseBreak -ErrorAction SilentlyContinue }
    if ($script:launchHeld) { $script:launchMutex.ReleaseMutex() }
    $script:launchMutex.Dispose()
}
