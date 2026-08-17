#Requires -Version 5.1
<#
.SYNOPSIS
  Stop and remove the Switcheroo Windows service.
#>
$ErrorActionPreference = "Stop"
$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepoRoot = Split-Path -Parent $script:ScriptDir
$script:DataDir = Join-Path $script:RepoRoot "data"
$script:WinswDir = Join-Path $script:ScriptDir "winsw"
$script:UninstallLog = Join-Path $env:TEMP "switcheroo-uninstall-service.log"
$script:ServiceName = "Switcheroo"

. (Join-Path $script:ScriptDir "Switcheroo.Monitor.ps1")

function Write-UninstallLog {
    param([string]$Message, [string]$Level = "INFO")
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    $dir = Split-Path -Parent $script:UninstallLog
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    Add-Content -LiteralPath $script:UninstallLog -Value $line -Encoding UTF8
    if ($Level -eq "ERROR") { Write-Host $line -ForegroundColor Red }
    else { Write-Host $line }
}

function Fail {
    param([string]$Message)
    Write-UninstallLog $Message -Level "ERROR"
    Write-UninstallLog "Uninstall log: $($script:UninstallLog)" -Level "ERROR"
    exit 1
}

try {
    if (-not (Test-IsAdministrator)) {
        Fail "Administrator rights are required to uninstall the service."
    }
    if (-not (Test-Path -LiteralPath $script:DataDir)) {
        New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    }
    $script:UninstallLog = Join-Path $script:DataDir "uninstall-service.log"
    Write-UninstallLog "Uninstall starting"

    $svc = Get-SwitcherooServiceInfo -Name $script:ServiceName
    if (-not $svc.Exists) {
        Write-UninstallLog "Service $($script:ServiceName) is not installed."
        Write-Host "Nothing to remove. Log: $($script:UninstallLog)"
        exit 0
    }

    try {
        Stop-Service -Name $script:ServiceName -Force -ErrorAction Stop
        Write-UninstallLog "Service stopped"
    }
    catch {
        Write-UninstallLog "Stop-Service: $($_.Exception.Message)" -Level "ERROR"
    }

    $winswExe = Join-Path $script:WinswDir "Switcheroo.exe"
    if (Test-Path -LiteralPath $winswExe) {
        $un = Start-Process -FilePath $winswExe -ArgumentList "uninstall" -Wait -PassThru -NoNewWindow
        Write-UninstallLog "WinSW uninstall exit $($un.ExitCode)"
        if ($un.ExitCode -ne 0) {
            $del = Start-Process -FilePath "sc.exe" -ArgumentList @("delete", $script:ServiceName) -Wait -PassThru -NoNewWindow
            Write-UninstallLog "sc delete exit $($del.ExitCode)"
        }
    }
    else {
        $del = Start-Process -FilePath "sc.exe" -ArgumentList @("delete", $script:ServiceName) -Wait -PassThru -NoNewWindow
        Write-UninstallLog "sc delete exit $($del.ExitCode) (WinSW exe missing)"
    }

    Start-Sleep -Seconds 1
    $left = Get-SwitcherooServiceInfo -Name $script:ServiceName
    if ($left.Exists) {
        Fail "Service still present after uninstall. See $($script:UninstallLog)"
    }
    Write-UninstallLog "Service removed"
    Write-Host "Uninstalled. Log: $($script:UninstallLog)"
    exit 0
}
catch {
    Fail $_.Exception.Message
}
