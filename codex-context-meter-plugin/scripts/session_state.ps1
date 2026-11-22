function Test-WindowsShuttingDown {
    if (-not ('CodexSessionState' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class CodexSessionState {
    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);

    public static bool IsShuttingDown() {
        return GetSystemMetrics(0x2000) != 0;
    }
}
'@
    }

    try {
        return [CodexSessionState]::IsShuttingDown()
    }
    catch {
        return $false
    }
}
