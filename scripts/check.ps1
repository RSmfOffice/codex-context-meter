$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $root
try {
    python -m py_compile `
        codex_context_meter\__init__.py `
        codex_context_meter\__main__.py `
        codex_context_meter\active_thread.py `
        codex_context_meter\session_stats.py `
        codex_context_meter\widget.py `
        codex_context_meter\window_follower.py `
        tests\test_codex_context_meter.py
    if ($LASTEXITCODE -ne 0) { throw 'Python compile check failed.' }

    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }

    node --check 'codex-context-meter-plugin\runtime\mcp.mjs'
    if ($LASTEXITCODE -ne 0) { throw 'Node MCP syntax check failed.' }

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
