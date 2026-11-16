# build-launcher.ps1 - compile launcher.cs into launcher.exe with the C#
# compiler that ships with .NET Framework 4 (present on every Windows 10/11).
#   powershell -File build-launcher.ps1 [-OutDir <tool dir>]
param([string]$OutDir = '', [switch]$Force, [switch]$Preview)
$ErrorActionPreference = 'Stop'
if ($OutDir -eq '') { $OutDir = Split-Path -Parent $PSScriptRoot }
$src = Join-Path $PSScriptRoot 'launcher.cs'
$out = Join-Path $OutDir 'launcher.exe'
$record = Join-Path $OutDir 'launcher.source.sha256'
$sourceHash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
if (-not $Force -and (Test-Path -LiteralPath $out) -and (Test-Path -LiteralPath $record) -and
    ([IO.File]::ReadAllText($record).Trim() -eq $sourceHash)) {
    "skip launcher.exe: already installed (launcher.cs sha256=$sourceHash)"; exit 0
}
$state = if (Test-Path -LiteralPath $out) { 'change' } else { 'new' }
"$state launcher.exe (launcher.cs sha256=$sourceHash)"
if ($Preview) { exit 0 }
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) { throw 'csc.exe (.NET Framework 4) not found' }
& $csc /nologo /target:winexe /optimize+ "/out:$out" $src
if ($LASTEXITCODE -ne 0) { throw "csc failed with $LASTEXITCODE" }
[IO.File]::WriteAllText($record, $sourceHash + "`n", [Text.Encoding]::ASCII)
$h = (Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash
Write-Output "BUILT $out sha256=$h"
