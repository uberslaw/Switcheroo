#Requires -Version 5.1
<#
.SYNOPSIS
  Restore Switcheroo SQLite from a backup file. Stop the service first.

.PARAMETER Path
  Backup file created by Backup-Switcheroo.ps1 (data\backups\switcheroo-*.db).
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServiceName = "Switcheroo"
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    throw "Stop the $ServiceName service before restore. Elevated: Stop-Service $ServiceName"
}
$archive = $Path
if (-not [System.IO.Path]::IsPathRooted($archive)) {
    $archive = Join-Path $RepoRoot $archive
}
if (-not (Test-Path -LiteralPath $archive)) {
    throw "Backup file not found: $archive"
}
Set-Location -LiteralPath $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}
& $Python -m app.services.backup restore $archive
if ($LASTEXITCODE -ne 0) {
    throw "Restore failed. See stderr."
}
Write-Host "Restored $archive. Start-Service $ServiceName when ready."
