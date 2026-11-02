$ErrorActionPreference = 'Stop'
$pluginRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectRoot = [IO.Path]::GetFullPath((Join-Path $pluginRoot '..'))
$sourcePackage = Join-Path $projectRoot 'codex_context_meter'
$sourceLauncher = Join-Path $projectRoot 'codex_context_meter.pyw'
$destination = Join-Path $pluginRoot 'app'
$packageDestination = Join-Path $destination 'codex_context_meter'

if (!(Test-Path -LiteralPath $sourcePackage) -or !(Test-Path -LiteralPath $sourceLauncher)) {
    throw "Context meter source not found under $projectRoot"
}

New-Item -ItemType Directory -Force -Path $packageDestination | Out-Null
Copy-Item -Path (Join-Path $sourcePackage '*') -Destination $packageDestination -Recurse -Force
Copy-Item -LiteralPath $sourceLauncher -Destination $destination -Force

@{
    ok = $true
    source = $projectRoot
    destination = $destination
} | ConvertTo-Json
