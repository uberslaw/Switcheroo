@echo off
setlocal EnableExtensions
title Switcheroo Launch Control

cd /d "%~dp0"
set "PS1=%~dp0Switcheroo-LaunchControl.ps1"
if not exist "%PS1%" (
  echo [ERROR] Missing %PS1%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%PS1%" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%SWITCHEROO_NOPAUSE%"=="1" (
  if not "%EXITCODE%"=="0" (
    echo.
    echo Launch Control exited with code %EXITCODE%.
    pause
  )
)
exit /b %EXITCODE%
