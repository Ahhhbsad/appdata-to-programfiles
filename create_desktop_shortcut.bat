@echo off
setlocal
cd /d "%~dp0"

echo Creating Desktop shortcut for AppData to Program Files Migrator...

set "TARGET_BAT=%~dp0launch.bat"
set "WORKING_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT_PATH=%DESKTOP%\AppData Migrator.lnk"

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$sc = $ws.CreateShortcut('%SHORTCUT_PATH%'); " ^
  "$sc.TargetPath = '%TARGET_BAT%'; " ^
  "$sc.WorkingDirectory = '%WORKING_DIR%'; " ^
  "$sc.Description = 'Promote user installed apps from AppData to Program Files'; " ^
  "$sc.IconLocation = 'imageres.dll,1'; " ^
  "$sc.Save()"

if exist "%SHORTCUT_PATH%" (
    echo [SUCCESS] Created shortcut: "%SHORTCUT_PATH%"
) else (
    echo [ERROR] Failed to create shortcut.
)

pause
