param([string]$Source = 'Z:\FastvideoBridge', [string]$Destination = 'C:\FastVideoBridge')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$updateBackup = $null
$updateStopped = $false
function Show-BridgeUpdate([string]$text,[string]$icon='Information') {
    [System.Windows.Forms.MessageBox]::Show($text,'FastVideo Bridge update','OK',$icon) | Out-Null
}
function Restore-BridgeStreams {
    & (Join-Path $updateTarget 'scripts\Start-Bridge.ps1') -NoBrowser | Out-Null
    if ($updateState -and $updateState.running) {
        Invoke-RestMethod "$updateBase/api/start" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 45 | Out-Null
    }
    foreach ($kind in @('receive','whip','srt')) {
        if ($updateOutputs[$kind] -and $updateOutputs[$kind].enabled) {
            $body=$updateForms.$kind
            if ($kind -eq 'receive' -and -not $body.PSObject.Properties['listen_port']) {
                $body | Add-Member -NotePropertyName listen_port -NotePropertyValue 0
                $body | Add-Member -NotePropertyName public_ip -NotePropertyValue '' -Force
            }
            Invoke-RestMethod "$updateBase/api/$kind" -Method Post -ContentType 'application/json' -Body ($body | ConvertTo-Json -Depth 12 -Compress) -TimeoutSec 45 | Out-Null
        }
    }
}
try {
    $updateSource=(Resolve-Path -LiteralPath $Source).Path.TrimEnd('\')
    $updateTarget=(Resolve-Path -LiteralPath $Destination).Path.TrimEnd('\')
    if ($updateTarget -ieq $updateSource -or (Split-Path $updateTarget -Leaf) -ieq 'Fastvideo') { throw 'Select a separate FastVideo Bridge installation. Original FastVideo will not be updated.' }
    foreach ($rel in @('scripts\app.py','static\bridge.html','fastvideo\whip_receive.py','scripts\Start-Bridge.ps1','scripts\Stop-Bridge.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $updateSource $rel))) { throw "Incomplete update source: $rel" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $updateTarget '.runtime\python.exe'))) { throw 'Install FastVideo Bridge first. This updater does not install Python or drivers.' }
    $config=Get-Content -LiteralPath (Join-Path $updateTarget 'fastvideo.settings.json') -Raw | ConvertFrom-Json
    $updateBase="http://127.0.0.1:$($config.http_port)"
    $updateState=$null; $updateForms=$null; $updateOutputs=@{}
    try { $updateState=Invoke-RestMethod "$updateBase/api/status" -TimeoutSec 3 } catch { }
    if ($updateState) {
        $ownerId=[int](Get-Content -LiteralPath (Join-Path $updateTarget 'work\pid-server.txt') -Raw)
        $ownerProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId"
        if (-not $ownerProcess -or (-not $ownerProcess.CommandLine.Contains((Join-Path $updateTarget 'app.py')) -and -not $ownerProcess.CommandLine.Contains((Join-Path $updateTarget 'scripts\app.py')))) { throw 'The control port belongs to another application. No files changed.' }
        # Credentials remain in memory, never in backup files or console output.
        $updateForms=Invoke-RestMethod "$updateBase/api/output-settings" -TimeoutSec 5
        foreach ($kind in @('whip','srt','receive')) { $updateOutputs[$kind]=Invoke-RestMethod "$updateBase/api/$kind" -TimeoutSec 5 }
    }
    if ([System.Windows.Forms.MessageBox]::Show("Update FastVideo Bridge?`r`n`r`nFrom: $updateSource`r`nTo: $updateTarget`r`n`r`nThe Bridge stops briefly. Replaced files are backed up. Settings, keys, users and runtime stay unchanged. Active inputs and outputs will be restarted.",'FastVideo Bridge update','YesNo','Question') -ne 'Yes') { exit 0 }
    $entries=@()
    foreach ($folder in @('fastvideo','static','scripts','documents','images')) {
        foreach ($file in Get-ChildItem -LiteralPath (Join-Path $updateSource $folder) -File -Recurse) {
            $relative=$file.FullName.Substring($updateSource.Length+1)
            if ($relative -match '(^|\\)(__pycache__|dev-legacy)(\\|$)' -or $file.Extension -in @('.pyc','.pyo') -or $file.Name -like 'unins*') { continue }
            $entries += [pscustomobject]@{Path=$relative; Source=$file.FullName}
        }
    }
    foreach ($rel in @('Start-Bridge.bat','Stop-Bridge.bat','gateway\fastvideo-gateway.exe')) {
        if (Test-Path -LiteralPath (Join-Path $updateSource $rel)) { $entries += [pscustomobject]@{Path=$rel;Source=(Join-Path $updateSource $rel)} }
    }
    $updateBackup=Join-Path $updateTarget ('work\update-backups\'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6))
    $staged=Join-Path $updateBackup 'new'
    $previous=Join-Path $updateBackup 'previous'
    foreach ($entry in $entries) {
        $file=Join-Path $staged $entry.Path
        New-Item -ItemType Directory -Path (Split-Path $file -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $entry.Source -Destination $file
        if ((Get-FileHash -LiteralPath $file).Hash -ne (Get-FileHash -LiteralPath $entry.Source).Hash) { throw "Staging verification failed: $($entry.Path)" }
    }
    if ($updateState) {
        $stop=Join-Path $updateTarget 'scripts\Stop-Bridge.ps1'
        if (-not (Test-Path -LiteralPath $stop)) { $stop=Join-Path $updateTarget 'Stop-Whip.ps1' }
        if (-not (Test-Path -LiteralPath $stop)) { throw 'Installed stop helper is missing. No files replaced.' }
        & $stop | Out-Null
        # Ask the Bridge itself and its process. Get-NetTCPConnection -State Listen finds no
        # listening port on some Windows builds, so that check let a running Bridge pass.
        $updateStillRunning=$false
        for ($updateWait=0; $updateWait -lt 10; $updateWait++) {
            $updateStillRunning=[bool](Get-Process -Id $ownerId -ErrorAction SilentlyContinue)
            if (-not $updateStillRunning) {
                try { Invoke-RestMethod "$updateBase/api/status" -TimeoutSec 2 | Out-Null; $updateStillRunning=$true } catch { }
            }
            if (-not $updateStillRunning) { break }
            Start-Sleep -Milliseconds 500
        }
        if ($updateStillRunning) { throw 'Bridge did not stop. No files replaced.' }
        $updateStopped=$true
    }
    $applied=@()
    try {
        foreach ($entry in $entries) {
            $target=Join-Path $updateTarget $entry.Path
            $old=Join-Path $previous $entry.Path
            if (Test-Path -LiteralPath $target) {
                New-Item -ItemType Directory -Path (Split-Path $old -Parent) -Force | Out-Null
                Copy-Item -LiteralPath $target -Destination $old
            }
            New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
            $applied += $entry.Path
            Copy-Item -LiteralPath (Join-Path $staged $entry.Path) -Destination $target -Force
            if ((Get-FileHash -LiteralPath $target).Hash -ne (Get-FileHash -LiteralPath (Join-Path $staged $entry.Path)).Hash) { throw "Update verification failed: $($entry.Path)" }
        }
    } catch {
        foreach ($rel in $applied) {
            $old=Join-Path $previous $rel
            if (Test-Path -LiteralPath $old) { Copy-Item -LiteralPath $old -Destination (Join-Path $updateTarget $rel) -Force }
        }
        throw
    }
    # Retire obsolete root launchers into the update backup; user data is untouched.
    $retired=Join-Path $updateBackup 'retired-launchers'
    foreach ($name in @('app.py','Start-Bridge.vbs','Stop-Bridge.vbs','Start-Bridge.cmd','Stop-Bridge.cmd','Start-Whip.ps1','Stop-Whip.ps1','Stop-FastVideo.ps1')) {
        $oldLauncher=Join-Path $updateTarget $name
        if (Test-Path -LiteralPath $oldLauncher) {
            New-Item -ItemType Directory -Path $retired -Force | Out-Null
            Move-Item -LiteralPath $oldLauncher -Destination (Join-Path $retired $name)
        }
    }
    # Move remaining old root documents, keeping their original contents in the backup if replaced.
    foreach ($file in Get-ChildItem -LiteralPath $updateTarget -File) {
        if ($file.Extension -notin @('.md','.txt')) { continue }
        $document=Join-Path (Join-Path $updateTarget 'documents') $file.Name
        if (Test-Path -LiteralPath $document) {
            New-Item -ItemType Directory -Path $retired -Force | Out-Null
            Move-Item -LiteralPath $file.FullName -Destination (Join-Path $retired $file.Name)
        } else {
            New-Item -ItemType Directory -Path (Split-Path $document -Parent) -Force | Out-Null
            Move-Item -LiteralPath $file.FullName -Destination $document
        }
    }
    # Keep the user-requested desktop BAT launchers in sync, without replacing unrelated files.
    $desktop=[Environment]::GetFolderPath('Desktop')
    foreach ($name in @('Start-Bridge.bat','Stop-Bridge.bat')) {
        $desktopFile=Join-Path $desktop $name
        if (-not (Test-Path -LiteralPath $desktopFile) -or (Get-Content -LiteralPath $desktopFile -Raw).Contains('bridgeHome=')) {
            Copy-Item -LiteralPath (Join-Path $updateTarget $name) -Destination $desktopFile -Force
        }
    }
    Restore-BridgeStreams
    Show-BridgeUpdate "Update complete.`r`nBackup: $updateBackup`r`nReload the control page. Configure firewall rules separately in Help."
} catch {
    $message=$_.Exception.Message
    if ($updateBackup) { $message += "`r`nBackup / staged files: $updateBackup" }
    if ($updateStopped) { $message += "`r`nThe Bridge may be stopped. Use Start-Bridge after resolving the error." }
    Show-BridgeUpdate $message 'Error'
    exit 1
}
