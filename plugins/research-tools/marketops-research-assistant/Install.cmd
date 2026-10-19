@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1" %*
set "MARKETOPS_INSTALL_EXIT=%ERRORLEVEL%"
if not "%MARKETOPS_INSTALL_EXIT%"=="0" echo Installation incomplete. Keep this directory and run Install.cmd again.
pause
exit /b %MARKETOPS_INSTALL_EXIT%
