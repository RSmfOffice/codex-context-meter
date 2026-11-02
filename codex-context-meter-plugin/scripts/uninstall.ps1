$ErrorActionPreference = 'Stop'
$taskName = 'Codex Context Meter'
$destination = Join-Path $env:USERPROFILE 'plugins\codex-context-meter'
$codex = Get-Command codex.exe -ErrorAction Stop

Try {
    & (Join-Path $destination 'scripts\stop.ps1') | Out-Null
} Catch {}
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Try {
    & $codex.Source plugin remove 'codex-context-meter@personal' --json
} Catch {}
if (Test-Path -LiteralPath $destination) {
    Move-Item -LiteralPath $destination -Destination ($destination + '.removed-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
}
Write-Output '{"ok":true}'
