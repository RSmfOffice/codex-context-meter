$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dataDir = Join-Path $env:LOCALAPPDATA 'CodexContextMeter'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$app = Join-Path $root 'app\codex_context_meter.pyw'
$log = Join-Path $dataDir 'supervisor.log'
$stateFile = Join-Path $dataDir 'widget-state.json'
$pauseFile = Join-Path $dataDir 'pause-until-host-exit.json'
$mutexName = 'Local\CodexContextMeterPluginSupervisor'
$created = $false
$mutex = [Threading.Mutex]::new($true, $mutexName, [ref]$created)
if (!$created) {
    $mutex.Dispose()
    return
}

function Write-Log([string]$message) {
    try {
        "[{0}] {1}" -f [DateTime]::Now.ToString('s'), $message |
            Add-Content -LiteralPath $log -Encoding utf8
    } catch {}
}

function Get-CodexProcess {
    return Get-Process ChatGPT -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
}

function Get-WidgetProcesses {
    return @(Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -in @('python.exe', 'pythonw.exe') -and
            $_.CommandLine -and
            $_.CommandLine.Contains($app)
        })
}

function Get-WidgetHealth {
    try {
        $state = Get-Content -LiteralPath $stateFile -Raw -Encoding utf8 |
            ConvertFrom-Json
        $widgetPid = [int]$state.pid
        if (!$state.ok -or $widgetPid -le 0) {
            return [pscustomobject]@{ healthy = $false; age = $null; pid = 0 }
        }
        $age = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -
            [long]($state.updatedAt * 1000)
        $process = Get-Process -Id $widgetPid -ErrorAction SilentlyContinue
        $healthy = (
            $null -ne $process -and
            $process.ProcessName -in @('python', 'pythonw') -and
            $age -lt 7000
        )
        return [pscustomobject]@{
            healthy = [bool]$healthy
            age = $age
            pid = $widgetPid
        }
    }
    catch {
        return [pscustomobject]@{ healthy = $false; age = $null; pid = 0 }
    }
}

function Start-Widget([int]$hostPid) {
    $pythonw = Get-Command pythonw.exe -ErrorAction Stop
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $pythonw.Source
    $start.Arguments = '"' + $app + '"'
    $start.WorkingDirectory = $root
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    [void][Diagnostics.Process]::Start($start)
    Write-Log ('started widget for host ' + $hostPid)
}

function Stop-Widgets([bool]$RemovePause) {
    foreach ($widget in Get-WidgetProcesses) {
        Stop-Process -Id $widget.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($RemovePause) {
        Remove-Item -LiteralPath $pauseFile -Force -ErrorAction SilentlyContinue
    }
}

try {
    Write-Log 'supervisor started'
    $lastStartAttempt = [DateTime]::MinValue
    $lastCleanup = [DateTime]::MinValue
    $startupGraceUntil = [DateTime]::UtcNow.AddMilliseconds(800)
    while ($true) {
        $hostProcess = Get-CodexProcess
        $health = Get-WidgetHealth
        $pause = $null
        if (Test-Path -LiteralPath $pauseFile) {
            try {
                $pause = Get-Content -LiteralPath $pauseFile -Raw -Encoding utf8 | ConvertFrom-Json
            } catch {}
        }

        if ($null -eq $hostProcess) {
            if (([DateTime]::UtcNow - $lastCleanup).TotalSeconds -ge 2) {
                Stop-Widgets -RemovePause $true
                $lastCleanup = [DateTime]::UtcNow
            }
            Start-Sleep -Milliseconds 500
            continue
        }

        if ($pause -and [string]$pause.hostPid -eq [string]$hostProcess.Id) {
            if (([DateTime]::UtcNow - $lastCleanup).TotalSeconds -ge 2) {
                Stop-Widgets -RemovePause $false
                $lastCleanup = [DateTime]::UtcNow
            }
            Start-Sleep -Milliseconds 500
            continue
        }
        if ($pause) {
            Remove-Item -LiteralPath $pauseFile -Force -ErrorAction SilentlyContinue
        }

        if (!$health.healthy -and [DateTime]::UtcNow -lt $startupGraceUntil) {
            Start-Sleep -Milliseconds 100
        }
        elseif (!$health.healthy -and ([DateTime]::UtcNow - $lastStartAttempt).TotalSeconds -ge 3) {
            Start-Widget -hostPid $hostProcess.Id
            $lastStartAttempt = [DateTime]::UtcNow
            Start-Sleep -Milliseconds 150
        }
        else {
            Start-Sleep -Milliseconds $(if ($health.healthy) { 500 } else { 150 })
        }
    }
}
catch {
    Write-Log ('fatal: ' + $_.Exception.Message)
}
finally {
    $mutex.Dispose()
}
