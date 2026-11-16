param([string]$ReqFile, [string]$OutFile)
# uia-helper.ps1 - minimal native UIA adapter (Phase 2, 2026-11-08).
# Runs inside the interactive desktop session via the resident executor.
# Elements are re-located from semantic properties on EVERY call;
# RuntimeId is used only as an identity check within a single call.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$script:sw = [System.Diagnostics.Stopwatch]::StartNew()
$script:timeoutMs = 15000

function Check-Timeout {
  if ($script:sw.ElapsedMilliseconds -gt $script:timeoutMs) { throw 'UIA_TIMEOUT_IN_HELPER' }
}

function Short-Type($ct) { return ($ct.ProgrammaticName -replace '^ControlType\.', '') }

function Get-Descriptor($el) {
  $c = $el.Current
  $r = $c.BoundingRectangle
  $b = $null
  if (-not $r.IsEmpty -and $r.Width -gt 0) {
    $b = @{ x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
  }
  $pats = @($el.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName -replace 'PatternIdentifiers\.Pattern$', '' })
  $anc = @()
  $winTitle = ''
  try {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $p = $walker.GetParent($el)
    $guard = 0
    while ($null -ne $p -and $guard -lt 40) {
      $guard++
      $pn = $p.Current.Name
      if ($pn) {
        $winTitle = $pn
        if ($anc.Count -lt 6) { $anc += $pn.Substring(0, [Math]::Min(48, $pn.Length)) }
      }
      $p = $walker.GetParent($p)
    }
  } catch {}
  return @{
    name = $c.Name
    controlType = (Short-Type $c.ControlType)
    className = $c.ClassName
    automationId = $c.AutomationId
    bounds = $b
    isEnabled = [bool]$c.IsEnabled
    isKeyboardFocusable = [bool]$c.IsKeyboardFocusable
    hasKeyboardFocus = [bool]$c.HasKeyboardFocus
    isOffscreen = [bool]$c.IsOffscreen
    patterns = $pats
    runtimeId = ($el.GetRuntimeId() -join '.')
    ancestors = $anc
    windowTitle = $winTitle
  }
}

function Locate($root, $q) {
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                       [System.Windows.Automation.Condition]::TrueCondition)
  $hits = @()
  $i = 0
  foreach ($el in $all) {
    $i++
    if ($i % 200 -eq 0) { Check-Timeout }
    # Per-element guard: live trees churn (views switch, items virtualize);
    # a dead element or a null Name/ClassName must skip, never abort the scan.
    $c = $null
    try { $c = $el.Current } catch { continue }
    if ($null -eq $c) { continue }
    if ($q.name) {
      if (-not $c.Name) { continue }
      if ($q.exact) {
        if (-not $c.Name.Equals([string]$q.name, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
      } else {
        if ($c.Name.IndexOf([string]$q.name, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
      }
    }
    if ($q.controlType) {
      if ((Short-Type $c.ControlType) -ne [string]$q.controlType) { continue }
    }
    if ($q.classContains) {
      if (-not $c.ClassName -or $c.ClassName.IndexOf([string]$q.classContains, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
    }
    $hits += $el
  }
  return $hits
}

function Get-Roots($app) {
  # Rooting v2 (2026-11-08 cross-app acceptance): a process can own several
  # top-level windows (explorer, UWP frame host), and MainWindowHandle picks
  # the wrong one or none. Search scope = ALL top-level windows owned by the
  # process; if the process owns none (or does not exist), fall back to
  # top-level windows whose title contains the app string.
  $base = $app -replace '\.exe$', ''
  $procs = @(Get-Process -Name $base -ErrorAction SilentlyContinue)
  $rootEl = [System.Windows.Automation.AutomationElement]::RootElement
  $wins = @($rootEl.FindAll([System.Windows.Automation.TreeScope]::Children,
             [System.Windows.Automation.Condition]::TrueCondition))
  $pidSet = @{}
  foreach ($p in $procs) { $pidSet[$p.Id] = $true }
  $roots = @()
  foreach ($w in $wins) {
    if ($pidSet.ContainsKey($w.Current.ProcessId)) { $roots += $w }
  }
  $via = 'process'
  if ($roots.Count -eq 0 -and $app) {
    foreach ($w in $wins) {
      $nm = $w.Current.Name
      if ($nm -and $nm.IndexOf([string]$base, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $roots += $w }
    }
    $via = 'title'
  }
  return @{ procs = $procs; roots = $roots; via = $via }
}

function Locate-All($roots, $q) {
  $hits = @()
  foreach ($r in $roots) { $hits += @(Locate $r $q) }
  return $hits
}

function Test-InRoots($el, $roots) {
  try {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $p = $el
    $guard = 0
    while ($null -ne $walker.GetParent($p) -and $guard -lt 60) { $p = $walker.GetParent($p); $guard++ }
    $h = $p.Current.NativeWindowHandle
    foreach ($r in $roots) { if ($r.Current.NativeWindowHandle -eq $h) { return $true } }
  } catch {}
  return $false
}

$result = $null
try {
  $req = Get-Content -Raw -Encoding UTF8 $ReqFile | ConvertFrom-Json
  if ($req.timeoutSec) { $script:timeoutMs = [int]$req.timeoutSec * 1000 }
  $op = [string]$req.op
  $q = $req.query
  $g = Get-Roots ([string]$req.app)
  if ($g.roots.Count -eq 0) {
    $result = @{ status = 'fail'; error = 'APP_NOT_FOUND'; op = $op }
  } elseif ($op -eq 'focused') {
    $fe = [System.Windows.Automation.AutomationElement]::FocusedElement
    if ($null -eq $fe) {
      $result = @{ status = 'unknown'; op = $op; error = 'NO_FOCUSED_ELEMENT' }
    } else {
      $inside = Test-InRoots $fe $g.roots
      $result = @{ status = 'ok'; op = $op; inApp = $inside; element = (Get-Descriptor $fe) }
    }
  } else {
    $hits = Locate-All $g.roots $q
    Check-Timeout
    $descs = @($hits | ForEach-Object {
      try { Get-Descriptor $_ } catch { @{ name = ''; error = 'ELEMENT_GONE' } }
    })
    if ($op -eq 'locate') {
      $result = @{ status = 'ok'; op = $op; count = $hits.Count; candidates = $descs;
                   rootsVia = $g.via; windows = @($g.roots | ForEach-Object { $_.Current.Name }) }
    } elseif ($hits.Count -eq 0) {
      $result = @{ status = 'fail'; error = 'NOT_FOUND'; op = $op; count = 0 }
    } elseif ($op -eq 'focus') {
      # per-element guard: an element can die between locate and this filter
      $focusable = @($hits | Where-Object { try { $_.Current.IsKeyboardFocusable } catch { $false } })
      if ($focusable.Count -ne 1) {
        $result = @{ status = 'fail'; error = 'AMBIGUOUS_OR_UNFOCUSABLE'; op = $op;
                     count = $hits.Count; focusableCount = $focusable.Count; candidates = $descs }
      } else {
        $el = $focusable[0]
        $rid = ($el.GetRuntimeId() -join '.')
        $el.SetFocus()
        Start-Sleep -Milliseconds 350
        $fe = [System.Windows.Automation.AutomationElement]::FocusedElement
        $feRid = if ($null -ne $fe) { ($fe.GetRuntimeId() -join '.') } else { '' }
        if ($feRid -eq $rid) {
          $result = @{ status = 'ok'; op = $op; element = (Get-Descriptor $el); focusVerified = $true }
        } else {
          $holder = if ($null -ne $fe) { Get-Descriptor $fe } else { $null }
          $result = @{ status = 'unknown'; op = $op; error = 'FOCUS_NOT_CONFIRMED';
                       targetRuntimeId = $rid; focusHolder = $holder }
        }
      }
    } elseif ($op -eq 'invoke') {
      $invokable = @($hits | Where-Object {
        try { ($_.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName }) -match 'InvokePattern' } catch { $false } })
      if ($invokable.Count -ne 1) {
        $result = @{ status = 'fail'; error = 'AMBIGUOUS_OR_NO_INVOKE'; op = $op;
                     count = $hits.Count; invokableCount = $invokable.Count; candidates = $descs }
      } else {
        $el = $invokable[0]
        $pat = $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pat.Invoke()
        Start-Sleep -Milliseconds 300
        $result = @{ status = 'ok'; op = $op; element = (Get-Descriptor $el); invoked = $true }
      }
    } elseif ($op -eq 'read_text') {
      if ($hits.Count -ne 1) {
        $result = @{ status = 'fail'; error = 'AMBIGUOUS'; op = $op; count = $hits.Count; candidates = $descs }
      } else {
        $el = $hits[0]
        $hasText = ($el.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName }) -match 'TextPattern'
        if (-not $hasText) {
          $result = @{ status = 'fail'; error = 'NO_TEXTPATTERN'; op = $op; element = (Get-Descriptor $el) }
        } else {
          $tp = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
          $txt = $tp.DocumentRange.GetText(4096)
          if ($null -eq $txt) { $txt = '' }
          $result = @{ status = 'ok'; op = $op; text = $txt; length = $txt.Length; element = (Get-Descriptor $el) }
        }
      }
    } else {
      $result = @{ status = 'fail'; error = "UNKNOWN_OP $op"; op = $op }
    }
  }
} catch {
  $msg = $_.Exception.Message
  $st = if ($msg -match 'TIMEOUT') { 'unknown' } else { 'fail' }
  $result = @{ status = $st; error = "HELPER_ERROR $msg" }
}
$result | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $OutFile
