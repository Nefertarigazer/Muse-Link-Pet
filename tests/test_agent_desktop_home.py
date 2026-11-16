"""Windows state-path regressions. Only temporary files and child environments change."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
class AgentDesktopHome(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="muselink-ad-home-")
        self.base = Path(temporary.name).resolve()
        self.assertEqual(self.base.parent, Path(tempfile.gettempdir()).resolve())
        self.addCleanup(temporary.cleanup)
        for relative in ("muselink-common.ps1", "executor-host-run.ps1", "muse-shot.ps1",
                         "install/fix-agent-desktop-owner.ps1", "install/site-config.ps1"):
            destination = self.base / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "pc" / relative, destination)

    def powershell(self, script):
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1')\n"
            + script + "\nexit 0\n"
        )
        encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        environment = dict(os.environ)
        # PS 5.1 must load its own Security module when tests start from PS 7.
        environment.pop("PSModulePath", None)
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_resolution_precedence_and_registry_fallback(self):
        self.powershell(fr"""
. {ps_quote(self.base / 'muselink-common.ps1')}
$env:AGENT_DESKTOP_HOME = 'X:\process-state'
$script:MLConfigCache = [pscustomobject]@{{ executor = [pscustomobject]@{{ agent_desktop_home = 'X:\site-state' }} }}
if ((Get-MLAgentDesktopHome 'X:\explicit-state') -ne 'X:\explicit-state') {{ throw 'explicit path lost' }}
if ((Get-MLAgentDesktopHome) -ne 'X:\site-state') {{ throw 'site path lost' }}
$script:MLConfigCache = [pscustomobject]@{{}}
if ((Get-MLAgentDesktopHome) -ne 'X:\process-state') {{ throw 'process path lost' }}
Remove-Item Env:AGENT_DESKTOP_HOME
$expected = [Environment]::GetEnvironmentVariable('AGENT_DESKTOP_HOME', 'User')
if ([string]::IsNullOrWhiteSpace($expected)) {{ $expected = [Environment]::GetEnvironmentVariable('AGENT_DESKTOP_HOME', 'Machine') }}
if ([string]::IsNullOrWhiteSpace($expected)) {{ $expected = Join-Path $env:USERPROFILE '.agent-desktop' }}
if ((Get-MLAgentDesktopHome) -ne $expected) {{ throw 'registry/default path lost' }}
""")

    def test_site_generation_and_host_child_environment(self):
        state = self.base / "state"
        fake_python = self.base / "fake-python.ps1"
        fake_python.write_text("$env:AGENT_DESKTOP_HOME\n", encoding="ascii")
        site = json.loads((ROOT / "config/muselink.site.example.json").read_text(encoding="utf-8"))
        site["site"].update(pc_host="100.64.0.1", pc_user="testuser", pc_tool_dir=str(self.base))
        site["pc"]["executor"].update(agent_desktop_home=str(state))
        site["site"]["pc_python"] = str(fake_python)
        site_path = self.base / "muselink.site.json"
        site_path.write_text(json.dumps(site), encoding="utf-8")
        result = self.powershell(fr"""
. {ps_quote(self.base / 'install/site-config.ps1')}
$site = Read-MLSite {ps_quote(site_path)}
$targets = @(Get-MLPcTargets {ps_quote(site_path)} $site {ps_quote(self.base)})
foreach ($target in $targets) {{ Write-MLConfigFile $target.Path $target.Object }}
$env:AGENT_DESKTOP_HOME = 'X:\stale-state'
& {ps_quote(self.base / 'executor-host-run.ps1')}
""")
        self.assertEqual(result.stdout.strip(), str(state))
        generated = json.loads((self.base / "muselink.json").read_text(encoding="utf-8"))
        self.assertEqual(generated["executor"]["agent_desktop_home"], str(state))

    def test_owner_report_uses_same_path_and_preserves_permissions(self):
        state = self.base / "state"
        state.mkdir()
        (state / "sample.txt").write_text("sample", encoding="ascii")
        (self.base / "muselink.json").write_text(
            json.dumps({"executor": {"agent_desktop_home": str(state)}}), encoding="utf-8")
        result = self.powershell(fr"""
$path = {ps_quote(state)}
$before = (Get-Acl -LiteralPath $path).Sddl
$env:AGENT_DESKTOP_HOME = 'X:\stale-state'
& {ps_quote(self.base / 'install/fix-agent-desktop-owner.ps1')}
if ((Get-Acl -LiteralPath $path).Sddl -ne $before) {{ throw 'report changed ACL' }}
""")
        self.assertIn("State directory: " + str(state), result.stdout)
        self.assertIn("REPORT ONLY", result.stdout)

    def test_standalone_screenshot_entry_uses_site_home(self):
        state = self.base / "state"
        fake_ad = self.base / "fake-ad.ps1"
        fake_ad.write_text("$env:AGENT_DESKTOP_HOME\n$global:LASTEXITCODE = 7\n", encoding="ascii")
        (self.base / "muselink.json").write_text(json.dumps({"executor": {
            "agent_desktop_home": str(state), "agent_desktop_exe": str(fake_ad),
        }}), encoding="utf-8")
        result = self.powershell(fr"""
$env:AGENT_DESKTOP_HOME = 'X:\stale-state'
& {ps_quote(self.base / 'muse-shot.ps1')} -Mode window -App 'test-window'
if ($LASTEXITCODE -ne 1) {{ throw 'expected stub capture failure' }}
""")
        self.assertIn(str(state), json.loads(result.stdout)["error"])

    def test_failed_acl_backup_prevents_owner_changes(self):
        state = self.base / "state"
        state.mkdir()
        self.powershell(fr"""
$global:Calls = 0
function Get-Acl {{
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetOwner((New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    $sid = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')))
    return $acl
}}
function icacls {{ $global:Calls++; $global:LASTEXITCODE = 9 }}
try {{
    & {ps_quote(self.base / 'install/fix-agent-desktop-owner.ps1')} -StateDir {ps_quote(state)} -Apply
    throw 'expected backup failure'
}} catch {{
    if ($_.Exception.Message -notlike 'ACL backup failed*') {{ throw }}
}}
if ($global:Calls -ne 1) {{ throw 'owner change attempted after failed backup' }}
""")


if __name__ == "__main__":
    unittest.main()
