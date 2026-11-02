param(
    [string]$Destination,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (!$Destination) {
    $Destination = Join-Path $env:USERPROFILE 'plugins\codex-context-meter'
}
$Destination = [IO.Path]::GetFullPath($Destination)
$dataDir = Join-Path $env:LOCALAPPDATA 'CodexContextMeter'
$taskName = 'Codex Context Meter'
$codex = Get-Command codex.exe -ErrorAction Stop
$python = Get-Command python.exe -ErrorAction Stop
$powerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$vbs = Join-Path $Destination 'scripts\launch-hidden.vbs'

& $python.Source -c "import PIL"
if ($LASTEXITCODE -ne 0) {
    throw 'Pillow is required. Run: python -m pip install -r requirements.txt'
}

& (Join-Path $PSScriptRoot 'sync-runtime.ps1') | Out-Null

if (Test-Path -LiteralPath $Destination) {
    $backup = Join-Path $dataDir ('plugin-backup-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Copy-Item -LiteralPath $Destination -Destination (Join-Path $backup 'codex-context-meter') -Recurse
}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Copy-Item -Path (Join-Path $source '*') -Destination $Destination -Recurse -Force

$actionArgs = '"' + $vbs + '"'
$action = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\wscript.exe') -Argument $actionArgs -WorkingDirectory $Destination
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval ([TimeSpan]::FromMinutes(1)) -StartWhenAvailable
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$task = New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Trigger $trigger -Description 'Starts the Codex context meter when the user logs in and keeps it following Codex.'
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null

$marketplacePath = Join-Path $env:USERPROFILE '.agents\plugins\marketplace.json'
New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($marketplacePath)) | Out-Null
if (Test-Path -LiteralPath $marketplacePath) {
    $marketplace = Get-Content -LiteralPath $marketplacePath -Raw -Encoding utf8 | ConvertFrom-Json
} else {
    $marketplace = [pscustomobject]@{
        name = 'personal'
        interface = [pscustomobject]@{ displayName = 'Personal' }
        plugins = @()
    }
}
$pluginEntry = [pscustomobject]@{
    name = 'codex-context-meter'
    source = [pscustomobject]@{ source = 'local'; path = './plugins/codex-context-meter' }
    policy = [pscustomobject]@{ installation = 'AVAILABLE'; authentication = 'ON_INSTALL' }
    category = 'Productivity'
}
$plugins = @($marketplace.plugins | Where-Object { $_.name -ne 'codex-context-meter' })
$plugins += $pluginEntry
$marketplace.plugins = $plugins
$marketplaceJson = $marketplace | ConvertTo-Json -Depth 12
[IO.File]::WriteAllText($marketplacePath, $marketplaceJson, [Text.UTF8Encoding]::new($false))

if (!$NoStart) {
    Start-ScheduledTask -TaskName $taskName
}

Push-Location $Destination
try {
    $pluginResult = & $codex.Source plugin add 'codex-context-meter@personal' --json
    if ($LASTEXITCODE -ne 0) {
        throw 'Codex could not install the personal context-meter plugin.'
    }
    $pluginResult
}
finally {
    Pop-Location
}

@{
    ok = $true
    pluginRoot = $Destination
    taskName = $taskName
    dataDir = $dataDir
} | ConvertTo-Json
