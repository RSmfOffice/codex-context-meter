$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonExe = (Get-Command python.exe -ErrorAction Stop).Source
$localPython = Get-ChildItem `
    -Path (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-*\python.exe') `
    -File `
    -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($localPython) {
    $pythonExe = $localPython.FullName
}
Push-Location $root
try {
    & $pythonExe -m py_compile `
        codex_context_meter\__init__.py `
        codex_context_meter\__main__.py `
        codex_context_meter\active_thread.py `
        codex_context_meter\handoff.py `
        codex_context_meter\session_stats.py `
        codex_context_meter\widget.py `
        codex_context_meter\window_follower.py `
        tests\test_codex_context_meter.py
    if ($LASTEXITCODE -ne 0) { throw 'Python compile check failed.' }

    & $pythonExe -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }

    node --check 'codex-context-meter-plugin\runtime\mcp.mjs'
    if ($LASTEXITCODE -ne 0) { throw 'Node MCP syntax check failed.' }
    $previousTestPython = $env:CONTEXT_METER_TEST_PYTHON
    $env:CONTEXT_METER_TEST_PYTHON = $pythonExe
    try {
        node 'tests\test_mcp_handoff.mjs'
    }
    finally {
        if ($null -eq $previousTestPython) {
            Remove-Item Env:CONTEXT_METER_TEST_PYTHON -ErrorAction SilentlyContinue
        }
        else {
            $env:CONTEXT_METER_TEST_PYTHON = $previousTestPython
        }
    }
    if ($LASTEXITCODE -ne 0) { throw 'MCP handoff integration test failed.' }

    $powerShellFiles = @(
        'codex_context_meter\active_thread.ps1',
        'codex-context-meter-plugin\scripts\install.ps1',
        'codex-context-meter-plugin\scripts\stop.ps1',
        'codex-context-meter-plugin\scripts\supervisor.ps1',
        'codex-context-meter-plugin\scripts\sync-runtime.ps1',
        'codex-context-meter-plugin\scripts\uninstall.ps1',
        'scripts\build-release.ps1',
        'scripts\check.ps1'
    )
    foreach ($file in $powerShellFiles) {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile(
            (Resolve-Path $file).Path,
            [ref]$tokens,
            [ref]$errors
        )
        if ($errors.Count -gt 0) {
            throw "PowerShell syntax check failed: $file"
        }
    }

    $version = (Get-Content -LiteralPath 'VERSION' -Raw).Trim()
    $plugin = Get-Content -LiteralPath 'codex-context-meter-plugin\.codex-plugin\plugin.json' -Raw -Encoding utf8 |
        ConvertFrom-Json
    if ($plugin.version -ne $version) {
        throw "Plugin version mismatch: $($plugin.version) != $version"
    }

    $mcpSource = Get-Content -LiteralPath 'codex-context-meter-plugin\runtime\mcp.mjs' -Raw
    if ($mcpSource -notmatch "const VERSION = '$([regex]::Escape($version))';") {
        throw 'MCP version does not match VERSION.'
    }

    $mirroredFiles = @(
        '__init__.py',
        '__main__.py',
        'active_thread.ps1',
        'active_thread.py',
        'handoff.py',
        'session_stats.py',
        'widget.py',
        'window_follower.py'
    )
    foreach ($name in $mirroredFiles) {
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path 'codex_context_meter' $name)).Hash
        $pluginHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path 'codex-context-meter-plugin\app\codex_context_meter' $name)).Hash
        if ($sourceHash -ne $pluginHash) {
            throw "Plugin runtime is out of sync: $name"
        }
    }

    @{
        ok = $true
        version = $version
        pythonTests = 10
    } | ConvertTo-Json
}
finally {
    Pop-Location
}
