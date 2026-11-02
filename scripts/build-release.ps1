param(
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (!$Version) {
    $Version = (Get-Content -LiteralPath (Join-Path $root 'VERSION') -Raw).Trim()
}
$releaseRoot = Join-Path $root 'dist'
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$temp = [IO.Path]::GetFullPath(
    (Join-Path $tempRoot ('codex-context-meter-build-' + [Guid]::NewGuid().ToString('N')))
)
if (!$temp.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unsafe temporary path.'
}
$bundle = Join-Path $temp ("codex-context-meter-v$Version")
$zipPath = Join-Path $releaseRoot ("codex-context-meter-v$Version.zip")
$checksumPath = Join-Path $releaseRoot ("codex-context-meter-v$Version.sha256")

function Copy-Tree {
    param(
        [string]$Source,
        [string]$Destination
    )
    $sourceRoot = [IO.Path]::GetFullPath($Source)
    $sourceUri = [Uri]($sourceRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar)
    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -Recurse -File -Force) {
        $fileUri = [Uri]$file.FullName
        $relative = [Uri]::UnescapeDataString(
            $sourceUri.MakeRelativeUri($fileUri).ToString()
        ).Replace('/', [IO.Path]::DirectorySeparatorChar)
        if ($relative -match '(^|\\)(\.git|__pycache__)(\\|$)') {
            continue
        }
        if ($file.Name -eq 'runtime-pythonw.txt') {
            continue
        }
        $target = Join-Path $Destination $relative
        New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target)) |
            Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
try {
    Copy-Tree `
        -Source (Join-Path $root 'codex_context_meter') `
        -Destination (Join-Path $bundle 'codex_context_meter')
    Copy-Tree `
        -Source (Join-Path $root 'codex-context-meter-plugin') `
        -Destination (Join-Path $bundle 'codex-context-meter-plugin')

    $files = @(
        'codex_context_meter.pyw',
        'start-codex-context-meter.cmd',
        'README.md',
        'CODEX_CONTEXT_METER.md',
        'CHANGELOG.md',
        'LICENSE',
        'requirements.txt',
        'VERSION'
    )
    foreach ($name in $files) {
        Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $bundle $name) -Force
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $bundle 'docs\images') | Out-Null
    Copy-Item `
        -LiteralPath (Join-Path $root 'docs\images\codex-context-meter.png') `
        -Destination (Join-Path $bundle 'docs\images\codex-context-meter.png') `
        -Force

    if (Test-Path -LiteralPath $zipPath) {
        $resolvedReleaseRoot = [IO.Path]::GetFullPath($releaseRoot)
        $resolvedZip = [IO.Path]::GetFullPath($zipPath)
        if (!$resolvedZip.StartsWith($resolvedReleaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Unsafe archive path.'
        }
        Remove-Item -LiteralPath $resolvedZip -Force
    }
    Compress-Archive -LiteralPath $bundle -DestinationPath $zipPath -CompressionLevel Optimal

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText(
        $checksumPath,
        "$hash  $([IO.Path]::GetFileName($zipPath))`n",
        [Text.UTF8Encoding]::new($false)
    )
    @{
        ok = $true
        version = $Version
        archive = $zipPath
        sha256 = $hash
        checksumFile = $checksumPath
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force
    }
}
