<#
Starts, stops and shows the CNC-Assistant agent of this computer (the local client that works with this
computer's files for CNC-Assistant's web page). It is a program of its own, installed apart from iyiCAM
(%LOCALAPPDATA%\CncAssistant\venv, settings in ~\.cnc-assistant\agent.json); this script only launches it and
reads its settings. iyiCAM needs neither.

  agent_windows.ps1            starts the agent if it is not running; shows its state and the client key
  agent_windows.ps1 -Stop      stops it
  agent_windows.ps1 -Restart   stops it and starts it again

(packaging\agent_windows.bat does the same and can be double-clicked.) The agent has no window of its own
(pythonw), so this is how to see whether it runs. Only the processes of "cnc_assistant --agent" are touched,
never another pythonw.
#>
param([switch]$Stop, [switch]$Restart)

$ErrorActionPreference = 'Stop'

$appDir   = Join-Path $env:LOCALAPPDATA 'CncAssistant'
$python   = Join-Path $appDir 'venv\Scripts\pythonw.exe'
$settings = Join-Path $env:USERPROFILE '.cnc-assistant\agent.json'

function Get-AgentProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*cnc_assistant*--agent*' })
}

function Read-Settings {
    if (-not (Test-Path $settings)) { return $null }
    try { return (Get-Content $settings -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

function Stop-Agent {
    $procs = Get-AgentProcesses
    if (-not $procs) { Write-Host 'The agent is not running.'; return }
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 20 -and (Get-AgentProcesses); $i++) { Start-Sleep -Milliseconds 250 }
    if (Get-AgentProcesses) { throw 'The agent could not be stopped.' }
    Write-Host 'The agent is stopped.'
}

function Start-Agent {
    if (-not (Test-Path $python)) { throw "The agent's Python was not found: $python (is the agent installed?)" }
    $s = Read-Settings
    if (-not $s -or -not $s.server_url -or -not $s.token) {
        throw ("The agent is not set up yet (no usable settings file: $settings). The first run is made in a console:`n" +
               "  & '$appDir\venv\Scripts\python.exe' -m cnc_assistant --agent --server-url https://SERVER")
    }
    Write-Host 'Starting the agent ...'
    Start-Process -FilePath $python -ArgumentList '-m', 'cnc_assistant', '--agent' `
        -WorkingDirectory $appDir -WindowStyle Hidden
    for ($i = 0; $i -lt 40 -and -not (Get-AgentProcesses); $i++) { Start-Sleep -Milliseconds 250 }
    Start-Sleep -Seconds 3                       # make sure it did not end at once (an import error, say)
    if (-not (Get-AgentProcesses)) {
        throw "The agent did not start or ended at once. To see why, run it in a console:`n  & '$appDir\venv\Scripts\python.exe' -m cnc_assistant --agent"
    }
}

# Has the agent an open connection to the server? (It waits for jobs by long polling; the connection is renewed now and then.)
function Test-Connected($procs, $serverUrl) {
    $ids = @($procs | ForEach-Object { $_.ProcessId })
    $port = 443
    try { $port = ([uri]$serverUrl).Port } catch { }
    for ($i = 0; $i -lt 20; $i++) {
        $c = Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
            Where-Object { $ids -contains $_.OwningProcess -and $_.RemotePort -eq $port }
        if ($c) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

try {
    if ($Stop) { Stop-Agent; return }
    if ($Restart) { Stop-Agent }

    $procs = Get-AgentProcesses
    $was = [bool]$procs
    if (-not $procs) { Start-Agent; $procs = Get-AgentProcesses }

    $s = Read-Settings
    $since = ($procs | Sort-Object CreationDate | Select-Object -First 1).CreationDate.ToString('yyyy-MM-dd HH:mm')
    Write-Host ''
    if ($was) { Write-Host "The agent is running (it was already; since $since)." -ForegroundColor Green }
    else      { Write-Host 'The agent is running (started just now).' -ForegroundColor Green }

    if ($s -and $s.server_url) {
        Write-Host "Server     : $($s.server_url)"
        if ($s.name) { Write-Host "Computer   : $($s.name)" }
        if (Test-Connected $procs $s.server_url) { Write-Host 'Connection : connected to the server.' -ForegroundColor Green }
        else { Write-Host 'Connection : no open connection to the server seen (internet or server?). Run this again in a moment.' -ForegroundColor Yellow }
    }
    if ($s -and $s.token) {
        Write-Host ''
        Write-Host "Client key (what CNC-Assistant's page asks for):"
        Write-Host "  $($s.token)" -ForegroundColor Cyan
        Write-Host '(The key of the iyiCAM web server is another one: iyicam serve --show-key)'
    }
}
catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
