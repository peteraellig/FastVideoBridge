param(
    [ValidateRange(1024,65535)][int]$ReceivePort = 56208,
    [ValidateRange(1024,65535)][int]$ControlPort = 18879,
    [ValidateSet('Sender','Receiver','Both')][string]$Role = 'Receiver',
    [ValidateSet('LAN','Internet')][string]$Scope = 'LAN',
    [string]$ProgramPath = '',
    [switch]$LegacyReceiver,
    [switch]$NdiOutput,
    [switch]$Elevated
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    if (-not $ProgramPath) {
        $bridgeRoot = Split-Path $PSScriptRoot -Parent
        $candidates = @((Join-Path $bridgeRoot '.runtime\python.exe'), 'C:\FastVideoBridge\.runtime\python.exe')
        $ProgramPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $ProgramPath) {
            $picker = New-Object System.Windows.Forms.OpenFileDialog
            $picker.Title = 'Select the Python executable used by FastVideo Bridge on THIS computer'
            $picker.Filter = 'Python executable (python.exe)|python.exe'
            if ($picker.ShowDialog() -ne 'OK') { exit 0 }
            $ProgramPath = $picker.FileName
        }
    }
    if (-not (Test-Path -LiteralPath $ProgramPath -PathType Leaf)) { throw 'Bridge Python executable not found.' }
    $ProgramPath = (Resolve-Path -LiteralPath $ProgramPath).Path
    if ($ProgramPath.Contains('"') -or $PSCommandPath.Contains('"')) { throw 'Invalid path.' }
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        $arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -ReceivePort {1} -ControlPort {2} -Role {3} -Scope {4} -ProgramPath "{5}" -Elevated' -f $PSCommandPath,$ReceivePort,$ControlPort,$Role,$Scope,$ProgramPath
        if ($NdiOutput) { $arguments += " -NdiOutput" }
        if ($LegacyReceiver) { $arguments += ' -LegacyReceiver' }
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList $arguments
        exit 0
    }
    $rules = @()
    if ($Role -in @('Receiver','Both')) {
        if ($LegacyReceiver) {
            $rules += @{ Name='Receiver-TCP'; Protocol='TCP'; Port="$ControlPort" }
            $rules += @{ Name='Receiver-UDP'; Protocol='UDP'; Port='Any' }
        } else {
            $rules += @{ Name='Receiver-TCP'; Protocol='TCP'; Port="$ReceivePort" }
            $rules += @{ Name='Receiver-UDP'; Protocol='UDP'; Port="$ReceivePort" }
        }
    }
    if ($Role -in @('Sender','Both')) { $rules += @{ Name='Sender-UDP'; Protocol='UDP'; Port='Any' } }
    if ($NdiOutput -and $Role -in @('Receiver','Both')) {
        $rules += @{ Name='NDI-LAN-TCP'; Protocol='TCP'; Port='Any'; LocalOnly=$true }
        $rules += @{ Name='NDI-LAN-UDP'; Protocol='UDP'; Port='Any'; LocalOnly=$true }
    }
    $remote = if ($Scope -eq 'LAN') { 'LocalSubnet' } else { 'Any' }
    $profile = if ($Scope -eq 'LAN') { @('Private','Domain') } else { @('Private','Domain','Public') }
    $details = ($rules | ForEach-Object { "$($_.Protocol) local port $($_.Port) $($_.Name)" }) -join "`r`n"
    $message = "Configure Windows Firewall on $env:COMPUTERNAME?`r`n`r`nRole: $Role`r`nProgram: $ProgramPath`r`nRemote addresses: $remote`r`nProfiles: $($profile -join ', ')`r`n$details`r`n`r`nOnly Bridge inbound allow rules are added/updated. Firewall stays enabled. NDI-LAN rules, if listed, allow only this program from LocalSubnet on Private/Domain profiles. No router or SRT listener rules are changed. Existing blocking rules are not removed."
    if ([System.Windows.Forms.MessageBox]::Show($message,'FastVideo Bridge - Confirm firewall rules','YesNo','Question') -ne 'Yes') { exit 0 }
    # Stable installation-specific names avoid duplicates, while keeping other installations separate.
    $hash = [Security.Cryptography.SHA256]::Create()
    $tag = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($ProgramPath.ToLowerInvariant())))).Replace('-','').Substring(0,12)
    foreach ($rule in $rules) {
        $ruleRemote = if ($rule.LocalOnly) { 'LocalSubnet' } else { $remote }
        $ruleProfile = if ($rule.LocalOnly) { @('Private','Domain') } else { $profile }
        $name = "FastVideoBridge-$tag-$($rule.Name)"
        $existing = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
        if ($existing) {
            $existing | Set-NetFirewallRule -Enabled True -Action Allow -Direction Inbound -Profile $ruleProfile
            $existing | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program $ProgramPath
            $existing | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol $rule.Protocol -LocalPort $rule.Port
            $existing | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $ruleRemote
        } else {
            New-NetFirewallRule -Name $name -DisplayName "FastVideo Bridge $($rule.Name) [$tag]" -Group 'FastVideo Bridge' -Program $ProgramPath -Direction Inbound -Action Allow -Enabled True -Protocol $rule.Protocol -LocalPort $rule.Port -RemoteAddress $ruleRemote -Profile $ruleProfile | Out-Null
        }
    }
    [System.Windows.Forms.MessageBox]::Show("Bridge rules configured on $env:COMPUTERNAME.`r`nRe-enable Windows Firewall if it was switched off, then test again.`r`nLAN rules require a Private or Domain network profile.",'FastVideo Bridge','OK','Information') | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'FastVideo Bridge - Firewall setup failed','OK','Error') | Out-Null
    exit 1
}
