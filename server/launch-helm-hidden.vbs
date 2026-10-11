' Launches the Helm PC service (helm_server.py) with no window.
Set objShell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
objShell.Run """C:\Python313\pythonw.exe"" """ & scriptDir & "\helm_server.py""", 0, False
