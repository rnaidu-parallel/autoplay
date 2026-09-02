param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$HarnessArguments
)

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$dotenvPath = Join-Path $repositoryRoot '.env'
if (-not $env:OPENROUTER_API_KEY -and (Test-Path -LiteralPath $dotenvPath)) {
    $keyEntry = Get-Content -LiteralPath $dotenvPath |
        Where-Object { $_ -match '^\s*OPENROUTER_API_KEY\s*=' } |
        Select-Object -Last 1
    if ($keyEntry) {
        $apiKey = ($keyEntry -split '=', 2)[1].Trim()
        if ($apiKey.Length -ge 2 -and (
            ($apiKey.StartsWith("'") -and $apiKey.EndsWith("'")) -or
            ($apiKey.StartsWith('"') -and $apiKey.EndsWith('"'))
        )) {
            $apiKey = $apiKey.Substring(1, $apiKey.Length - 2)
        }
        if ($apiKey) {
            $env:OPENROUTER_API_KEY = $apiKey
        }
    }
}

$pythonCandidates = @()
if ($env:AUTOPLAY_PYTHON) {
    $pythonCandidates += $env:AUTOPLAY_PYTHON
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
