@echo off
setlocal
cd /d "%~dp0"

title AppData to Program Files Migrator

:: Check if running with Administrator rights
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo =============================================================
    echo  Requesting Administrator Privileges...
    echo =============================================================
    echo Moving apps to Program Files and writing to HKLM registry
    echo requires elevation. Triggering Windows UAC prompt...
    echo.
    if "%~1"=="" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)

:: Ensure python is accessible
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please ensure Python is installed and added to PATH.
    pause
    exit /b 1
)

:: Run Migrator
python "%~dp0main.py" %*

if %errorlevel% neq 0 (
    echo.
    echo Application exited with code %errorlevel%.
    pause
)
