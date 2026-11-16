#requires -Version 5.1
# ============================================================================
# Muse popup (Windows Forms, PowerShell only)
#
# Input  (UTF-8, in this script's own directory -- the tool dir):
#   popup-text.txt     body text
#   popup-mode.txt     info | choice | input
#   popup-options.txt  one option per line, max 4 (choice mode only)
#   popup-reqid.txt    ReqId of the request this card answers (written by
#                      popup-stage.ps1); echoed into popup-done.txt so every
#                      consumer can check the answer belongs to its request
# Output (UTF-8 without BOM, same folder, written in this order):
#   popup-result.txt   chosen option text / typed text (empty when dismissed)
#   popup-kind.txt     info | choice | typed | input
#   popup-done.txt     line 1 ISO-8601 timestamp, line 2 "REQ:<ReqId>" (last)
#
# Focus etiquette (2026-11-12): the card is shown WITHOUT taking keyboard
# focus (top-most, chime and re-ring unchanged), so keys the user is typing
# elsewhere never land in it and an Enter can never confirm it by accident.
# The first click on the card activates it and focuses the answer box.
# muselink.json -> popup.activate_on_show = true restores the old behaviour;
# a key guard (popup.key_guard_ms, default 800) then drops keys typed in the
# first moments after the card appears.
# If the window is closed with the X button, result is empty and kind is the
# mode's base kind (info / choice / input), and popup-done.txt is still written.
#   popup-draft.txt    unsent typed draft scoped to the exact popup text:
#                      restored on reopen, deleted on any successful submit,
#                      kept when the window is dismissed with X.
# Fixed UI strings come from i18n/*.json. This script stays ASCII so it also
# parses correctly in Windows PowerShell 5.1 without a BOM.
#
# Identity protocol v3 (implemented 2026-11-12) (delay cards, docs/protocol.en.md
# section 4): optional input popup-expires-in.txt = whole seconds (1..3600).
# When present, the card shows a visible countdown and closes itself that many
# seconds after it is SHOWN (relative, so PC/VM clock skew does not matter),
# writing result empty, kind "expired" and popup-done.txt as usual. A click
# that lands after the deadline is ignored. Without the file nothing changes.
# ============================================================================

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'i18n/i18n.ps1')
Initialize-MLI18n (Join-Path $PSScriptRoot 'i18n')

# ---- WinForms needs an STA thread -------------------------------------------
if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne [System.Threading.ApartmentState]::STA) {
    $exe = (Get-Process -Id $PID).Path
    & $exe -STA -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath
    exit $LASTEXITCODE
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ---- Tiny native helper (DPI, drag, DWM, foreground) -------------------------
if (-not ('MusePopup.Native' -as [type])) {
    try {
        Add-Type -Namespace MusePopup -Name Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
[DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
[DllImport("user32.dll")] public static extern bool ReleaseCapture();
[DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
[DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
[DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
[DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int val, int size);
[DllImport("dwmapi.dll")] public static extern int DwmExtendFrameIntoClientArea(IntPtr hwnd, int[] margins);
'@
    } catch {}
}

# ---- System DPI awareness (must happen before any window exists) ------------
try { [void][MusePopup.Native]::SetProcessDpiAwarenessContext([IntPtr](-2)) }
catch { try { [void][MusePopup.Native]::SetProcessDPIAware() } catch {} }

[System.Windows.Forms.Application]::EnableVisualStyles()
try { [System.Windows.Forms.Application]::SetCompatibleTextRenderingDefault($false) } catch {}

$g0 = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
$script:Scale = [double]$g0.DpiX / 96.0
$g0.Dispose()
if ($script:Scale -lt 1) { $script:Scale = 1.0 }

# ---- Strings (escaped) --------------------------------------------------------
$script:T = @{
    Ok = T 'popup.ok'
    Other = T 'popup.other'
    Back = T 'popup.back'
    Send = T 'popup.send'
    NewLine = T 'popup.newline'
}
$script:NoStart = [regex]::Unescape('\uFF0C\u3002\u3001\uFF1B\uFF1A\uFF01\uFF1F\uFF09\u3011\u300B\u300D\u300F\u201D\u2019\u2026') + ',.;:!?)]}'
$script:NoEnd   = [regex]::Unescape('\uFF08\u3010\u300A\u300C\u300E\u201C\u2018') + '([{'
$script:TokRx   = '[A-Za-z0-9_\-./:@#%&+=~''\u2019]+|\s+|.'
$script:Ellipsis = [string][char]0x2026

# ---- Paths and file helpers ---------------------------------------------------
$script:Dir  = $PSScriptRoot
if (-not $script:Dir) { $script:Dir = Split-Path -Parent $PSCommandPath }
$script:Utf8 = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $script:Dir 'muselink-common.ps1')
$script:ActivateOnShow = [bool](Get-MLSetting 'popup' 'activate_on_show' $false)
$script:PetName = [string](Get-MLSetting 'pet' 'display_name' 'Muse Pet')
$script:KeyGuardMs = [int](Get-MLSetting 'popup' 'key_guard_ms' 800)
$script:ShownAt = $null
$script:EverActivated = $false

function Read-Utf8([string]$name) {
    $p = Join-Path $script:Dir $name
    if (Test-Path -LiteralPath $p) { return [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8) }
    return ''
}
function Write-Utf8([string]$name, [string]$content) {
    [System.IO.File]::WriteAllText((Join-Path $script:Dir $name), $content, $script:Utf8)
}

# popup-stage owns stale-output cleanup. Clearing here would erase an answer
# that arrived from the pet between staging and this window's startup.

# ---- Optional attention chime -------------------------------------------------
# If popup-sound.txt exists in the tool dir, play a sound as the popup
# appears, for moments when the user may not be watching the screen. Content
# 'beep' = three-note rising chime; any other content is a .wav file name,
# looked up first in the tool dir, then in C:\Windows\Media. Standing
# default since 2026-11-01: 'muse-chime.wav' in the tool dir - the notification
# sound the user personally picked for Muse (our shared signal). No file =
# silent.
function Play-PopupSound {
  # Playback runs in a background runspace. A synchronous PlaySync() here (the
  # Shown handler's first line) froze the UI thread before the first WM_PAINT,
  # and DWM presented the unpainted surface as translucent "liquid glass" for
  # the length of the sound (diagnosed + experiment-verified 2026-11-03).
  # Never block the Shown handler again.
  try {
    $sndPath = Join-Path $script:Dir 'popup-sound.txt'
    if (Test-Path -LiteralPath $sndPath) {
        $snd = ([System.IO.File]::ReadAllText($sndPath)).Trim()
        if ($snd) {
            $wav = $null
            if ($snd -ne 'beep') {
                $wav = Join-Path $script:Dir $snd
                if (-not (Test-Path -LiteralPath $wav)) { $wav = Join-Path 'C:\Windows\Media' $snd }
                if (-not (Test-Path -LiteralPath $wav)) { $wav = $null }
            }
            if (($snd -eq 'beep') -or $wav) {
                $rs = [runspacefactory]::CreateRunspace()
                $rs.Open()
                $ps = [powershell]::Create()
                $ps.Runspace = $rs
                [void]$ps.AddScript({
                    param($mode, $path)
                    if ($mode -eq 'beep') {
                        [console]::Beep(1046, 150); [console]::Beep(1318, 150); [console]::Beep(1568, 280)
                    } elseif ($path) {
                        (New-Object System.Media.SoundPlayer $path).PlaySync()
                    }
                }).AddArgument($snd).AddArgument($wav)
                [void]$ps.BeginInvoke()
                $script:SoundPs = $ps
                $script:SoundRs = $rs
            }
        }
    }
  } catch {}
}

# ---- Attention re-ring (user rule, 2026-11-03) --------------------------------
# If the user has not interacted with the card at all, the chime repeats
# every minute, at most 3 rings in total (the appearance ring counts as
# the first). Any interaction -- pressing a button, clicking or typing
# into the input box, pressing a key, grabbing the card -- stops the
# re-ring for good. This lives in the popup itself so it does not depend
# on Muse remembering to summon again. Test knob: an integer in
# popup-ring-interval-sec.txt (5..600) overrides the 60 s interval.
$script:RingCount   = 0
$script:RingMax     = 3
$script:UserTouched = $false
$script:RingTimer   = $null

function Stop-RingTimer {
    $script:UserTouched = $true
    if ($script:RingTimer) {
        try { $script:RingTimer.Stop() } catch {}
    }
}

function Note-UserTouch {
    # Only counts once the ring timer exists, so the programmatic focus
    # grab during Shown is never mistaken for the user's touch.
    if ($script:RingTimer) { Stop-RingTimer }
}

function Add-TouchWatch([System.Windows.Forms.Control]$ctrl) {
    # Blanket coverage: a click ANYWHERE on the card counts as a touch.
    # WinForms mouse events do not bubble, and a click into the already
    # focused input box fires neither GotFocus nor TextChanged, so every
    # control gets its own MouseDown watch (wired at the end of
    # Build-Card, which rebuilds the controls on every view switch).
    try { $ctrl.Add_MouseDown({ Note-UserTouch }) } catch {}
    foreach ($ch in @($ctrl.Controls)) { Add-TouchWatch $ch }
}

function Start-RingTimer {
    try {
        $sndPath = Join-Path $script:Dir 'popup-sound.txt'
        if (-not (Test-Path -LiteralPath $sndPath)) { return }
        if (-not (([System.IO.File]::ReadAllText($sndPath)).Trim())) { return }
        $intervalSec = 60
        $intPath = Join-Path $script:Dir 'popup-ring-interval-sec.txt'
        if (Test-Path -LiteralPath $intPath) {
            $v = 0
            if ([int]::TryParse((([System.IO.File]::ReadAllText($intPath)).Trim()), [ref]$v) -and $v -ge 5 -and $v -le 600) { $intervalSec = $v }
        }
        $script:RingTimer = New-Object System.Windows.Forms.Timer
        $script:RingTimer.Interval = $intervalSec * 1000
        $script:RingTimer.Add_Tick({
            if ($script:UserTouched -or $script:RingCount -ge $script:RingMax) {
                try { $script:RingTimer.Stop() } catch {}
                return
            }
            $script:RingCount++
            Play-PopupSound
        })
        $script:RingTimer.Start()
    } catch {}
}

$script:Text    = (Read-Utf8 'popup-text.txt').Trim()
$script:ReqId   = (Read-Utf8 'popup-reqid.txt').Trim()
$script:Mode    = (Read-Utf8 'popup-mode.txt').Trim().ToLowerInvariant()
$script:ExpiresIn = 0
$script:ExpiryAt = $null
$script:ExpiryTimer = $null
$script:HeaderTitle = 'Muse'
$script:HeaderPanel = $null
$expRaw = (Read-Utf8 'popup-expires-in.txt').Trim()
if ($expRaw -match '^[0-9]{1,4}$') {
    $expN = [int]$expRaw
    if ($expN -ge 1 -and $expN -le 3600) { $script:ExpiresIn = $expN }
}
$script:Options = @((Read-Utf8 'popup-options.txt') -split '\r?\n' |
    Where-Object { $_.Trim().Length -gt 0 } | ForEach-Object { $_.Trim() } | Select-Object -First 4)

if ($script:Mode -notin @('info', 'choice', 'input')) {
    if ($script:Options.Count -gt 0) { $script:Mode = 'choice' } else { $script:Mode = 'info' }
}
if ($script:Mode -eq 'choice' -and $script:Options.Count -eq 0) { $script:Mode = 'input' }
$script:FromChoice = ($script:Mode -eq 'choice')
$script:BaseKind   = $script:Mode      # info | choice | input
$script:Saved      = $false
$script:ExternalKind = ''
$script:LinkTimer = $null
$script:CloseTimer = $null
$script:StampLayer = $null
$script:AnswerFeedbackStarted = $false
$script:PopupClosing = $false
$script:ConfirmPlayer = $null
$script:Draft      = ''
# Draft persistence (2026-11-03): popup-draft.txt = popup text, a --DRAFT--
# separator line, then the unsent draft. Restored only when the popup text
# matches exactly; deleted on submit (Finish-Popup); kept on X-dismiss.
try {
    $savedDraft = Read-Utf8 'popup-draft.txt'
    if ($savedDraft) {
        $dsep = "`n--DRAFT--`n"
        $didx = $savedDraft.IndexOf($dsep)
        if ($didx -ge 0 -and $savedDraft.Substring(0, $didx) -eq $script:Text) {
            $script:Draft = $savedDraft.Substring($didx + $dsep.Length)
        }
    }
} catch {}
$script:TextBox    = $null
$script:SendBtn    = $null
$script:UseRegion  = $true
$script:CloseRect  = [System.Drawing.Rectangle]::Empty
$script:PendingState = ''

# ---- Small utilities ----------------------------------------------------------
function Px([double]$v) { return [int][Math]::Round($v * $script:Scale) }
function Clr([string]$hex) { return [System.Drawing.ColorTranslator]::FromHtml($hex) }
function Mix-Clr($a, $b, [double]$t) {
    return [System.Drawing.Color]::FromArgb(255,
        [int]($a.R + ($b.R - $a.R) * $t),
        [int]($a.G + ($b.G - $a.G) * $t),
        [int]($a.B + ($b.B - $a.B) * $t))
}
function PtF([double]$x, [double]$y) { return [System.Drawing.PointF]::new([single]$x, [single]$y) }

function Set-DoubleBuffered($c) {
    $pi = $c.GetType().GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'Instance,NonPublic')
    if ($pi) { $pi.SetValue($c, $true, $null) }
}

function New-RoundPath([double]$x, [double]$y, [double]$w, [double]$h, [double]$r) {
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $r = [Math]::Max(0.5, [Math]::Min($r, [Math]::Min($w, $h) / 2))
    $d  = [single]($r * 2)
    $fx = [single]$x; $fy = [single]$y; $fw = [single]$w; $fh = [single]$h
    $p.AddArc($fx, $fy, $d, $d, [single]180, [single]90)
    $p.AddArc([single]($fx + $fw - $d), $fy, $d, $d, [single]270, [single]90)
    $p.AddArc([single]($fx + $fw - $d), [single]($fy + $fh - $d), $d, $d, [single]0, [single]90)
    $p.AddArc($fx, [single]($fy + $fh - $d), $d, $d, [single]90, [single]90)
    $p.CloseFigure()
    return $p
}

# ---- Colours, fonts, text flags -----------------------------------------------
$script:White = Clr '#FFFFFF'
$script:Ink   = Clr '#1D1D1F'
$script:Sub   = Clr '#6E6E73'
$script:Blue  = Clr '#0071E3'
$script:RingC = Clr '#D1E5FA'
$script:Hair  = Clr '#C7C7CC'
$script:EdgeC = Clr '#D5D5DA'

$script:StPrimary = @{ Bg = (Clr '#0071E3'); BgHover = (Clr '#0066CC'); BgDown = (Clr '#005BB8'); BgOff = (Clr '#A6CDF5'); Fg = (Clr '#FFFFFF') }
$script:StGray    = @{ Bg = (Clr '#F2F2F7'); BgHover = (Clr '#E5E5EA'); BgDown = (Clr '#D8D8DD'); BgOff = (Clr '#F2F2F7'); Fg = (Clr '#1D1D1F') }
function New-Pastel([string]$bgHex, [string]$fgHex) {
    $bg = Clr $bgHex; $fg = Clr $fgHex
    return @{ Bg = $bg; BgHover = (Mix-Clr $bg $fg 0.07); BgDown = (Mix-Clr $bg $fg 0.14); BgOff = $bg; Fg = $fg }
}
$script:Pastels = @(
    (New-Pastel '#E5F0FF' '#0050B3'),   # blue
    (New-Pastel '#E3F5EA' '#17653A'),   # green
    (New-Pastel '#FFF0E0' '#9A4A00'),   # orange
    (New-Pastel '#EFEAFB' '#5333A8')    # purple
)

$px = [System.Drawing.GraphicsUnit]::Pixel
$reg = [System.Drawing.FontStyle]::Regular
$boldStyle = [System.Drawing.FontStyle]::Bold
$script:FontBody     = New-Object System.Drawing.Font($script:MLLocale.font, [single](15 * $script:Scale), $reg, $px)
$script:FontBodyBold = New-Object System.Drawing.Font($script:MLLocale.font, [single](15 * $script:Scale), $boldStyle, $px)
$script:FontTitle    = New-Object System.Drawing.Font($script:MLLocale.font,  [single](13 * $script:Scale), $reg, $px)
$script:FontHint  = New-Object System.Drawing.Font($script:MLLocale.font, [single](12 * $script:Scale), $reg, $px)
$script:FontChip  = New-Object System.Drawing.Font($script:MLLocale.font,  [single](11 * $script:Scale), $reg, $px)

$script:TFLeft    = [System.Windows.Forms.TextFormatFlags]'Left, VerticalCenter, NoPadding, NoPrefix, SingleLine, NoClipping'
$script:TFCenter  = [System.Windows.Forms.TextFormatFlags]'HorizontalCenter, VerticalCenter, NoPadding, NoPrefix, SingleLine, NoClipping'
$script:TFMeasure = [System.Windows.Forms.TextFormatFlags]'NoPadding, NoPrefix, SingleLine'

# ---- Icon (muse-icon.png) ------------------------------------------------------
$script:IconImg = $null
$script:IconStream = $null
$iconCandidates = @((Join-Path $script:Dir 'muse-icon.png'))
if ($PSScriptRoot) { $iconCandidates += (Join-Path $PSScriptRoot 'muse-icon.png') }
foreach ($cand in $iconCandidates) {
    if ($cand -and (Test-Path -LiteralPath $cand)) {
        try {
            $bytes = [System.IO.File]::ReadAllBytes($cand)
            $script:IconStream = New-Object System.IO.MemoryStream(, $bytes)
            $script:IconImg = [System.Drawing.Image]::FromStream($script:IconStream)
            break
        } catch { $script:IconImg = $null }
    }
}

# ---- Text measuring and wrapping (CJK aware, with line-break rules) ------------
function Get-TextWidth([string]$text, $font) {
    if ([string]::IsNullOrEmpty($text)) { return 0 }
    return [System.Windows.Forms.TextRenderer]::MeasureText($text, $font, [System.Drawing.Size]::new(100000, 100000), $script:TFMeasure).Width
}

function Get-WrapLines([string]$text, $font, [int]$maxW) {
    $lines = New-Object 'System.Collections.Generic.List[string]'
    foreach ($para in ($text -split '\r?\n')) {
        if ($para.Length -eq 0) { $lines.Add(''); continue }
        $tokens = New-Object 'System.Collections.Generic.List[string]'
        foreach ($m in [regex]::Matches($para, $script:TokRx)) {
            $v = $m.Value
            if ($v.Length -gt 1 -and $v.Trim().Length -gt 0 -and (Get-TextWidth $v $font) -gt $maxW) {
                foreach ($ch in $v.ToCharArray()) { $tokens.Add([string]$ch) }
            } else { $tokens.Add($v) }
        }
        $cur = New-Object 'System.Collections.Generic.List[string]'
        foreach ($t in $tokens) {
            if ($t.Trim().Length -eq 0) { if ($cur.Count -gt 0) { $cur.Add($t) }; continue }
            if ($cur.Count -gt 0 -and (Get-TextWidth (($cur -join '') + $t) $font) -gt $maxW) {
                while ($cur.Count -gt 0 -and $cur[$cur.Count - 1].Trim().Length -eq 0) { $cur.RemoveAt($cur.Count - 1) }
                $carry = New-Object 'System.Collections.Generic.List[string]'
                if ($t.Length -eq 1 -and $script:NoStart.Contains($t) -and $cur.Count -gt 1) {
                    $carry.Insert(0, $cur[$cur.Count - 1]); $cur.RemoveAt($cur.Count - 1)
                }
                while ($cur.Count -gt 1 -and $cur[$cur.Count - 1].Length -eq 1 -and $script:NoEnd.Contains($cur[$cur.Count - 1])) {
                    $carry.Insert(0, $cur[$cur.Count - 1]); $cur.RemoveAt($cur.Count - 1)
                }
                $lines.Add(($cur -join ''))
                $cur = $carry
            }
            $cur.Add($t)
        }
        $lines.Add((($cur -join '').TrimEnd()))
    }
    return , $lines.ToArray()
}

function Format-PopupMessage([string]$raw, $fontWrap, [int]$maxW) {
    $outLines = New-Object 'System.Collections.Generic.List[string]'
    $outMasks = New-Object 'System.Collections.Generic.List[bool[]]'
    $paras = @($raw -split '\r?\n')
    $parsed = New-Object 'System.Collections.Generic.List[hashtable]'
    $anyBold = $false
    foreach ($p in $paras) {
        $sb = New-Object System.Text.StringBuilder
        $flags = New-Object 'System.Collections.Generic.List[bool]'
        $ms = [regex]::Matches($p, '\*\*([^\r\n*](?:[^\r\n*]|\*(?!\*))*?)\*\*')
        $pos = 0
        $hasExplicit = ($ms.Count -gt 0)
        if ($hasExplicit) {
            foreach ($m in $ms) {
                if ($m.Index -gt $pos) {
                    $seg = $p.Substring($pos, $m.Index - $pos)
                    [void]$sb.Append($seg)
                    for ($i = 0; $i -lt $seg.Length; $i++) { $flags.Add($false) }
                }
                $inner = $m.Groups[1].Value
                [void]$sb.Append($inner)
                for ($i = 0; $i -lt $inner.Length; $i++) { $flags.Add($true) }
                $pos = $m.Index + $m.Length
            }
            if ($pos -lt $p.Length) {
                $seg = $p.Substring($pos)
                [void]$sb.Append($seg)
                for ($i = 0; $i -lt $seg.Length; $i++) { $flags.Add($false) }
            }
            $anyBold = $true
        } else {
            [void]$sb.Append($p)
            for ($i = 0; $i -lt $p.Length; $i++) { $flags.Add($false) }
        }
        $clean = $sb.ToString()
        $mask = $flags.ToArray()
        if (-not $hasExplicit -and $clean.Length -gt 0) {
            $mLead = [regex]::Match($clean, '^([^\r\n:\uFF1A]{1,36}[:\uFF1A])')
            if ($mLead.Success) {
                $prefix = $mLead.Groups[1].Value
                if ($prefix -notmatch '^(?:https?|ftp|[A-Za-z]):$') {
                    for ($i = 0; $i -lt $mLead.Length; $i++) { $mask[$i] = $true; $anyBold = $true }
                }
            }
            $qRx = '(\u300C[^\r\n\u300D]{1,48}\u300D|\u300E[^\r\n\u300F]{1,48}\u300F|\u3010[^\r\n\u3011]{1,48}\u3011|\u201C[^\r\n\u201D]{1,48}\u201D|"[^"\r\n]{1,48}")'
            foreach ($qm in [regex]::Matches($clean, $qRx)) {
                for ($i = $qm.Index; $i -lt ($qm.Index + $qm.Length); $i++) { $mask[$i] = $true; $anyBold = $true }
            }
        }
        $parsed.Add(@{ Text = $clean; Mask = $mask })
    }
    if (-not $anyBold) {
        for ($pi = 0; $pi -lt $parsed.Count; $pi++) {
            $clean = $parsed[$pi].Text
            if ($clean.Trim().Length -gt 0) {
                $mask = $parsed[$pi].Mask
                $end = [Math]::Min($clean.Length, 48)
                $mClause = [regex]::Match($clean.Substring(0, $end), '^(.{1,48}?)(?:[\uFF0C\u3002\uFF1F\uFF01,?!.]|$)')
                $len = if ($mClause.Success -and $mClause.Groups[1].Length -gt 0) { $mClause.Groups[1].Length } else { $end }
                for ($i = 0; $i -lt $len; $i++) { $mask[$i] = $true }
                break
            }
        }
    }
    foreach ($item in $parsed) {
        $para = $item.Text
        $pmask = $item.Mask
        if ($para.Length -eq 0) {
            $outLines.Add('')
            $outMasks.Add([bool[]]@())
            continue
        }
        $tokens = New-Object 'System.Collections.Generic.List[hashtable]'
        foreach ($m in [regex]::Matches($para, $script:TokRx)) {
            $v = $m.Value
            $idx0 = $m.Index
            if ($v.Length -gt 1 -and $v.Trim().Length -gt 0 -and (Get-TextWidth $v $fontWrap) -gt $maxW) {
                for ($ci = 0; $ci -lt $v.Length; $ci++) {
                    $tokens.Add(@{ T = [string]$v[$ci]; M = [bool[]]@($pmask[$idx0 + $ci]) })
                }
            } else {
                $sub = New-Object 'bool[]' $v.Length
                for ($ci = 0; $ci -lt $v.Length; $ci++) { $sub[$ci] = $pmask[$idx0 + $ci] }
                $tokens.Add(@{ T = $v; M = $sub })
            }
        }
        $cur = New-Object 'System.Collections.Generic.List[hashtable]'
        $curText = { param($list) (($list | ForEach-Object { $_.T }) -join '') }
        foreach ($tok in $tokens) {
            if ($tok.T.Trim().Length -eq 0) {
                if ($cur.Count -gt 0) { $cur.Add($tok) }
                continue
            }
            if ($cur.Count -gt 0 -and (Get-TextWidth ((& $curText $cur) + $tok.T) $fontWrap) -gt $maxW) {
                while ($cur.Count -gt 0 -and $cur[$cur.Count - 1].T.Trim().Length -eq 0) { $cur.RemoveAt($cur.Count - 1) }
                $carry = New-Object 'System.Collections.Generic.List[hashtable]'
                if ($tok.T.Length -eq 1 -and $script:NoStart.Contains($tok.T) -and $cur.Count -gt 1) {
                    $carry.Insert(0, $cur[$cur.Count - 1]); $cur.RemoveAt($cur.Count - 1)
                }
                while ($cur.Count -gt 1 -and $cur[$cur.Count - 1].T.Length -eq 1 -and $script:NoEnd.Contains($cur[$cur.Count - 1].T)) {
                    $carry.Insert(0, $cur[$cur.Count - 1]); $cur.RemoveAt($cur.Count - 1)
                }
                $lineTxt = (& $curText $cur)
                $lineM = New-Object 'System.Collections.Generic.List[bool]'
                foreach ($x in $cur) { foreach ($b in $x.M) { $lineM.Add($b) } }
                $outLines.Add($lineTxt)
                $outMasks.Add($lineM.ToArray())
                $cur = $carry
            }
            $cur.Add($tok)
        }
        $tailTxt = (& $curText $cur).TrimEnd()
        $tailM = New-Object 'System.Collections.Generic.List[bool]'
        foreach ($x in $cur) { foreach ($b in $x.M) { if ($tailM.Count -lt $tailTxt.Length) { $tailM.Add($b) } } }
        $outLines.Add($tailTxt)
        $outMasks.Add($tailM.ToArray())
    }
    $finalSb = New-Object System.Text.StringBuilder
    $runs = New-Object 'System.Collections.Generic.List[hashtable]'
    $offset = 0
    for ($li = 0; $li -lt $outLines.Count; $li++) {
        if ($li -gt 0) {
            [void]$finalSb.Append("`n")
            $offset++
        }
        $lt = $outLines[$li]
        $lm = $outMasks[$li]
        [void]$finalSb.Append($lt)
        $runStart = -1
        for ($ci = 0; $ci -lt $lt.Length; $ci++) {
            $isB = ($ci -lt $lm.Length -and $lm[$ci])
            if ($isB -and $runStart -lt 0) { $runStart = $offset + $ci }
            elseif (-not $isB -and $runStart -ge 0) {
                $runs.Add(@{ Start = $runStart; Len = ($offset + $ci - $runStart) })
                $runStart = -1
            }
        }
        if ($runStart -ge 0) { $runs.Add(@{ Start = $runStart; Len = ($offset + $lt.Length - $runStart) }) }
        $offset += $lt.Length
    }
    return @{ Text = $finalSb.ToString(); Lines = $outLines.ToArray(); Runs = $runs.ToArray() }
}

# ---- Native-backed helpers ------------------------------------------------------
function Start-Drag {
    try {
        [void][MusePopup.Native]::ReleaseCapture()
        [void][MusePopup.Native]::SendMessage($script:Form.Handle, 0xA1, [IntPtr]2, [IntPtr]::Zero)
    } catch {}
}

function Set-Foreground {
    try {
        $f = $script:Form
        $fg = [MusePopup.Native]::GetForegroundWindow()
        $fgPid = [uint32]0
        $fgThread = [MusePopup.Native]::GetWindowThreadProcessId($fg, [ref]$fgPid)
        $me = [MusePopup.Native]::GetCurrentThreadId()
        $attached = $false
        if ($fgThread -ne 0 -and $fgThread -ne $me) { $attached = [MusePopup.Native]::AttachThreadInput($me, $fgThread, $true) }
        [void][MusePopup.Native]::SetForegroundWindow($f.Handle)
        $f.Activate()
        if ($attached) { [void][MusePopup.Native]::AttachThreadInput($me, $fgThread, $false) }
    } catch { try { $script:Form.Activate() } catch {} }
}

# ---- Result handling ------------------------------------------------------------
function Save-Result([string]$kind, [string]$result) {
    if ($script:Saved) { return $false }
    $file = Join-Path ([IO.Path]::GetTempPath()) ('popup-result-' + [guid]::NewGuid().ToString('N') + '.txt')
    try {
        [IO.File]::WriteAllText($file, $result, $script:Utf8)
        # In this process (a new powershell.exe costs about half a second before
        # the stamp can start); the same script in a child process if that fails.
        $completer = Join-Path $script:Dir 'Complete-MuseWait.ps1'
        $global:LASTEXITCODE = 0
        try { & $completer -Kind $kind -ResultFile $file -ReqId $script:ReqId | Out-Null }
        catch { & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $completer -Kind $kind -ResultFile $file -ReqId $script:ReqId | Out-Null }
        if ($LASTEXITCODE -eq 3) { Sync-ExternalAnswer; return $false }
        if ($LASTEXITCODE -ne 0) { return $false }
        $done = Get-MLDoneInfo (Join-Path $script:Dir 'popup-done.txt')
        if ($done.ReqId -ne $script:ReqId) { return $false }
        $script:Saved = $true
        if ($kind -ne 'expired') { Play-ConfirmSound }
        return $true
    } catch { return $false }
    finally { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
}

function Play-ConfirmSound {
    try {
        $presence = (Read-Utf8 'pet\pet-presence.json') | ConvertFrom-Json
        $age = ([DateTimeOffset]::Now - [DateTimeOffset]::Parse($presence.at)).TotalSeconds
        if ($presence.visible -eq $true -and $age -ge 0 -and $age -lt 5) { return }
    } catch {}
    try {
        if (-not [bool](Get-MLSetting 'pet' 'sound' $true)) { return }
        $wav = Join-Path $script:Dir 'pet\pet-confirm.wav'
        if (Test-Path -LiteralPath $wav) {
            $script:ConfirmPlayer = New-Object System.Media.SoundPlayer $wav
            $script:ConfirmPlayer.Play()
            $script:ConfirmUntil = [DateTime]::UtcNow.AddMilliseconds(600)
        }
    } catch {}
}

function Sync-ExternalAnswer {
    if ($script:Saved) { return }
    $done = Get-MLDoneInfo (Join-Path $script:Dir 'popup-done.txt')
    if ($done.ReqId -ne $script:ReqId -or $script:ReqId -eq '') { return }
    $script:ExternalKind = (Read-Utf8 'popup-kind.txt').Trim()
    $script:Saved = $true
    foreach ($timer in @($script:RingTimer, $script:ExpiryTimer)) { if ($timer) { $timer.Stop() } }
    if ($script:ExternalKind -eq 'pet_done') {
        Start-AnsweredStamp
        return
    }
    if ($script:ExternalKind -eq 'pet_typed') {
        Start-AnsweredStamp
        return
    }
    $script:Form.Close()
}

# ---- Answered stamp ------------------------------------------------------------
# Runs only after the answer is on disk. The card shows the stamp on itself: a
# light veil over its own content (still readable), then a solid green badge
# that drops onto the card and settles, a white check drawn in, and a ring
# that spreads from the landing. The card closes HoldMs after the last phase.
# The stamp is a child control covering the card's client area, drawn from a
# snapshot of the card. A separate window was tried first (2026-11-14): the
# card is TopMost and re-asserts it, so an owned layered window ended up
# behind the card and the user saw nothing. A child control cannot.
# Cosmetic only: any failure, or the backstop timer, simply closes the card.
function Initialize-AnsweredStampType {
    if ('MusePopup.AnsweredStamp' -as [type]) { return }
    Add-Type -ErrorAction Stop -ReferencedAssemblies @(
        [System.Windows.Forms.Form].Assembly.Location,
        [System.Drawing.Bitmap].Assembly.Location
    ) -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Windows.Forms;
namespace MusePopup {
    public sealed class AnsweredStamp : Control {
        // ms from the start; the badge lands at DropAt + DropMs
        public const double VeilMs = 220, DropAt = 60, DropMs = 260, ImpactMs = 170;
        public const double DrawAt = 400, DrawMs = 360, HaloMs = 560, HoldMs = 1000;
        public const int WashAlpha = 92;            // white veil, about 36 percent
        public static double EndMs { get { return Math.Max(DrawAt + DrawMs, DropAt + DropMs + Math.Max(ImpactMs, HaloMs)); } }
        public event EventHandler Finished;
        readonly Bitmap under, soft;
        readonly float unit;
        readonly Stopwatch clock = new Stopwatch();
        readonly Timer timer = new Timer();
        bool finished, failed;
        static readonly Color Green = Color.FromArgb(52, 199, 89);
        public AnsweredStamp(Form card, float scale) {
            unit = Math.Max(1f, scale);
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.Opaque, true);
            Size s = card.ClientSize;
            under = new Bitmap(Math.Max(1, s.Width), Math.Max(1, s.Height), PixelFormat.Format32bppArgb);
            card.DrawToBitmap(under, new Rectangle(0, 0, under.Width, under.Height));
            soft = Soften(under);
            Bounds = new Rectangle(0, 0, under.Width, under.Height);
            timer.Interval = 15;
            timer.Tick += delegate { Step(); };
            card.Controls.Add(this);
            BringToFront();
        }
        public void Start() { clock.Start(); timer.Start(); Invalidate(); }
        // The softened copy: half resolution and back, about one logical pixel.
        static Bitmap Soften(Bitmap src) {
            int w = Math.Max(1, src.Width / 2), h = Math.Max(1, src.Height / 2);
            using (ImageAttributes wrap = new ImageAttributes())
            using (Bitmap half = new Bitmap(w, h, PixelFormat.Format32bppArgb)) {
                wrap.SetWrapMode(WrapMode.TileFlipXY);
                using (Graphics g = Graphics.FromImage(half)) {
                    g.InterpolationMode = InterpolationMode.HighQualityBilinear;
                    g.DrawImage(src, new Rectangle(0, 0, w, h), 0, 0, src.Width, src.Height, GraphicsUnit.Pixel, wrap);
                }
                Bitmap back = new Bitmap(src.Width, src.Height, PixelFormat.Format32bppArgb);
                using (Graphics g = Graphics.FromImage(back)) {
                    g.InterpolationMode = InterpolationMode.HighQualityBilinear;
                    g.DrawImage(half, new Rectangle(0, 0, src.Width, src.Height), 0, 0, w, h, GraphicsUnit.Pixel, wrap);
                }
                return back;
            }
        }
        static double Unit01(double t) { return t < 0 ? 0 : t > 1 ? 1 : t; }
        static double EaseOut(double t) { return 1 - Math.Pow(1 - t, 3); }
        void Step() {
            if (finished) return;
            if (failed || clock.Elapsed.TotalMilliseconds >= EndMs + HoldMs) { Finish(); return; }
            Invalidate();
        }
        void Finish() {
            if (finished) return;
            finished = true;
            timer.Stop();
            EventHandler h = Finished;
            if (h != null) h(this, EventArgs.Empty);
        }
        public double ElapsedMs { get { return clock.Elapsed.TotalMilliseconds; } }
        protected override void OnPaint(PaintEventArgs e) {
            try { Draw(e.Graphics, clock.Elapsed.TotalMilliseconds); } catch { failed = true; }
        }
        // One frame at ms after the start (also used by the tests).
        public void Draw(Graphics g, double ms) {
                double veil = 1 - Math.Pow(1 - Unit01(ms / VeilMs), 4);
                Rectangle all = new Rectangle(0, 0, under.Width, under.Height);
                if (veil >= 1) {
                    g.DrawImageUnscaled(soft, 0, 0);
                } else {
                    g.DrawImageUnscaled(under, 0, 0);
                    if (veil > 0) {
                        ColorMatrix m = new ColorMatrix(); m.Matrix33 = (float)veil;
                        using (ImageAttributes a = new ImageAttributes()) {
                            a.SetColorMatrix(m);
                            g.DrawImage(soft, all, 0, 0, soft.Width, soft.Height, GraphicsUnit.Pixel, a);
                        }
                    }
                }
                if (veil > 0)
                    using (Brush wash = new SolidBrush(Color.FromArgb((int)(WashAlpha * veil), 255, 255, 255)))
                        g.FillRectangle(wash, all);
                Badge(g, ms);
        }
        // badge box in pixels: about a quarter of the card's short side
        float Box() { return (float)(Math.Min(76, Math.Max(56, Math.Min(Width, Height) / unit * 0.26)) * unit); }
        void Badge(Graphics g, double ms) {
            double drop = Unit01((ms - DropAt) / DropMs);
            if (drop <= 0) return;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            float box = Box(), cx = Width / 2f, cy = Height / 2f;
            double landed = ms - DropAt - DropMs;
            double impact = Unit01(landed / ImpactMs);
            // falls from above the card (bigger, accelerating), hits, squashes, settles
            double scale = drop < 1 ? 1.6 - 0.6 * drop * drop : 1 - 0.08 * Math.Sin(Math.PI * impact);
            int alpha = (int)(255 * Unit01(drop / 0.45));
            float r = (float)(box * 24 / 52 * scale);
            double halo = Unit01(landed / HaloMs);
            if (landed > 0 && halo < 1) {
                float hr = (float)(box * 24 / 52 * (1 + 0.45 * EaseOut(halo)));
                using (Brush b = new SolidBrush(Color.FromArgb((int)(0.28 * 255 * (1 - halo)), Green)))
                    g.FillEllipse(b, cx - hr, cy - hr, 2 * hr, 2 * hr);
            }
            float sr = r + 10 * unit, sy = cy + (float)(6 * unit * Math.Min(1, scale));
            using (GraphicsPath p = new GraphicsPath()) {
                p.AddEllipse(cx - sr, sy - sr, 2 * sr, 2 * sr);
                using (PathGradientBrush b = new PathGradientBrush(p)) {
                    b.CenterColor = Color.FromArgb((int)(46 * alpha / 255.0), 0, 0, 0);
                    b.SurroundColors = new Color[] { Color.FromArgb(0, 0, 0, 0) };
                    b.FocusScales = new PointF(r / sr, r / sr);
                    g.FillPath(b, p);
                }
            }
            using (Brush b = new SolidBrush(Color.FromArgb(alpha, Green)))
                g.FillEllipse(b, cx - r, cy - r, 2 * r, 2 * r);
            double c = EaseOut(Unit01((ms - DrawAt) / DrawMs));
            if (c <= 0) return;
            // the CheckFX path M16 26.5 L23 33.5 L36.5 19 in a 52 box, centre 26,26
            float k = r / 24;
            PointF a0 = new PointF(cx - 10 * k, cy + 0.5f * k), a1 = new PointF(cx - 3 * k, cy + 7.5f * k), a2 = new PointF(cx + 10.5f * k, cy - 7 * k);
            double l1 = Math.Sqrt(98), l2 = Math.Sqrt(13.5 * 13.5 + 14.5 * 14.5), len = c * (l1 + l2);
            using (Pen pen = new Pen(Color.FromArgb(alpha, 255, 255, 255), 4.6f * k)) {
                pen.StartCap = pen.EndCap = LineCap.Round; pen.LineJoin = LineJoin.Round;
                if (len <= l1) {
                    float f = (float)(len / l1);
                    g.DrawLine(pen, a0, new PointF(a0.X + (a1.X - a0.X) * f, a0.Y + (a1.Y - a0.Y) * f));
                } else {
                    float f = (float)((len - l1) / l2);
                    g.DrawLines(pen, new PointF[] { a0, a1, new PointF(a1.X + (a2.X - a1.X) * f, a1.Y + (a2.Y - a1.Y) * f) });
                }
            }
        }
        protected override void Dispose(bool disposing) {
            if (disposing) { timer.Stop(); timer.Dispose(); under.Dispose(); soft.Dispose(); }
            base.Dispose(disposing);
        }
    }
}
'@
}

function Stop-AnsweredStamp {
    # Idempotent; the control goes with the card anyway.
    $stamp = $script:StampLayer; $script:StampLayer = $null
    try { if ($stamp) { $stamp.Dispose() } } catch {}
}

function Start-AnsweredStamp {
    if ($script:AnswerFeedbackStarted -or $script:PopupClosing) { return }
    $script:AnswerFeedbackStarted = $true
    foreach ($timer in @($script:LinkTimer, $script:RingTimer, $script:ExpiryTimer)) { try { if ($timer) { $timer.Stop() } } catch {} }
    # Backstop: whatever happens to the stamp, the card closes.
    $script:CloseTimer = New-Object System.Windows.Forms.Timer
    $script:CloseTimer.Interval = 4000
    $script:CloseTimer.Add_Tick({ try { $script:CloseTimer.Stop() } catch {}; $script:Form.Close() })
    $script:CloseTimer.Start()
    try {
        $ErrorActionPreference = 'Stop'
        Initialize-AnsweredStampType
        $stamp = New-Object MusePopup.AnsweredStamp ($script:Form, [single]$script:Scale)
        $script:StampLayer = $stamp
        $stamp.Add_Finished({ $script:Form.Close() })
        $stamp.Start()
    } catch {
        Stop-AnsweredStamp
        $script:Form.Close()
    }
}
function Send-PetSupplement([string]$text) {
    $file = Join-Path ([IO.Path]::GetTempPath()) ('pet-supplement-' + [guid]::NewGuid().ToString('N') + '.txt')
    try {
        [IO.File]::WriteAllText($file, $text, $script:Utf8)
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $script:Dir 'muse-call.ps1') -Type message -NoWait -As pet -Thread pet/chat -TextFile $file | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
    finally { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
}
function Finish-Popup([string]$kind, [string]$result) {
    if ($script:AnswerFeedbackStarted -or $script:PopupClosing) { return }
    Sync-ExternalAnswer
    if ($script:ExternalKind -ne '' -and $script:ExternalKind -ne 'pet_done') { return }
    if ($script:ExternalKind -eq 'pet_done') {
        if ($result.Trim().Length -gt 0 -and -not (Send-PetSupplement $result)) {
            $script:HeaderTitle = (T 'popup.send_failed')
            if ($script:HeaderPanel) { $script:HeaderPanel.Invalidate() }
            return
        }
    } else {
        if ($script:ExpiresIn -gt 0 -and $null -ne $script:ExpiryAt -and [DateTime]::UtcNow -gt $script:ExpiryAt) {
            $kind = 'expired'
            $result = ''
        }
        if (-not (Save-Result $kind $result)) {
            # A pet answer may have won while the completer was starting.
            if ($script:ExternalKind -eq 'pet_done' -and $result.Trim().Length -gt 0) {
                if (-not (Send-PetSupplement $result)) { return }
            } else { return }
        }
    }
    try { Remove-Item -LiteralPath (Join-Path $script:Dir 'popup-draft.txt') -Force -ErrorAction SilentlyContinue } catch {}
    if ($script:ExternalKind -eq '' -and $kind -in @('info', 'choice', 'typed', 'input')) {
        try {
            $mbDir = Join-Path $script:Dir 'mailbox'
            if (Test-Path -LiteralPath $mbDir) {
                $hist = [ordered]@{
                    ts = ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0)
                    req_id = $script:ReqId
                    ask = $script:Text
                    kind = $kind
                    answer = $result
                } | ConvertTo-Json -Compress
                [IO.File]::AppendAllText((Join-Path $mbDir 'pet-history.jsonl'), ($hist + "`n"), $script:Utf8)
            }
        } catch {}
        Start-AnsweredStamp
        return
    }
    if ($script:AnswerFeedbackStarted) { return }
    $script:Form.Close()
}
function Submit-Input {
    if (-not $script:TextBox) { return }
    $txt = $script:TextBox.Text.Trim()
    if ($txt.Length -eq 0) { return }
    if ($script:Mode -eq 'input') { Finish-Popup 'input' $txt } else { Finish-Popup 'typed' $txt }
}

# ---- Painting -------------------------------------------------------------------
function Draw-CardBorder($g) {
    if (-not $script:UseRegion) { return }
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $pen = New-Object System.Drawing.Pen($script:EdgeC, 1.0)
    $path = New-RoundPath 0.5 0.5 ($script:Form.Width - 1) ($script:Form.Height - 1) ((Px 14) - 0.5)
    $g.DrawPath($pen, $path)
    $pen.Dispose(); $path.Dispose()
}

function Draw-Pencil($g, [double]$x0, [double]$y0, [double]$size, $color) {
    $k = $size / 24.0
    $pen = New-Object System.Drawing.Pen($color, [single](2.0 * $k))
    $pen.StartCap = 'Round'; $pen.EndCap = 'Round'; $pen.LineJoin = 'Round'
    $pts = [System.Drawing.PointF[]]@(
        (PtF ($x0 + 16.5 * $k) ($y0 + 3.5 * $k)),
        (PtF ($x0 + 19.5 * $k) ($y0 + 6.5 * $k)),
        (PtF ($x0 + 7.0 * $k)  ($y0 + 19.0 * $k)),
        (PtF ($x0 + 3.0 * $k)  ($y0 + 20.0 * $k)),
        (PtF ($x0 + 4.0 * $k)  ($y0 + 16.0 * $k))
    )
    $g.DrawPolygon($pen, $pts)
    $g.DrawLine($pen, (PtF ($x0 + 12.0 * $k) ($y0 + 20.0 * $k)), (PtF ($x0 + 21.0 * $k) ($y0 + 20.0 * $k)))
    $pen.Dispose()
}

function Draw-FallbackIcon($g, [int]$x, [int]$y, [int]$s) {
    $rc = [System.Drawing.Rectangle]::new($x, $y, $s, $s)
    $br = New-Object System.Drawing.Drawing2D.LinearGradientBrush($rc, (Clr '#5AC8FA'), (Clr '#0A64D8'), [single]45)
    $g.FillEllipse($br, $rc)
    $br.Dispose()
    $k = $s / 18.0
    $raw = @(9,4.4, 9.3,7.2, 10.8,8.7, 13.6,9, 10.8,9.3, 9.3,10.8, 9,13.6, 8.7,10.8, 7.2,9.3, 4.4,9, 7.2,8.7, 8.7,7.2, 9,4.4)
    $pts = New-Object 'System.Collections.Generic.List[System.Drawing.PointF]'
    for ($i = 0; $i -lt $raw.Count; $i += 2) { $pts.Add((PtF ($x + $raw[$i] * $k) ($y + $raw[$i + 1] * $k))) }
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddBeziers($pts.ToArray())
    $path.CloseFigure()
    $wb = New-Object System.Drawing.SolidBrush($script:White)
    $g.FillPath($wb, $path)
    $wb.Dispose(); $path.Dispose()
}

function Paint-Header($c, $e) {
    $g = $e.Graphics
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $h = $c.Height
    $iconS = Px 18
    $ix = Px 22
    $iy = [int](($h - $iconS) / 2)
    if ($script:IconImg) {
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $ia = New-Object System.Drawing.Imaging.ImageAttributes
        $ia.SetWrapMode([System.Drawing.Drawing2D.WrapMode]::TileFlipXY)
        $dest = [System.Drawing.Rectangle]::new($ix, $iy, $iconS, $iconS)
        $g.DrawImage($script:IconImg, $dest, 0, 0, [int]$script:IconImg.Width, [int]$script:IconImg.Height, [System.Drawing.GraphicsUnit]::Pixel, $ia)
        $ia.Dispose()
    } else {
        Draw-FallbackIcon $g $ix $iy $iconS
    }
    $tx = $ix + $iconS + (Px 8)
    $tw = [Math]::Max((Px 60), $script:CloseRect.X - $tx - (Px 8))
    [System.Windows.Forms.TextRenderer]::DrawText($g, $script:HeaderTitle, $script:FontTitle,
        [System.Drawing.Rectangle]::new($tx, 0, $tw, $h), $script:Ink, $script:White, $script:TFLeft)

    $r = $script:CloseRect
    if ($c.Tag.Hover) {
        $hb = New-Object System.Drawing.SolidBrush((Clr '#ECECEF'))
        $g.FillEllipse($hb, $r)
        $hb.Dispose()
    }
    $cx = $r.X + $r.Width / 2.0
    $cy = $r.Y + $r.Height / 2.0
    $a = 4.5 * $script:Scale
    $pen = New-Object System.Drawing.Pen($script:Sub, [single](1.5 * $script:Scale))
    $pen.StartCap = 'Round'; $pen.EndCap = 'Round'
    $g.DrawLine($pen, (PtF ($cx - $a) ($cy - $a)), (PtF ($cx + $a) ($cy + $a)))
    $g.DrawLine($pen, (PtF ($cx + $a) ($cy - $a)), (PtF ($cx - $a) ($cy + $a)))
    $pen.Dispose()

    $hpen = New-Object System.Drawing.Pen((Clr '#EFEFF4'), 1.0)
    $g.DrawLine($hpen, (Px 22), ($h - 1), ($c.Width - (Px 22)), ($h - 1))
    $hpen.Dispose()

    Draw-CardBorder $g
}

function Paint-Button($c, $e) {
    $t = $c.Tag; $st = $t.Style; $g = $e.Graphics
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $bg = $st.Bg; $fg = $st.Fg
    if (-not $t.Enabled) { $bg = $st.BgOff } elseif ($t.Down) { $bg = $st.BgDown } elseif ($t.Hover) { $bg = $st.BgHover }
    $path = New-RoundPath 0 0 $c.Width $c.Height (Px 10)
    $br = New-Object System.Drawing.SolidBrush($bg)
    $g.FillPath($br, $path)
    $br.Dispose(); $path.Dispose()

    $lh = Px 22
    $n = $t.Lines.Count
    $y0 = [int](($c.Height - $n * $lh) / 2)
    if ($t.Pencil) {
        $iconS = Px 15; $gap = Px 6
        $tw = Get-TextWidth $t.Lines[0] $script:FontBody
        $sx = [int](($c.Width - ($iconS + $gap + $tw)) / 2)
        Draw-Pencil $g $sx (($c.Height - $iconS) / 2.0) $iconS $script:Blue
        [System.Windows.Forms.TextRenderer]::DrawText($g, $t.Lines[0], $script:FontBody,
            [System.Drawing.Rectangle]::new($sx + $iconS + $gap, $y0, $tw + (Px 4), $lh), $fg, $bg, $script:TFLeft)
    } else {
        for ($i = 0; $i -lt $n; $i++) {
            if ($t.Lines[$i].Length -eq 0) { continue }
            [System.Windows.Forms.TextRenderer]::DrawText($g, $t.Lines[$i], $script:FontBody,
                [System.Drawing.Rectangle]::new($t.Pad, $y0 + $i * $lh, $c.Width - 2 * $t.Pad, $lh), $fg, $bg, $script:TFCenter)
        }
    }
}

function Paint-InputHost($c, $e) {
    $g = $e.Graphics
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $ring = Px 4
    $bw = [Math]::Max(1, (Px 1))
    $r = Px 10
    $w = $c.Width; $h = $c.Height
    $focused = $c.Tag.Focused
    if ($focused) {
        $p0 = New-RoundPath 0 0 $w $h ($r + $ring)
        $b0 = New-Object System.Drawing.SolidBrush($script:RingC)
        $g.FillPath($b0, $p0); $b0.Dispose(); $p0.Dispose()
    }
    $edge = $script:Hair
    if ($focused) { $edge = $script:Blue }
    $p1 = New-RoundPath $ring $ring ($w - 2 * $ring) ($h - 2 * $ring) $r
    $b1 = New-Object System.Drawing.SolidBrush($edge)
    $g.FillPath($b1, $p1); $b1.Dispose(); $p1.Dispose()
    $in = $ring + $bw
    $p2 = New-RoundPath $in $in ($w - 2 * $in) ($h - 2 * $in) ($r - $bw)
    $b2 = New-Object System.Drawing.SolidBrush($script:White)
    $g.FillPath($b2, $p2); $b2.Dispose(); $p2.Dispose()
}

function Paint-Hint($c, $e) {
    $g = $e.Graphics
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $h = $c.Height
    $chipH = Px 18
    $cy = [int](($h - $chipH) / 2)
    $chipBg = Clr '#F2F2F7'
    $chipLine = Clr '#D1D1D6'
    $chipInk = Clr '#3A3A3C'
    $segments = @(
        @{ K = 'chip'; T = 'Enter'; G = 5 },
        @{ K = 'text'; T = $script:T.Send; G = 16 },
        @{ K = 'chip'; T = 'Shift'; G = 3 },
        @{ K = 'text'; T = '+'; G = 3 },
        @{ K = 'chip'; T = 'Enter'; G = 5 },
        @{ K = 'text'; T = $script:T.NewLine; G = 0 }
    )
    $x = 0
    foreach ($sg in $segments) {
        if ($sg.K -eq 'chip') {
            $tw = Get-TextWidth $sg.T $script:FontChip
            $cwid = [Math]::Max((Px 18), $tw + 2 * (Px 6))
            $p = New-RoundPath $x $cy $cwid $chipH (Px 5)
            $fb = New-Object System.Drawing.SolidBrush($chipBg)
            $g.FillPath($fb, $p); $fb.Dispose(); $p.Dispose()
            $bp = New-RoundPath ($x + 0.5) ($cy + 0.5) ($cwid - 1) ($chipH - 1) ((Px 5) - 0.5)
            $pen = New-Object System.Drawing.Pen($chipLine, 1.0)
            $g.DrawPath($pen, $bp); $pen.Dispose(); $bp.Dispose()
            [System.Windows.Forms.TextRenderer]::DrawText($g, $sg.T, $script:FontChip,
                [System.Drawing.Rectangle]::new([int]$x, $cy, [int]$cwid, $chipH), $chipInk, $chipBg, $script:TFCenter)
            $x += $cwid + (Px $sg.G)
        } else {
            $tw = Get-TextWidth $sg.T $script:FontHint
            [System.Windows.Forms.TextRenderer]::DrawText($g, $sg.T, $script:FontHint,
                [System.Drawing.Rectangle]::new([int]$x, 0, $tw + (Px 4), $h), $script:Sub, $script:White, $script:TFLeft)
            $x += $tw + (Px $sg.G)
        }
    }
}

# ---- Control factories ----------------------------------------------------------
function New-Surface([int]$x, [int]$y, [int]$w, [int]$h) {
    $p = New-Object System.Windows.Forms.Panel
    $p.SetBounds($x, $y, $w, $h)
    $p.BackColor = $script:White
    $p.TabStop = $false
    Set-DoubleBuffered $p
    return $p
}

function New-RoundButton([int]$X, [int]$Y, [int]$W, [string]$Text, [hashtable]$Style, [scriptblock]$OnClick, $Payload, [bool]$Pencil) {
    $pad = Px 16
    $lines = Get-WrapLines $Text $script:FontBody ($W - 2 * $pad)
    $h = [Math]::Max((Px 44), $lines.Count * (Px 22) + 2 * (Px 11))
    $b = New-Surface $X $Y $W $h
    $b.Cursor = [System.Windows.Forms.Cursors]::Hand
    $b.Tag = @{ Lines = $lines; Style = $Style; Pad = $pad; Hover = $false; Down = $false; Enabled = $true; Pencil = $Pencil; OnClick = $OnClick; Payload = $Payload }
    $b.Add_Paint({ param($sender, $e) Paint-Button $sender $e })
    $b.Add_MouseEnter({ param($sender, $e) $sender.Tag.Hover = $true; $sender.Invalidate() })
    $b.Add_MouseLeave({ param($sender, $e) $sender.Tag.Hover = $false; $sender.Tag.Down = $false; $sender.Invalidate() })
    $b.Add_MouseDown({ param($sender, $e)
        Note-UserTouch
        if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { $sender.Tag.Down = $true; $sender.Invalidate() }
    })
    $b.Add_MouseUp({ param($sender, $e)
        $was = $sender.Tag.Down
        $sender.Tag.Down = $false
        $sender.Invalidate()
        if ($was -and $e.Button -eq [System.Windows.Forms.MouseButtons]::Left -and $sender.Tag.Enabled -and
            $e.X -ge 0 -and $e.Y -ge 0 -and $e.X -lt $sender.Width -and $e.Y -lt $sender.Height) {
            & $sender.Tag.OnClick $sender.Tag.Payload
        }
    })
    $script:Form.Controls.Add($b)
    return $b
}

# ---- Card construction ------------------------------------------------------------
function Build-Card([string]$state) {
    $f = $script:Form
    $f.SuspendLayout()
    foreach ($c in @($f.Controls)) { $f.Controls.Remove($c); $c.Dispose() }
    $script:TextBox = $null
    $script:SendBtn = $null
    $script:State = $state

    $W    = $script:CardW
    $pad  = Px 22
    $cw   = $W - 2 * $pad
    $hdrH = Px 46

    # header: icon + title on the left, close on the right, both centred vertically
    $hdr = New-Surface 0 0 $W $hdrH
    $hdr.Tag = @{ Hover = $false }
    $closeS = Px 28
    $script:CloseRect = [System.Drawing.Rectangle]::new(($W - (Px 14) - $closeS), [int](($hdrH - $closeS) / 2), $closeS, $closeS)
    $hdr.Add_Paint({ param($sender, $e) Paint-Header $sender $e })
    $hdr.Add_MouseMove({ param($sender, $e)
        $hv = $script:CloseRect.Contains($e.Location)
        if ($hv -ne $sender.Tag.Hover) {
            $sender.Tag.Hover = $hv
            if ($hv) { $sender.Cursor = [System.Windows.Forms.Cursors]::Hand } else { $sender.Cursor = [System.Windows.Forms.Cursors]::Default }
            $sender.Invalidate()
        }
    })
    $hdr.Add_MouseLeave({ param($sender, $e)
        if ($sender.Tag.Hover) { $sender.Tag.Hover = $false; $sender.Cursor = [System.Windows.Forms.Cursors]::Default; $sender.Invalidate() }
    })
    $hdr.Add_MouseDown({ param($sender, $e)
        if ($e.Button -ne [System.Windows.Forms.MouseButtons]::Left) { return }
        if ($script:CloseRect.Contains($e.Location)) { $script:Form.Close() } else { Start-Drag }
    })
    $f.Controls.Add($hdr)
    $script:HeaderPanel = $hdr

    $y = $hdrH + (Px 14)

    # message: read-only RichTextBox for bolding key/core content while keeping
    # mouse selection and Ctrl+C. Normalize line breaks to LF (`n) so index
    # offsets for bold runs match RichTextBox 1-for-1, then measure tight height.
    if ($script:Text.Length -gt 0) {
        $lineH = [Math]::Max($script:FontBody.Height, $script:FontBodyBold.Height)
        $fmt = Format-PopupMessage $script:Text $script:FontBodyBold ($cw - (Px 6))
        $m = New-Object System.Windows.Forms.RichTextBox
        $m.Multiline = $true
        $m.ReadOnly = $true
        $m.BorderStyle = [System.Windows.Forms.BorderStyle]::None
        $m.ScrollBars = [System.Windows.Forms.RichTextBoxScrollBars]::None
        $m.WordWrap = $true
        $m.DetectUrls = $false
        $m.ShortcutsEnabled = $true
        $m.TabStop = $false
        $m.HideSelection = $false
        $m.Font = $script:FontBody
        $m.ForeColor = $script:Ink
        $m.BackColor = $script:White
        $m.AccessibleName = 'Message'
        $maxMeasureH = [Math]::Max(1, $script:MaxMsgLines) * [int][Math]::Ceiling($lineH * 1.5)
        $m.SetBounds($pad, $y, $cw, $maxMeasureH)
        $m.Text = $fmt.Text
        foreach ($run in $fmt.Runs) {
            if ($run.Len -gt 0 -and ($run.Start + $run.Len) -le $m.TextLength) {
                $m.Select($run.Start, $run.Len)
                $m.SelectionFont = $script:FontBodyBold
            }
        }
        $m.Select(0, 0)
        $f.Controls.Add($m)
        [void]$m.Handle
        $actualLines = [Math]::Max(1, $m.GetLineFromCharIndex($m.TextLength) + 1)
        $stepH = $lineH
        if ($actualLines -ge 2) {
            $y0 = $m.GetPositionFromCharIndex($m.GetFirstCharIndexFromLine(0)).Y
            $y1 = $m.GetPositionFromCharIndex($m.GetFirstCharIndexFromLine(1)).Y
            if (($y1 - $y0) -gt $stepH) { $stepH = $y1 - $y0 }
        } else {
            $stepH = [int][Math]::Ceiling($lineH * 1.35)
        }
        $maxBoxH = [Math]::Max(1, $script:MaxMsgLines) * $stepH + (Px 2)
        if ($actualLines -gt $script:MaxMsgLines) {
            $m.ScrollBars = [System.Windows.Forms.RichTextBoxScrollBars]::Vertical
            $mh = $maxBoxH
        } else {
            $endY = $m.GetPositionFromCharIndex($m.TextLength).Y
            $mh = [Math]::Min($maxBoxH, [Math]::Max(($actualLines * $stepH + (Px 2)), ($endY + $stepH + (Px 2))))
        }
        $m.SetBounds($pad, $y, $cw, $mh)
        $y += $mh
    }

    if ($state -eq 'info') {
        $y += Px 18
        $b = New-RoundButton -X $pad -Y $y -W $cw -Text $script:T.Ok -Style $script:StPrimary -OnClick { param($p) Finish-Popup 'info' '' } -Payload $null -Pencil $false
        $y += $b.Height
    }
    elseif ($state -eq 'choice') {
        $y += Px 18
        for ($i = 0; $i -lt $script:Options.Count; $i++) {
            $opt = $script:Options[$i]
            $ostyle = $script:Pastels[$i % 4]
            if ($script:TestPicked -and $script:TestPicked -eq $opt) { $ostyle = $script:StPrimary }
            $b = New-RoundButton -X $pad -Y $y -W $cw -Text $opt -Style $ostyle -OnClick { param($p)
                if (Test-Path (Join-Path $script:Dir 'popup-test-nosubmit.txt')) {
                    # Test mode (knob file present): a click only marks the
                    # pick (highlight + marker file), it does not submit --
                    # lets the user verify touch detection on a live window.
                    Note-UserTouch
                    $script:TestPicked = $p
                    try { [System.IO.File]::WriteAllText((Join-Path $script:Dir 'popup-test-picked.txt'), $p, (New-Object System.Text.UTF8Encoding($false))) } catch {}
                    Switch-To 'choice'
                } else {
                    Finish-Popup 'choice' $p
                }
            } -Payload $opt -Pencil $false
            $y += $b.Height + (Px 8)
        }
        $b = New-RoundButton -X $pad -Y $y -W $cw -Text $script:T.Other -Style $script:StGray -OnClick { param($p) Switch-To 'typing' } -Payload $null -Pencil $true
        $y += $b.Height
    }
    else {
        # typing: rounded input host (focus ring) with a 4-line borderless TextBox inside
        $y += Px 16
        $ring = Px 4
        $bw = [Math]::Max(1, (Px 1))
        $padX = Px 12
        $padY = Px 10
        $lineH = $script:FontBody.Height
        $innerH = 4 * $lineH
        $hostH = 2 * ($ring + $bw) + 2 * $padY + $innerH
        $hostW = $cw + 2 * $ring
        $hostPanel = New-Surface ($pad - $ring) ($y - $ring) $hostW $hostH
        $hostPanel.Tag = @{ Focused = $false }
        $hostPanel.Cursor = [System.Windows.Forms.Cursors]::IBeam
        $hostPanel.Add_Paint({ param($sender, $e) Paint-InputHost $sender $e })
        $hostPanel.Add_MouseDown({ param($sender, $e) if ($sender.Controls.Count -gt 0) { [void]$sender.Controls[0].Focus() } })

        $tb = New-Object System.Windows.Forms.TextBox
        $tb.Multiline = $true
        $tb.BorderStyle = [System.Windows.Forms.BorderStyle]::None
        $tb.ScrollBars = [System.Windows.Forms.ScrollBars]::None
        $tb.WordWrap = $true
        $tb.AcceptsReturn = $true
        $tb.MaxLength = 1000000
        $tb.Font = $script:FontBody
        $tb.ForeColor = $script:Ink
        $tb.BackColor = $script:White
        $tb.AccessibleName = 'Answer'
        $tb.SetBounds(($ring + $bw + $padX), ($ring + $bw + $padY), ($hostW - 2 * ($ring + $bw + $padX)), $innerH)
        $tb.Text = ($script:Draft -replace "\r?\n", "`r`n")
        $tb.Add_KeyDown({ param($sender, $e)
            if ($e.KeyCode -eq [System.Windows.Forms.Keys]::Return -and -not $e.Shift) {
                $e.SuppressKeyPress = $true
                $e.Handled = $true
                Submit-Input
            }
            elseif ($e.Control -and $e.KeyCode -eq [System.Windows.Forms.Keys]::A) {
                $e.SuppressKeyPress = $true
                $e.Handled = $true
                $sender.SelectAll()
            }
        })
        $tb.Add_TextChanged({ param($sender, $e)
            # The visual dwell must not recreate a draft after an answer was saved.
            if ($script:AnswerFeedbackStarted -or $script:PopupClosing) { return }
            Note-UserTouch
            $script:Draft = $sender.Text
            try { Write-Utf8 'popup-draft.txt' ($script:Text + "`n--DRAFT--`n" + $sender.Text) } catch {}
            if ($script:SendBtn) {
                $script:SendBtn.Tag.Enabled = ($sender.Text.Trim().Length -gt 0)
                $script:SendBtn.Invalidate()
            }
        })
        $tb.Add_GotFocus({ param($sender, $e) $sender.Parent.Tag.Focused = $true; $sender.Parent.Invalidate(); Note-UserTouch })
        $tb.Add_LostFocus({ param($sender, $e) $sender.Parent.Tag.Focused = $false; $sender.Parent.Invalidate() })
        $hostPanel.Controls.Add($tb)
        $f.Controls.Add($hostPanel)
        $script:TextBox = $tb
        $y += $hostH - 2 * $ring

        # hint row (below the box, never overlapping it)
        $y += Px 8
        $hintH = Px 18
        $hint = New-Surface $pad $y $cw $hintH
        $hint.Add_Paint({ param($sender, $e) Paint-Hint $sender $e })
        $hint.Add_MouseDown({ param($sender, $e) if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { Start-Drag } })
        $f.Controls.Add($hint)
        $y += $hintH

        # action row
        $y += Px 16
        if ($script:FromChoice) {
            $gap = Px 8
            $half = [int](($cw - $gap) / 2)
            $back = New-RoundButton -X $pad -Y $y -W $half -Text $script:T.Back -Style $script:StGray -OnClick { param($p) Switch-To 'choice' } -Payload $null -Pencil $false
            $send = New-RoundButton -X ($pad + $half + $gap) -Y $y -W ($cw - $half - $gap) -Text $script:T.Send -Style $script:StPrimary -OnClick { param($p) Submit-Input } -Payload $null -Pencil $false
        } else {
            $send = New-RoundButton -X $pad -Y $y -W $cw -Text $script:T.Send -Style $script:StPrimary -OnClick { param($p) Submit-Input } -Payload $null -Pencil $false
        }
        $send.Tag.Enabled = ($tb.Text.Trim().Length -gt 0)
        $script:SendBtn = $send
        $y += $send.Height
    }

    $f.ClientSize = [System.Drawing.Size]::new($W, ($y + $pad))
    Set-CardShape
    foreach ($c in @($f.Controls)) { Add-TouchWatch $c }
    $f.ResumeLayout($false)
    $f.Invalidate($true)
}

function Set-CardShape {
    if (-not $script:UseRegion) { return }
    $f = $script:Form
    $p = New-RoundPath 0 0 $f.Width $f.Height (Px 14)
    $f.Region = New-Object System.Drawing.Region($p)
    $p.Dispose()
}

function Place-Form([bool]$center) {
    $f = $script:Form
    if ($center) {
        $wa = [System.Windows.Forms.Screen]::FromPoint([System.Windows.Forms.Cursor]::Position).WorkingArea
        $x = $wa.Left + [int](($wa.Width - $f.Width) / 2)
        $y = $wa.Top + [int](($wa.Height - $f.Height) / 2)
    } else {
        $wa = [System.Windows.Forms.Screen]::FromControl($f).WorkingArea
        $x = $f.Left
        $y = $f.Top
    }
    if ($x + $f.Width -gt $wa.Right)   { $x = $wa.Right - $f.Width }
    if ($y + $f.Height -gt $wa.Bottom) { $y = $wa.Bottom - $f.Height }
    if ($x -lt $wa.Left) { $x = $wa.Left }
    if ($y -lt $wa.Top)  { $y = $wa.Top }
    $f.Location = [System.Drawing.Point]::new($x, $y)
}

function Focus-Input {
    if ($script:TextBox -and -not $script:TextBox.IsDisposed) {
        [void]$script:TextBox.Focus()
        $script:TextBox.Select($script:TextBox.TextLength, 0)
    }
}

function Invoke-Switch {
    if ($script:AnswerFeedbackStarted -or $script:PopupClosing) { return }
    $st = $script:PendingState
    Build-Card $st
    Place-Form $false
    if ($st -eq 'typing') { Focus-Input }
}
function Switch-To([string]$state) {
    if ($script:AnswerFeedbackStarted -or $script:PopupClosing) { return }
    # deferred, so the clicked button is not disposed inside its own event
    $script:PendingState = $state
    [void]$script:Form.BeginInvoke([System.Action]{ Invoke-Switch })
}

# ---- Form -------------------------------------------------------------------------
# A Form subclass whose ShowWithoutActivation is true: the card appears on top
# without stealing keyboard focus. Clicking it activates it normally.
$form = $null
if (-not $script:ActivateOnShow) {
    try {
        if (-not ('MusePopup.CardForm' -as [type])) {
            Add-Type -ReferencedAssemblies ([System.Windows.Forms.Form].Assembly.Location) -TypeDefinition @'
namespace MusePopup {
    public class CardForm : System.Windows.Forms.Form {
        protected override bool ShowWithoutActivation { get { return true; } }
    }
}
'@
        }
        $form = New-Object MusePopup.CardForm
    } catch { $form = $null }
}
if ($null -eq $form) {
    $form = New-Object System.Windows.Forms.Form
    $script:ActivateOnShow = $true   # fallback: old behaviour, key guard below
}
$script:Form = $form
$form.Text = 'Muse'
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$form.ShowInTaskbar = $false
$form.TopMost = $true
$form.KeyPreview = $true
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::None
$form.BackColor = $script:White
Set-DoubleBuffered $form

$form.Add_Paint({ param($sender, $e) Draw-CardBorder $e.Graphics })
$form.Add_MouseDown({ param($sender, $e) Note-UserTouch; if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { Start-Drag } })
$form.Add_KeyDown({ param($sender, $e)
    # Key guard: keys arriving right after the card appeared were typed for
    # some other window. Drop them (KeyPreview runs before the answer box).
    if ($null -eq $script:ShownAt -or
        ([DateTime]::UtcNow - $script:ShownAt).TotalMilliseconds -lt $script:KeyGuardMs) {
        $e.SuppressKeyPress = $true
        $e.Handled = $true
        return
    }
    Note-UserTouch
    if ($script:Mode -eq 'info' -and $e.KeyCode -eq [System.Windows.Forms.Keys]::Return) {
        $e.SuppressKeyPress = $true
        Finish-Popup 'info' ''
    }
})
$form.Add_FormClosing({
    $script:PopupClosing = $true
    Stop-AnsweredStamp
    [void](Save-Result $script:BaseKind '')
})
$form.Add_FormClosed({
    Stop-AnsweredStamp
    foreach ($timer in @($script:LinkTimer, $script:CloseTimer, $script:StampWarmTimer)) { if ($timer) { $timer.Stop(); $timer.Dispose() } }
    try { if ($script:RingTimer) { $script:RingTimer.Stop(); $script:RingTimer.Dispose() } } catch {}
    try { if ($script:ExpiryTimer) { $script:ExpiryTimer.Stop(); $script:ExpiryTimer.Dispose() } } catch {}
    try { if ($script:IconImg) { $script:IconImg.Dispose() } } catch {}
    try { if ($script:IconStream) { $script:IconStream.Dispose() } } catch {}
    try { if ($script:FontBodyBold) { $script:FontBodyBold.Dispose() } } catch {}
})
$form.Add_Shown({
    $script:ShownAt = [DateTime]::UtcNow
    $script:LinkTimer = New-Object System.Windows.Forms.Timer
    $script:LinkTimer.Interval = 500
    $script:LinkTimer.Add_Tick({ Sync-ExternalAnswer })
    $script:LinkTimer.Start()
    Sync-ExternalAnswer
    # Warm up the answered-stamp type while the user reads the card:
    # compiling it costs a second or more, and paying that on the click
    # path makes the stamp appear late. One-shot timer; if this fails,
    # Start-AnsweredStamp retries, so a failure here is harmless.
    $script:StampWarmTimer = New-Object System.Windows.Forms.Timer
    $script:StampWarmTimer.Interval = 250
    $script:StampWarmTimer.Add_Tick({
        try { $script:StampWarmTimer.Stop(); $script:StampWarmTimer.Dispose() } catch {}
        try { Initialize-AnsweredStampType } catch {}
    })
    $script:StampWarmTimer.Start()
    if ($script:Saved) { return }
    Play-PopupSound
    $script:RingCount = 1
    if ($script:ActivateOnShow) {
        Set-Foreground
        if ($script:TextBox) { Focus-Input }
    }
    Start-RingTimer
    if ($script:ExpiresIn -gt 0) {
        $script:ExpiryAt = [DateTime]::UtcNow.AddSeconds($script:ExpiresIn)
        $script:ExpiryTimer = New-Object System.Windows.Forms.Timer
        $script:ExpiryTimer.Interval = 500
        $script:ExpiryTimer.Add_Tick({
            if ($null -eq $script:ExpiryAt) { return }
            $left = ($script:ExpiryAt - [DateTime]::UtcNow).TotalSeconds
            if ($left -le 0) {
                try { $script:ExpiryTimer.Stop() } catch {}
                Finish-Popup 'expired' ''
            } else {
                $script:HeaderTitle = (T 'popup.expiry' @{seconds=[int][Math]::Ceiling($left)})
                if ($script:HeaderPanel) { $script:HeaderPanel.Invalidate() }
            }
        })
        $script:ExpiryTimer.Start()
    }
})
$form.Add_Activated({
    # first activation by the user (click / Alt-Tab): put the caret in the
    # answer box, as the old auto-focus did
    if (-not $script:EverActivated) {
        $script:EverActivated = $true
        if ($script:TextBox) { Focus-Input }
    }
})

# Win11: native rounded corners + shadow from DWM. Otherwise: region + hairline border.
$hwnd = $form.Handle
try {
    $pref = 2   # DWMWCP_ROUND
    $hr = [MusePopup.Native]::DwmSetWindowAttribute($hwnd, 33, [ref]$pref, 4)
    if ($hr -eq 0) {
        $script:UseRegion = $false
        [void][MusePopup.Native]::DwmExtendFrameIntoClientArea($hwnd, [int[]](1, 1, 1, 1))
    }
} catch { $script:UseRegion = $true }

# ---- Message layout (widen the card for structured or long text) ----------------
$script:MsgLines = @()
$script:CardW = Px 360
$wa0 = [System.Windows.Forms.Screen]::FromPoint([System.Windows.Forms.Cursor]::Position).WorkingArea
$linePx = [Math]::Max((Px 20), $script:FontBody.Height)
$maxLines = [Math]::Max(4, [int][Math]::Floor(($wa0.Height - (Px 420)) / $linePx))
$script:MaxMsgLines = $maxLines
if ($script:Text.Length -gt 0) {
    $shortFmt = Format-PopupMessage $script:Text $script:FontBodyBold ((Px 360) - 2 * (Px 22) - (Px 6))
    $widths = if ($shortFmt.Lines.Count -le 4 -and $shortFmt.Text.Length -le 80) { @(360, 440, 500, 560) } else { @(440, 500, 560) }
    $lines = @()
    foreach ($dw in $widths) {
        $script:CardW = Px $dw
        $lines = (Format-PopupMessage $script:Text $script:FontBodyBold ($script:CardW - 2 * (Px 22) - (Px 6))).Lines
        if ($lines.Count -le $maxLines) { break }
    }
    $script:MsgLines = $lines
}

# ---- Go -----------------------------------------------------------------------------
$startState = 'info'
if ($script:Mode -eq 'choice') { $startState = 'choice' }
elseif ($script:Mode -eq 'input') { $startState = 'typing' }

Build-Card $startState
Place-Form $true
[System.Windows.Forms.Application]::Run($form)
# Keep asynchronous confirmation audio alive after the card has disappeared.
if ($script:ConfirmUntil) {
    $left = ($script:ConfirmUntil - [DateTime]::UtcNow).TotalMilliseconds
    if ($left -gt 0) { Start-Sleep -Milliseconds ([int]$left) }
}
