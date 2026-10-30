@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0builder.ps1" %*
set "result=%ERRORLEVEL%"
if %result% EQU 10 exit /b 0
echo.
pause
exit /b %result%
