@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Detach a hidden PowerShell host so the WinForms UI shows without a stuck
REM console window. The form is independent of this .cmd process.
start "Switcheroo Launch Control" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0Switcheroo-LaunchControl.ps1" %*
exit /b 0
