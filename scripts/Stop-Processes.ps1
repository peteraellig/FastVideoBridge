$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path $PSScriptRoot -Parent
$settings = Get-Content -LiteralPath (Join-Path $bridgeRoot 'fastvideo.settings.json') -Raw | ConvertFrom-Json
$httpPort = if ($settings.http_port) { [int]$settings.http_port } else { 8879 }
$baseURL = "http://127.0.0.1:$httpPort"
function Get-CanonicalProjectPath([string]$path) {
    if ($path -match '^([A-Za-z]):\\') {
        $drive = Get-PSDrive -Name $Matches[1] -ErrorAction SilentlyContinue
        if ($drive.DisplayRoot) { return (Join-Path $drive.DisplayRoot $path.Substring(3)).TrimEnd('\').ToLowerInvariant() }
    }
    return $path.TrimEnd('\').ToLowerInvariant()
}
try { Invoke-RestMethod "$baseURL/api/stop?preserve=1" -Method Post -TimeoutSec 8 | Out-Null } catch { }
New-Item -ItemType Directory -Path (Join-Path $bridgeRoot 'work') -Force | Out-Null
'1' | Set-Content -LiteralPath (Join-Path $bridgeRoot 'work\stop-request')
$targets = @(
    @{ PidFile='pid-server.txt'; Expected=(Join-Path $bridgeRoot 'scripts\app.py'); LegacyExpected=(Join-Path $bridgeRoot 'app.py'); IsExe=$false },
    @{ PidFile='pid-gateway.txt'; Expected=(Join-Path $bridgeRoot 'gateway\fastvideo-gateway.exe'); IsExe=$true }
)
foreach ($target in $targets) {
    $processFile = Join-Path $bridgeRoot ('work\'+$target.PidFile)
    if (-not (Test-Path -LiteralPath $processFile)) { continue }
    $fastvideoProcessId = [int](Get-Content -LiteralPath $processFile -Raw)
    $fastvideoProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$fastvideoProcessId"
    if ($fastvideoProcess -and (($target.IsExe -and $fastvideoProcess.ExecutablePath) -or (-not $target.IsExe -and $fastvideoProcess.CommandLine))) {
        $matchesProject = if ($target.IsExe) { (Get-CanonicalProjectPath $fastvideoProcess.ExecutablePath) -eq (Get-CanonicalProjectPath $target.Expected) } else { $fastvideoProcess.CommandLine -like "*$($target.Expected)*" -or ($target.LegacyExpected -and ($fastvideoProcess.CommandLine -like "*$($target.LegacyExpected)*" -or $fastvideoProcess.CommandLine.ToLowerInvariant().Contains((Get-CanonicalProjectPath $target.LegacyExpected)))) -or $fastvideoProcess.CommandLine.ToLowerInvariant().Contains((Get-CanonicalProjectPath $target.Expected)) }
        # A stale pid file (e.g. after a reboot) can name an unrelated process: never stop that one,
        # but do not abort either (FastVideo 0.0.4.1 had the same problem).
        if ($matchesProject) { Stop-Process -Id $fastvideoProcessId -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $processFile -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
Write-Host 'FastVideo Bridge stopped.'
