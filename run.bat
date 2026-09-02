@echo off
setlocal
rem Site Harvester - Windows launcher.
rem Builds its own Python environment inside this folder on first run,
rem downloads the headless browser it needs, then opens the app window.
rem Nothing is installed system-wide.

cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo Python was not found.
        echo   Install it:  winget install -e --id Python.Python.3.12
        echo.
        pause
        exit /b 1
    )
    set "PY=python"
) else (
    set "PY=py -3"
)

if not exist ".venv-win\Scripts\python.exe" (
    echo Creating the Python environment ^(first run only^)...
    %PY% -m venv ".venv-win"
    if errorlevel 1 goto :failed
    ".venv-win\Scripts\python.exe" -m pip install --upgrade pip --quiet
    echo Installing libraries ^(yt-dlp and Playwright - this takes a minute^)...
    ".venv-win\Scripts\python.exe" -m pip install -r requirements.txt --quiet
    if errorlevel 1 goto :failed
    echo Downloading the headless browser ^(Chromium^)...
    ".venv-win\Scripts\python.exe" -m playwright install chromium
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo.
    echo Note: ffmpeg was not found. Everything works except best-quality
    echo       and embedded video. To add it:  winget install -e --id Gyan.FFmpeg
    echo.
)

start "" ".venv-win\Scripts\pythonw.exe" "site_harvester.py"
exit /b 0

:failed
echo.
echo Setup failed. See the messages above.
echo.
pause
exit /b 1
