# pet.ps1 - ordinary-user window; UTF-8 JSON preserves every argument.
$ErrorActionPreference = 'Stop'
$python = Join-Path (Split-Path -Parent $PSScriptRoot) 'mailbox\.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Shared mailbox venv missing. Run mailbox\install.ps1 -Apply.' }
$env:PYTHONPATH = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding($false)
$argFile = Join-Path ([IO.Path]::GetTempPath()) ('pet-args-' + [guid]::NewGuid().ToString('N') + '.json')
$values = @()
foreach ($a in $args) { $values += [string]$a }
$json = ConvertTo-Json -InputObject @($values) -Compress
[IO.File]::WriteAllText($argFile, $json, $utf8)
$code = 1
$child = $null
try {
    # PowerShell does not wait for GUI-subsystem pythonw invoked with &.
    # Wait on the direct child handle directly; Start-Process -Wait in
    # Windows PowerShell 5.1 can block on detached browser grandchildren.
    $app = Join-Path $PSScriptRoot 'app.py'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $python
    $psi.Arguments = '"' + $app + '" --args-file "' + $argFile + '"'
    $loc = Get-Location
    if ($loc.Provider.Name -eq 'FileSystem') { $psi.WorkingDirectory = $loc.ProviderPath }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $child = [System.Diagnostics.Process]::Start($psi)
    $child.WaitForExit()
    $code = $child.ExitCode
} finally {
    if ($null -ne $child) { $child.Dispose() }
    Remove-Item -LiteralPath $argFile -Force -ErrorAction SilentlyContinue
}
exit $code
