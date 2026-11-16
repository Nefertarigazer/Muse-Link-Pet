# web-bridge.ps1 - one-shot loopback receiver for web-AI completions.
# Listens on http://127.0.0.1:<port>/ for the userscript notifier
# (pc/userscripts/muselink-web-ai-notifier.user.js), delivers the body through
# Complete-MuseWait.ps1 (kind=web_ai_done) and exits. No standing listener.
#
# Authentication (2026-11-12): a random nonce is minted per run.
#   GET  /nonce          -> {"nonce": "..."}  (no CORS header: an ordinary web
#                           page cannot read it; the userscript's privileged
#                           GM_xmlhttpRequest can)
#   POST /done/<nonce>   -> accepted only with the right nonce, an allowed or
#                           absent Origin, and a JSON body {"text": "...", ...}
# Anything else gets 403/404 and is ignored, so a random page in the user's
# browser can no longer push text into Muse's context.
param(
    [int]$Port = 0,
    [int]$MaxWaitSeconds = 1800,
    [Parameter(Mandatory=$true)][string]$ReqId
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'muselink-common.ps1')
if ($Port -le 0) { $Port = [int](Get-MLSetting 'web_bridge' 'port' 18923) }
$allowed = @(Get-MLSetting 'web_bridge' 'allowed_origins' @('https://chatgpt.com', 'https://gemini.google.com', 'https://claude.ai'))
$maxBody = 1048576
$completer = Get-MLPath 'Complete-MuseWait.ps1'
$nonce = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')

function Send-Json($res, [int]$code, [string]$json) {
    $buf = [System.Text.Encoding]::UTF8.GetBytes($json)
    $res.StatusCode = $code
    $res.ContentType = 'application/json'
    $res.ContentLength64 = $buf.Length
    $res.OutputStream.Write($buf, 0, $buf.Length)
    $res.Close()
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
try {
    $listener.Start()
} catch {
    Write-MLUtf8 (Get-MLPath 'web-bridge-error.txt') ("{0} bind failed: {1}" -f [DateTime]::Now.ToString('s'), $_.Exception.Message)
    exit 0
}

try {
    $deadline = [DateTime]::Now.AddSeconds($MaxWaitSeconds)
    $body = $null
    while ([DateTime]::Now -lt $deadline -and $null -eq $body) {
        $async = $listener.BeginGetContext($null, $null)
        $remain = [int](($deadline - [DateTime]::Now).TotalMilliseconds)
        if ($remain -le 0 -or -not $async.AsyncWaitHandle.WaitOne($remain)) { break }
        $ctx = $listener.EndGetContext($async)
        $req = $ctx.Request
        $res = $ctx.Response
        try {
            $origin = $req.Headers['Origin']
            if ($origin -and ($allowed -notcontains $origin)) { Send-Json $res 403 '{"ok":false}'; continue }
            $path = $req.Url.AbsolutePath
            if ($req.HttpMethod -eq 'GET' -and $path -eq '/nonce') {
                Send-Json $res 200 ('{"nonce":"' + $nonce + '"}')
                continue
            }
            if ($req.HttpMethod -ne 'POST' -or $path -ne ('/done/' + $nonce)) { Send-Json $res 404 '{"ok":false}'; continue }
            if ($req.ContentLength64 -gt $maxBody) { Send-Json $res 413 '{"ok":false}'; continue }
            $reader = New-Object System.IO.StreamReader($req.InputStream, [System.Text.Encoding]::UTF8)
            $raw = $reader.ReadToEnd()
            $reader.Close()
            $obj = $null
            try { $obj = $raw | ConvertFrom-Json } catch { $obj = $null }
            if ($null -eq $obj -or -not ($obj.PSObject.Properties.Name -contains 'text')) { Send-Json $res 400 '{"ok":false}'; continue }
            $body = $raw
            Send-Json $res 200 '{"ok":true}'
        } catch {
            try { $res.Abort() } catch {}
        }
    }
    if ($null -ne $body) {
        & $completer -Kind 'web_ai_done' -Result $body -ReqId $ReqId
    }
}
finally {
    $listener.Stop()
    $listener.Close()
}
