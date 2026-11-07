param(
    [string]$Destination,
    [switch]$NoStart,
    [string]$TeamBoardRoot,
    [string]$DashboardUrl = 'http://127.0.0.1:8765',
    [switch]$NoTeamBoardIntegration
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

function Test-TeamBoardRoot([string]$Path) {
    return (
        $Path -and
        (Test-Path -LiteralPath (Join-Path $Path 'src\teamctl.py') -PathType Leaf)
    )
}

function Resolve-TeamBoardRoot {
    $candidates = @(
        $TeamBoardRoot,
        $env:CODEX_TEAM_BOARD_ROOT,
        $env:CODEX_TEAM_ROOT,
        (Join-Path $env:USERPROFILE 'plugins\codex-team-board'),
        (Join-Path $env:USERPROFILE 'CodexTools\codex-team-board'),
        (Join-Path (Split-Path $source -Parent) 'codex-team-board'),
        (Join-Path (Split-Path (Split-Path $source -Parent) -Parent) 'codex-team-board'),
        (Join-Path $env:LOCALAPPDATA 'CodexTeamBoard')
    )
    foreach ($candidate in $candidates) {
        if (!$candidate) { continue }
        $resolved = [IO.Path]::GetFullPath($candidate)
        if (Test-TeamBoardRoot $resolved) {
            return $resolved
        }
    }
    return $null
}

function Write-TeamBoardConfig([string]$Root, [string]$Url) {
    $configPath = Join-Path $dataDir 'config.json'
    $config = @{}
    if (Test-Path -LiteralPath $configPath) {
        try {
            $existing = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 |
                ConvertFrom-Json
            foreach ($property in $existing.PSObject.Properties) {
                $config[$property.Name] = $property.Value
            }
        }
        catch {}
    }
    $config['team_dashboard_url'] = $Url
    $config['team_board_root'] = $Root
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $config |
        ConvertTo-Json -Depth 20 |
        Set-Content -LiteralPath $configPath -Encoding utf8
}

function Set-ManagedAgentInstructions([string]$Path, [string]$Block) {
    $parent = [IO.Path]::GetDirectoryName($Path)
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $content = if (Test-Path -LiteralPath $Path) {
        [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)
    } else {
        ''
    }
    $start = '<!-- CODEX_CONTEXT_METER_AGENTS:START -->'
    $end = '<!-- CODEX_CONTEXT_METER_AGENTS:END -->'
    $wrapped = $start + "`n" + $Block.Trim() + "`n" + $end
    $startIndex = $content.IndexOf($start, [StringComparison]::Ordinal)
    $endIndex = if ($startIndex -ge 0) {
        $content.IndexOf($end, $startIndex, [StringComparison]::Ordinal)
    } else {
        -1
    }
    if ($startIndex -ge 0 -and $endIndex -ge $startIndex) {
        $content = (
            $content.Substring(0, $startIndex) +
            $wrapped +
            $content.Substring($endIndex + $end.Length)
        )
    }
    elseif ($content.Trim()) {
        $content = $content.TrimEnd() + "`n`n" + $wrapped + "`n"
    }
    else {
        $content = "# Global Instructions`n`n" + $wrapped + "`n"
    }
    [IO.File]::WriteAllText($Path, $content, [Text.UTF8Encoding]::new($false))
}

& $python.Source -c "import PIL"
if ($LASTEXITCODE -ne 0) {
    throw 'Pillow is required. Run: python -m pip install -r requirements.txt'
}

& (Join-Path $PSScriptRoot 'sync-runtime.ps1') | Out-Null

$resolvedTeamBoardRoot = $null
if (!$NoTeamBoardIntegration) {
    try {
        $dashboardUri = [Uri]$DashboardUrl
    }
    catch {
        throw "Invalid DashboardUrl: $DashboardUrl"
    }
    if ($dashboardUri.Scheme -ne 'http') {
        throw 'DashboardUrl must use http.'
    }
    $resolvedTeamBoardRoot = Resolve-TeamBoardRoot
    if (!$resolvedTeamBoardRoot) {
        throw (
            'Codex team board was not found. Install RSmfOffice/codex-team-board first, ' +
            'pass -TeamBoardRoot <path>, or use -NoTeamBoardIntegration.'
        )
    }
    [Environment]::SetEnvironmentVariable(
        'CODEX_TEAM_BOARD_ROOT',
        $resolvedTeamBoardRoot,
        'User'
    )
    [Environment]::SetEnvironmentVariable(
        'CODEX_TEAM_DASHBOARD_URL',
        $DashboardUrl,
        'User'
    )
    $env:CODEX_TEAM_BOARD_ROOT = $resolvedTeamBoardRoot
    $env:CODEX_TEAM_DASHBOARD_URL = $DashboardUrl
    Write-TeamBoardConfig -Root $resolvedTeamBoardRoot -Url $DashboardUrl
}

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
    $agentInstructions = [IO.File]::ReadAllText(
        (Join-Path $PSScriptRoot 'handoff-agent-block.md'),
        [Text.Encoding]::UTF8
    )
    Set-ManagedAgentInstructions `
        -Path (Join-Path $env:USERPROFILE '.codex\AGENTS.md') `
        -Block $agentInstructions
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
    teamBoardRoot = $resolvedTeamBoardRoot
    dashboardUrl = $(if ($resolvedTeamBoardRoot) { $DashboardUrl } else { $null })
    teamBoardIntegration = [bool]$resolvedTeamBoardRoot
} | ConvertTo-Json
