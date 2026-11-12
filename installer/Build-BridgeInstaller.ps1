param([string]$CompilerPath='', [string]$PythonExe='C:\Fastvideo\.venv\Scripts\python.exe', [switch]$Lean)
$ErrorActionPreference='Stop'
$bridgeRoot=Split-Path $PSScriptRoot -Parent
if (-not $CompilerPath) {
 $bridgeCandidates=@((Join-Path $PSScriptRoot 'tools\inno\ISCC.exe'), (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'), (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'), (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'))
 $CompilerPath=$bridgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $CompilerPath) { throw 'Install Inno Setup or pass -CompilerPath with the path to ISCC.exe.' }
if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Pass -PythonExe with a Python 3.12 environment containing the pinned Bridge dependencies.' }
$bridgeStage=Join-Path ([System.IO.Path]::GetTempPath()) ('FastVideoBridge-build-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8))
$bridgePackages=& $PythonExe -c 'import pathlib,aiortc;print(pathlib.Path(aiortc.__file__).parents[1])'
& $PythonExe (Join-Path $PSScriptRoot 'prepare_payload.py') --stage $bridgeStage --packages $bridgePackages $(if ($Lean) { '--lean' })
if ($LASTEXITCODE -ne 0) { throw 'Payload preparation failed.' }
$bridgeLog=Join-Path $PSScriptRoot 'build.log'
& $CompilerPath '/Q' ('/DPayloadDir='+$bridgeStage) $(if ($Lean) { '/DLean=1' }) (Join-Path $PSScriptRoot 'FastVideoBridge.iss') *> $bridgeLog
if ($LASTEXITCODE -ne 0) { throw "Installer build failed; see $bridgeLog" }
$bridgeSetup=Join-Path $bridgeRoot ('dist\FastVideoBridge-Setup-0.0.5.14'+$(if ($Lean) { '-lean' })+'.exe')
$bridgeReport=Join-Path $PSScriptRoot ('smoke-report-0.0.5.14'+$(if ($Lean) { '-lean' })+'.json')
& $PythonExe (Join-Path $PSScriptRoot 'verify_installer.py') --setup $bridgeSetup --report $bridgeReport
if ($LASTEXITCODE -ne 0) { throw "Built installer failed installed-runtime verification; see $bridgeReport" }
$bridgeHash=(Get-FileHash -LiteralPath $bridgeSetup -Algorithm SHA256).Hash
$bridgeHash+'  '+(Split-Path $bridgeSetup -Leaf) | Set-Content -LiteralPath ($bridgeSetup+'.sha256')
Write-Host "Built: $bridgeSetup"
