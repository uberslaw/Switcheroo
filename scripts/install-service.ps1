#Requires -Version 5.1
<#
.SYNOPSIS
  Install Switcheroo as a Windows service (WinSW), start it, verify /health.

.DESCRIPTION
  Fail-fast prerequisites, create .venv if needed, write WinSW XML, install
  service Switcheroo (Automatic Delayed Start + restart on failure), start it,
  verify service Running and GET /health. Logs to data\install-service.log.
  Must be run as administrator.
#>
$ErrorActionPreference = "Stop"
$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepoRoot = Split-Path -Parent $script:ScriptDir
$script:DataDir = Join-Path $script:RepoRoot "data"
$script:WinswDir = Join-Path $script:ScriptDir "winsw"
$script:InstallLog = Join-Path $env:TEMP "switcheroo-install-service.log"
$script:ServiceName = "Switcheroo"
$script:WinswVersion = "2.12.0"
$script:WinswUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe"
# SHA256 of WinSW.NET461.exe v2.12.0. Filled after first successful download if the
# pinned file is missing; install still verifies size > 100 KB.
$script:WinswSha256 = "B5066B7BBDFBA1293E5D15CDA3CAAEA88FBEAB35BD5B38C41C913D492AADFC4F"

. (Join-Path $script:ScriptDir "Switcheroo.Monitor.ps1")

function Write-InstallLog {
    param([string]$Message, [string]$Level = "INFO")
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    $dir = Split-Path -Parent $script:InstallLog
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    Add-Content -LiteralPath $script:InstallLog -Value $line -Encoding UTF8
    if ($Level -eq "ERROR") { Write-Host $line -ForegroundColor Red }
    elseif ($Level -eq "WARN") { Write-Host $line -ForegroundColor Yellow }
    else { Write-Host $line }
}

function Fail {
    param([string]$Message, [int]$Code = 1)
    Write-InstallLog $Message -Level "ERROR"
    Write-InstallLog "Install log: $($script:InstallLog)" -Level "ERROR"
    exit $Code
}

function Assert-Administrator {
    if (-not (Test-IsAdministrator)) {
        Fail "Administrator rights are required. Right-click PowerShell and Run as administrator, then: powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    }
}

function Get-DotEnvMap {
    $map = @{}
    $path = Join-Path $script:RepoRoot ".env"
    if (-not (Test-Path -LiteralPath $path)) { return $map }
    Get-Content -LiteralPath $path -ErrorAction SilentlyContinue | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#") -or $line -notmatch "=") { return }
        $idx = $line.IndexOf("=")
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
        $map[$key] = $val
    }
    return $map
}

function Get-PythonMajorMinor {
    param([string]$PythonExe)
    $raw = & $PythonExe -c "import sys; print('%d.%d' % (sys.version_info[0], sys.version_info[1]))"
    if ($LASTEXITCODE -ne 0) { throw "Could not read Python version from $PythonExe" }
    return $raw.Trim()
}

function Test-PythonAtLeast312 {
    param([string]$VersionText)
    $parts = $VersionText.Split(".")
    $maj = [int]$parts[0]
    $min = [int]$parts[1]
    return ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 12))
}

function Find-HostPython {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function New-WinswXml {
    param(
        [string]$XmlPath,
        [string]$PythonExe,
        [string]$WorkDir,
        [string]$LogDir
    )
    $py = [System.Security.SecurityElement]::Escape($PythonExe)
    $wd = [System.Security.SecurityElement]::Escape($WorkDir)
    $ld = [System.Security.SecurityElement]::Escape($LogDir)
    $xml = @"
<service>
  <id>Switcheroo</id>
  <name>Switcheroo</name>
  <description>Switcheroo port-status website. Automatic Delayed Start. Restart on failure.</description>
  <executable>$py</executable>
  <arguments>-m app</arguments>
  <workingdirectory>$wd</workingdirectory>
  <logpath>$ld</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>2048</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
  <env name="PYTHONUNBUFFERED" value="1" />
  <onfailure action="restart" delay="5 sec" />
  <onfailure action="restart" delay="10 sec" />
  <onfailure action="restart" delay="30 sec" />
  <resetfailure>1 hour</resetfailure>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <stoptimeout>30 sec</stoptimeout>
  <stopparentprocessfirst>true</stopparentprocessfirst>
</service>
"@
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($XmlPath, $xml, $utf8)
}

function Get-VenvPythonLockers {
    param(
        [string]$ProjectDir,
        [string]$ServiceName = ""
    )
    $root = [System.IO.Path]::GetFullPath($ProjectDir).TrimEnd('\')
    $needles = @(
        (Join-Path $root '.venv')
        (Join-Path $root 'venv')
    )

    function Test-VenvNeedle {
        param([string]$Hay)
        if ([string]::IsNullOrWhiteSpace($Hay)) { return $false }
        foreach ($n in $needles) {
            if ([string]::IsNullOrWhiteSpace($n)) { continue }
            if ($Hay.IndexOf($n, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
            $fwd = $n.Replace('\', '/')
            if ($Hay.IndexOf($fwd, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
        }
        return $false
    }

    $rows = @{}
    try {
        $procs = Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='pythonservice.exe'" -ErrorAction Stop
        foreach ($p in @($procs)) {
            $procId = [int]$p.ProcessId
            if ($procId -le 0) { continue }
            $rows[$procId] = [pscustomobject]@{
                Pid         = $procId
                ParentPid   = [int]$p.ParentProcessId
                Name        = [string]$p.Name
                Path        = [string]$p.ExecutablePath
                CommandLine = [string]$p.CommandLine
            }
        }
    }
    catch {
        Write-InstallLog "Win32_Process query failed: $($_.Exception.Message)" -Level "WARN"
    }

    $servicePids = @{}
    if ($ServiceName) {
        try {
            $svc = Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + ($ServiceName.Replace("'", "''")) + "'") -ErrorAction Stop
            if ($svc -and [int]$svc.ProcessId -gt 0) {
                $procId = [int]$svc.ProcessId
                $servicePids[$procId] = $true
                $pathName = ([string]$svc.PathName).Trim().Trim('"')
                if ($rows.ContainsKey($procId)) {
                    if (-not $rows[$procId].Path) { $rows[$procId].Path = $pathName }
                    if (-not $rows[$procId].CommandLine) { $rows[$procId].CommandLine = $pathName }
                }
                else {
                    $leaf = if ($pathName) { [System.IO.Path]::GetFileName($pathName) } else { "service" }
                    $rows[$procId] = [pscustomobject]@{
                        Pid         = $procId
                        ParentPid   = 0
                        Name        = $leaf
                        Path        = $pathName
                        CommandLine = $pathName
                    }
                }
            }
        }
        catch { }
    }

    $matched = @{}
    foreach ($row in $rows.Values) {
        $inRoot = (Test-VenvNeedle $row.Path) -or (Test-VenvNeedle $row.CommandLine)
        $moduleApp = [string]$row.CommandLine -match '-m\s+app'
        if ($inRoot -or ($moduleApp -and $inRoot) -or $servicePids.ContainsKey($row.Pid) -or ($row.ParentPid -gt 0 -and $servicePids.ContainsKey($row.ParentPid))) {
            $matched[$row.Pid] = $row
        }
    }
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($row in $rows.Values) {
            if ($matched.ContainsKey($row.Pid)) { continue }
            if ($row.ParentPid -gt 0 -and $matched.ContainsKey($row.ParentPid)) {
                $matched[$row.Pid] = $row
                $changed = $true
            }
        }
    }

    $hits = @($matched.Values | Sort-Object Pid | ForEach-Object {
            [pscustomobject]@{
                Pid         = $_.Pid
                Name        = $_.Name
                Path        = $_.Path
                CommandLine = $_.CommandLine
            }
        })
    return @($hits)
}

function Write-VenvLockers {
    param($Lockers, [string]$Context)
    Write-Host ""
    if (-not $Lockers -or @($Lockers).Count -eq 0) {
        Write-InstallLog "$Context : no python.exe / pythonw.exe / pythonservice.exe using this install's .venv, venv, or python -m app"
        Write-InstallLog "Note: Heimdall (C:\Heimdall) and Serraview Insights (C:\Serraview Insights) use other venvs. They do not lock this product's site-packages. The blocker is this product's python."
        Write-Host "Python using this venv: (none)"
        return
    }
    Write-InstallLog "$Context : $(@($Lockers).Count) process(es) using this venv (pip cannot overwrite locked files):" -Level "WARN"
    Write-Host "Python processes using this venv:" -ForegroundColor Yellow
    foreach ($h in @($Lockers)) {
        $line = "  PID $($h.Pid)  $($h.Name)  path=$($h.Path)  cmd=$($h.CommandLine)"
        Write-InstallLog $line -Level "WARN"
        Write-Host $line -ForegroundColor Yellow
    }
}

function Stop-ProductServiceForPip {
    param([string]$ServiceName)
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) {
        Write-InstallLog "Service $ServiceName is not installed yet (nothing to stop before pip)"
        return
    }
    if ($svc.Status -eq 'Stopped') {
        Write-InstallLog "Service $ServiceName already Stopped"
        return
    }
    Write-InstallLog "Stopping $ServiceName before pip (running venv python holds package files)"
    Write-Host "Stopping $ServiceName so pip can update this venv..." -ForegroundColor Cyan
    try {
        if ($svc.Status -ne 'StopPending') {
            $svc.Stop()
        }
        $svc.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(12))
    }
    catch {
        $ex = $_.Exception
        $parts = @()
        while ($ex) {
            if ($ex -is [System.ComponentModel.Win32Exception]) {
                $parts += "$($ex.GetType().Name) (Win32 $($ex.NativeErrorCode)): $($ex.Message)"
            }
            else {
                $parts += "$($ex.GetType().Name): $($ex.Message)"
            }
            $ex = $ex.InnerException
        }
        Write-InstallLog "Stop-Service failed: $($parts -join ' — ')" -Level "WARN"
    }
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne 'Stopped') {
        $winsw = Join-Path $script:RepoRoot "scripts\winsw\Switcheroo.exe"
        if (Test-Path -LiteralPath $winsw) {
            Write-InstallLog "Service still $($svc.Status); trying WinSW stop"
            try { & $winsw stop } catch { Write-InstallLog $_.Exception.Message -Level "WARN" }
            Start-Sleep -Seconds 2
        }
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -ne 'Stopped') {
            $cim = Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + ($ServiceName.Replace("'", "''")) + "'") -ErrorAction SilentlyContinue
            $svcPid = if ($cim) { [int]$cim.ProcessId } else { 0 }
            if ($svcPid -gt 0) {
                Write-InstallLog "Service still $($svc.Status); taskkill PID $svcPid"
                Start-Process -FilePath "taskkill.exe" -ArgumentList "/F", "/PID", "$svcPid", "/T" -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue | Out-Null
            }
        }
    }
    $deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 400
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if (-not $svc -or $svc.Status -eq 'Stopped') { break }
    } while ((Get-Date) -lt $deadline)
    $state = if ($svc) { [string]$svc.Status } else { "missing" }
    Write-InstallLog "Service $ServiceName status=$state"
}

function Assert-VenvUnlockedForPip {
    param([string]$ProjectDir, [string]$ServiceName)
    $deadline = (Get-Date).AddSeconds(15)
    $lockers = @()
    do {
        $lockers = @(Get-VenvPythonLockers -ProjectDir $ProjectDir -ServiceName $ServiceName)
        if ($lockers.Count -eq 0) { return }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    Write-VenvLockers -Lockers $lockers -Context "After stopping $ServiceName"
    $pids = (@($lockers) | ForEach-Object { $_.Pid }) -join ', '
    Fail "Cannot pip while this venv is loaded. Close PID(s) $pids (Cursor, a second Launch Control Start, leftover python), then retry Install. Other products' venvs do not lock these files."
}

try {
    Assert-Administrator

    if (-not (Test-Path -LiteralPath $script:DataDir)) {
        New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    }
    $script:InstallLog = Join-Path $script:DataDir "install-service.log"
    Write-InstallLog "Switcheroo service install starting"
    Write-InstallLog "RepoRoot=$($script:RepoRoot)"

    $probe = Join-Path $script:DataDir ".write_probe"
    try {
        Set-Content -LiteralPath $probe -Value "ok" -Encoding ASCII
        Remove-Item -LiteralPath $probe -Force
    }
    catch {
        Fail "data\ is not writable: $($_.Exception.Message)"
    }
    Write-InstallLog "Verified writable data dir $($script:DataDir)"

    $envExample = Join-Path $script:RepoRoot ".env.example"
    $envFile = Join-Path $script:RepoRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile)) {
        if (-not (Test-Path -LiteralPath $envExample)) {
            Fail "Missing .env and .env.example"
        }
        Copy-Item -LiteralPath $envExample -Destination $envFile
        Write-InstallLog "Created .env from .env.example (lab defaults - not production)"
    }
    else {
        Write-InstallLog "Using existing .env"
    }
    if (-not (Test-Path -LiteralPath $envFile)) {
        Fail ".env was not written"
    }

    $envMap = Get-DotEnvMap
    $bindHost = "127.0.0.1"
    $bindPort = 8080
    if ($envMap.ContainsKey("SWITCHEROO_HOST") -and $envMap["SWITCHEROO_HOST"]) {
        $bindHost = $envMap["SWITCHEROO_HOST"]
    }
    if ($envMap.ContainsKey("SWITCHEROO_PORT") -and $envMap["SWITCHEROO_PORT"]) {
        $parsed = 0
        if ([int]::TryParse($envMap["SWITCHEROO_PORT"], [ref]$parsed)) { $bindPort = $parsed }
    }
    if ($bindHost -eq "0.0.0.0" -or $bindHost -eq "::") {
        Write-InstallLog "SWITCHEROO_HOST=$bindHost binds all interfaces. Restrict with Windows Firewall / reverse proxy. Do not expose to the internet." -Level "WARN"
    }
    else {
        Write-InstallLog "Bind $bindHost`:$bindPort (loopback is the safe default). For a team host set SWITCHEROO_HOST=0.0.0.0 in .env and firewall the port."
    }

    $venvPython = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $hostPy = Find-HostPython
        if (-not $hostPy) {
            Fail "Python 3.12+ is not on PATH. Install Python 3.12 or newer, then re-run."
        }
        $ver = Get-PythonMajorMinor -PythonExe $hostPy
        Write-InstallLog "Host Python $ver at $hostPy"
        if (-not (Test-PythonAtLeast312 -VersionText $ver)) {
            Fail "Switcheroo requires Python 3.12 or newer. Found $ver at $hostPy"
        }
        Write-InstallLog "Creating virtual environment .venv"
        & $hostPy -m venv (Join-Path $script:RepoRoot ".venv")
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            Fail "python -m venv failed. Check that venv is installed for this Python."
        }
    }
    $venvVer = Get-PythonMajorMinor -PythonExe $venvPython
    Write-InstallLog "Venv Python $venvVer at $venvPython"
    if (-not (Test-PythonAtLeast312 -VersionText $venvVer)) {
        Fail "Venv Python is $venvVer. Recreate .venv with Python 3.12+."
    }

    Write-VenvLockers -Lockers (Get-VenvPythonLockers -ProjectDir $script:RepoRoot -ServiceName $script:ServiceName) -Context "Before stopping service"
    Stop-ProductServiceForPip -ServiceName $script:ServiceName
    Write-VenvLockers -Lockers (Get-VenvPythonLockers -ProjectDir $script:RepoRoot -ServiceName $script:ServiceName) -Context "After stopping $($script:ServiceName)"
    Assert-VenvUnlockedForPip -ProjectDir $script:RepoRoot -ServiceName $script:ServiceName

    $req = Join-Path $script:RepoRoot "requirements.txt"
    if (-not (Test-Path -LiteralPath $req)) { Fail "Missing $req" }
    Write-InstallLog "pip install -r requirements.txt"
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r $req
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed (exit $LASTEXITCODE)" }
    Write-InstallLog "pip install completed"

    icacls $script:DataDir /grant "NT AUTHORITY\SYSTEM:(OI)(CI)M" | Out-Null
    Write-InstallLog "Granted SYSTEM modify on $($script:DataDir)"

    if (-not (Test-Path -LiteralPath $script:WinswDir)) {
        New-Item -ItemType Directory -Path $script:WinswDir -Force | Out-Null
    }
    $winswSrc = Join-Path $script:WinswDir "WinSW.NET461.exe"
    if (-not (Test-Path -LiteralPath $winswSrc)) {
        $alt = Join-Path $script:WinswDir "WinSW.exe"
        if (Test-Path -LiteralPath $alt) {
            $winswSrc = $alt
            Write-InstallLog "Using local $alt"
        }
        else {
            Write-InstallLog "Downloading WinSW v$($script:WinswVersion) from GitHub"
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -Uri $script:WinswUrl -OutFile $winswSrc -UseBasicParsing
            }
            catch {
                Fail "Could not download WinSW ($($_.Exception.Message)). Place WinSW.NET461.exe from $($script:WinswUrl) into $($script:WinswDir) and re-run."
            }
        }
    }
    if (-not (Test-Path -LiteralPath $winswSrc)) {
        Fail "WinSW binary missing at $winswSrc"
    }
    $hash = (Get-FileHash -LiteralPath $winswSrc -Algorithm SHA256).Hash.ToUpperInvariant()
    Write-InstallLog "WinSW SHA256=$hash size=$((Get-Item -LiteralPath $winswSrc).Length)"
    if ((Get-Item -LiteralPath $winswSrc).Length -lt 100000) {
        Fail "WinSW binary is too small; download looks corrupt. Delete $winswSrc and retry."
    }
    if ($script:WinswSha256 -notmatch "PLACEHOLDER" -and $hash -ne $script:WinswSha256) {
        Fail "WinSW SHA256 mismatch. Expected $($script:WinswSha256) got $hash"
    }

    $winswExe = Join-Path $script:WinswDir "Switcheroo.exe"
    $winswXml = Join-Path $script:WinswDir "Switcheroo.xml"
    Copy-Item -LiteralPath $winswSrc -Destination $winswExe -Force
    New-WinswXml -XmlPath $winswXml -PythonExe $venvPython -WorkDir $script:RepoRoot -LogDir $script:DataDir
    if (-not (Test-Path -LiteralPath $winswExe)) { Fail "Failed to write $winswExe" }
    if (-not (Test-Path -LiteralPath $winswXml)) { Fail "Failed to write $winswXml" }
    $xmlText = Get-Content -LiteralPath $winswXml -Raw
    if ($xmlText -notmatch [regex]::Escape($venvPython.Replace("\", "\\")) -and $xmlText -notlike "*$venvPython*") {
        Fail "WinSW XML did not contain python path $venvPython"
    }
    if ($xmlText -notmatch [regex]::Escape($script:RepoRoot) -and $xmlText -notlike "*$($script:RepoRoot)*") {
        Fail "WinSW XML did not contain working directory $($script:RepoRoot)"
    }
    Write-InstallLog "Wrote $winswXml (read-back ok)"

    $existing = Get-SwitcherooServiceInfo -Name $script:ServiceName
    $listenPid = Get-SwitcherooListenPid -Port $bindPort
    if ($listenPid -gt 0 -and -not ($existing.Exists -and $existing.State -eq "Running")) {
        Fail "Port $bindPort is already in use (PID $listenPid). Stop Launch Control's python process or that listener, then re-run."
    }

    if ($existing.Exists) {
        Write-InstallLog "Service already installed; stopping before refresh"
        try { Stop-Service -Name $script:ServiceName -Force -ErrorAction SilentlyContinue } catch { }
        $un = Start-Process -FilePath $winswExe -ArgumentList "uninstall" -Wait -PassThru -NoNewWindow
        Write-InstallLog "WinSW uninstall exit $($un.ExitCode)"
        Start-Sleep -Seconds 1
    }

    $inst = Start-Process -FilePath $winswExe -ArgumentList "install" -Wait -PassThru -NoNewWindow
    if ($inst.ExitCode -ne 0) {
        Fail "WinSW install failed (exit $($inst.ExitCode)). See $($script:InstallLog) and data\Switcheroo.wrapper.log"
    }
    Write-InstallLog "WinSW install exit 0"

    $scCfg = Start-Process -FilePath "sc.exe" -ArgumentList @("config", $script:ServiceName, "start=", "delayed-auto") -Wait -PassThru -NoNewWindow
    Write-InstallLog "sc config delayed-auto exit $($scCfg.ExitCode)"
    $scFail = Start-Process -FilePath "sc.exe" -ArgumentList @(
        "failure", $script:ServiceName, "reset=", "86400", "actions=", "restart/5000/restart/10000/restart/30000"
    ) -Wait -PassThru -NoNewWindow
    Write-InstallLog "sc failure restart-on-fail exit $($scFail.ExitCode)"

    Start-Service -Name $script:ServiceName
    Write-InstallLog "Start-Service issued"

    $deadline = (Get-Date).AddSeconds(45)
    $svc = $null
    do {
        Start-Sleep -Milliseconds 500
        $svc = Get-SwitcherooServiceInfo -Name $script:ServiceName
    } while ((Get-Date) -lt $deadline -and $svc.State -ne "Running")

    if (-not $svc.Exists -or $svc.State -ne "Running") {
        Fail "Service did not reach Running (state='$($svc.State)'). Check data\switcheroo.log and data\Switcheroo.err.log"
    }
    Write-InstallLog "Service Running PID=$($svc.ProcessId)"

    $health = $null
    $healthDeadline = (Get-Date).AddSeconds(30)
    do {
        $health = Get-SwitcherooHealth -BindHost $bindHost -Port $bindPort
        if ($health.Ok) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $healthDeadline)

    if (-not $health.Ok) {
        Fail "GET $($health.Url) failed after service start: $($health.Error). App log: $(Join-Path $script:DataDir 'switcheroo.log')"
    }
    Write-InstallLog ("Health ok {0}ms at {1}" -f $health.LatencyMs, $health.Url)

    $appPid = Resolve-SwitcherooPid -Port $bindPort -PidFile (Join-Path $script:DataDir "switcheroo.pid") -ServiceProcessId $svc.ProcessId
    Write-InstallLog "Resolved app/listen PID=$appPid (service wrapper PID=$($svc.ProcessId))"

    Write-Host ""
    Write-Host "Switcheroo Windows service is installed and running."
    Write-Host "  Service:     $($script:ServiceName)  (Automatic Delayed Start, restart on failure)"
    Write-Host "  State:       Running"
    Write-Host "  App PID:     $appPid"
    Write-Host "  Health:      ok $($health.LatencyMs)ms  $($health.Url)"
    Write-Host "  App log:     $(Join-Path $script:DataDir 'switcheroo.log')"
    Write-Host "  Wrapper logs:$(Join-Path $script:DataDir 'Switcheroo.out.log')  $(Join-Path $script:DataDir 'Switcheroo.err.log')"
    Write-Host "  Install log: $($script:InstallLog)"
    Write-Host "  Monitor:     $(Join-Path $script:ScriptDir 'Switcheroo-LaunchControl.cmd')"
    Write-Host ""
    Write-Host "Launch Control does not need admin to show status, PID, logs, and health."
    Write-Host "Start/Stop/Restart of the service does need Run as administrator."
    exit 0
}
catch {
    Fail $_.Exception.Message
}
