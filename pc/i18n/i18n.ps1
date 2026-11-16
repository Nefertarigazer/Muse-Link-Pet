# PowerShell 5.1 / ASCII. Display language, never regional format settings.
function Resolve-MLLocale([string]$SystemTag, [string[]]$Available) {
    $tags = @{}
    foreach ($tag in $Available) { $tags[$tag.ToLowerInvariant()] = $tag }
    $ui = 'en'; $docs = 'en'
    $pattern = '^(?:[A-Za-z]{2,3}(?:-[A-Za-z]{3}){0,3}|[A-Za-z]{4}|[A-Za-z]{5,8})(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?(?:-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*(?:-[0-9A-WY-Za-wy-z](?:-[A-Za-z0-9]{2,8})+)*(?:-x(?:-[A-Za-z0-9]{1,8})+)?$'
    if ($SystemTag -cmatch $pattern -and $Matches[0].Length -eq $SystemTag.Length) {
        $parts = @($SystemTag.ToLowerInvariant().Split('-'))
        $candidates = @()
        if ($parts[0] -eq 'zh') {
            $docs = 'zh'; $scriptTag = ''; $region = ''
            foreach ($part in $parts | Select-Object -Skip 1) {
                if ($part.Length -eq 1) { break }
                if (-not $scriptTag -and $part -cmatch '^[a-z]{4}$') { $scriptTag = $part }
                if (-not $region -and $part -cmatch '^[a-z]{2}$') { $region = $part }
            }
            if ($scriptTag -eq 'hant' -or ($scriptTag -ne 'hans' -and $region -in @('tw','hk','mo'))) {
                $candidates = @('zh-hant','zh-hans','en')
            } else { $candidates = @('zh-hans','en') }
        } elseif ($parts[0] -eq 'en') {
            $candidates = @('en')
        } else {
            $candidate = @($parts)
            while ($candidate.Count -gt 0) {
                $candidates += $candidate -join '-'
                $candidate = @($candidate | Select-Object -First ($candidate.Count - 1))
                if ($candidate.Count -gt 0 -and $candidate[-1].Length -eq 1) {
                    $candidate = @($candidate | Select-Object -First ($candidate.Count - 1))
                }
            }
            $candidates += 'en'
        }
        foreach ($candidate in $candidates) {
            if ($tags.ContainsKey($candidate)) { $ui = $tags[$candidate]; break }
        }
    }
    $font = 'Segoe UI'
    switch ($ui) {
        'zh-Hans' { $font = 'Microsoft YaHei UI' }
        'zh-Hant' { $font = 'Microsoft JhengHei UI' }
        'ja' { $font = 'Yu Gothic UI' }
        'ko' { $font = 'Malgun Gothic' }
    }
    return @{system=$SystemTag; ui=$ui; docs=$docs; font=$font; dir='ltr'}
}

function Initialize-MLI18n([string]$Directory = $PSScriptRoot) {
    $available = @(Get-ChildItem -LiteralPath $Directory -Filter '*.json' | ForEach-Object { $_.BaseName })
    # The language chosen in the pet menu (pc/i18n/__init__.py set_language), then
    # MUSELINK_LOCALE, then the Windows UI language; same order as Python.
    $inputTag = $null
    try {
        $pref = Join-Path (Split-Path -Parent $Directory) 'ui-language.txt'
        if (Test-Path -LiteralPath $pref) {
            $chosen = ([IO.File]::ReadAllText($pref, [Text.Encoding]::UTF8)).Trim()
            if ($available -contains $chosen) { $inputTag = $chosen }
        }
    } catch {}
    if ($null -eq $inputTag) { $inputTag = [Environment]::GetEnvironmentVariable('MUSELINK_LOCALE') }
    if ($null -eq $inputTag) { $inputTag = [Globalization.CultureInfo]::CurrentUICulture.Name }
    $script:MLLocale = Resolve-MLLocale $inputTag $available
    $script:MLBase = @{}; $script:MLMessages = @{}; $script:MLMissing = @{}
    foreach ($tag in @('en', $script:MLLocale.ui) | Select-Object -Unique) {
        $path = Join-Path $Directory ($tag + '.json')
        $pack = @{}
        if (Test-Path -LiteralPath $path) {
            $obj = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8) | ConvertFrom-Json
            foreach ($property in $obj.PSObject.Properties) { $pack[$property.Name] = $property.Value }
        }
        if ($tag -eq 'en') { $script:MLBase = $pack }
        if ($tag -eq $script:MLLocale.ui) { $script:MLMessages = $pack }
    }
    if ($script:MLMessages.ContainsKey('_meta')) {
        $meta = $script:MLMessages['_meta']
        if ($meta.font) { $script:MLLocale.font = $meta.font }
        if ($meta.dir) { $script:MLLocale.dir = $meta.dir }
    }
}

function Format-MLMessage([string]$Message, [hashtable]$Arguments = @{}) {
    return [regex]::Replace($Message, '\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}', {
        param($match)
        if ($match.Value -eq '{{') { return '{' }
        if ($match.Value -eq '}}') { return '}' }
        $name = $match.Groups[1].Value
        if ($Arguments.ContainsKey($name)) { return [string]$Arguments[$name] }
        return $match.Value
    })
}

function T([string]$Key, [hashtable]$Arguments = @{}) {
    $names = @{brand='MuseLink'; muse='Muse'; codex='Codex'; kiro='Kiro'; workbuddy='WorkBuddy'}
    foreach ($name in $Arguments.Keys) { $names[$name] = $Arguments[$name] }
    if ($script:MLMessages.ContainsKey($Key)) { $message = $script:MLMessages[$Key] }
    else {
        if (-not $script:MLMissing.ContainsKey($Key)) {
            $script:MLMissing[$Key] = $true
            Write-Warning ('Missing i18n key: ' + $Key)
        }
        $message = $Key
        if ($script:MLBase.ContainsKey($Key)) { $message = $script:MLBase[$Key] }
    }
    return Format-MLMessage $message $names
}

function Get-MLPlural([string]$Key, $N, [hashtable]$Arguments = @{}) {
    $category = 'other'
    if ($script:MLLocale.ui.Split('-')[0] -eq 'en' -and $N -eq 1) { $category = 'one' }
    $values = @{} + $Arguments; $values.n = $N
    return T ($Key + '_' + $category) $values
}
