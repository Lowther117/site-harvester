@echo off
setlocal
rem Site Harvester - Windows builder.
rem Produces dist\Site Harvester\Site Harvester.exe using PyInstaller.
rem You do not need this to use the app - run.bat is enough. This is only
rem for making a standalone folder you can move to another PC.

cd /d "%~dp0"

echo ================================================
echo   Building Site Harvester.exe
echo ================================================
echo.

rem Find a REAL Python - the py launcher counts, the Microsoft Store's fake
rem python.exe stub does not - and install Python automatically if there is
rem none (winget first, python.org directly when winget is broken).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_python.ps1" >nul
if errorlevel 1 goto :no_python
set "SYSPY="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_python.ps1" -NoInstall`) do set "SYSPY=%%I"
if not defined SYSPY goto :no_python
echo Using Python: %SYSPY%
echo.

if exist ".buildvenv-win" rmdir /s /q ".buildvenv-win"
"%SYSPY%" -m venv ".buildvenv-win"
if errorlevel 1 goto :failed
set "VPY=.buildvenv-win\Scripts\python.exe"

"%VPY%" -m pip install --upgrade pip --quiet
echo Installing libraries...
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo Downloading the headless browser ^(Chromium^)...
"%VPY%" -m playwright install chromium

rem WeasyPrint is an optional fallback; only bundle it if it is installed.
set "WEASY_FLAG="
"%VPY%" -c "import weasyprint" >nul 2>nul
if not errorlevel 1 set "WEASY_FLAG=--collect-all weasyprint"

echo Building...
"%VPY%" -m PyInstaller ^
  --name "Site Harvester" ^
  --windowed ^
  --noconfirm ^
  --clean ^
  %WEASY_FLAG% ^
  --collect-all yt_dlp ^
  --collect-all playwright ^
  --collect-all pypdf ^
  --hidden-import pypdf ^
  site_harvester.py
if errorlevel 1 goto :failed

rem The intermediate "build" folder also contains a Site Harvester.exe, but it
rem is a half-assembled bootloader with no _internal alongside it. Launching
rem that one fails with "Failed to load Python DLL ... python3xx.dll". Delete it
rem so there is only one exe to find, and open the real one for the user.
if exist "build\Site Harvester\Site Harvester.exe" del /q "build\Site Harvester\Site Harvester.exe"

echo.
echo ================================================
echo   Done
echo ================================================
echo.
echo Your app is the one in the DIST folder:
echo.
echo   %CD%\dist\Site Harvester\Site Harvester.exe
echo.
echo Do not run anything from the "build" folder - that is scratch work
echo left behind by the builder, and it will not start.
echo.
echo Opening the dist folder now...
start "" "%CD%\dist\Site Harvester"
echo.
pause
exit /b 0

:no_python
echo.
echo Python could not be found, and the automatic install did not complete
echo either (the messages above say why). If Python was just installed for
echo the first time, close this window and run build.bat again - a fresh
echo window picks up the new install.
echo.
pause
exit /b 1

:failed
echo.
echo Build ended with an error. Scroll up to see what went wrong.
echo.
pause
exit /b 1
