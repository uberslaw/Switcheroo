@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "EXE=%~dp0..\launch-control\bin\Release\net8.0-windows\Switcheroo.LaunchControl.exe"
if not exist "%EXE%" set "EXE=%~dp0..\launch-control\bin\Debug\net8.0-windows\Switcheroo.LaunchControl.exe"
if not exist "%EXE%" (
  echo Building Switcheroo Launch Control...
  where dotnet >nul 2>&1
  if errorlevel 1 (
    echo .NET 8 SDK is required. Install from https://dotnet.microsoft.com/download
    echo Then run this launcher again.
    pause
    exit /b 1
  )
  dotnet build "%~dp0..\launch-control\Switcheroo.LaunchControl.csproj" -c Release
  if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
  )
  set "EXE=%~dp0..\launch-control\bin\Release\net8.0-windows\Switcheroo.LaunchControl.exe"
)

if not exist "%EXE%" (
  echo Could not find Switcheroo.LaunchControl.exe
  pause
  exit /b 1
)

start "" "%EXE%" %*
exit /b 0
