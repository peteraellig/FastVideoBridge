$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$goCommand = Get-Command go -ErrorAction SilentlyContinue
$goFile = if ($goCommand) { $goCommand.Source } else { Join-Path $PSScriptRoot '..\work\go-runtime\go\bin\go.exe' }
if (-not (Test-Path -LiteralPath $goFile)) { throw 'Go SDK von go.dev installieren. Zum normalen Betrieb wird nur die mitgelieferte EXE benötigt.' }
& $goFile test ./...
if ($LASTEXITCODE -ne 0) { throw 'Go-Tests fehlgeschlagen.' }
& $goFile build -trimpath -o fastvideo-gateway.exe .
if ($LASTEXITCODE -ne 0) { throw 'Go-Build fehlgeschlagen. Laufenden Gateway vor dem Build stoppen.' }
