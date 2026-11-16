# PowerShell 5.1 / ASCII: shared resolution and format vectors.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $root 'pc/i18n/i18n.ps1')
$vectors = [IO.File]::ReadAllText((Join-Path $root 'spec/test-vectors/i18n.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
foreach ($row in $vectors.resolve) {
    $actual = Resolve-MLLocale $row.system $row.available
    if ($actual.system -cne $row.system -or $actual.ui -cne $row.ui -or $actual.docs -cne $row.docs) {
        throw ('Resolve mismatch: ' + ($row | ConvertTo-Json -Compress))
    }
}
foreach ($row in $vectors.format.cases) {
    $script:MLLocale = @{ui=$row.tag}
    $script:MLBase = @{}; $script:MLMessages = @{}; $script:MLMissing = @{}
    foreach ($prop in $vectors.format.packs.en.PSObject.Properties) { $script:MLBase[$prop.Name] = $prop.Value }
    foreach ($prop in $vectors.format.packs.($row.tag).PSObject.Properties) { $script:MLMessages[$prop.Name] = $prop.Value }
    $arguments = @{}
    foreach ($prop in $row.args.PSObject.Properties) { $arguments[$prop.Name] = $prop.Value }
    if ($null -ne $row.n) { $actual = Get-MLPlural $row.key $row.n $arguments }
    else { $actual = T $row.key $arguments }
    if ($actual -cne $row.expected) { throw ('Format mismatch: ' + $row.key + ': ' + $actual) }
}
Write-Output ('OK PS: ' + $vectors.resolve.Count + ' resolve, ' + $vectors.format.cases.Count + ' format vectors')
