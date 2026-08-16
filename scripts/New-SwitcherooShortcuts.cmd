@echo off
setlocal EnableExtensions
title Switcheroo shortcuts

cd /d "%~dp0"
set "TARGET=%~dp0Switcheroo-LaunchControl.cmd"
if not exist "%TARGET%" (
  echo [ERROR] Missing %TARGET%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $repo = Split-Path -Parent '%~dp0'; ^
   $target = '%TARGET%'; ^
   $desktop = [Environment]::GetFolderPath('Desktop'); ^
   $paths = @( ^
     (Join-Path $desktop 'Switcheroo Launch Control.lnk'), ^
     (Join-Path $repo 'Switcheroo Launch Control.lnk'), ^
     (Join-Path '%~dp0' 'Switcheroo-LaunchControl.lnk') ^
   ); ^
   foreach ($p in $paths) { ^
     $s = $ws.CreateShortcut($p); ^
     $s.TargetPath = $target; ^
     $s.WorkingDirectory = $repo; ^
     $s.WindowStyle = 1; ^
     $s.Description = 'Switcheroo Launch Control'; ^
     $s.Save(); ^
     Write-Host ('Wrote ' + $p); ^
   }"

if errorlevel 1 (
  echo Shortcut creation failed.
  pause
  exit /b 1
)
echo.
echo Shortcuts created on the desktop and in the repo folder.
pause
exit /b 0
