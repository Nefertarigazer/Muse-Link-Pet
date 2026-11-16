# mailbox.ps1 - ordinary-user window; UTF-8 JSON preserves every argument.
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Mailbox venv missing. Run mailbox\install.ps1 -Apply.' }
$env:PYTHONPATH = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding($false)
$argFile = Join-Path ([IO.Path]::GetTempPath()) ('mailbox-args-' + [guid]::NewGuid().ToString('N') + '.json')
$values = @()
foreach ($a in $args) { $values += [string]$a }
$json = ConvertTo-Json -InputObject @($values) -Compress
[IO.File]::WriteAllText($argFile, $json, $utf8)
try {
    # PowerShell does not wait for GUI-subsystem pythonw invoked with &.
    # Keep the UTF-8 argument file alive until the child has consumed it.
    $app = Join-Path $PSScriptRoot 'app.py'
    $nativeArgs = '"' + $app + '" --args-file "' + $argFile + '"'
    $child = Start-Process -FilePath $python -ArgumentList $nativeArgs -WindowStyle Hidden -Wait -PassThru
    $code = $child.ExitCode
} finally {
    Remove-Item -LiteralPath $argFile -Force -ErrorAction SilentlyContinue
}
exit $code
