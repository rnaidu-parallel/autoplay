<#
.SYNOPSIS
  One command for a stream session: overlay, harness (game + agent), chat agent with replies, OBS.

  .\broadcast\session.ps1 start   [-BudgetUsd 50] [-NoRecord] [-Live]   start everything; -Live also starts the broadcast
  .\broadcast\session.ps1 live                                            start the Twitch/Kick outputs (stream.cjs start)
  .\broadcast\session.ps1 status                                          what is running, where the farmer is, chat health
  .\broadcast\session.ps1 stop                                            stop the outputs, then chat, harness (+ffmpeg), overlay

  Logs: broadcast\local\session\*.log. PIDs: broadcast\local\session\pids.json. OBS stays open after stop.
#>
param(
    [Parameter(Position = 0)][ValidateSet('start', 'live', 'stop', 'status')][string]$Command = 'status',
    [double]$BudgetUsd = 50,
    [switch]$NoRecord,
    [switch]$Live,
    [int]$WorldTimeoutMinutes = 10
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sessionDir = Join-Path $root 'broadcast\local\session'
$pidsPath = Join-Path $sessionDir 'pids.json'
$runHarness = Join-Path $root 'harness\run-harness.ps1'
$obsExe = Join-Path $root 'broadcast\local\obs-studio\bin\64bit\obs64.exe'
$overlayUrl = 'http://127.0.0.1:8765/state.json'
$node = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
if (-not (Test-Path -LiteralPath $node)) { $node = (Get-Command node -ErrorAction SilentlyContinue).Source }
New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null

function Read-DotEnv([string]$name) {
    $line = Get-Content -LiteralPath (Join-Path $root '.env') -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -Last 1
    if (-not $line) { return '' }
    return ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
}

function Get-Pids {
    if (Test-Path -LiteralPath $pidsPath) { return Get-Content -LiteralPath $pidsPath -Raw | ConvertFrom-Json }
    return $null
}

function Test-Alive([int]$processId) {
    return $processId -gt 0 -and $null -ne (Get-Process -Id $processId -ErrorAction SilentlyContinue)
}

function Get-ObsProcess {
    return Get-Process -Name obs64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $obsExe } | Select-Object -First 1
}

function Get-OverlayState {
    try { return Invoke-RestMethod -Uri $overlayUrl -TimeoutSec 5 } catch { return $null }
}

function Start-Piece([string]$name, [string[]]$harnessArguments) {
    $stdout = Join-Path $sessionDir "$name.out.log"
    $stderr = Join-Path $sessionDir "$name.err.log"
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runHarness) + $harnessArguments
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WorkingDirectory $root `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    Write-Host ("{0,-8} pid {1}  ({2})" -f $name, $process.Id, ($harnessArguments -join ' '))
    return $process.Id
}

function Stop-Tree([string]$name, [int]$processId) {
    if (-not (Test-Alive $processId)) { Write-Host ("{0,-8} not running" -f $name); return }
    & taskkill.exe /T /F /PID $processId 2>&1 | Out-Null
    Write-Host ("{0,-8} stopped (pid {1} and children)" -f $name, $processId)
}

function Invoke-Stream([string]$verb) {
    if (-not (Get-ObsProcess)) { Write-Host 'OBS is not running; nothing to do for the broadcast.'; return }
    & $node (Join-Path $root 'broadcast\stream.cjs') $verb
}

function Start-Session {
    $pids = Get-Pids
    if ($pids -and ((Test-Alive $pids.harness) -or (Test-Alive $pids.overlay) -or (Test-Alive $pids.chat))) {
        throw 'A session is already running; run .\broadcast\session.ps1 stop first.'
    }
    if (-not (Read-DotEnv 'OPENROUTER_API_KEY')) { throw 'OPENROUTER_API_KEY missing in .env' }
    $channel = Read-DotEnv 'TWITCH_CHANNEL'
    $kick = Read-DotEnv 'KICK_CHANNEL'
    if (-not $channel -and -not $kick) { throw 'Set TWITCH_CHANNEL or KICK_CHANNEL in .env' }
    $canReply = (Test-Path -LiteralPath (Join-Path $root 'chat\twitch-tokens.json')) -or (Test-Path -LiteralPath (Join-Path $root 'chat\kick-tokens.json'))
    if (-not $canReply) { Write-Warning 'No chat login found (chat-agent --twitch-login / --kick-login); starting chat without --reply.' }
    $stopMarker = Join-Path $root 'harness\state\STOP'
    if (Test-Path -LiteralPath $stopMarker) { Remove-Item -LiteralPath $stopMarker -Force; Write-Host 'Removed harness\state\STOP from the last stop.' }

    if (-not (Get-ObsProcess)) {
        if (-not (Test-Path -LiteralPath $obsExe)) { throw "OBS not found at $obsExe; run broadcast\setup.ps1 first." }
        Start-Process -FilePath $obsExe -WorkingDirectory (Split-Path -Parent $obsExe) | Out-Null
        Write-Host 'OBS      starting'
        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline -and -not (Test-NetConnection -ComputerName 127.0.0.1 -Port 4455 -InformationLevel Quiet -WarningAction SilentlyContinue)) { Start-Sleep -Seconds 2 }
    } else { Write-Host 'OBS      already running' }

    $overlayPid = Start-Piece 'overlay' @('overlay', '--run', 'latest', '--port', '8765')
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline -and -not (Get-OverlayState)) { Start-Sleep -Seconds 1 }
    if (-not (Get-OverlayState)) { Stop-Tree 'overlay' $overlayPid; throw "Overlay did not answer on $overlayUrl; see $sessionDir\overlay.err.log" }

    $harnessArguments = @('run', '--agent', '--forever', '--continuous', '--budget-usd', "$BudgetUsd")
    if (-not $NoRecord) { $harnessArguments += '--record-video' }
    $harnessPid = Start-Piece 'harness' $harnessArguments

    $chatArguments = @('chat-agent', '--transport', 'irc', '--mode', 'bind')
    if ($channel) { $chatArguments += @('--channel', $channel) }
    if ($kick) { $chatArguments += @('--kick-channel', $kick) }
    if ($canReply) { $chatArguments += '--reply' }
    $chatPid = Start-Piece 'chat' $chatArguments

    @{ overlay = $overlayPid; harness = $harnessPid; chat = $chatPid; startedAt = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $pidsPath -Encoding utf8

    Write-Host "Waiting for the game world (up to $WorldTimeoutMinutes min)..."
    $deadline = (Get-Date).AddMinutes($WorldTimeoutMinutes)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        if (-not (Test-Alive $harnessPid)) { throw "Harness exited early; see $sessionDir\harness.err.log" }
        $state = Get-OverlayState
        if ($state -and $state.game -and $state.game.worldReady -eq $true) { $ready = $true; break }
        Write-Host ("  {0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $(if ($state) { $state.status } else { 'no overlay state' }))
    }
    if (-not $ready) { Write-Warning 'World not ready yet; the harness keeps trying. Check status before going live.' }
    else { Write-Host 'World ready.' }
    Show-Status
    if ($Live) {
        if (-not $ready) { throw 'Not going live without a ready world.' }
        Invoke-Stream 'start'
    } else {
        Write-Host 'Broadcast not started. Watch a few decisions, then: .\broadcast\session.ps1 live'
    }
}

function Stop-Session {
    Invoke-Stream 'stop'
    $pids = Get-Pids
    if (-not $pids) { Write-Host 'No pids.json; nothing of ours to stop.'; return }
    New-Item -ItemType File -Path (Join-Path $root 'harness\state\STOP') -Force | Out-Null
    Stop-Tree 'chat' $pids.chat
    Stop-Tree 'harness' $pids.harness
    Stop-Tree 'overlay' $pids.overlay
    Remove-Item -LiteralPath $pidsPath -Force
    $ffmpeg = Get-Process -Name ffmpeg -ErrorAction SilentlyContinue
    if ($ffmpeg) { Write-Warning ("ffmpeg still running (pid {0}); not ours to kill unless the harness owned it." -f ($ffmpeg.Id -join ',')) }
    Write-Host 'harness\state\STOP left in place; start removes it.'
}

function Show-Status {
    $pids = Get-Pids
    foreach ($name in 'overlay', 'harness', 'chat') {
        $processId = if ($pids) { [int]$pids.$name } else { 0 }
        Write-Host ("{0,-8} {1}" -f $name, $(if (Test-Alive $processId) { "running (pid $processId)" } else { 'not running' }))
    }
    Write-Host ("{0,-8} {1}" -f 'OBS', $(if (Get-ObsProcess) { 'running' } else { 'not running' }))
    $state = Get-OverlayState
    if ($state) {
        $game = $state.game
        Write-Host ("farmer   run {0}  status {1}  {2} {3} {4}  {5}  worldReady={6}" -f $state.session.runId, $state.status,
            $game.season, $game.day, $game.time, $game.location, $game.worldReady)
        if ($state.speech) { Write-Host ("say      {0}" -f $state.speech) }
    } else { Write-Host 'overlay  no state (not serving or no run yet)' }
    $chatState = Join-Path $root 'chat\state.json'
    if (Test-Path -LiteralPath $chatState) {
        $chat = Get-Content -LiteralPath $chatState -Raw | ConvertFrom-Json
        $age = if ($chat.updated_at) { [int]((Get-Date) - ([datetime]'1970-01-01').AddSeconds($chat.updated_at).ToLocalTime()).TotalSeconds } else { -1 }
        Write-Host ("chat     mode {0}  last window {1}s ago  received {2} accepted {3}{4}" -f $chat.mode, $age,
            $chat.last_window.received, $chat.last_window.accepted, $(if ($chat.relay_error) { "  RELAY ERROR: $($chat.relay_error)" } else { '' }))
    }
    if (Get-ObsProcess) { Invoke-Stream 'status' }
}

switch ($Command) {
    'start' { Start-Session }
    'live' { Invoke-Stream 'start' }
    'stop' { Stop-Session }
    'status' { Show-Status }
}
