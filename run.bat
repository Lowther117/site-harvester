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
rem The Find tab's free web search, ddgs. Checked on every start so that an
rem environment made before the Find tab existed picks it up as well. It is
rem optional: without it the tab falls back to its slower built-in search.
".venv-win\Scripts\python.exe" -c "import ddgs" >nul 2>&1
if errorlevel 1 (
    echo Adding the web-search library for the Find tab...
    ".venv-win\Scripts\python.exe" -m pip install --retries 1 --timeout 10 -r requirements-find.txt --quiet
)
start "" ".venv-win\Scripts\pythonw.exe" "site_harvester.py"
exit /b 0

:failed
echo.
echo Setup did not finish. Scroll up to see what went wrong.
echo You can run this file again - it picks up where it left off.
echo.
pause
exit /b 1
