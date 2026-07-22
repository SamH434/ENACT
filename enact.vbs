' ENACT launcher -> runs the engine and dashboard invisibly.
' this VBScript wrapper is what your desktop shortcut points at. it spawns
' the batch file with WindowStyle 0, which means no visible cmd window.
Set WshShell = CreateObject("WScript.Shell")
' resolve the directory this .vbs lives in so shortcuts work from anywhere
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
' launch the actual worker batch with WindowStyle 0 (hidden), bWaitOnReturn False (async)
WshShell.Run """" & strPath & "\enact-silent.bat""", 0, False