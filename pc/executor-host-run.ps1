# executor-host-run.ps1 - start the executor host (target of the scheduled
# task "executor-host", launched through launcher.exe: no console window).
# The python interpreter can be pinned in muselink.json -> executor.python.
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:AGENT_DESKTOP_HOME = Get-MLAgentDesktopHome
$py = Get-MLSetting 'executor' 'python' 'python'
& $py (Get-MLPath 'executor-host.py')
