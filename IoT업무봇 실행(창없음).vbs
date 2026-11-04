' Bot Hub launcher for Windows without a console window.
' Output goes to runs\hub.log. Stop with the stop .bat in this folder.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
If Not fso.FolderExists(dir & "\runs") Then fso.CreateFolder(dir & "\runs")
sh.Run "cmd /c chcp 65001 >nul & (where py >nul 2>nul && py -3 -I hub.py || python -I hub.py) > runs\hub.log 2>&1", 0, False
