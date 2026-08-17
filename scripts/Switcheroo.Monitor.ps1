#Requires -Version 5.1
# Shared Launch Control helpers. Dot-source from the GUI and install scripts.
# Keep algorithms in sync with app/launch_monitor.py.

$script:SwitcherooServiceName = "Switcheroo"
$script:SwitcherooServiceCache = $null
$script:SwitcherooServiceCacheAt = [datetime]::MinValue
$script:SwitcherooListenPidCache = 0
$script:SwitcherooListenPidCachePort = 0
$script:SwitcherooListenPidCacheAt = [datetime]::MinValue
$script:SwitcherooServiceCacheTtlSec = 5
$script:SwitcherooListenPidCacheTtlSec = 12

function Get-SwitcherooProbeHost {
    param([string]$BindHost)
    if ($BindHost -eq "0.0.0.0" -or $BindHost -eq "::" -or $BindHost -eq "[::]") {
        return "127.0.0.1"
    }
    return $BindHost
}

function Get-SwitcherooProbeUrl {
    param([string]$BindHost, [int]$Port)
    $h = Get-SwitcherooProbeHost -BindHost $BindHost
    return "http://${h}:${Port}"
}

function Get-SwitcherooHealthUrl {
    param([string]$BindHost, [int]$Port)
    return (Get-SwitcherooProbeUrl -BindHost $BindHost -Port $Port).TrimEnd("/") + "/health"
}

function Get-ParsedScQueryexPid {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return 0 }
    $m = [regex]::Match($Text, '(?im)^\s*PID\s*:\s*(\d+)')
    if ($m.Success) { return [int]$m.Groups[1].Value }
    return 0
}

function Get-ParsedNetstatListenPid {
    param([string]$Text, [int]$Port)
    if ([string]::IsNullOrWhiteSpace($Text)) { return 0 }
    $pattern = "(?im):$Port\s+\S+\s+LISTENING\s+(\d+)"
    $m = [regex]::Match($Text, $pattern)
    if ($m.Success) { return [int]$m.Groups[1].Value }
    return 0
}

function Get-MappedMonitorStatus {
    param(
        [bool]$ServiceExists,
        [string]$ServiceState = "",
        [int]$ServiceExitCode = 0,
        [bool]$HealthOk = $false,
        [bool]$PidAlive = $false,
        [bool]$StartingGrace = $false
    )
    if ($ServiceExists) {
        $key = (($ServiceState | Out-String).ToLower() -replace "\s", "").Trim()
        if ($key -eq "startpending") { return "Starting" }
        if ($key -eq "stoppending") { return "Stopping" }
        if ($key -eq "running") {
            if ($HealthOk) { return "Running" }
            if ($StartingGrace) { return "Starting" }
            return "Unreachable"
        }
        if ($key -eq "stopped") {
            if ($ServiceExitCode -ne 0 -and $ServiceExitCode -ne 1077) {
                return "Stopped (failed)"
            }
            return "Stopped"
        }
        if ($key -eq "paused") { return "Stopped" }
    }
    if ($HealthOk) { return "Running" }
    if ($PidAlive) {
        if ($StartingGrace) { return "Starting" }
        return "Unreachable"
    }
    return "Stopped"
}

function Get-SwitcherooPidLabel {
    param([int]$ProcessId, [string]$Status)
    if ($ProcessId -gt 0) { return "PID $ProcessId" }
    if ($Status -eq "Running" -or $Status -eq "Starting" -or $Status -eq "Unreachable" -or $Status -eq "Stopping") {
        return "PID resolving..."
    }
    return "PID -"
}

function Get-SwitcherooHealthLabel {
    param([object]$Ok, [object]$LatencyMs, [string]$ErrorText = "")
    if ($null -eq $Ok) { return "Health: not checked" }
    $latency = "-"
    if ($null -ne $LatencyMs) { $latency = "$([int]$LatencyMs)ms" }
    if ([bool]$Ok) { return "Health: ok $latency" }
    $detail = if ($ErrorText) { $ErrorText.Trim() } else { "fail" }
    $detail = $detail -replace "[\r\n]+", " "
    if ($detail.Length -gt 80) { $detail = $detail.Substring(0, 77) + "..." }
    return "Health: fail $latency ($detail)"
}

function Get-FileTailLines {
    param(
        [string]$Path,
        [int]$MaxLines = 200,
        [long]$AfterOffset = 0,
        [int]$MaxFollowBytes = 65536
    )
    $empty = [pscustomobject]@{ Lines = @(); Offset = [int64]0 }
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return $empty
    }
    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $item) { return $empty }
    $size = [int64]$item.Length
    $bytes = $null
    $fs = $null
    try {
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $size = [int64]$fs.Length
        if ($AfterOffset -gt 0 -and $AfterOffset -le $size) {
            [void]$fs.Seek($AfterOffset, [System.IO.SeekOrigin]::Begin)
            $remain = [int]($size - $AfterOffset)
            if ($MaxFollowBytes -gt 0 -and $remain -gt $MaxFollowBytes) {
                # Skip a huge burst rather than stalling the UI; jump to near end.
                [void]$fs.Seek(-$MaxFollowBytes, [System.IO.SeekOrigin]::End)
                $remain = $MaxFollowBytes
            }
            $bytes = New-Object byte[] $remain
            $read = $fs.Read($bytes, 0, $remain)
            if ($read -lt $remain) {
                $trim = New-Object byte[] $read
                [Array]::Copy($bytes, $trim, $read)
                $bytes = $trim
            }
        }
        else {
            $cap = 256000
            if ($size -gt $cap) {
                [void]$fs.Seek(-$cap, [System.IO.SeekOrigin]::End)
            }
            else {
                [void]$fs.Seek(0, [System.IO.SeekOrigin]::Begin)
            }
            $remain = [int]($fs.Length - $fs.Position)
            $bytes = New-Object byte[] $remain
            [void]$fs.Read($bytes, 0, $remain)
        }
    }
    catch {
        return $empty
    }
    finally {
        if ($fs) { $fs.Dispose() }
    }
    if (-not $bytes -or $bytes.Length -eq 0) {
        return [pscustomobject]@{ Lines = @(); Offset = $size }
    }
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    $lines = $text -split "\r?\n", 0, "RegexMatch"
    if ($lines.Count -gt 0 -and $lines[-1] -eq "") {
        if ($lines.Count -eq 1) { $lines = @() }
        else { $lines = $lines[0..($lines.Count - 2)] }
    }
    if ($AfterOffset -le 0 -or $AfterOffset -gt $size) {
        if ($MaxLines -gt 0 -and $lines.Count -gt $MaxLines) {
            $lines = $lines[($lines.Count - $MaxLines)..($lines.Count - 1)]
        }
    }
    elseif ($MaxLines -gt 0 -and $lines.Count -gt $MaxLines) {
        $lines = $lines[($lines.Count - $MaxLines)..($lines.Count - 1)]
    }
    return [pscustomobject]@{ Lines = @($lines); Offset = $size }
}

function Test-SwitcherooPidAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ($null -ne $p)
    }
    catch {
        return $false
    }
}

function Read-SwitcherooPidFile {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return 0 }
    try {
        $raw = (Get-Content -LiteralPath $Path -TotalCount 1 -ErrorAction Stop)
        if ($null -eq $raw) { return 0 }
        $n = 0
        if ([int]::TryParse($raw.ToString().Trim(), [ref]$n)) { return $n }
    }
    catch { }
    return 0
}

function Get-SwitcherooListenPid {
    param(
        [int]$Port,
        [switch]$Force
    )
    if ($Port -le 0) { return 0 }

    $now = Get-Date
    if (-not $Force -and
        $script:SwitcherooListenPidCachePort -eq $Port -and
        ($now - $script:SwitcherooListenPidCacheAt).TotalSeconds -lt $script:SwitcherooListenPidCacheTtlSec) {
        $cached = [int]$script:SwitcherooListenPidCache
        if ($cached -le 0 -or (Test-SwitcherooPidAlive -ProcessId $cached)) {
            return $cached
        }
    }

    $found = 0
    try {
        # LocalPort filter is far cheaper than enumerating all connections / full netstat.
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        foreach ($c in @($conns)) {
            if ($c.OwningProcess -gt 0) {
                $found = [int]$c.OwningProcess
                break
            }
        }
    }
    catch { }

    if ($found -le 0) {
        try {
            # Narrow netstat: TCP only + findstr for this port (never dump full -ano every tick).
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = "cmd.exe"
            $psi.Arguments = "/c netstat -ano -p tcp | findstr `":$Port `" | findstr /I LISTENING"
            $psi.UseShellExecute = $false
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $psi.CreateNoWindow = $true
            $proc = [System.Diagnostics.Process]::Start($psi)
            $text = $proc.StandardOutput.ReadToEnd()
            [void]$proc.WaitForExit(3000)
            $found = Get-ParsedNetstatListenPid -Text $text -Port $Port
        }
        catch {
            $found = 0
        }
    }

    $script:SwitcherooListenPidCache = $found
    $script:SwitcherooListenPidCachePort = $Port
    $script:SwitcherooListenPidCacheAt = $now
    return $found
}

function Get-SwitcherooPythonPid {
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop
        foreach ($p in @($procs)) {
            $cmd = [string]$p.CommandLine
            if ($cmd -and ($cmd -match '-m\s+app\b' -or $cmd -match '-m app')) {
                return [int]$p.ProcessId
            }
        }
    }
    catch { }
    return 0
}

function Get-SwitcherooServiceInfo {
    param(
        [string]$Name = "Switcheroo",
        [switch]$Force
    )
    $now = Get-Date
    if (-not $Force -and
        $null -ne $script:SwitcherooServiceCache -and
        $script:SwitcherooServiceCache.Name -eq $Name -and
        ($now - $script:SwitcherooServiceCacheAt).TotalSeconds -lt $script:SwitcherooServiceCacheTtlSec) {
        return $script:SwitcherooServiceCache
    }

    $info = [pscustomobject]@{
        Exists      = $false
        Name        = $Name
        State       = ""
        ProcessId   = 0
        ExitCode    = 0
        StartMode   = ""
        DisplayName = $Name
    }
    try {
        $svc = Get-CimInstance -ClassName Win32_Service -Filter "Name='$Name'" -ErrorAction Stop
        if ($svc) {
            $info.Exists = $true
            $info.State = [string]$svc.State
            $info.ProcessId = [int]$svc.ProcessId
            $info.ExitCode = [int]$svc.ExitCode
            $info.StartMode = [string]$svc.StartMode
            $info.DisplayName = [string]$svc.DisplayName
            $script:SwitcherooServiceCache = $info
            $script:SwitcherooServiceCacheAt = $now
            return $info
        }
    }
    catch { }
    try {
        $text = (sc.exe queryex $Name 2>&1 | Out-String)
        $global:LASTEXITCODE = 0
        if ($text -match "1060" -or $text -match "does not exist") {
            $script:SwitcherooServiceCache = $info
            $script:SwitcherooServiceCacheAt = $now
            return $info
        }
        if ($text -match "SERVICE_NAME") {
            $info.Exists = $true
            $info.ProcessId = Get-ParsedScQueryexPid -Text $text
            if ($text -match "RUNNING") { $info.State = "Running" }
            elseif ($text -match "START_PENDING") { $info.State = "Start Pending" }
            elseif ($text -match "STOP_PENDING") { $info.State = "Stop Pending" }
            elseif ($text -match "STOPPED") { $info.State = "Stopped" }
        }
    }
    catch { }
    $script:SwitcherooServiceCache = $info
    $script:SwitcherooServiceCacheAt = $now
    return $info
}

function Get-SwitcherooHealth {
    param([string]$BindHost, [int]$Port, [int]$TimeoutSec = 2)
    $uri = Get-SwitcherooHealthUrl -BindHost $BindHost -Port $Port
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $resp = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        $sw.Stop()
        $ok = ($resp.StatusCode -eq 200 -and $resp.Content -match '"ok"\s*:\s*true')
        $err = $null
        if (-not $ok) { $err = "HTTP $($resp.StatusCode)" }
        return [pscustomobject]@{
            Ok         = [bool]$ok
            LatencyMs  = [int]$sw.ElapsedMilliseconds
            Error      = $err
            Url        = $uri
        }
    }
    catch {
        $sw.Stop()
        $msg = $_.Exception.Message
        if ($_.Exception.InnerException) {
            $msg = $_.Exception.InnerException.Message
        }
        return [pscustomobject]@{
            Ok         = $false
            LatencyMs  = [int]$sw.ElapsedMilliseconds
            Error      = $msg
            Url        = $uri
        }
    }
}

function Resolve-SwitcherooPid {
    param(
        [int]$Port,
        [string]$PidFile,
        [int]$ServiceProcessId = 0,
        [int]$ChildPid = 0,
        [switch]$AllowListenProbe,
        [switch]$AllowPythonProbe
    )
    # Cheap sources first: never hit listen/netstat/WMI when service/child/pidfile is alive.
    $filePid = Read-SwitcherooPidFile -Path $PidFile
    foreach ($id in @($ChildPid, $ServiceProcessId, $filePid)) {
        if ($id -gt 0 -and (Test-SwitcherooPidAlive -ProcessId $id)) {
            return [int]$id
        }
    }

    $listen = 0
    if ($AllowListenProbe) {
        $listen = Get-SwitcherooListenPid -Port $Port
        if ($listen -gt 0 -and (Test-SwitcherooPidAlive -ProcessId $listen)) {
            return [int]$listen
        }
    }
    else {
        # Use cached listen PID if still fresh/alive (no new probe).
        if ($script:SwitcherooListenPidCachePort -eq $Port -and
            $script:SwitcherooListenPidCache -gt 0 -and
            (Test-SwitcherooPidAlive -ProcessId $script:SwitcherooListenPidCache)) {
            return [int]$script:SwitcherooListenPidCache
        }
    }

    $pyPid = 0
    if ($AllowPythonProbe -and $listen -le 0 -and $filePid -le 0 -and $ServiceProcessId -le 0 -and $ChildPid -le 0) {
        $pyPid = Get-SwitcherooPythonPid
        if ($pyPid -gt 0 -and (Test-SwitcherooPidAlive -ProcessId $pyPid)) {
            return [int]$pyPid
        }
    }

    if ($listen -gt 0) { return [int]$listen }
    if ($ServiceProcessId -gt 0) { return [int]$ServiceProcessId }
    if ($filePid -gt 0) { return [int]$filePid }
    if ($ChildPid -gt 0) { return [int]$ChildPid }
    return 0
}

function Test-IsAdministrator {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $p = New-Object Security.Principal.WindowsPrincipal($id)
        return [bool]$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    catch {
        return $false
    }
}
