$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path $PSScriptRoot -Parent
$whipSettings = Get-Content -LiteralPath (Join-Path $bridgeRoot 'fastvideo.settings.json') -Raw | ConvertFrom-Json
if ($whipSettings.http_port -eq 8879 -or $whipSettings.gateway_port -eq 8880 -or $whipSettings.bridge_port -eq 9999) { throw 'Refusing to touch original FastVideo ports.' }
try { Invoke-RestMethod "http://127.0.0.1:$($whipSettings.http_port)/api/whip" -Method Delete -TimeoutSec 8 | Out-Null } catch { }
try { Invoke-RestMethod "http://127.0.0.1:$($whipSettings.http_port)/api/srt" -Method Delete -TimeoutSec 8 | Out-Null } catch { }
& (Join-Path $PSScriptRoot 'Stop-Processes.ps1')
