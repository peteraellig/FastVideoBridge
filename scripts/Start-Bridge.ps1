param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$whipProject = Split-Path $PSScriptRoot -Parent
$whipSettings = Get-Content -LiteralPath (Join-Path $whipProject 'fastvideo.settings.json') -Raw | ConvertFrom-Json
if ($whipSettings.http_port -eq 8879 -or $whipSettings.gateway_port -eq 8880 -or $whipSettings.bridge_port -eq 9999) { throw 'Experiment must use isolated ports.' }
$whipBase = "http://127.0.0.1:$($whipSettings.http_port)"
$whipExisting = $null
try { $whipExisting = Invoke-RestMethod "$whipBase/api/status" -TimeoutSec 2 } catch { }
if ($whipExisting) {
    $bridgePidPath = Join-Path $whipProject 'work\pid-server.txt'
    $bridgeOwner = $null
    if (Test-Path -LiteralPath $bridgePidPath) {
        $bridgeOwnerId = [int](Get-Content -LiteralPath $bridgePidPath -Raw)
        $bridgeOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$bridgeOwnerId"
    }
    $bridgeExpected = Join-Path $whipProject 'scripts\app.py'
    $bridgeLegacyExpected = Join-Path $whipProject 'app.py'
    $bridgeLegacyCanonical = $bridgeLegacyExpected
    $bridgeCanonical = $bridgeExpected
    if ($bridgeExpected -match '^([A-Za-z]):\\') {
        $bridgeDrive = Get-PSDrive -Name $Matches[1] -ErrorAction SilentlyContinue
        if ($bridgeDrive.DisplayRoot) { $bridgeCanonical = Join-Path $bridgeDrive.DisplayRoot $bridgeExpected.Substring(3); $bridgeLegacyCanonical = Join-Path $bridgeDrive.DisplayRoot $bridgeLegacyExpected.Substring(3) }
    }
    if (-not $bridgeOwner -or (-not $bridgeOwner.CommandLine.Contains($bridgeExpected) -and -not $bridgeOwner.CommandLine.Contains($bridgeCanonical) -and -not $bridgeOwner.CommandLine.Contains($bridgeLegacyExpected) -and -not $bridgeOwner.CommandLine.Contains($bridgeLegacyCanonical))) {
        throw "Port $($whipSettings.http_port) is already used by another installation. Stop that installation first."
    }
}
if (-not $whipExisting) {
    $whipPython = Join-Path $whipProject '.runtime\python.exe'
    if (-not (Test-Path -LiteralPath $whipPython)) { $whipPython = Join-Path $whipProject '.venv\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $whipPython)) { throw 'Bridge Python runtime is missing. Repair or reinstall FastVideo Bridge.' }
    $whipWork = Join-Path $whipProject 'work'
    New-Item -ItemType Directory -Path $whipWork -Force | Out-Null
    Remove-Item -LiteralPath (Join-Path $whipWork 'stop-request') -ErrorAction SilentlyContinue
    $whipService = Join-Path $whipProject 'fastvideo\service.py'
    Start-Process -FilePath $whipPython -ArgumentList ('"{0}"' -f $whipService) -WorkingDirectory $whipProject -WindowStyle Hidden -RedirectStandardOutput (Join-Path $whipWork 'supervisor.stdout.log') -RedirectStandardError (Join-Path $whipWork 'supervisor.stderr.log')
    for ($whipAttempt=0; $whipAttempt -lt 40; $whipAttempt++) {
        Start-Sleep -Milliseconds 500
        try { $whipExisting = Invoke-RestMethod "$whipBase/api/status" -TimeoutSec 2; break } catch { }
    }
    if (-not $whipExisting) { throw 'WHIP sender did not start; see work logs.' }
}
# 'FastVideo LAN 0.1' is how Bridges before 0.0.4.0 identify themselves.
if ($whipExisting.application -notin 'FastVideo Bridge', 'FastVideo LAN 0.1') { throw 'Test port belongs to another application.' }
if (-not $NoBrowser) { Start-Process "$whipBase/sender" }
Write-Host "FastVideo Bridge: $whipBase/sender"

