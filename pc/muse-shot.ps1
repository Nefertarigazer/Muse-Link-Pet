# muse-shot.ps1 - the PC-side screenshot program behind vm/shot.sh.
# Rules baked in: PNG <= 400 KB is sent as-is; larger shots are re-encoded as
# JPEG (quality 80, stepping down to 50 if still too big) with the SAME pixel
# size, so coordinates read from the image stay valid. The native PNG always
# stays at <tool dir>\shots\last-<mode>.png.
# Output: line 1 = JSON metadata, line 2 = base64 image.
param(
  [Parameter(Mandatory=$true)][ValidateSet("window","screen")][string]$Mode,
  [string]$App = ""
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
$env:AGENT_DESKTOP_HOME = Get-MLAgentDesktopHome
$THRESHOLD = 409600
$QUALITIES = @(80, 70, 60, 50)
$dir = Get-MLPath 'shots'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$ad = Get-MLSetting 'executor' 'agent_desktop_exe' (Join-Path $env:APPDATA "npm\node_modules\agent-desktop\bin\agent-desktop-win32-x64.exe")
$native = Join-Path $dir ("last-" + $Mode + ".png")
try {
  if ($Mode -eq "window") {
    if (-not $App) { throw "window mode needs -App" }
    $capOut = & $ad screenshot $native --app $App 2>&1 | Out-String
  } else {
    $capOut = & $ad screenshot $native --screen 0 2>&1 | Out-String
  }
  if (($LASTEXITCODE -ne 0) -or (-not (Test-Path $native))) { throw ("capture failed: " + $capOut.Trim()) }
  $srcBytes = (Get-Item $native).Length
  Add-Type -AssemblyName System.Drawing
  $img = [System.Drawing.Image]::FromFile($native)
  $w = $img.Width; $h = $img.Height
  $fmt = "png"; $payloadPath = $native; $quality = $null
  if ($srcBytes -gt $THRESHOLD) {
    $jpg = Join-Path $dir ("last-" + $Mode + ".jpg")
    $enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
    foreach ($q in $QUALITIES) {
      $pars = New-Object System.Drawing.Imaging.EncoderParameters(1)
      $pars.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]$q)
      $img.Save($jpg, $enc, $pars)
      $quality = $q
      if ((Get-Item $jpg).Length -le $THRESHOLD) { break }
    }
    $fmt = "jpeg"; $payloadPath = $jpg
  }
  $img.Dispose()
  $bytes = [IO.File]::ReadAllBytes($payloadPath)
  $meta = @{ fmt=$fmt; bytes=$bytes.Length; src_bytes=$srcBytes; w=$w; h=$h; native=$native; quality=$quality; over_limit=($bytes.Length -gt $THRESHOLD) } | ConvertTo-Json -Compress
  [Console]::WriteLine($meta)
  [Console]::WriteLine([Convert]::ToBase64String($bytes))
} catch {
  [Console]::WriteLine((@{ fmt="error"; error=$_.Exception.Message } | ConvertTo-Json -Compress))
  exit 1
}
