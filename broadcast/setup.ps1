$ErrorActionPreference = 'Stop'
$broadcastLocal = Join-Path $PSScriptRoot 'local'
$obsDirectory = Join-Path $broadcastLocal 'obs-studio'
New-Item -ItemType Directory -Path $broadcastLocal -Force | Out-Null

function Get-ReleaseArchive($Repository, $Tag, $AssetName, $Destination) {
    $release = Invoke-RestMethod "https://api.github.com/repos/$Repository/releases/tags/$Tag"
    $asset = $release.assets | Where-Object { $_.name -eq $AssetName }
    if (-not $asset -or $asset.digest -notmatch '^sha256:[0-9a-f]{64}$') {
        throw "No SHA-256 release metadata for $AssetName"
    }
    $archive = Join-Path $broadcastLocal $AssetName
    if (-not (Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive
    }
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ("sha256:$actual" -ne $asset.digest) { throw "Checksum mismatch for $AssetName" }
    Expand-Archive -LiteralPath $archive -DestinationPath $Destination -Force
    Write-Host "Verified and extracted $AssetName"
}

Get-ReleaseArchive 'obsproject/obs-studio' '32.2.2' 'OBS-Studio-32.2.2-Windows-x64.zip' $obsDirectory
Get-ReleaseArchive 'sorayuki/obs-multi-rtmp' '0.7.4.3' 'obs-multi-rtmp-0.7.4.0-windows-x64.zip' (Join-Path $broadcastLocal 'multi-rtmp')
Copy-Item -Path (Join-Path $broadcastLocal 'multi-rtmp\obs-plugins\*') -Destination (Join-Path $obsDirectory 'obs-plugins') -Recurse -Force
Copy-Item -Path (Join-Path $broadcastLocal 'multi-rtmp\data\obs-plugins\*') -Destination (Join-Path $obsDirectory 'data\obs-plugins') -Recurse -Force
New-Item -ItemType File -Path (Join-Path $obsDirectory 'portable_mode.txt') -Force | Out-Null
Write-Host "Portable OBS: $obsDirectory"
