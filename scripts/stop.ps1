<#
.SYNOPSIS
  Stop the Patch Tuesday game server and web client started by scripts\start.ps1.

.DESCRIPTION
  Stops the process ids saved in logs\run\pids.json (with their child processes). Then, as a
  fallback, stops a process still listening on port 8000 or 5173 only if its command line shows
  it is this project's uvicorn (game.server:app) or Vite. Nothing else is touched.

  An advance started from the game (Advance week) runs as a child process of the server, so
  stopping the server would kill it half-way. While an advance runs (the server's /api/status
  says advancing, or data\.advance.lock is held by a live process) this script refuses and
  changes nothing. -Force stops everything anyway; the killed advance's lock is then removed
  (advance.py writes its pid into the lock, and a lock whose process is gone is stale).

  scripts\stop.ps1          # refuses while an advance runs
  scripts\stop.ps1 -Force   # stops it anyway
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root 'logs\run\pids.json'
$Lock = Join-Path $Root 'data\.advance.lock'
$Advance = Join-Path $Root 'scripts\advance.py'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }
$stopped = @()

function Get-LockState {
    # advance.py --lock-status prints {"lock": "free"|"held"|"stale", ...}
    if (-not (Test-Path $Lock)) { return 'free' }
    try {
        $out = & $Python $Advance --lock-status 2>$null | Select-Object -Last 1
        $state = [string]($out | ConvertFrom-Json).lock
        if ($state) { return $state }
    } catch { }
    return 'held'
}

function Stop-Tree([int]$Id, [string]$Label) {
    $p = Get-Process -Id $Id -ErrorAction SilentlyContinue
    if (-not $p) { return $false }
    & taskkill.exe /PID $Id /T /F 2>&1 | Out-Null
    Start-Sleep -Milliseconds 300
    if (Get-Process -Id $Id -ErrorAction SilentlyContinue) {
        Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
    }
    $script:stopped += "$Label (pid $Id)"
    return $true
}

$advancing = $false
try {
    $s = Invoke-RestMethod 'http://127.0.0.1:8000/api/status' -TimeoutSec 3
    if ($s.advancing) { $advancing = $true }
} catch { }
if ((Get-LockState) -eq 'held') { $advancing = $true }
if ($advancing -and -not $Force) {
    Write-Warning ('An advance is running (the server reports advancing, or data\.advance.lock is held). ' +
        'Stopping the server now would kill it half-way. Wait for it to finish (about 30 s a week, ' +
        '2 min for day 0), or run scripts\stop.ps1 -Force.')
    exit 1
}

if (Test-Path $PidFile) {
    try {
        $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
        if ($pids.server) { Stop-Tree ([int]$pids.server) 'game server' | Out-Null }
        if ($pids.web) { Stop-Tree ([int]$pids.web) 'web client' | Out-Null }
    } catch {
        Write-Warning "Could not read ${PidFile}: $_"
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

foreach ($port in @(8000, 5173)) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $cmd = [string]$proc.CommandLine
        $ours = ($cmd -match 'game\.server:app') -or ($cmd -match 'vite' -and $cmd -match [regex]::Escape((Join-Path $Root 'web')))
        if ($ours) {
            Stop-Tree ([int]$proc.ProcessId) "listener on port $port" | Out-Null
        } else {
            Write-Warning "Port $port is held by pid $($proc.ProcessId) ($($proc.Name)), which is not Patch Tuesday; left running."
        }
    }
}

if ($stopped.Count) {
    $stopped | ForEach-Object { Write-Host "Stopped $_" }
} else {
    Write-Host 'Nothing to stop: Patch Tuesday is not running.'
}
if (Test-Path $Lock) {
    Start-Sleep -Milliseconds 500
    $after = Get-LockState
    if ($after -eq 'stale') {
        Remove-Item $Lock -Force -ErrorAction SilentlyContinue
        Write-Host 'Removed data\.advance.lock left by the stopped advance.'
    } elseif ($after -eq 'held') {
        Write-Warning 'data\.advance.lock is held by a live process (an advance started outside the game, for example in WSL). It removes the lock when it finishes.'
    }
}
if ($advancing -and $Force) {
    Write-Warning 'An advance was stopped half-way (-Force). serving\ still holds the last complete week; run python scripts\advance.py again, or bash scripts/reset.sh for day 0.'
}
