@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1" %*
set "MARKETOPS_INSTALL_EXIT=%ERRORLEVEL%"
if not "%MARKETOPS_INSTALL_EXIT%"=="0" echo Installation incomplete. Keep this directory and run Install.cmd again.
rem Automation sets MARKETOPS_NO_PAUSE=1 (or CI); a double-click still pauses.
if "%MARKETOPS_NO_PAUSE%"=="1" exit /b %MARKETOPS_INSTALL_EXIT%
if defined CI exit /b %MARKETOPS_INSTALL_EXIT%
pause
exit /b %MARKETOPS_INSTALL_EXIT%
