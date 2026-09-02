@echo off
setlocal
rem Site Harvester - Windows launcher.
rem
rem First run: sets everything up - Python environment, the headless browser,
rem and a portable ffmpeg. Nothing is installed system-wide and no admin
rem permission is needed. After that it just opens.

cd /d "%~dp0"

if not exist ".venv-win\Scripts\python.exe" goto :setup
if not exist "tools\ffmpeg" goto :setup
goto :run

:setup
echo.
echo Setting up. This happens once and takes a few minutes.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 goto :failed
if not exist ".venv-win\Scripts\python.exe" goto :failed

:run
start "" ".venv-win\Scripts\pythonw.exe" "site_harvester.py"
exit /b 0

:failed
echo.
echo Setup did not finish. Scroll up to see what went wrong.
echo You can run this file again - it picks up where it left off.
echo.
pause
exit /b 1
