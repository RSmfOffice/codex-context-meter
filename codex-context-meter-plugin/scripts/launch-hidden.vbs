Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(scriptDir)
script = fso.BuildPath(scriptDir, "supervisor.ps1")
widget = fso.BuildPath(fso.BuildPath(root, "app"), "codex_context_meter.pyw")
dataDir = fso.BuildPath(shell.ExpandEnvironmentStrings("%LOCALAPPDATA%"), "CodexContextMeter")
If Not fso.FolderExists(dataDir) Then
    fso.CreateFolder(dataDir)
End If
pauseFile = fso.BuildPath(dataDir, "pause-until-host-exit.json")
errorFile = fso.BuildPath(dataDir, "bootstrap.log")
quote = Chr(34)

If Not fso.FileExists(pauseFile) Then
    pythonw = fso.BuildPath(fso.BuildPath(shell.ExpandEnvironmentStrings("%LOCALAPPDATA%"), "Microsoft\WindowsApps"), "pythonw.exe")
    If Not fso.FileExists(pythonw) Then
        pythonw = "pythonw.exe"
    End If
    widgetCommand = "cmd.exe /d /c start " & quote & quote & " /b " & quote & pythonw & quote & " " & quote & widget & quote
    On Error Resume Next
    shell.Run widgetCommand, 0, False
    If Err.Number <> 0 Then
        Set logStream = fso.OpenTextFile(errorFile, 8, True)
        logStream.WriteLine Now & " widget launch failed " & Err.Number & ": " & Err.Description
        logStream.Close
    End If
    On Error GoTo 0
End If

command = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & script & """"
shell.Run command, 0, False
