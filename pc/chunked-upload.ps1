# chunked-upload.ps1 - chunked parallel upload from the PC to your own server
# (formerly pc-to-a1-chunked-upload.ps1). Used by the release pipeline
# (vm/bridge/pipelines/publish-release.py); usable on its own.
#
# Why: on a lossy long-haul link a single scp stream collapses (measured
# 0.13 MB/s); 16 parallel chunk streams measured 0.85 MB/s on the same link.
#
#   powershell -File chunked-upload.ps1 -ArgsFile <json>
#     {"files":[{"path":"D:\\out\\app.zip","size":123,"remote_name":"App-1.0.zip"}],
#      "dest":"user@host", "key":"keys\\upload-key", "remote_dir":"stage",
#      "chunks":16, "parallel":16}
#   (dest/key default to muselink.json -> upload.dest / upload.key)
# Legacy form: -Specs 'name|size;name|size' -Root <dir> (file found by name).
#
# Design rules (each bought with a real failure, 2026-11-08):
# 1. ASCII only in this file (PS 5.1 reads BOM-less files as ANSI). Non-ASCII
#    paths arrive through the JSON args file, never as literals.
# 2. Never name a local variable like a parameter (names are case-insensitive:
#    $chunks vs [int]$Chunks threw a type-conversion error).
# 3. Never wait on a network child without a wall-clock deadline: on this
#    route ssh/scp can wedge forever AFTER auth. Every child runs through
#    Invoke-Bounded (Start-Process + WaitForExit(timeout) + Kill, stdin from an
#    empty file).
# 4. Remote state is read with ONE ssh call per round (name -> size map); a
#    chunk counts as sent only when the map shows its exact size. Exit codes
#    are hints; sizes are truth. Re-running resumes: correct chunks are skipped.
# 5. Failures are LOUD: status.txt ends with FATAL + line info. status.txt is
#    the heartbeat: START / STAGING_OK / FOUND / SPLIT_OK / CHUNKS_OK / MAP /
#    ROUND / KILLED / ALLDONE | FATAL.
# 6. The work dir is emptied at start and the local chunks are deleted after
#    ALLDONE (they once piled up to 2 GB).
param(
  [string]$ArgsFile = '',
  [string]$Specs = '',
  [string]$Root = '',
  [string]$RemoteDir = '',
  [int]$Chunks = 0,
  [int]$Parallel = 0
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
$work = Get-MLPath 'upload-work'
$status = Join-Path $work 'status.txt'
$emptyIn = Join-Path $work 'empty.in'
function Log($m) { Add-Content -Path $status -Value ("{0} {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 }

# ---- inputs
$cfgDest = [string](Get-MLSetting 'upload' 'dest' '')
$cfgKey = [string](Get-MLSetting 'upload' 'key' '')
$items = @()   # each: @{ path; size; remote }
$a = $null
if ($ArgsFile -ne '') { $a = (Read-MLUtf8 $ArgsFile) | ConvertFrom-Json }
$dest = if ($a -and $a.dest) { [string]$a.dest } else { $cfgDest }
$keyRel = if ($a -and $a.key) { [string]$a.key } else { $cfgKey }
$key = if ([IO.Path]::IsPathRooted($keyRel)) { $keyRel } else { Get-MLPath $keyRel }
if ($RemoteDir -eq '') { $RemoteDir = if ($a -and $a.remote_dir) { [string]$a.remote_dir } else { [string](Get-MLSetting 'upload' 'remote_dir' 'stage') } }
if ($Chunks -le 0) { $Chunks = if ($a -and $a.chunks) { [int]$a.chunks } else { 16 } }
if ($Parallel -le 0) { $Parallel = if ($a -and $a.parallel) { [int]$a.parallel } else { 16 } }
# Start-Process joins -ArgumentList with spaces: quote anything path-like.
$sshOpts = @('-i', ('"' + $key + '"'), '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=6', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20')

function Invoke-Bounded($exe, $argList, $timeoutSec, $tag) {
  $outF = Join-Path $work ($tag + '.out')
  $errF = Join-Path $work ($tag + '.err')
  $p = Start-Process -FilePath $exe -ArgumentList $argList -PassThru -WindowStyle Hidden -RedirectStandardInput $emptyIn -RedirectStandardOutput $outF -RedirectStandardError $errF
  if (-not $p.WaitForExit($timeoutSec * 1000)) {
    try { $p.Kill() } catch {}
    return @{ timedOut = $true; out = '' }
  }
  $txt = ''
  if (Test-Path $outF) { $txt = (Get-Content $outF -Raw -ErrorAction SilentlyContinue) }
  if ($null -eq $txt) { $txt = '' }
  return @{ timedOut = $false; out = $txt }
}
function Remote-Map {
  $cmd = "cd ~/$RemoteDir 2>/dev/null && { stat -c '%n %s' .; stat -c '%n %s' *.part* 2>/dev/null; true; } || echo NODIR"
  $r = Invoke-Bounded 'ssh.exe' ($sshOpts + @($dest, ('"' + $cmd + '"'))) 45 'map'
  $map = @{}
  if ($r.timedOut) { Log 'MAP_TIMEOUT'; return @{ map = $map; nodir = $false; timeout = $true } }
  $nodir = $false
  foreach ($line in ($r.out -split "`n")) {
    $t = $line.Trim()
    if ($t -eq 'NODIR') { $nodir = $true }
    elseif ($t -match '^(.+)\s+(\d+)$') { $map[$matches[1]] = [int64]$matches[2] }
  }
  return @{ map = $map; nodir = $nodir; timeout = $false }
}

try {
  New-Item -ItemType Directory -Force $work | Out-Null
  Get-ChildItem -LiteralPath $work -File -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  New-Item -ItemType File -Force $emptyIn | Out-Null
  Set-Content -Path $status -Value ("{0} START remote={1}" -f (Get-Date -Format 'HH:mm:ss'), $RemoteDir) -Encoding UTF8
  if ($dest -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') { throw ('bad or missing dest: ' + $dest) }
  if ($RemoteDir -notmatch '^[A-Za-z0-9._/-]+$') { throw ('bad remote_dir: ' + $RemoteDir) }
  if (-not (Test-Path -LiteralPath $key)) { throw ('upload key not found: ' + $key) }

  if ($a) {
    foreach ($f in @($a.files)) {
      $p = [string]$f.path
      $remote = if ($f.remote_name) { [string]$f.remote_name } else { [IO.Path]::GetFileName($p) }
      if ($remote -notmatch '^[A-Za-z0-9._-]+$') { throw ('bad remote_name: ' + $remote) }
      if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw ('not found: ' + $p) }
      $len = (Get-Item -LiteralPath $p).Length
      if ($f.size -and [int64]$f.size -ne $len) { throw ('size mismatch: ' + $remote + ' actual ' + $len) }
      $items += @{ path = $p; size = $len; remote = $remote }
      Log ("FOUND " + $remote + " " + $len)
    }
  } else {
    foreach ($spec in $Specs.Split(';')) {
      $kv = $spec.Split('|')
      $found = Get-ChildItem $Root -Recurse -Filter $kv[0] -File | Select-Object -First 1
      if ($null -eq $found) { throw ('not found: ' + $kv[0]) }
      if ($found.Length -ne [int64]$kv[1]) { throw ('size mismatch: ' + $kv[0] + ' actual ' + $found.Length) }
      $items += @{ path = $found.FullName; size = $found.Length; remote = $found.Name }
      Log ("FOUND " + $kv[0] + " " + $found.Length)
    }
  }
  if ($items.Count -eq 0) { throw 'no files to upload' }

  # Process exit codes are unreliable under this task host (observed blank
  # on success), so nothing gates on them: staging is proven by the map.
  $null = Invoke-Bounded 'ssh.exe' ($sshOpts + @($dest, ('"mkdir -p ~/' + $RemoteDir + '"'))) 45 'mkdir'
  $probe = Remote-Map
  if ($probe.timeout) { throw 'remote unreachable at staging probe' }
  if ($probe.nodir) { throw 'remote dir missing after mkdir' }
  Log 'STAGING_OK'

  $chunkFiles = @()
  foreach ($item in $items) {
    $fs = [IO.File]::OpenRead($item.path)
    $len = $fs.Length
    $size = [Math]::Ceiling($len / $Chunks)
    $buf = New-Object byte[] (1024 * 1024)
    for ($i = 0; $i -lt $Chunks; $i++) {
      if ($fs.Position -ge $len) { break }
      $cpath = Join-Path $work ("{0}.part{1:D2}" -f $item.remote, $i)
      $out = [IO.File]::Create($cpath)
      $left = [Math]::Min($size, $len - $fs.Position)
      while ($left -gt 0) {
        $n = $fs.Read($buf, 0, [Math]::Min($buf.Length, $left))
        if ($n -le 0) { break }
        $out.Write($buf, 0, $n)
        $left -= $n
      }
      $out.Close()
      $chunkFiles += $cpath
    }
    $fs.Close()
    Log ("SPLIT_OK " + $item.remote + " " + $len)
  }
  if ($chunkFiles.Count -eq 0) { throw 'no chunks produced' }
  foreach ($c in $chunkFiles) { if ((Get-Item $c).Length -le 0) { throw ('empty chunk: ' + $c) } }
  Log ("CHUNKS_OK " + $chunkFiles.Count)

  $pending = @($chunkFiles)
  for ($round = 1; $round -le 4 -and $pending.Count -gt 0; $round++) {
    $map = (Remote-Map).map
    Log ("MAP round " + $round + " remote=" + $map.Count)
    $todo = @()
    foreach ($c in $pending) {
      $nm = [IO.Path]::GetFileName($c)
      if ($map.ContainsKey($nm) -and $map[$nm] -eq (Get-Item $c).Length) { Log ("SKIP " + $nm) }
      else { $todo += $c }
    }
    if ($todo.Count -eq 0) { $pending = @(); break }
    Log ("ROUND " + $round + " sending " + $todo.Count)
    for ($b = 0; $b -lt $todo.Count; $b += $Parallel) {
      $batch = $todo[$b..([Math]::Min($b + $Parallel, $todo.Count) - 1)]
      $procs = @()
      $idx = 0
      foreach ($c in $batch) {
        $rel = $RemoteDir + '/' + [IO.Path]::GetFileName($c)
        $outF = Join-Path $work ('scp' + $round + '-' + $b + '-' + $idx + '.out')
        $errF = Join-Path $work ('scp' + $round + '-' + $b + '-' + $idx + '.err')
        $p = Start-Process -FilePath 'scp.exe' -ArgumentList ($sshOpts + @(('"' + $c + '"'), ($dest + ':' + $rel))) -PassThru -WindowStyle Hidden -RedirectStandardInput $emptyIn -RedirectStandardOutput $outF -RedirectStandardError $errF
        $procs += $p
        $idx++
      }
      $deadline = (Get-Date).AddSeconds(300)
      $killed = 0
      foreach ($p in $procs) {
        $wait = [int]([Math]::Max(0, ($deadline - (Get-Date)).TotalMilliseconds))
        if (-not $p.WaitForExit($wait)) { try { $p.Kill(); $killed++ } catch {} }
      }
      if ($killed -gt 0) { Log ("KILLED " + $killed) }
      Log ("BATCH_DONE " + $batch.Count)
    }
    $map2 = (Remote-Map).map
    $pending = @()
    foreach ($c in $todo) {
      $nm = [IO.Path]::GetFileName($c)
      if (-not ($map2.ContainsKey($nm) -and $map2[$nm] -eq (Get-Item $c).Length)) { $pending += $c }
    }
    Log ("ROUND " + $round + " remaining " + $pending.Count)
  }
  if ($pending.Count -gt 0) { throw ('failed chunks: ' + $pending.Count) }
  foreach ($c in $chunkFiles) { Remove-Item -LiteralPath $c -Force -ErrorAction SilentlyContinue }
  Log 'ALLDONE'
} catch {
  try { Log ('FATAL ' + $_.Exception.Message + ' @line ' + $_.InvocationInfo.ScriptLineNumber + ': ' + $_.InvocationInfo.Line.Trim()) } catch {}
  exit 1
}
