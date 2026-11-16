# Watch-LocalTask.ps1 - terminal-state trigger for local tasks
# (2026-11-03, design by the seminar advisor, fused/deployed by Muse;
# hardened the same night after the advisor's code review):
#   - -ReqId is MANDATORY and the completer enforces it, so a stale
#     watcher can never deliver into a newer wait's mailbox.
#   - Every wait loop checks Test-StillMyWait: once popup-pending.txt
#     is gone (claimed / expired / superseded) the watcher exits on its
#     own -- mode B can no longer orphan-loop forever on a file that
#     will never appear; its lifetime is bound to the wait contract.
#   - Mode C runs the command in a CHILD powershell.exe process, so a
#     cmdlet-ending command still yields a real integer ExitCode, an
#     'exit' inside the command cannot kill the watcher, and
#     Invoke-Expression is gone.
#   - Any fatal error is written to watch-error.txt instead of dying
#     silently.
# Binds to a first-party terminal signal instead of polling external
# state: a process handle exit (mode A), the output file's write-lock
# release + size stability (mode B), or a wrapped command's exit
# (mode C). On completion it calls Complete-MuseWait.ps1, which drops
# the result into the shared mailbox (popup-done.txt), waking Muse
# through the usual main path / hook fallback.
# Fusion note: -CommandFile added by Muse (mode C reads the command
# from a UTF-8 file) so callers never fight WMI/shell quoting.
param(
    [int]$TargetPid = 0,
    [string]$OutputFile = '',
    [int]$QuietSeconds = 5,
    [string]$Command = '',
    [string]$CommandFile = '',
    [string]$TaskName = 'local-task',
    [Parameter(Mandatory=$true)][string]$ReqId
)

$ErrorActionPreference = 'Continue'
$dir = $PSScriptRoot
$completer  = Join-Path $dir 'Complete-MuseWait.ps1'
$pendingFile = Join-Path $dir 'popup-pending.txt'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# True while the armed wait still belongs to this watcher. If the
# pending file vanished or now names a different ReqId, the wait is
# over (claimed, expired or superseded) and the watcher must exit.
# A transient read failure counts as "still mine" (do not suicide on
# a sharing hiccup).
function Test-StillMyWait {
    if (-not (Test-Path -LiteralPath $pendingFile)) { return $false }
    try {
        $cur = ([System.IO.File]::ReadAllText($pendingFile, [System.Text.Encoding]::UTF8)).Trim()
        return ($cur -eq $ReqId)
    } catch {
        return $true
    }
}

function Test-FileLocked([string]$path) {
    try {
        $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $fs.Close()
        $fs.Dispose()
        return $false
    } catch {
        return $true
    }
}

try {
    # ---- Mode A: kernel handle wait on a numeric PID (no self-match)
    if ($TargetPid -gt 0) {
        if ($TargetPid -eq $PID) { exit 1 }
        try {
            $proc = [System.Diagnostics.Process]::GetProcessById($TargetPid)
            $procName = $proc.ProcessName
            while (-not $proc.WaitForExit(2000)) {
                if (-not (Test-StillMyWait)) { exit 0 }
            }
            $exitCode = $proc.ExitCode
            $msg = "Task [$TaskName] (PID=$TargetPid, process=$procName) exited, ExitCode=$exitCode"
        } catch {
            $msg = "Task [$TaskName] (PID=$TargetPid) no longer existed when the watcher bound to it (already finished)."
        }
        & $completer -Kind 'process_done' -Result $msg -ReqId $ReqId
        exit 0
    }

    # ---- Mode B: output file appears, write lock released, size stable
    if ($OutputFile -ne '') {
        while (-not (Test-Path -LiteralPath $OutputFile)) {
            if (-not (Test-StillMyWait)) { exit 0 }
            Start-Sleep -Seconds 2
        }
        $stableCount = 0
        $lastSize = -1L
        while ($true) {
            if (-not (Test-StillMyWait)) { exit 0 }
            Start-Sleep -Seconds 1
            if (-not (Test-Path -LiteralPath $OutputFile)) {
                $stableCount = 0; continue
            }
            $item = Get-Item -LiteralPath $OutputFile -ErrorAction SilentlyContinue
            if (-not $item -or $item.Length -le 0) {
                $stableCount = 0; continue
            }
            if (Test-FileLocked $OutputFile) {
                $stableCount = 0
                $lastSize = $item.Length
                continue
            }
            if ($item.Length -eq $lastSize) {
                $stableCount++
                if ($stableCount -ge $QuietSeconds) { break }
            } else {
                $lastSize = $item.Length
                $stableCount = 0
            }
        }
        $finalMB = [Math]::Round($lastSize / 1MB, 2)
        $msg = "File export complete: $OutputFile (size: ${finalMB} MB, write lock released)"
        & $completer -Kind 'file_done' -Result $msg -ReqId $ReqId
        exit 0
    }

    # ---- Mode C: run the command in a child powershell.exe so the
    # exit code is always a real integer and the wrapped command can
    # never terminate this watcher.
    if ($CommandFile -ne '' -or $Command -ne '') {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        if ($CommandFile -ne '') {
            $output = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $CommandFile 2>&1 | Out-String
        } else {
            $bytes = [System.Text.Encoding]::Unicode.GetBytes($Command)
            $b64 = [Convert]::ToBase64String($bytes)
            $output = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $b64 2>&1 | Out-String
        }
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        $sw.Stop()
        $sec = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
        if ($output.Length -gt 3000) {
            $output = $output.Substring($output.Length - 3000)
        }
        $msg = "Command [$TaskName] finished (elapsed ${sec}s, ExitCode=$code)`n---output tail---`n$output"
        & $completer -Kind 'cmd_done' -Result $msg -ReqId $ReqId
        exit 0
    }
} catch {
    [System.IO.File]::WriteAllText((Join-Path $dir 'watch-error.txt'),
        ("{0} req={1} error: {2}" -f [DateTime]::Now.ToString('s'), $ReqId, $_.ToString()),
        $utf8NoBom)
    exit 1
}
