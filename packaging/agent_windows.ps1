<#
Runs the iyiCAM agent of this computer in the background and shows whether it works.

The agent (iyicam agent, docs\web.md) is what lets a hub on a server (iyicam serve --hub) work with the engine and the
files of THIS computer: it connects outward to the hub, so nothing has to reach into this computer. A browser signs in
on the hub with the CLIENT KEY of this agent, which the agent makes itself and which is shown here.

  agent_windows.ps1 -ServerUrl https://hub.example.com -ServerKey KEY
                                first time (or to change them): keeps the settings, checks them at the hub, starts
                                the agent. The server key is that of the hub ("iyicam serve --show-key" there).
  agent_windows.ps1             starts the agent if it is not running; shows its state and the client key
  agent_windows.ps1 -Stop       stops it
  agent_windows.ps1 -Restart    stops it and starts it again
  agent_windows.ps1 -NewKey     a new client key (the old one stops working at once)

It needs the 64-bit Python 3.11 or newer from python.org. The agent has its own environment (.venv of this folder,
the same as packaging\web_windows.bat, no Qt) and no window; what it does is in %USERPROFILE%\.iyicam\agent.log
(IYICAM_HOME). Only the processes of "iyicam agent" are touched, never another python.
#>
param([string]$ServerUrl, [string]$ServerKey, [string]$Name, [switch]$Stop, [switch]$Restart, [switch]$NewKey)

$ErrorActionPreference = 'Stop'

$root     = Split-Path -Parent $PSScriptRoot
$venvPy   = Join-Path $root '.venv\Scripts\python.exe'
$venvPyw  = Join-Path $root '.venv\Scripts\pythonw.exe'
$dataDir  = if ($env:IYICAM_HOME) { $env:IYICAM_HOME } else { Join-Path $env:USERPROFILE '.iyicam' }
$settings = Join-Path $dataDir 'agent.json'
$stateF   = Join-Path $dataDir 'agent.state.json'
$logF     = Join-Path $dataDir 'agent.log'

# the processes of "python -m iyicam agent" that stay (not the short ones: --setup, --show-key, --new-key)
function Get-AgentProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -match '-m\s+iyicam\s+agent' -and $_.CommandLine -notmatch '--(setup|show-key|new-key)' })
}

function Read-Json($path) {
    if (-not (Test-Path $path)) { return $null }
    try { return (Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

# runs a program and only says whether it succeeded (what it prints is of no interest, and a program that prints to
# stderr must not stop this script)
function Test-Run([string]$exe, [string[]]$arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $exe @arguments *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } finally { $ErrorActionPreference = $old }
}

function Find-Python {
    $probe = 'import struct, sys; sys.exit(0 if struct.calcsize("P") == 8 and sys.version_info >= (3, 11) else 1)'
    foreach ($c in @(@{ Exe = 'py'; Pre = @('-3') }, @{ Exe = 'python'; Pre = @() })) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if ($cmd -and (Test-Run $cmd.Source (@($c.Pre) + @('-c', $probe)))) { return @{ Exe = $cmd.Source; Pre = @($c.Pre) } }
    }
    return $null
}

# the environment: .venv with iyiCAM in it (core only: the agent needs no window)
function Ensure-Environment {
    if (-not (Test-Path $venvPy)) {
        $py = Find-Python
        if (-not $py) { throw 'This needs the 64-bit Python 3.11 or newer (python.org, "Windows installer (64-bit)").' }
        Write-Host 'Making the environment (.venv) ...'
        & $py.Exe @($py.Pre) -m venv (Join-Path $root '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not make the environment (.venv).' }
    }
    if (-not (Test-Run $venvPy @('-c', 'import iyicam.service.agent'))) {
        Write-Host 'Installing iyiCAM ...'
        & $venvPy -m pip install --quiet --disable-pip-version-check -e $root
        if ($LASTEXITCODE -ne 0) { throw 'Installing iyiCAM failed.' }
    }
}

function Stop-Agent([switch]$Quiet) {
    $procs = Get-AgentProcesses
    if (-not $procs) { if (-not $Quiet) { Write-Host 'The agent is not running.' }; return }
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 20 -and (Get-AgentProcesses); $i++) { Start-Sleep -Milliseconds 250 }
    if (Get-AgentProcesses) { throw 'The agent could not be stopped.' }
    if (-not $Quiet) { Write-Host 'The agent is stopped.' }
}

# keeps the settings and registers at the hub (nothing is kept when the hub does not take it)
function Invoke-Setup {
    if (-not (Test-Path $settings) -and (-not $ServerUrl -or -not $ServerKey)) {
        throw ("The agent is not set up yet. Give the address of the hub and its server key (the one that 'iyicam serve --show-key' prints there):`n" +
               "  packaging\agent_windows.bat -ServerUrl https://hub.example.com -ServerKey ABCDEF-GHJKMN-PQRSTU-VWXYZ2")
    }
    $a = @('-m', 'iyicam', 'agent', $(if ($NewKey) { '--new-key' } else { '--setup' }))
    if ($ServerUrl) { $a += @('--server-url', $ServerUrl) }
    if ($Name)      { $a += @('--name', $Name) }
    if ($ServerKey) { $env:IYICAM_SERVER_KEY = $ServerKey }          # (not on the command line: it would show in the list of processes)
    try { & $venvPy @a | Out-Null } finally { Remove-Item Env:\IYICAM_SERVER_KEY -ErrorAction SilentlyContinue }
    if ($LASTEXITCODE -ne 0) { throw 'The hub did not take the settings (see above).' }
}

function Start-Agent {
    Write-Host 'Starting the agent ...'
    $t0 = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Start-Process -FilePath $venvPyw -ArgumentList '-m', 'iyicam', 'agent' -WorkingDirectory $root -WindowStyle Hidden
    for ($i = 0; $i -lt 60; $i++) {                                # up to 15 s: it says what it found at the hub
        Start-Sleep -Milliseconds 250
        $st = Read-Json $stateF
        if ($st -and $st.updated -ge $t0 -and $st.state -notin @('', 'connecting')) { break }
    }
    if (-not (Get-AgentProcesses)) {
        throw "The agent did not start or ended at once. To see why, run it in a console:`n  & '$venvPy' -m iyicam agent"
    }
}

function Show-Agent {
    $procs = Get-AgentProcesses
    $s  = Read-Json $settings
    $st = Read-Json $stateF
    Write-Host ''
    if (-not $procs) {
        Write-Host 'The agent is not running.  Start it:  packaging\agent_windows.bat' -ForegroundColor Yellow
        return
    }
    $since = ($procs | Sort-Object CreationDate | Select-Object -First 1).CreationDate.ToString('yyyy-MM-dd HH:mm')
    Write-Host "The agent is running (since $since)." -ForegroundColor Green
    if ($s) {
        Write-Host "Hub        : $($s.server_url)"
        Write-Host "Computer   : $($s.name)"
    }
    $age = if ($st -and $st.last_ok) { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [long]$st.last_ok } else { $null }
    if ($st -and $st.state -eq 'connected' -and $null -ne $age -and $age -lt 90) {
        Write-Host 'Connection : connected to the hub.' -ForegroundColor Green
    } elseif ($st -and $st.state -and $st.state -ne 'connected') {
        Write-Host "Connection : $($st.state): $($st.message)" -ForegroundColor Yellow
    } else {
        Write-Host "Connection : no recent sign of life from the agent; run this again in a moment (the log: $logF)" -ForegroundColor Yellow
    }
    if ($s -and $s.token) {
        Write-Host ''
        Write-Host 'Client key (what the page of the hub asks for):'
        Write-Host "  $($s.token)" -ForegroundColor Cyan
        Write-Host '(The server key of the hub is another one and is never typed on the page.)'
    }
}

try {
    if ($Stop) { Stop-Agent; return }
    Ensure-Environment
    if ($ServerUrl -or $ServerKey -or $Name -or $NewKey -or -not (Test-Path $settings)) { Invoke-Setup }
    if ($Restart -or $ServerUrl -or $ServerKey -or $NewKey) { Stop-Agent -Quiet }      # (changed settings are read at the start)
    if (-not (Get-AgentProcesses)) { Start-Agent }
    Show-Agent
}
catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
