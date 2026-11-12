@echo off
setlocal
set "bridgeHome=%~dp0"
if exist "%bridgeHome%scripts\Invoke-Bridge.ps1" goto launch
set "bridgeHome=C:\FastVideoBridge\"
if exist "%bridgeHome%fastvideo.settings.json" goto localInstall
if exist "%bridgeHome%scripts\Invoke-Bridge.ps1" goto launch
:missing
echo FastVideo Bridge updater required: scripts\Invoke-Bridge.ps1 is missing.
pause
exit /b 1
:localInstall
if not exist "%bridgeHome%scripts\Invoke-Bridge.ps1" goto missing
:launch
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%bridgeHome%scripts\Invoke-Bridge.ps1" -Action Stop
exit /b 0
