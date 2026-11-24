<#
.SYNOPSIS
  Start Patch Tuesday: the game server (FastAPI, port 8000) and the web client (Vite, port 5173).

.DESCRIPTION
  Both run hidden in the background. Logs go to logs\run\, process ids to logs\run\pids.json.
  Stop them with scripts\stop.ps1. Secrets stay in .env; this script never reads or prints them.

  scripts\start.ps1                 # normal 2 s ticks
  scripts\start.ps1 -TickSeconds 1  # faster rehearsal (sets PT_TICK_SECONDS for the server)
  scripts\start.ps1 -Band -Telem    # also switch on BAND_ENABLED=1 / TELEM_ENABLED=1

  -Band also switches on "Call for backup" (a Coach agent that can call a specialist into the
  Blue team through a BAND room) when the BAND_KEY_* variables and BAND_BLUE_CHAT_ID are in .env;
  scripts\band_setup.py writes them. The start-up summary says whether it is on, and why not.

  If PowerShell refuses to run local scripts (execution policy Restricted, or a zip download
  that carries Mark-of-the-Web), run it as:
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#>
[CmdletBinding()]
param(
    [double]$TickSeconds = 0,
    [switch]$Band,
    [switch]$Telem,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $Root 'logs\run'
$PidFile = Join-Path $RunDir 'pids.json'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Web = Join-Path $Root 'web'
$ServerPort = 8000
$WebPort = 5173

function Test-Listening([int]$Port) {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Wait-Http([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

if (-not (Test-Path $Python)) {
    throw "No Python venv at .venv. Create it: py -3.12 -m venv .venv; .venv\Scripts\pip install -r game\requirements.txt"
}
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { throw 'Node.js is not on PATH (Node 20.19+ / 22.12+ is needed for Vite).' }
$vite = Join-Path $Web 'node_modules\vite\bin\vite.js'
if (-not (Test-Path $vite)) {
    Write-Host 'Installing web dependencies (npm install) ...'
    Push-Location $Web
    try { & npm.cmd install --no-fund --no-audit | Out-Host } finally { Pop-Location }
}
foreach ($p in @($ServerPort, $WebPort)) {
    if (Test-Listening $p) {
        throw "Port $p is already in use. If Patch Tuesday is running, stop it first: scripts\stop.ps1"
    }
}
if (-not (Test-Path (Join-Path $Root 'serving\world.json'))) {
    Write-Warning 'serving\world.json is missing: the server will use serving_mock\. Build day 0 with: python scripts\advance.py --day0'
}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

# Environment for the server process only (inherited by Start-Process, restored afterwards).
$saved = @{}
$overrides = @{}
if ($TickSeconds -gt 0) { $overrides['PT_TICK_SECONDS'] = [string]$TickSeconds }
if ($Band) { $overrides['BAND_ENABLED'] = '1' }
if ($Telem) { $overrides['TELEM_ENABLED'] = '1' }
$overrides['PYTHONUNBUFFERED'] = '1'
foreach ($k in $overrides.Keys) {
    $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
    [Environment]::SetEnvironmentVariable($k, $overrides[$k], 'Process')
}
try {
    $server = Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'uvicorn', 'game.server:app', '--host', '127.0.0.1', '--port', "$ServerPort",
                        '--ws-max-size', '4096') `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $RunDir "server-$stamp.out.log") `
        -RedirectStandardError (Join-Path $RunDir "server-$stamp.log")
} finally {
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
}

$webProc = Start-Process -FilePath $node `
    -ArgumentList @($vite, '--port', "$WebPort", '--strictPort') `
    -WorkingDirectory $Web -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RunDir "web-$stamp.log") `
    -RedirectStandardError (Join-Path $RunDir "web-$stamp.err.log")

@{ server = $server.Id; web = $webProc.Id; started = (Get-Date).ToString('s');
   server_log = "logs\run\server-$stamp.log"; web_log = "logs\run\web-$stamp.log" } |
    ConvertTo-Json | Set-Content -Path $PidFile -Encoding utf8

$okServer = Wait-Http "http://127.0.0.1:$ServerPort/api/state" $TimeoutSeconds
$okWeb = Wait-Http "http://localhost:$WebPort/" $TimeoutSeconds
$okProxy = $okServer -and $okWeb -and (Wait-Http "http://localhost:$WebPort/api/world" 10)

if (-not $okServer) {
    Write-Warning "The game server did not answer on port $ServerPort. Last log lines:"
    Get-Content (Join-Path $RunDir "server-$stamp.log") -Tail 20 -ErrorAction SilentlyContinue | Out-Host
}
if (-not $okWeb) {
    Write-Warning "Vite did not answer on port $WebPort. Last log lines:"
    Get-Content (Join-Path $RunDir "web-$stamp.err.log") -Tail 20 -ErrorAction SilentlyContinue | Out-Host
}
if ($okServer) {
    try {
        $s = Invoke-RestMethod "http://127.0.0.1:$ServerPort/api/status" -TimeoutSec 5
        $bandText = if ($s.band.active) { 'on' } elseif ($s.band.disabled_reason) { "off ($($s.band.disabled_reason))" } else { 'off' }
        Write-Host ("World {0} from {1}, tick {2}s | LLM reasons: {3} | BAND: {4} | Telem: {5}" -f `
            $s.asof_date, $s.data_source, $s.tick_seconds, $(if ($s.llm.enabled) { 'on' } else { 'templates' }),
            $bandText, $(if ($s.telem.enabled) { 'on' } else { 'off' }))
        $t = $s.band_team
        if ($t) {
            $teamText = if ($t.active) { 'on (Coach posts in the BAND room "Patch Tuesday - Blue team")' }
                elseif ($t.offline) { 'on without BAND (PT_BACKUP_OFFLINE=1: Coach decides by itself)' }
                elseif (-not $t.enabled_flag) { 'off (start with -Band to switch it on)' }
                elseif ($t.disabled_reason) { "off ($($t.disabled_reason))" }
                else { 'off' }
            Write-Host "Call for backup: $teamText"
        }
    } catch { }
}
Write-Host ''
Write-Host "Game server : http://localhost:$ServerPort   (pid $($server.Id))"
Write-Host "Web client  : http://localhost:$WebPort   (pid $($webProc.Id))"
if ($okServer -and $okWeb -and $okProxy) {
    Write-Host ''
    Write-Host "Open http://localhost:$WebPort  (stop with scripts\stop.ps1)"
} else {
    Write-Warning 'Something did not start cleanly; see logs\run\. Stop with scripts\stop.ps1'
    exit 1
}
