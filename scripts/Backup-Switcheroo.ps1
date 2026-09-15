#Requires -Version 5.1
<#
.SYNOPSIS
  Online SQLite backup of Switcheroo into data\backups\.

.DESCRIPTION
  Uses the SQLite backup API via Python (not Copy-Item on a live file).
  Run from an account that can read C:\Switcheroo\data.
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}
& $Python -m app.services.backup backup
if ($LASTEXITCODE -ne 0) {
    throw "Backup failed. See stderr."
}
