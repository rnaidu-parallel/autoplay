param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$HarnessArguments
)

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$dotenvPath = Join-Path $repositoryRoot '.env'
if (Test-Path -LiteralPath $dotenvPath) {
    foreach ($dotenvName in @(
        'OPENROUTER_API_KEY', 'TWITCH_CHANNEL', 'TWITCH_CLIENT_ID', 'TWITCH_ACCESS_TOKEN',
        'TWITCH_BROADCASTER_ID', 'TWITCH_USER_ID', 'TWITCH_CLIENT_SECRET', 'TWITCH_REDIRECT_URL',
        'KICK_CLIENT_ID', 'KICK_CLIENT_SECRET', 'KICK_CHANNEL', 'KICK_BROADCASTER_USER_ID', 'KICK_CHATROOM_ID'
    )) {
        if (-not [Environment]::GetEnvironmentVariable($dotenvName, 'Process')) {
            $entry = Get-Content -LiteralPath $dotenvPath |
                Where-Object { $_ -match "^\s*$dotenvName\s*=" } |
                Select-Object -Last 1
            if ($entry) {
                $value = ($entry -split '=', 2)[1].Trim()
                if ($value.Length -ge 2 -and (
                    ($value.StartsWith("'") -and $value.EndsWith("'")) -or
                    ($value.StartsWith('"') -and $value.EndsWith('"'))
                )) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                if ($value) {
                    [Environment]::SetEnvironmentVariable($dotenvName, $value, 'Process')
                }
            }
        }
    }
}

$pythonCandidates = @()
if ($env:AUTOPLAY_PYTHON) {
    $pythonCandidates += $env:AUTOPLAY_PYTHON
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $pythonCandidates += $venvPython
}
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    $pythonCandidates += $bundledPython
}

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $pythonCandidates += $pyLauncher.Source
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $pythonCandidates += $pythonCommand.Source
}

$pythonExecutable = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExecutable) {
    throw 'Python 3.11 or newer is required. Set AUTOPLAY_PYTHON to the Python executable.'
}

$env:PYTHONPATH = $PSScriptRoot
& $pythonExecutable -m autoplay_harness @HarnessArguments
exit $LASTEXITCODE
