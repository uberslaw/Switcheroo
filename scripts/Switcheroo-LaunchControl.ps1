#Requires -Version 5.1
<#
.SYNOPSIS
  Switcheroo Launch Control - start/stop the site, live console, diagnostics.

.DESCRIPTION
  Supervises `python -m app` (PID + /health). Not a Windows service.
  Prefer scripts\Switcheroo-LaunchControl.cmd or the desktop shortcut.
#>
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepoRoot = Split-Path -Parent $script:ScriptDir
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
$script:LastStatus = ""

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

function Get-ProbeUrl {
    $h = $script:BindHost
    if ($h -eq "0.0.0.0" -or $h -eq "::" -or $h -eq "[::]") { $h = "127.0.0.1" }
    return "http://${h}:$($script:BindPort)"
}

function Write-LcLog {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    if (-not $script:ConsoleBox -or -not $script:ConsoleBox.IsHandleCreated) {
        Write-Host $line
        return
    }
    $append = {
        $box = $script:ConsoleBox
        $box.AppendText($line + [Environment]::NewLine)
        if ($box.Lines.Count -gt 2000) {
            $box.Text = ($box.Lines | Select-Object -Skip ($box.Lines.Count - 1500)) -join [Environment]::NewLine
        }
        $box.SelectionStart = $box.Text.Length
        $box.ScrollToCaret()
    }
    if ($script:ConsoleBox.InvokeRequired) {
        [void]$script:ConsoleBox.BeginInvoke([Action]$append)
    }
    else {
        & $append
    }
}

function Test-PidAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return $null -ne $p
    }
    catch {
        return $false
    }
}

function Read-PidFile {
    if (-not (Test-Path -LiteralPath $script:PidFile)) { return 0 }
    try {
        $raw = (Get-Content -LiteralPath $script:PidFile -TotalCount 1 -ErrorAction Stop).Trim()
        $n = 0
        if ([int]::TryParse($raw, [ref]$n)) { return $n }
    }
    catch { }
    return 0
}

function Write-PidFile {
    param([int]$ProcessId)
    if (-not (Test-Path -LiteralPath $script:DataDir)) {
        New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    }
    Set-Content -LiteralPath $script:PidFile -Value "$ProcessId" -Encoding ASCII
}

function Test-HealthOk {
    $uri = (Get-ProbeUrl).TrimEnd("/") + "/health"
    try {
        $resp = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 2
        return [bool]($resp.ok -eq $true)
    }
    catch {
        return $false
    }
}

function Get-RuntimeStatus {
    $health = Test-HealthOk
    $pidVal = 0
    if ($script:Child -and -not $script:Child.HasExited) {
        $pidVal = $script:Child.Id
    }
    if ($pidVal -le 0) { $pidVal = Read-PidFile }
    $alive = Test-PidAlive -ProcessId $pidVal
    if ($health) { return [pscustomobject]@{ Name = "Running"; Pid = $pidVal } }
    if ($alive) {
        $ageOk = $false
        if ($script:StartedAt) {
            $ageOk = ((Get-Date) - $script:StartedAt).TotalSeconds -lt 20
        }
        if ($ageOk) { return [pscustomobject]@{ Name = "Starting"; Pid = $pidVal } }
        return [pscustomobject]@{ Name = "Unreachable"; Pid = $pidVal }
    }
    return [pscustomobject]@{ Name = "Stopped"; Pid = 0 }
}

function Update-StatusUi {
    Import-BindSettings
    $st = Get-RuntimeStatus
    $color = switch ($st.Name) {
        "Running" { [System.Drawing.Color]::FromArgb(22, 122, 66) }
        "Starting" { [System.Drawing.Color]::FromArgb(178, 132, 0) }
        "Unreachable" { [System.Drawing.Color]::FromArgb(176, 42, 42) }
        default { [System.Drawing.Color]::FromArgb(90, 90, 90) }
    }
    if ($script:StatusLabel) {
        $script:StatusLabel.Text = $st.Name
        $script:StatusLabel.ForeColor = $color
    }
    $probe = Get-ProbeUrl
    if ($script:MetaLabel) {
        $script:MetaLabel.Text = "Bind $probe   Python $($script:PythonPath)"
    }
    $pidText = if ($st.Pid -gt 0) { "PID $($st.Pid)" } else { "PID -" }
    if ($script:PidLabel) { $script:PidLabel.Text = $pidText }
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
    if ($st.Name -ne $script:LastStatus) {
        if ($script:LastStatus) { Write-LcLog "Status: $($st.Name)" }
        $script:LastStatus = $st.Name
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
    if ($script:Busy) { return }
    $script:Busy = $true
    try {
        Import-BindSettings
        if (Test-HealthOk) {
            Write-LcLog "Already running (health ok). Refusing a second start."
            Update-StatusUi
            return
        }
        $py = Ensure-Venv
        if (-not (Test-Path -LiteralPath $script:DataDir)) {
            New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
        }
        Write-LcLog "Starting $py -m app"
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
            if ($e.Data) { Write-LcLog $e.Data }
        }
        $proc.add_OutputDataReceived($outHandler)
        $proc.add_ErrorDataReceived($outHandler)
        [void]$proc.Start()
        $proc.BeginOutputReadLine()
        $proc.BeginErrorReadLine()
        $script:Child = $proc
        $script:StartedAt = Get-Date
        Write-PidFile -ProcessId $proc.Id
        Write-LcLog "Process started PID $($proc.Id)"
    }
    catch {
        Write-LcLog $_.Exception.Message -Level "ERROR"
    }
    finally {
        $script:Busy = $false
        Update-StatusUi
    }
}

function Stop-SwitcherooProcess {
    if ($script:Busy) { return }
    $script:Busy = $true
    try {
        $pidVal = 0
        if ($script:Child -and -not $script:Child.HasExited) {
            $pidVal = $script:Child.Id
        }
        if ($pidVal -le 0) { $pidVal = Read-PidFile }
        if ($pidVal -le 0 -or -not (Test-PidAlive -ProcessId $pidVal)) {
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
        while ((Get-Date) -lt $deadline -and (Test-PidAlive -ProcessId $pidVal)) {
            Start-Sleep -Milliseconds 200
        }
        if (Test-PidAlive -ProcessId $pidVal) {
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
    catch {
        Write-LcLog $_.Exception.Message -Level "ERROR"
    }
    finally {
        $script:Busy = $false
        Update-StatusUi
    }
}

function Restart-SwitcherooProcess {
    Stop-SwitcherooProcess
    Start-Sleep -Milliseconds 400
    Start-SwitcherooProcess
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
    Update-StatusUi
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

$form = New-Object System.Windows.Forms.Form
$form.Text = "Switcheroo Launch Control"
$form.Size = New-Object System.Drawing.Size(1000, 680)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.MinimumSize = New-Object System.Drawing.Size(860, 560)
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$script:Form = $form

$header = New-Object System.Windows.Forms.Panel
$header.Dock = [System.Windows.Forms.DockStyle]::Top
$header.Height = 72
$header.BackColor = [System.Drawing.Color]::FromArgb(27, 54, 93)
$form.Controls.Add($header)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Switcheroo Launch Control"
$title.ForeColor = [System.Drawing.Color]::White
$title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 14)
$title.Location = New-Object System.Drawing.Point(16, 8)
$title.AutoSize = $true
$header.Controls.Add($title)

$meta = New-Object System.Windows.Forms.Label
$meta.ForeColor = [System.Drawing.Color]::FromArgb(200, 214, 232)
$meta.Location = New-Object System.Drawing.Point(16, 40)
$meta.AutoSize = $true
$script:MetaLabel = $meta
$header.Controls.Add($meta)

$left = New-Object System.Windows.Forms.Panel
$left.Dock = [System.Windows.Forms.DockStyle]::Left
$left.Width = 280
$left.BackColor = [System.Drawing.Color]::FromArgb(245, 246, 248)
$left.AutoScroll = $true
$form.Controls.Add($left)

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

$y = 104
$left.Controls.Add((New-LcButton "Start" $y { Start-SwitcherooProcess }))
$y += 40
$left.Controls.Add((New-LcButton "Stop" $y { Stop-SwitcherooProcess }))
$y += 40
$left.Controls.Add((New-LcButton "Restart" $y { Restart-SwitcherooProcess }))
$y += 40
$left.Controls.Add((New-LcButton "Refresh status" $y { Update-StatusUi }))
$y += 40
$left.Controls.Add((New-LcButton "Open in browser" $y {
    $url = (Get-ProbeUrl).TrimEnd("/") + "/"
    Start-Process $url | Out-Null
    Write-LcLog "Opened $url"
}))

$y += 52
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

$y += 52
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

$console = New-Object System.Windows.Forms.RichTextBox
$console.Dock = [System.Windows.Forms.DockStyle]::Fill
$console.ReadOnly = $true
$console.BackColor = [System.Drawing.Color]::FromArgb(18, 18, 18)
$console.ForeColor = [System.Drawing.Color]::FromArgb(220, 220, 220)
$console.Font = New-Object System.Drawing.Font("Consolas", 9)
$console.HideSelection = $false
$console.WordWrap = $false
$script:ConsoleBox = $console
$form.Controls.Add($console)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({ Update-StatusUi })
$form.Add_Shown({
    Write-LcLog "Launch Control ready. Start the site from this window (this is the console)."
    Update-StatusUi
    $timer.Start()
})
$form.Add_FormClosing({
    $timer.Stop()
})

[void][System.Windows.Forms.Application]::Run($form)
