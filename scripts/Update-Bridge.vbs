Option Explicit
Dim bridgeShell, bridgeFS, bridgeScript, bridgeResult
Set bridgeShell = CreateObject("WScript.Shell")
Set bridgeFS = CreateObject("Scripting.FileSystemObject")
bridgeScript = bridgeFS.BuildPath(bridgeFS.GetParentFolderName(WScript.ScriptFullName), "Update-Bridge.ps1")
bridgeResult = bridgeShell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & bridgeScript & """", 0, True)
