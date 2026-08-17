#Requires -Version 5.1
<#
.SYNOPSIS
  Switcheroo Launch Control - out-of-band monitor for the Windows service (or a local python process).

.DESCRIPTION
  Quiet by default: shows service status, PID, health, and status-change / error events.
  Full log follow is opt-in via "Follow logs". Start/Stop/Restart target the Windows
  service when installed; otherwise Start supervises `python -m app` for this session.
#>
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepoRoot = Split-Path -Parent $script:ScriptDir
$monitorPs1 = Join-Path $script:ScriptDir "Switcheroo.Monitor.ps1"
if (-not (Test-Path -LiteralPath $monitorPs1)) {
    [System.Windows.Forms.MessageBox]::Show(
        "Missing $monitorPs1",
        "Switcheroo Launch Control",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}
. $monitorPs1

$script:DataDir = Join-Path $script:RepoRoot "data"
$script:PidFile = Join-Path $script:DataDir "switcheroo.pid"
$script:DiagFlag = Join-Path $script:DataDir "diagnostics.enabled"
$script:LogSwitcheroo = Join-Path $script:DataDir "switcheroo.log"
$script:LogAudit = Join-Path $script:DataDir "audit.log"
$script:LogDiagnostics = Join-Path $script:DataDir "diagnostics.log"
$script:Child = $null
$script:StartedAt = $null
$script:Busy = $false
$script:BindHost = "127.0.0.1"
$script:BindPort = 8080
$script:PythonPath = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"
$script:Form = $null
$script:ConsoleBox = $null
$script:StatusLabel = $null
$script:MetaLabel = $null
$script:DiagLabel = $null
$script:PidLabel = $null
$script:HealthLabel = $null
$script:ModeLabel = $null
$script:ServiceLabel = $null
$script:LastChangeLabel = $null
$script:FollowLogsBtn = $null
$script:AdminLabel = $null
$script:LastStatus = ""
$script:LastStatusChangeAt = $null
$script:LastStatusChangeMsg = ""
$script:LogOffset = [int64]0
$script:LogReady = $false
$script:FollowLogs = $false
$script:PendingLines = New-Object System.Collections.Generic.Queue[string]
$script:IsAdmin = [bool](Test-IsAdministrator)
$script:ServiceName = "Switcheroo"
$script:TickBusy = $false
$script:TickCount = 0
$script:LastBindImportAt = [datetime]::MinValue
$script:LastListenProbeAt = [datetime]::MinValue
$script:BindImportIntervalSec = 30
$script:ListenProbeIntervalSec = 12
$script:ConsoleApproxChars = 0
$script:MaxConsoleChars = 80000
$script:QuietIssueCapPerTick = 12
$script:ProbeInFlight = $false
$script:ProbeRunspace = $null
$script:Closing = $false
$script:MonitorPs1Path = $monitorPs1
$script:StatusPollIntervalMs = 4000
$script:ProbeGate = [hashtable]::Synchronized(@{ InFlight = $false })
$script:ProbeAsyncResult = $null
$script:ProbePowershell = $null
$script:ProbeStartedAt = [datetime]::MinValue
$script:NextProbeAt = [datetime]::MinValue
function Read-DotEnv {
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

function Import-BindSettings {
    $envMap = Read-DotEnv
    if ($envMap.ContainsKey("SWITCHEROO_HOST") -and $envMap["SWITCHEROO_HOST"]) {
        $script:BindHost = $envMap["SWITCHEROO_HOST"]
    }
    if ($envMap.ContainsKey("SWITCHEROO_PORT") -and $envMap["SWITCHEROO_PORT"]) {
        $parsed = 0
        if ([int]::TryParse($envMap["SWITCHEROO_PORT"], [ref]$parsed)) {
            $script:BindPort = $parsed
        }
    }
    $dataRel = "data"
    if ($envMap.ContainsKey("SWITCHEROO_DATA_DIR") -and $envMap["SWITCHEROO_DATA_DIR"]) {
        $dataRel = $envMap["SWITCHEROO_DATA_DIR"]
    }
    if ([System.IO.Path]::IsPathRooted($dataRel)) {
        $script:DataDir = $dataRel
    }
    else {
        $script:DataDir = Join-Path $script:RepoRoot $dataRel
    }
    $script:PidFile = Join-Path $script:DataDir "switcheroo.pid"
    $script:DiagFlag = Join-Path $script:DataDir "diagnostics.enabled"
    $logRel = "data/switcheroo.log"
    if ($envMap.ContainsKey("SWITCHEROO_LOG_FILE") -and $envMap["SWITCHEROO_LOG_FILE"]) {
        $logRel = $envMap["SWITCHEROO_LOG_FILE"]
    }
    if ([System.IO.Path]::IsPathRooted($logRel)) {
        $script:LogSwitcheroo = $logRel
    }
    else {
        $script:LogSwitcheroo = Join-Path $script:RepoRoot ($logRel -replace "/", "\")
    }
    $script:LogAudit = Join-Path $script:DataDir "audit.log"
    $script:LogDiagnostics = Join-Path $script:DataDir "diagnostics.log"
}

function Add-ConsoleLine {
    param([string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) { return }
    [void]$script:PendingLines.Enqueue($Message)
}

function Write-LcLog {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "HH:mm:ss"
    Add-ConsoleLine "[$ts] [$Level] $Message"
    Flush-ConsoleQueue
}

function Flush-ConsoleQueue {
    if (-not $script:ConsoleBox) { return }
    if (-not $script:ConsoleBox.IsHandleCreated) { return }
    if ($script:PendingLines.Count -eq 0) { return }
    $sb = New-Object System.Text.StringBuilder
    $drained = 0
    # Cap UI work per flush so a log burst cannot freeze the form.
    $cap = if ($script:FollowLogs) { 80 } else { 24 }
    while ($script:PendingLines.Count -gt 0 -and $drained -lt $cap) {
        [void]$sb.AppendLine($script:PendingLines.Dequeue())
        $drained++
    }
    $chunk = $sb.ToString()
    if ([string]::IsNullOrEmpty($chunk)) { return }
    try {
        $box = $script:ConsoleBox
        $box.AppendText($chunk)
        $script:ConsoleApproxChars += $chunk.Length
        # Avoid RichTextBox.Lines (very expensive); truncate by character budget.
        if ($script:ConsoleApproxChars -gt $script:MaxConsoleChars) {
            $text = $box.Text
            if ($text.Length -gt 60000) {
                $box.Text = $text.Substring($text.Length - 60000)
                $script:ConsoleApproxChars = $box.Text.Length
            }
            else {
                $script:ConsoleApproxChars = $text.Length
            }
        }
        # Auto-scroll only while following logs — ScrollToCaret is expensive during drag/focus.
        if ($script:FollowLogs) {
            $box.SelectionStart = $box.Text.Length
            [void]$box.ScrollToCaret()
        }
    }
    catch {
        # Prefer not to spam the host console; drop on failure.
    }
}

function Test-ImportantLogLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return $false }
    return [bool]($Line -match '(?i)\b(ERROR|CRITICAL|FATAL|EXCEPTION|WARN(?:ING)?|FAIL(?:ED|URE)?)\b')
}

function Update-LogTail {
    # Always advance the file offset so enabling Follow logs later does not dump history.
    $after = if ($script:LogReady) { $script:LogOffset } else { [int64]0 }
    $app = Get-FileTailLines -Path $script:LogSwitcheroo -MaxLines 120 -AfterOffset $after -MaxFollowBytes 65536
    $script:LogOffset = [int64]$app.Offset

    if (-not $script:LogReady) {
        $script:LogReady = $true
        if ($script:FollowLogs) {
            Add-ConsoleLine "Following $($script:LogSwitcheroo)"
            if ($app.Lines -and $app.Lines.Count -gt 0) {
                $lines = @($app.Lines)
                if ($lines.Count -gt 40) { $lines = $lines[($lines.Count - 40)..($lines.Count - 1)] }
                foreach ($line in $lines) { Add-ConsoleLine $line }
            }
            else {
                Add-ConsoleLine "No app log lines yet."
            }
        }
        Flush-ConsoleQueue
        return
    }

    if (-not ($app.Lines -and $app.Lines.Count -gt 0)) {
        Flush-ConsoleQueue
        return
    }

    $lines = @($app.Lines)
    if ($script:FollowLogs) {
        if ($lines.Count -gt 60) {
            Add-ConsoleLine "[log] … skipped $($lines.Count - 60) lines (catching up)"
            $lines = $lines[($lines.Count - 60)..($lines.Count - 1)]
        }
        foreach ($line in $lines) { Add-ConsoleLine $line }
    }
    else {
        $important = @($lines | Where-Object { Test-ImportantLogLine $_ })
        if ($important.Count -gt $script:QuietIssueCapPerTick) {
            Add-ConsoleLine "[issues] … $($important.Count - $script:QuietIssueCapPerTick) older issue lines skipped"
            $important = $important[($important.Count - $script:QuietIssueCapPerTick)..($important.Count - 1)]
        }
        foreach ($line in $important) { Add-ConsoleLine $line }
    }
    Flush-ConsoleQueue
}

function Set-FollowLogsMode {
    param([bool]$Enabled)
    $script:FollowLogs = $Enabled
    if ($script:FollowLogsBtn) {
        $script:FollowLogsBtn.Text = if ($Enabled) { "Follow logs: ON" } else { "Follow logs: OFF" }
    }
    if ($Enabled) {
        # Start from current end — do not flood with backlog.
        if (Test-Path -LiteralPath $script:LogSwitcheroo) {
            try {
                $script:LogOffset = [int64](Get-Item -LiteralPath $script:LogSwitcheroo).Length
            }
            catch {
                $script:LogOffset = [int64]0
            }
        }
        $script:LogReady = $true
        Write-LcLog "Follow logs ON — live tail of switcheroo.log"
    }
    else {
        Write-LcLog "Follow logs OFF — showing status changes and WARN/ERROR only"
    }
}

function Import-BindSettingsIfDue {
    param([switch]$Force)
    $now = Get-Date
    if (-not $Force -and ($now - $script:LastBindImportAt).TotalSeconds -lt $script:BindImportIntervalSec) {
        return
    }
    Import-BindSettings
    $script:LastBindImportAt = $now
}

function Get-RuntimeStatus {
    Import-BindSettingsIfDue
    $svc = Get-SwitcherooServiceInfo -Name $script:ServiceName
    $health = Get-SwitcherooHealth -BindHost $script:BindHost -Port $script:BindPort -TimeoutSec 1
    $childPid = 0
    if ($script:Child -and -not $script:Child.HasExited) {
        $childPid = [int]$script:Child.Id
    }
    $now = Get-Date
    $needListen = ($now - $script:LastListenProbeAt).TotalSeconds -ge $script:ListenProbeIntervalSec
    $pidVal = 0
    if ($needListen) {
        $pidVal = Resolve-SwitcherooPid -Port $script:BindPort -PidFile $script:PidFile `
            -ServiceProcessId $svc.ProcessId -ChildPid $childPid -AllowListenProbe
        $script:LastListenProbeAt = $now
    }
    else {
        $pidVal = Resolve-SwitcherooPid -Port $script:BindPort -PidFile $script:PidFile `
            -ServiceProcessId $svc.ProcessId -ChildPid $childPid
    }
    if ($pidVal -le 0 -and -not $svc.Exists -and -not $health.Ok) {
        # Last resort only when nothing else knows the process.
        $pidVal = Resolve-SwitcherooPid -Port $script:BindPort -PidFile $script:PidFile `
            -ServiceProcessId $svc.ProcessId -ChildPid $childPid -AllowListenProbe -AllowPythonProbe
        $script:LastListenProbeAt = $now
    }
    $alive = Test-SwitcherooPidAlive -ProcessId $pidVal
    $grace = $false
    if ($script:StartedAt) {
        $grace = ((Get-Date) - $script:StartedAt).TotalSeconds -lt 25
    }
    elseif ($svc.Exists -and $svc.State -eq "Start Pending") {
        $grace = $true
    }
    $name = Get-MappedMonitorStatus -ServiceExists $svc.Exists -ServiceState $svc.State -ServiceExitCode $svc.ExitCode -HealthOk $health.Ok -PidAlive $alive -StartingGrace $grace
    $mode = "attached process (python -m app)"
    if ($svc.Exists) {
        $mode = "Windows service '$($svc.Name)' ($($svc.StartMode))"
    }
    else {
        $mode = "no Windows service; attached/local process"
    }
    return [pscustomobject]@{
        Name    = $name
        Pid     = $pidVal
        Health  = $health
        Service = $svc
        Mode    = $mode
    }
}

function Apply-StatusFromSnapshot {
    param($Snapshot)
    if (-not $Snapshot) { return }

    $name = [string]$Snapshot.Name
    $pidVal = [int]$Snapshot.Pid
    $color = switch ($name) {
        "Running" { [System.Drawing.Color]::FromArgb(22, 122, 66) }
        "Starting" { [System.Drawing.Color]::FromArgb(178, 132, 0) }
        "Stopping" { [System.Drawing.Color]::FromArgb(178, 132, 0) }
        "Unreachable" { [System.Drawing.Color]::FromArgb(176, 42, 42) }
        "Stopped (failed)" { [System.Drawing.Color]::FromArgb(176, 42, 42) }
        default { [System.Drawing.Color]::FromArgb(90, 90, 90) }
    }
    if ($script:StatusLabel) {
        $script:StatusLabel.Text = $name
        $script:StatusLabel.ForeColor = $color
    }
    $probe = Get-SwitcherooProbeUrl -BindHost $script:BindHost -Port $script:BindPort
    if ($script:MetaLabel) {
        $script:MetaLabel.Text = "Bind $probe   Python $($script:PythonPath)"
    }
    if ($script:PidLabel) {
        $script:PidLabel.Text = Get-SwitcherooPidLabel -ProcessId $pidVal -Status $name
    }

    $svcExists = [bool]$Snapshot.ServiceExists
    if ($null -ne $Snapshot.Service) {
        $svcExists = [bool]$Snapshot.Service.Exists
        $svcState = [string]$Snapshot.Service.State
        $svcMode = [string]$Snapshot.Service.StartMode
        $svcExit = [int]$Snapshot.Service.ExitCode
    }
    else {
        $svcState = [string]$Snapshot.ServiceState
        $svcMode = [string]$Snapshot.ServiceStartMode
        $svcExit = [int]$Snapshot.ServiceExitCode
    }

    if ($script:ServiceLabel) {
        if ($svcExists) {
            $exitBit = ""
            if ($svcState -match "Stop" -and $svcExit -ne 0) {
                $exitBit = "  exit $svcExit"
            }
            $script:ServiceLabel.Text = "Service: $svcState ($svcMode)$exitBit"
        }
        else {
            $script:ServiceLabel.Text = "Service: not installed"
        }
    }

    $healthOk = $false
    $healthMs = $null
    $healthErr = ""
    if ($null -ne $Snapshot.Health) {
        $healthOk = [bool]$Snapshot.Health.Ok
        $healthMs = $Snapshot.Health.LatencyMs
        $healthErr = [string]$Snapshot.Health.Error
    }
    else {
        $healthOk = [bool]$Snapshot.HealthOk
        $healthMs = $Snapshot.HealthLatencyMs
        $healthErr = [string]$Snapshot.HealthError
    }

    if ($script:HealthLabel) {
        $script:HealthLabel.Text = Get-SwitcherooHealthLabel -Ok $healthOk -LatencyMs $healthMs -ErrorText $healthErr
        $script:HealthLabel.ForeColor = if ($healthOk) {
            [System.Drawing.Color]::FromArgb(46, 160, 90)
        }
        else {
            [System.Drawing.Color]::FromArgb(220, 120, 80)
        }
    }
    if ($script:ModeLabel) {
        $script:ModeLabel.Text = "Mode: $([string]$Snapshot.Mode)"
    }
    if ($script:LastChangeLabel) {
        if ($script:LastStatusChangeAt) {
            $ago = $script:LastStatusChangeAt.ToString("HH:mm:ss")
            $script:LastChangeLabel.Text = "Last change: $ago  $($script:LastStatusChangeMsg)"
        }
        else {
            $script:LastChangeLabel.Text = "Last change: —"
        }
    }
    $diagOn = Test-Path -LiteralPath $script:DiagFlag
    if ($script:DiagLabel) {
        $script:DiagLabel.Text = if ($diagOn) { "Diagnostics: ON" } else { "Diagnostics: OFF" }
        $script:DiagLabel.ForeColor = if ($diagOn) {
            [System.Drawing.Color]::FromArgb(22, 122, 66)
        }
        else {
            [System.Drawing.Color]::FromArgb(90, 90, 90)
        }
    }
    if ($script:AdminLabel) {
        if ($svcExists -and -not $script:IsAdmin) {
            $script:AdminLabel.Text = "Run as administrator to control the service"
            $script:AdminLabel.Visible = $true
        }
        elseif (-not $svcExists) {
            $script:AdminLabel.Text = "Service not installed. Use Install Windows service (UAC) for reboot-safe start."
            $script:AdminLabel.Visible = $true
        }
        else {
            $script:AdminLabel.Visible = $false
        }
    }
    if ($name -ne $script:LastStatus) {
        $pidText = Get-SwitcherooPidLabel -ProcessId $pidVal -Status $name
        $msg = "$name  $pidText"
        $script:LastStatusChangeAt = Get-Date
        $script:LastStatusChangeMsg = $msg
        if ($script:LastStatus) {
            Write-LcLog "Status: $msg"
        }
        elseif ($script:LastChangeLabel) {
            $script:LastChangeLabel.Text = "Last change: $($script:LastStatusChangeAt.ToString('HH:mm:ss'))  $msg"
        }
        $script:LastStatus = $name
    }
}

function Update-StatusUi {
    # Synchronous path for rare cases; prefer Start-StatusProbeAsync so the UI stays responsive.
    $st = Get-RuntimeStatus
    Apply-StatusFromSnapshot -Snapshot ([pscustomobject]@{
            Name              = $st.Name
            Pid               = $st.Pid
            Health            = $st.Health
            Service           = $st.Service
            Mode              = $st.Mode
            ServiceExists     = $st.Service.Exists
            ServiceState      = $st.Service.State
            ServiceStartMode  = $st.Service.StartMode
            ServiceExitCode   = $st.Service.ExitCode
            HealthOk          = $st.Health.Ok
            HealthLatencyMs   = $st.Health.LatencyMs
            HealthError       = $st.Health.Error
        })
}

function Initialize-BgProbeRunspace {
    if ($script:ProbeRunspace) { return }
    $rs = [System.Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace()
    $rs.ApartmentState = [System.Threading.ApartmentState]::MTA
    $rs.ThreadOptions = [System.Management.Automation.Runspaces.PSThreadOptions]::ReuseThread
    $rs.Open()
    $init = [powershell]::Create()
    $init.Runspace = $rs
    try {
        [void]$init.AddScript("param([string]`$p)`n. `$p").AddArgument($script:MonitorPs1Path)
        [void]$init.Invoke()
        if ($init.HadErrors) {
            $err = ($init.Streams.Error | Select-Object -First 1)
            throw "Probe runspace init failed: $err"
        }
    }
    finally {
        $init.Dispose()
    }
    $script:ProbeRunspace = $rs
}

function Close-BgProbeRunspace {
    $rs = $script:ProbeRunspace
    $script:ProbeRunspace = $null
    if (-not $rs) { return }
    try { $rs.Close() } catch { }
    try { $rs.Dispose() } catch { }
}

function Complete-StatusProbeOnUi {
    param($Holder)
    try {
        if ($script:Closing) { return }
        if ($Holder.Error -and -not $Holder.Ok) {
            Add-ConsoleLine "[monitor] $($Holder.Error)"
            Flush-ConsoleQueue
            return
        }
        $snap = $Holder.Result
        if (-not $snap) { return }
        Apply-StatusFromSnapshot -Snapshot $snap
        if ($snap.ListenUsed) {
            $script:LastListenProbeAt = Get-Date
        }
        if ($null -ne $snap.LogOffset) {
            $script:LogOffset = [int64]$snap.LogOffset
            $script:LogReady = $true
        }
        foreach ($line in @($snap.LogLines)) {
            if ($line) { Add-ConsoleLine $line }
        }
        Flush-ConsoleQueue
    }
    catch {
        Add-ConsoleLine "[monitor] $($_.Exception.Message)"
        Flush-ConsoleQueue
    }
    finally {
        $script:ProbeGate.InFlight = $false
        $script:ProbeInFlight = $false
        $script:ProbeAsyncResult = $null
        $script:ProbePowershell = $null
        $script:NextProbeAt = (Get-Date).AddMilliseconds($script:StatusPollIntervalMs)
    }
}

function Receive-StatusProbeIfReady {
    # Called only on the UI thread. Completes a finished background probe without blocking.
    if (-not $script:ProbeAsyncResult) { return $false }
    if (-not $script:ProbeAsyncResult.IsCompleted) {
        # Safety: abandon a stuck probe so the UI never wedges forever.
        if (((Get-Date) - $script:ProbeStartedAt).TotalSeconds -gt 30) {
            try {
                if ($script:ProbePowershell) { $script:ProbePowershell.Stop() }
            }
            catch { }
            try {
                if ($script:ProbePowershell) { $script:ProbePowershell.Dispose() }
            }
            catch { }
            $script:ProbeAsyncResult = $null
            $script:ProbePowershell = $null
            $script:ProbeGate.InFlight = $false
            $script:ProbeInFlight = $false
            Add-ConsoleLine "[monitor] status probe timed out"
            Flush-ConsoleQueue
        }
        return $false
    }

    $ps = $script:ProbePowershell
    $ar = $script:ProbeAsyncResult
    $holder = [hashtable]::Synchronized(@{
            Result = $null
            Error  = $null
            Ok     = $false
        })
    try {
        $out = $ps.EndInvoke($ar)
        if ($ps.HadErrors) {
            $err = $ps.Streams.Error | Select-Object -First 1
            if ($err) { $holder.Error = [string]$err }
        }
        if ($out -and $out.Count -gt 0) {
            $holder.Result = $out[$out.Count - 1]
            $holder.Ok = $true
        }
    }
    catch {
        $holder.Error = $_.Exception.Message
        $holder.Ok = $false
    }
    finally {
        try { $ps.Dispose() } catch { }
        $script:ProbeAsyncResult = $null
        $script:ProbePowershell = $null
    }
    Complete-StatusProbeOnUi -Holder $holder
    return $true
}

function Start-StatusProbeAsync {
    param([switch]$Force)
    if ($script:Closing -or $script:Busy) { return }
    if ($script:ProbeGate.InFlight) { return }
    if (-not $script:Form -or $script:Form.IsDisposed) { return }

    try {
        Initialize-BgProbeRunspace
    }
    catch {
        Add-ConsoleLine "[monitor] $($_.Exception.Message)"
        Flush-ConsoleQueue
        return
    }

    Import-BindSettingsIfDue -Force:$Force

    $now = Get-Date
    $needListen = $Force -or (($now - $script:LastListenProbeAt).TotalSeconds -ge $script:ListenProbeIntervalSec)
    $childPid = 0
    if ($script:Child -and -not $script:Child.HasExited) {
        try { $childPid = [int]$script:Child.Id } catch { $childPid = 0 }
    }
    $startedAtUtc = $null
    if ($script:StartedAt) {
        $startedAtUtc = [datetime]$script:StartedAt.ToUniversalTime()
    }

    $payload = @{
        ServiceName  = [string]$script:ServiceName
        BindHost     = [string]$script:BindHost
        BindPort     = [int]$script:BindPort
        PidFile      = [string]$script:PidFile
        ChildPid     = [int]$childPid
        StartedAtUtc = $startedAtUtc
        AllowListen  = [bool]$needListen
        ForceService = [bool]$Force
        LogPath      = [string]$script:LogSwitcheroo
        LogOffset    = [int64]$script:LogOffset
        LogReady     = [bool]$script:LogReady
        FollowLogs   = [bool]$script:FollowLogs
        QuietCap     = [int]$script:QuietIssueCapPerTick
    }

    $probeScript = @'
param($p)
$ErrorActionPreference = "Continue"
$listenUsed = [bool]$p.AllowListen
$svc = Get-SwitcherooServiceInfo -Name $p.ServiceName -Force:$([bool]$p.ForceService)
$health = Get-SwitcherooHealth -BindHost $p.BindHost -Port $p.BindPort -TimeoutSec 1
$childPid = [int]$p.ChildPid
if ($p.AllowListen) {
    $pidVal = Resolve-SwitcherooPid -Port $p.BindPort -PidFile $p.PidFile `
        -ServiceProcessId $svc.ProcessId -ChildPid $childPid -AllowListenProbe
}
else {
    $pidVal = Resolve-SwitcherooPid -Port $p.BindPort -PidFile $p.PidFile `
        -ServiceProcessId $svc.ProcessId -ChildPid $childPid
}
if ($pidVal -le 0 -and -not $svc.Exists -and -not $health.Ok) {
    $pidVal = Resolve-SwitcherooPid -Port $p.BindPort -PidFile $p.PidFile `
        -ServiceProcessId $svc.ProcessId -ChildPid $childPid -AllowListenProbe -AllowPythonProbe
    $listenUsed = $true
}
$alive = Test-SwitcherooPidAlive -ProcessId $pidVal
$grace = $false
if ($p.StartedAtUtc) {
    $grace = ([datetime]::UtcNow - [datetime]$p.StartedAtUtc).TotalSeconds -lt 25
}
elseif ($svc.Exists -and $svc.State -eq "Start Pending") {
    $grace = $true
}
$name = Get-MappedMonitorStatus -ServiceExists $svc.Exists -ServiceState $svc.State `
    -ServiceExitCode $svc.ExitCode -HealthOk $health.Ok -PidAlive $alive -StartingGrace $grace
if ($svc.Exists) {
    $mode = "Windows service '$($svc.Name)' ($($svc.StartMode))"
}
else {
    $mode = "no Windows service; attached/local process"
}

$logLines = New-Object System.Collections.Generic.List[string]
$logOffset = [int64]$p.LogOffset
$after = if ($p.LogReady) { [int64]$p.LogOffset } else { [int64]0 }
$app = Get-FileTailLines -Path $p.LogPath -MaxLines 120 -AfterOffset $after -MaxFollowBytes 65536
$logOffset = [int64]$app.Offset
if ($app.Lines -and $app.Lines.Count -gt 0) {
    $lines = @($app.Lines)
    if ($p.FollowLogs) {
        if ($lines.Count -gt 60) {
            [void]$logLines.Add("[log] … skipped $($lines.Count - 60) lines (catching up)")
            $lines = $lines[($lines.Count - 60)..($lines.Count - 1)]
        }
        foreach ($line in $lines) { [void]$logLines.Add([string]$line) }
    }
    else {
        foreach ($line in $lines) {
            if ($line -match '(?i)\b(ERROR|CRITICAL|FATAL|EXCEPTION|WARN(?:ING)?|FAIL(?:ED|URE)?)\b') {
                [void]$logLines.Add([string]$line)
            }
        }
        if ($logLines.Count -gt $p.QuietCap) {
            $skip = $logLines.Count - $p.QuietCap
            $kept = $logLines.GetRange($logLines.Count - $p.QuietCap, $p.QuietCap)
            $logLines.Clear()
            [void]$logLines.Add("[issues] … $skip older issue lines skipped")
            foreach ($line in $kept) { [void]$logLines.Add($line) }
        }
    }
}

[pscustomobject]@{
    Name             = $name
    Pid              = [int]$pidVal
    HealthOk         = [bool]$health.Ok
    HealthLatencyMs  = $health.LatencyMs
    HealthError      = $health.Error
    ServiceExists    = [bool]$svc.Exists
    ServiceState     = [string]$svc.State
    ServiceStartMode = [string]$svc.StartMode
    ServiceExitCode  = [int]$svc.ExitCode
    Mode             = $mode
    ListenUsed       = [bool]$listenUsed
    LogLines         = @($logLines)
    LogOffset        = $logOffset
}
'@

    $ps = [powershell]::Create()
    $ps.Runspace = $script:ProbeRunspace
    [void]$ps.AddScript($probeScript).AddArgument($payload)

    try {
        $script:ProbeGate.InFlight = $true
        $script:ProbeInFlight = $true
        $script:ProbePowershell = $ps
        $script:ProbeStartedAt = Get-Date
        $script:ProbeAsyncResult = $ps.BeginInvoke()
    }
    catch {
        $script:ProbeGate.InFlight = $false
        $script:ProbeInFlight = $false
        $script:ProbeAsyncResult = $null
        $script:ProbePowershell = $null
        try { $ps.Dispose() } catch { }
        Add-ConsoleLine "[monitor] $($_.Exception.Message)"
        Flush-ConsoleQueue
    }
}

function Ensure-Venv {
    $py = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"
    $script:PythonPath = $py
    if (Test-Path -LiteralPath $py) { return $py }
    Write-LcLog "Creating virtual environment .venv"
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python is not on PATH. Install Python 3.12 or newer and retry."
    }
    Push-Location $script:RepoRoot
    try {
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "python -m venv failed (exit $LASTEXITCODE)" }
        Write-LcLog "Installing requirements (first run)..."
        & $py -m pip install --upgrade pip
        & $py -m pip install -r (Join-Path $script:RepoRoot "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }
    }
    finally {
        Pop-Location
    }
    $envFile = Join-Path $script:RepoRoot ".env"
    $example = Join-Path $script:RepoRoot ".env.example"
    if (-not (Test-Path -LiteralPath $envFile) -and (Test-Path -LiteralPath $example)) {
        Copy-Item -LiteralPath $example -Destination $envFile
        Write-LcLog "Created .env from .env.example (lab defaults)"
    }
    return $py
}

function Start-SwitcherooProcess {
    $py = Ensure-Venv
    if (-not (Test-Path -LiteralPath $script:DataDir)) {
        New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    }
    Write-LcLog "Starting $py -m app (session process; install the Windows service for reboot-safe start)"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $py
    $psi.Arguments = "-m app"
    $psi.WorkingDirectory = $script:RepoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1"
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.EnableRaisingEvents = $true
    $outHandler = [System.Diagnostics.DataReceivedEventHandler] {
        param($sender, $e)
        if (-not $e.Data) { return }
        if ($script:FollowLogs -or (Test-ImportantLogLine $e.Data)) {
            Add-ConsoleLine $e.Data
        }
    }
    $proc.add_OutputDataReceived($outHandler)
    $proc.add_ErrorDataReceived($outHandler)
    [void]$proc.Start()
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()
    $script:Child = $proc
    $script:StartedAt = Get-Date
    if (-not (Test-Path -LiteralPath $script:DataDir)) {
        New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    }
    Set-Content -LiteralPath $script:PidFile -Value "$($proc.Id)" -Encoding ASCII
    Write-LcLog "Process started PID $($proc.Id)"
}

function Stop-SwitcherooProcess {
    $pidVal = 0
    if ($script:Child -and -not $script:Child.HasExited) {
        $pidVal = $script:Child.Id
    }
    if ($pidVal -le 0) { $pidVal = Read-SwitcherooPidFile -Path $script:PidFile }
    if ($pidVal -le 0) { $pidVal = Get-SwitcherooListenPid -Port $script:BindPort -Force }
    if ($pidVal -le 0 -or -not (Test-SwitcherooPidAlive -ProcessId $pidVal)) {
        Write-LcLog "Already stopped"
        if (Test-Path -LiteralPath $script:PidFile) {
            Remove-Item -LiteralPath $script:PidFile -Force -ErrorAction SilentlyContinue
        }
        $script:Child = $null
        return
    }
    Write-LcLog "Stopping PID $pidVal"
    try { [void]$script:Child.CloseMainWindow() } catch { }
    Start-Process -FilePath "taskkill.exe" -ArgumentList "/PID", "$pidVal", "/T" -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue | Out-Null
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline -and (Test-SwitcherooPidAlive -ProcessId $pidVal)) {
        Start-Sleep -Milliseconds 200
    }
    if (Test-SwitcherooPidAlive -ProcessId $pidVal) {
        Write-LcLog "Force-killing PID $pidVal" -Level "WARN"
        Start-Process -FilePath "taskkill.exe" -ArgumentList "/F", "/PID", "$pidVal", "/T" -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue | Out-Null
        try { $script:Child.Kill() } catch { }
    }
    $script:Child = $null
    $script:StartedAt = $null
    if (Test-Path -LiteralPath $script:PidFile) {
        Remove-Item -LiteralPath $script:PidFile -Force -ErrorAction SilentlyContinue
    }
    Write-LcLog "Stopped"
}

function Assert-CanControlService {
    if ($script:IsAdmin) { return $true }
    Write-LcLog "Run as administrator to control the service" -Level "ERROR"
    return $false
}

function Start-SwitcherooTarget {
    if ($script:Busy) { return }
    $script:Busy = $true
    try {
        Import-BindSettingsIfDue -Force
        $health = Get-SwitcherooHealth -BindHost $script:BindHost -Port $script:BindPort -TimeoutSec 2
        if ($health.Ok) {
            Write-LcLog "Already running (health ok). Refusing a second start."
            return
        }
        $svc = Get-SwitcherooServiceInfo -Name $script:ServiceName -Force
        if ($svc.Exists) {
            if (-not (Assert-CanControlService)) { return }
            Write-LcLog "Starting Windows service $($script:ServiceName)"
            $script:StartedAt = Get-Date
            Start-Service -Name $script:ServiceName -ErrorAction Stop
            Write-LcLog "Start-Service issued"
        }
        else {
            Start-SwitcherooProcess
        }
    }
    catch {
        Write-LcLog $_.Exception.Message -Level "ERROR"
        if ($_.Exception.Message -match "Access is denied|Cannot open|privilege") {
            Write-LcLog "Run as administrator to control the service" -Level "ERROR"
        }
    }
    finally {
        $script:Busy = $false
        [void](Start-StatusProbeAsync -Force)
    }
}

function Stop-SwitcherooTarget {
    if ($script:Busy) { return }
    $script:Busy = $true
    try {
        Import-BindSettingsIfDue -Force
        $svc = Get-SwitcherooServiceInfo -Name $script:ServiceName -Force
        if ($svc.Exists) {
            if (-not (Assert-CanControlService)) { return }
            Write-LcLog "Stopping Windows service $($script:ServiceName)"
            Stop-Service -Name $script:ServiceName -Force -ErrorAction Stop
            Write-LcLog "Stop-Service issued"
            $script:StartedAt = $null
        }
        else {
            Stop-SwitcherooProcess
        }
    }
    catch {
        Write-LcLog $_.Exception.Message -Level "ERROR"
        if ($_.Exception.Message -match "Access is denied|Cannot open|privilege") {
            Write-LcLog "Run as administrator to control the service" -Level "ERROR"
        }
    }
    finally {
        $script:Busy = $false
        [void](Start-StatusProbeAsync -Force)
    }
}

function Restart-SwitcherooTarget {
    $svc = Get-SwitcherooServiceInfo -Name $script:ServiceName -Force
    if ($svc.Exists) {
        if (-not (Assert-CanControlService)) { return }
        if ($script:Busy) { return }
        $script:Busy = $true
        try {
            Write-LcLog "Restarting Windows service $($script:ServiceName)"
            Restart-Service -Name $script:ServiceName -Force -ErrorAction Stop
            $script:StartedAt = Get-Date
            Write-LcLog "Restart-Service issued"
        }
        catch {
            Write-LcLog $_.Exception.Message -Level "ERROR"
        }
        finally {
            $script:Busy = $false
            [void](Start-StatusProbeAsync -Force)
        }
        return
    }
    Stop-SwitcherooTarget
    Start-Sleep -Milliseconds 400
    Start-SwitcherooTarget
}

function Open-PathOrFolder {
    param([string]$Path, [string]$FallbackDir)
    if (Test-Path -LiteralPath $Path) {
        Start-Process -FilePath $Path | Out-Null
        Write-LcLog "Opened $Path"
        return
    }
    if (-not (Test-Path -LiteralPath $FallbackDir)) {
        New-Item -ItemType Directory -Path $FallbackDir -Force | Out-Null
    }
    Start-Process -FilePath "explorer.exe" -ArgumentList $FallbackDir | Out-Null
    Write-LcLog "No file yet; opened $FallbackDir"
}

function Set-DiagnosticsMode {
    param([bool]$Enabled)
    if (-not (Test-Path -LiteralPath $script:DataDir)) {
        New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    }
    if ($Enabled) {
        Set-Content -LiteralPath $script:DiagFlag -Value "on" -Encoding ASCII
        Write-LcLog "Diagnostics ON (data\diagnostics.enabled). Reproduce the failure, then open diagnostics.log."
    }
    else {
        if (Test-Path -LiteralPath $script:DiagFlag) {
            Remove-Item -LiteralPath $script:DiagFlag -Force -ErrorAction SilentlyContinue
        }
        Write-LcLog "Diagnostics OFF"
    }
    [void](Start-StatusProbeAsync)
}

function Invoke-ElevatedScript {
    param([string]$FileName)
    $path = Join-Path $script:ScriptDir $FileName
    if (-not (Test-Path -LiteralPath $path)) {
        Write-LcLog "Missing $path" -Level "ERROR"
        return
    }
    $full = (Resolve-Path -LiteralPath $path).Path
    $needUac = -not $script:IsAdmin
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $full)
    if ($needUac) {
        Write-LcLog "Launching $FileName with UAC elevation"
        try {
            Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argList | Out-Null
        }
        catch {
            Write-LcLog "UAC cancelled or blocked — Windows service was not installed or changed." -Level "ERROR"
        }
        return
    }

    Write-LcLog "Already elevated — running $FileName in this token (no UAC spawn)"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = (($argList | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '""') + '"' } else { $_ }
    }) -join " ")
    $psi.WorkingDirectory = $script:RepoRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    try { $psi.EnvironmentVariables["LC_INSTALL_NOPAUSE"] = "1" } catch { }
    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
        if (-not $proc) {
            Write-LcLog "Installer did not start a process." -Level "ERROR"
            return
        }
        $stderr = $proc.StandardError.ReadToEnd()
        $stdout = $proc.StandardOutput.ReadToEnd()
        $proc.WaitForExit()
        if ($stderr) { Write-LcLog $stderr.TrimEnd() -Level "WARN" }
        if ($stdout) { Write-LcLog $stdout.TrimEnd() }
        Write-LcLog ("Installer exit code: {0}" -f $proc.ExitCode)
    }
    catch {
        Write-LcLog ("Install failed: {0}" -f $_.Exception.Message) -Level "ERROR"
    }
}

function New-LcButton {
    param([string]$Text, [int]$Y, [scriptblock]$Click)
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $Text
    $btn.Location = New-Object System.Drawing.Point(16, $Y)
    $btn.Size = New-Object System.Drawing.Size(248, 32)
    $btn.FlatStyle = [System.Windows.Forms.FlatStyle]::System
    $btn.Add_Click($Click)
    return $btn
}

Import-BindSettings
$script:LastBindImportAt = Get-Date

$form = New-Object System.Windows.Forms.Form
$form.Text = "Switcheroo Launch Control"
$form.Size = New-Object System.Drawing.Size(1040, 720)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.MinimumSize = New-Object System.Drawing.Size(900, 600)
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$script:Form = $form

$header = New-Object System.Windows.Forms.Panel
$header.Dock = [System.Windows.Forms.DockStyle]::Top
$header.Height = 72
$header.BackColor = [System.Drawing.Color]::FromArgb(27, 54, 93)
[void]$form.Controls.Add($header)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Switcheroo Launch Control"
$title.ForeColor = [System.Drawing.Color]::White
$title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 14)
$title.Location = New-Object System.Drawing.Point(16, 8)
$title.AutoSize = $true
[void]$header.Controls.Add($title)

$meta = New-Object System.Windows.Forms.Label
$meta.ForeColor = [System.Drawing.Color]::FromArgb(200, 214, 232)
$meta.Location = New-Object System.Drawing.Point(16, 40)
$meta.AutoSize = $true
$script:MetaLabel = $meta
[void]$header.Controls.Add($meta)

$body = New-Object System.Windows.Forms.TableLayoutPanel
$body.Dock = [System.Windows.Forms.DockStyle]::Fill
$body.ColumnCount = 2
$body.RowCount = 1
[void]$body.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 288)))
[void]$body.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
[void]$form.Controls.Add($body)

$left = New-Object System.Windows.Forms.Panel
$left.Dock = [System.Windows.Forms.DockStyle]::Fill
$left.BackColor = [System.Drawing.Color]::FromArgb(245, 246, 248)
$left.AutoScroll = $true
[void]$body.Controls.Add($left, 0, 0)

$right = New-Object System.Windows.Forms.TableLayoutPanel
$right.Dock = [System.Windows.Forms.DockStyle]::Fill
$right.ColumnCount = 1
$right.RowCount = 2
[void]$right.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 118)))
[void]$right.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
[void]$body.Controls.Add($right, 1, 0)

$healthBar = New-Object System.Windows.Forms.Panel
$healthBar.Dock = [System.Windows.Forms.DockStyle]::Fill
$healthBar.BackColor = [System.Drawing.Color]::FromArgb(32, 32, 32)
$right.Controls.Add($healthBar, 0, 0)

$svcLbl = New-Object System.Windows.Forms.Label
$svcLbl.Text = "Service: —"
$svcLbl.ForeColor = [System.Drawing.Color]::FromArgb(220, 220, 220)
$svcLbl.Location = New-Object System.Drawing.Point(12, 8)
$svcLbl.AutoSize = $true
$script:ServiceLabel = $svcLbl
$healthBar.Controls.Add($svcLbl)

$healthLbl = New-Object System.Windows.Forms.Label
$healthLbl.Text = "Health: not checked"
$healthLbl.ForeColor = [System.Drawing.Color]::FromArgb(220, 220, 220)
$healthLbl.Location = New-Object System.Drawing.Point(12, 30)
$healthLbl.AutoSize = $true
$script:HealthLabel = $healthLbl
$healthBar.Controls.Add($healthLbl)

$modeLbl = New-Object System.Windows.Forms.Label
$modeLbl.Text = "Mode: -"
$modeLbl.ForeColor = [System.Drawing.Color]::FromArgb(170, 170, 170)
$modeLbl.Location = New-Object System.Drawing.Point(12, 52)
$modeLbl.AutoSize = $true
$script:ModeLabel = $modeLbl
$healthBar.Controls.Add($modeLbl)

$lastChangeLbl = New-Object System.Windows.Forms.Label
$lastChangeLbl.Text = "Last change: —"
$lastChangeLbl.ForeColor = [System.Drawing.Color]::FromArgb(190, 190, 190)
$lastChangeLbl.Location = New-Object System.Drawing.Point(12, 74)
$lastChangeLbl.AutoSize = $true
$script:LastChangeLabel = $lastChangeLbl
$healthBar.Controls.Add($lastChangeLbl)

$eventsCaption = New-Object System.Windows.Forms.Label
$eventsCaption.Text = "Events / issues (status changes + WARN/ERROR; use Follow logs for full tail)"
$eventsCaption.ForeColor = [System.Drawing.Color]::FromArgb(150, 150, 150)
$eventsCaption.Location = New-Object System.Drawing.Point(12, 96)
$eventsCaption.AutoSize = $true
$healthBar.Controls.Add($eventsCaption)

$console = New-Object System.Windows.Forms.RichTextBox
$console.Dock = [System.Windows.Forms.DockStyle]::Fill
$console.ReadOnly = $true
$console.DetectUrls = $false
$console.BackColor = [System.Drawing.Color]::FromArgb(18, 18, 18)
$console.ForeColor = [System.Drawing.Color]::FromArgb(220, 220, 220)
$console.Font = New-Object System.Drawing.Font("Consolas", 9)
$console.HideSelection = $false
$console.WordWrap = $false
$console.ScrollBars = [System.Windows.Forms.RichTextBoxScrollBars]::Both
$script:ConsoleBox = $console
$right.Controls.Add($console, 0, 1)

$statusCaption = New-Object System.Windows.Forms.Label
$statusCaption.Text = "Status"
$statusCaption.Location = New-Object System.Drawing.Point(16, 16)
$statusCaption.AutoSize = $true
$left.Controls.Add($statusCaption)

$status = New-Object System.Windows.Forms.Label
$status.Text = "Stopped"
$status.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 16)
$status.Location = New-Object System.Drawing.Point(16, 36)
$status.AutoSize = $true
$script:StatusLabel = $status
$left.Controls.Add($status)

$pidLbl = New-Object System.Windows.Forms.Label
$pidLbl.Text = "PID -"
$pidLbl.Location = New-Object System.Drawing.Point(16, 72)
$pidLbl.AutoSize = $true
$script:PidLabel = $pidLbl
$left.Controls.Add($pidLbl)

$adminLbl = New-Object System.Windows.Forms.Label
$adminLbl.Text = ""
$adminLbl.ForeColor = [System.Drawing.Color]::FromArgb(160, 80, 0)
$adminLbl.Location = New-Object System.Drawing.Point(16, 94)
$adminLbl.Size = New-Object System.Drawing.Size(256, 32)
$adminLbl.Visible = $false
$script:AdminLabel = $adminLbl
$left.Controls.Add($adminLbl)

$y = 130
$left.Controls.Add((New-LcButton "Start" $y { Start-SwitcherooTarget }))
$y += 40
$left.Controls.Add((New-LcButton "Stop" $y { Stop-SwitcherooTarget }))
$y += 40
$left.Controls.Add((New-LcButton "Restart" $y { Restart-SwitcherooTarget }))
$y += 40
$left.Controls.Add((New-LcButton "Refresh status" $y {
    Import-BindSettingsIfDue -Force
    Write-LcLog "Status refresh requested"
    [void](Start-StatusProbeAsync -Force)
}))
$y += 40
$followBtn = New-LcButton "Follow logs: OFF" $y {
    Set-FollowLogsMode -Enabled (-not $script:FollowLogs)
}
$script:FollowLogsBtn = $followBtn
$left.Controls.Add($followBtn)
$y += 40
$left.Controls.Add((New-LcButton "Open in browser" $y {
    $url = (Get-SwitcherooProbeUrl -BindHost $script:BindHost -Port $script:BindPort).TrimEnd("/") + "/"
    Start-Process $url | Out-Null
    Write-LcLog "Opened $url"
}))

$y += 48
$svcCaption = New-Object System.Windows.Forms.Label
$svcCaption.Text = "Windows service"
$svcCaption.Location = New-Object System.Drawing.Point(16, $y)
$svcCaption.AutoSize = $true
$left.Controls.Add($svcCaption)
$y += 22
$left.Controls.Add((New-LcButton "Install Windows service" $y { Invoke-ElevatedScript "install-service.ps1" }))
$y += 40
$left.Controls.Add((New-LcButton "Uninstall Windows service" $y { Invoke-ElevatedScript "uninstall-service.ps1" }))

$y += 48
$folders = New-Object System.Windows.Forms.Label
$folders.Text = "Logs"
$folders.Location = New-Object System.Drawing.Point(16, $y)
$folders.AutoSize = $true
$left.Controls.Add($folders)
$y += 24
$left.Controls.Add((New-LcButton "Open data folder" $y {
    if (-not (Test-Path -LiteralPath $script:DataDir)) {
        New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    }
    Start-Process -FilePath "explorer.exe" -ArgumentList $script:DataDir | Out-Null
    Write-LcLog "Opened $($script:DataDir)"
}))
$y += 40
$left.Controls.Add((New-LcButton "Open switcheroo.log" $y {
    Open-PathOrFolder -Path $script:LogSwitcheroo -FallbackDir $script:DataDir
}))
$y += 40
$left.Controls.Add((New-LcButton "Open audit.log" $y {
    Open-PathOrFolder -Path $script:LogAudit -FallbackDir $script:DataDir
}))
$y += 40
$left.Controls.Add((New-LcButton "Open diagnostics.log" $y {
    Open-PathOrFolder -Path $script:LogDiagnostics -FallbackDir $script:DataDir
}))

$y += 48
$diagCaption = New-Object System.Windows.Forms.Label
$diagCaption.Text = "Diagnostics"
$diagCaption.Location = New-Object System.Drawing.Point(16, $y)
$diagCaption.AutoSize = $true
$left.Controls.Add($diagCaption)
$y += 22
$diagLbl = New-Object System.Windows.Forms.Label
$diagLbl.Text = "Diagnostics: OFF"
$diagLbl.Location = New-Object System.Drawing.Point(16, $y)
$diagLbl.AutoSize = $true
$script:DiagLabel = $diagLbl
$left.Controls.Add($diagLbl)
$y += 26
$left.Controls.Add((New-LcButton "Diagnostics ON" $y { Set-DiagnosticsMode -Enabled $true }))
$y += 40
$left.Controls.Add((New-LcButton "Diagnostics OFF" $y { Set-DiagnosticsMode -Enabled $false }))

$timer = New-Object System.Windows.Forms.Timer
# Short tick only polls completion / schedules work — never runs CIM/HTTP on the UI thread.
$timer.Interval = 250
$timer.Add_Tick({
    if ($script:Closing -or $script:Busy) { return }
    try {
        [void](Receive-StatusProbeIfReady)
        if ($script:ProbeGate.InFlight) { return }
        if ((Get-Date) -lt $script:NextProbeAt) { return }
        $script:TickCount++
        [void](Start-StatusProbeAsync)
    }
    catch {
        Add-ConsoleLine "[monitor] $($_.Exception.Message)"
        Flush-ConsoleQueue
    }
})
$form.Add_Shown({
    try {
        Write-LcLog "Launch Control ready (quiet mode). Status changes and WARN/ERROR appear here."
        Write-LcLog "Use Follow logs for a live tail of switcheroo.log."
        if ($script:IsAdmin) {
            Write-LcLog "Running elevated: Start/Stop/Restart can control the Windows service."
        }
        else {
            Write-LcLog "Not elevated: status, PID, and health still work. Run as administrator to Start/Stop/Restart the service."
        }
        # Seek to end so quiet mode does not dump historical INFO spam on first probe.
        if (Test-Path -LiteralPath $script:LogSwitcheroo) {
            try {
                $script:LogOffset = [int64](Get-Item -LiteralPath $script:LogSwitcheroo).Length
            }
            catch { }
        }
        $script:LogReady = $true
        try { Initialize-BgProbeRunspace } catch { }
        $script:NextProbeAt = Get-Date
        [void](Start-StatusProbeAsync -Force)
        $timer.Start()
    }
    catch {
        Add-ConsoleLine "[monitor] $($_.Exception.Message)"
        Flush-ConsoleQueue
    }
})
$form.Add_FormClosing({
    $script:Closing = $true
    $timer.Stop()
    try {
        if ($script:ProbePowershell -and $script:ProbeAsyncResult -and -not $script:ProbeAsyncResult.IsCompleted) {
            $script:ProbePowershell.Stop()
        }
    }
    catch { }
    try {
        if ($script:ProbePowershell) { $script:ProbePowershell.Dispose() }
    }
    catch { }
    $script:ProbeAsyncResult = $null
    $script:ProbePowershell = $null
    $script:ProbeGate.InFlight = $false
    Close-BgProbeRunspace
})

[void][System.Windows.Forms.Application]::Run($form)
