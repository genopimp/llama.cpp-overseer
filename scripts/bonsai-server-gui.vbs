' Silent launcher for the Bonsai 2 server controller GUI.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = dir & "\bonsai-server-gui.ps1"
cmd = "powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File """ & ps1 & """"
sh.Run cmd, 0, False
