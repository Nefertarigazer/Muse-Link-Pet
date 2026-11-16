@echo off
rem muse-call.cmd - shim so any agent can call the bridge from any shell.
rem For text with shell metacharacters or quotes use -TextFile instead of -Text.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0muse-call.ps1" %*
exit /b %ERRORLEVEL%
