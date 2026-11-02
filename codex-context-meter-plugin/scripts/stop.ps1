$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$app = Join-Path $root 'app\codex_context_meter.pyw'
$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and
        $_.CommandLine -and
        $_.CommandLine.Contains($app)
    }
foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
$pauseFile = Join-Path $env:LOCALAPPDATA 'CodexContextMeter\pause-until-host-exit.json'
$hostProcess = Get-Process ChatGPT -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
if ($hostProcess) {
    @{
        hostPid = $hostProcess.Id
        createdAt = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $pauseFile -Encoding utf8
}
Write-Output '{"ok":true}'
