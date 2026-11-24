<#
Runs the iyiCAM agent of this computer in the background and shows whether it works.

The agent (iyicam agent, docs\web.md) only shares folders of THIS computer with the web app: it connects outward to the
server (iyicam serve), so nothing has to reach into this computer, and the app in the browser opens and saves the files of
those folders through it. It does no CAM work (that runs in the browser).

  agent_windows.ps1 -ServerUrl https://iyicam.example.com -ServerKey KEY [-Root D:\Jobs -Root E:\Cut] [-Name WORKSHOP]
                                first time (or to change them): keeps the settings and (re)starts the agent. The key is
                                that of the server ("iyicam serve --show-key" there); the folders default to the home folder.
  agent_windows.ps1             starts the agent if it is not running; shows its state
  agent_windows.ps1 -Stop       stops it
  agent_windows.ps1 -Restart    stops it and starts it again

It needs the 64-bit Python 3.11 or newer from python.org. The agent has its own environment (.venv of this folder,
the same as packaging\web_windows.bat, no Qt) and no window; what it says is in %USERPROFILE%\.iyicam\agent.log
(IYICAM_HOME). Only the processes of "iyicam agent" are touched, never another python.
#>
param([string]$ServerUrl, [string]$ServerKey, [string[]]$Root, [string]$Name, [switch]$Stop, [switch]$Restart)

$ErrorActionPreference = 'Stop'

$here     = Split-Path -Parent $PSScriptRoot
$venvPy   = Join-Path $here '.venv\Scripts\python.exe'
$venvPyw  = Join-Path $here '.venv\Scripts\pythonw.exe'
$dataDir  = if ($env:IYICAM_HOME) { $env:IYICAM_HOME } else { Join-Path $env:USERPROFILE '.iyicam' }
$settings = Join-Path $dataDir 'agent.json'
$logF     = Join-Path $dataDir 'agent.log'
$errF     = Join-Path $dataDir 'agent.err.log'

function Get-AgentProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -match '-m\s+iyicam\s+agent' })
}

function Read-Json($path) {
    if (-not (Test-Path $path)) { return $null }
    try { return (Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

# runs a program and only says whether it succeeded (a program that prints to stderr must not stop this script)
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
        & $py.Exe @($py.Pre) -m venv (Join-Path $here '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not make the environment (.venv).' }
    }
    if (-not (Test-Run $venvPy @('-c', 'import iyicam.share.agent'))) {
        Write-Host 'Installing iyiCAM ...'
        & $venvPy -m pip install --quiet --disable-pip-version-check -e $here
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

# the settings are kept in the user folder (the key too: it is the user's own folder, and on the command line it would
# show in the list of processes)
function Save-Settings {
    $s = Read-Json $settings
    if (-not $s -and (-not $ServerUrl -or -not $ServerKey)) {
        throw ("The agent is not set up yet. Give the address of the server and its key (the one that 'iyicam serve --show-key' prints there):`n" +
               "  packaging\agent_windows.bat -ServerUrl https://iyicam.example.com -ServerKey ABCDEF-GHJKMN-PQRSTU-VWXYZ2 -Root D:\Jobs")
    }
    $new = [ordered]@{
        server_url = $(if ($ServerUrl) { $ServerUrl } else { $s.server_url })
        key        = $(if ($ServerKey) { $ServerKey } else { $s.key })
        roots      = $(if ($Root) { @($Root) } elseif ($s -and $s.roots) { @($s.roots) } else { @() })
        name       = $(if ($Name) { $Name } elseif ($s) { $s.name } else { '' })
    }
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $new | ConvertTo-Json | Set-Content -Path $settings -Encoding UTF8
}

function Start-Agent {
    $s = Read-Json $settings
    $a = @('-m', 'iyicam', 'agent', '--server', $s.server_url)
    foreach ($r in @($s.roots)) { if ($r) { $a += @('--root', "`"$r`"") } }
    if ($s.name) { $a += @('--name', "`"$($s.name)`"") }
    Write-Host 'Starting the agent ...'
    $env:IYICAM_KEY = $s.key                                        # (inherited by the agent, not on its command line)
    try {
        Start-Process -FilePath $venvPyw -ArgumentList $a -WorkingDirectory $here -WindowStyle Hidden `
            -RedirectStandardOutput $logF -RedirectStandardError $errF
    } finally { Remove-Item Env:\IYICAM_KEY -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 40; $i++) {                                 # up to 10 s: it says whether the server took it
        Start-Sleep -Milliseconds 250
        if (-not (Get-AgentProcesses)) { break }
        if ((Get-Content $errF, $logF -ErrorAction SilentlyContinue) -match 'connected|refused|cannot reach') { break }
    }
    if (-not (Get-AgentProcesses)) {
        $why = (Get-Content $errF, $logF -ErrorAction SilentlyContinue | Select-Object -Last 5) -join "`n"
        throw "The agent did not start or ended at once:`n$why"
    }
}

function Show-Agent {
    $procs = Get-AgentProcesses
    $s = Read-Json $settings
    Write-Host ''
    if (-not $procs) {
        Write-Host 'The agent is not running.  Start it:  packaging\agent_windows.bat' -ForegroundColor Yellow
        return
    }
    $since = ($procs | Sort-Object CreationDate | Select-Object -First 1).CreationDate.ToString('yyyy-MM-dd HH:mm')
    Write-Host "The agent is running (since $since)." -ForegroundColor Green
    if ($s) {
        Write-Host "Server  : $($s.server_url)"
        Write-Host "Folders : $(if ($s.roots) { ($s.roots -join ', ') } else { 'the home folder' })"
        if ($s.name) { Write-Host "Computer: $($s.name)" }
    }
    $last = Get-Content $errF, $logF -ErrorAction SilentlyContinue | Select-Object -Last 3
    if ($last) { Write-Host ''; Write-Host 'Last words of the agent:'; $last | ForEach-Object { Write-Host "  $_" } }
}

try {
    if ($Stop) { Stop-Agent; return }
    Ensure-Environment
    $changed = $ServerUrl -or $ServerKey -or $Root -or $Name
    if ($changed -or -not (Test-Path $settings)) { Save-Settings }
    if ($Restart -or $changed) { Stop-Agent -Quiet }                # (the settings are read when it starts)
    if (-not (Get-AgentProcesses)) { Start-Agent }
    Show-Agent
}
catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
