param([ValidateSet('Start','Stop')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
try {
    & (Join-Path $PSScriptRoot ($Action+'-Bridge.ps1'))
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'FastVideo Bridge - '+$Action,'OK','Error') | Out-Null
    exit 1
}
