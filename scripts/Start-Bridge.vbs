Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(files.GetParentFolderName(WScript.ScriptFullName))
result = shell.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & folder & "\scripts\Start-Bridge.ps1""", 0, True)

If result <> 0 Then MsgBox "FastVideo Bridge could not start. See the logs in " & folder & "\work", vbExclamation, "FastVideo Bridge"
