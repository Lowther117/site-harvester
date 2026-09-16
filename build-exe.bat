@echo off
rem OPTIONAL: build a standalone "Site Harvester.exe" that runs without Python.
rem The normal folder + run.bat setup is unchanged by this.
rem
rem The noisy output of pip and PyInstaller goes to build-win-log.txt so this
rem window stays readable; if anything fails, the tail of that log is shown
rem here. The finished exe is tested before this script claims success.

setlocal
cd /d "%~dp0"
set "HERE=%~dp0"
set "VENV=%HERE%.venv-build"
set "PY=%VENV%\Scripts\python.exe"
set "LOG=%HERE%build-win-log.txt"
set "EXE=%HERE%dist\Site Harvester\Site Harvester.exe"

echo Site Harvester standalone build> "%LOG%"
echo %DATE% %TIME%>> "%LOG%"
echo.
echo Building the standalone Site Harvester.exe. This takes a few minutes.
echo Detail goes to build-win-log.txt.

rem ---------------------------------------------------------------------
rem 1. A Python to build with - installed automatically if the PC has none.
rem ---------------------------------------------------------------------
echo.
echo == Python
if exist "%PY%" goto :haveenv

set "SYSPY="
for /f "delims=" %%p in ('powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%ensure_python.ps1" 2^>nul') do set "SYSPY=%%p"
if not defined SYSPY goto :nopython
if not exist "%SYSPY%" goto :nopython
echo    Using %SYSPY%
"%SYSPY%" -m venv "%VENV%" >> "%LOG%" 2>&1
if not exist "%PY%" (
    echo    ERROR: could not create the build environment.
    goto :failed
)

:haveenv
echo    Installing PyInstaller...
"%PY%" -m pip install --upgrade pip --quiet >> "%LOG%" 2>&1
"%PY%" -m pip install --upgrade --only-binary :all: pyinstaller >> "%LOG%" 2>&1
if errorlevel 1 (
    echo    ERROR: could not install PyInstaller.
    goto :failed
)

rem ---------------------------------------------------------------------
rem 2. Everything the app should carry with it.
rem
rem --only-binary :all: everywhere: a missing wheel then fails in seconds
rem instead of trying to compile from source and hunting for Visual Studio.
rem ---------------------------------------------------------------------
echo.
echo == Libraries
echo    Crawler, yt-dlp, Playwright, pypdf...
"%PY%" -m pip install --only-binary :all: -r "%HERE%requirements.txt" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo    ERROR: requirements.txt did not install - the app cannot work without it.
    goto :failed
)

rem requirements-fallback.txt is NOT a second try at the line above: it holds
rem WeasyPrint, the lower-fidelity PDF fallback used only when Chromium refuses
rem to start, and on Windows it also needs the Pango libraries from MSYS2. So it
rem is attempted separately, a failure here is fine, and --collect-all is only
rem passed when the import actually works.
echo    Optional PDF fallback ^(WeasyPrint^)...
"%PY%" -m pip install --only-binary :all: -r "%HERE%requirements-fallback.txt" >> "%LOG%" 2>&1
set "WEASY_FLAG="
"%PY%" -c "import weasyprint" >nul 2>&1
if not errorlevel 1 set "WEASY_FLAG=--collect-all weasyprint"
if defined WEASY_FLAG (echo    ...will be bundled) else (echo    ...not available, building without it)

rem The headless Chromium prints the pages for the clickable PDF, so it is
rem not optional. PLAYWRIGHT_BROWSERS_PATH=0 makes Playwright save it INSIDE
rem its own package, and --collect-all playwright below then carries it into
rem the build - so the app works on a PC that has never seen Playwright. (The
rem app sets the same variable on start-up when it finds the bundled copy.)
echo    Headless browser ^(Chromium^) - bundled in, about 200 MB...
set "PLAYWRIGHT_BROWSERS_PATH=0"
"%PY%" -m playwright install chromium >> "%LOG%" 2>&1
if errorlevel 1 echo    WARNING: Chromium not downloaded - the PDF and "Render JavaScript" need it.
set "PLAYWRIGHT_BROWSERS_PATH="

rem ffmpeg merges best-quality video and audio streams. imageio-ffmpeg is a
rem static ffmpeg wrapped as an ordinary pip package, so it is baked in the
rem same way as everything else; the app finds it through imageio_ffmpeg.
echo    ffmpeg - bundled in...
set "FFMPEG_FLAG="
"%PY%" -m pip install --only-binary :all: imageio-ffmpeg >> "%LOG%" 2>&1
"%PY%" -c "import imageio_ffmpeg" >nul 2>&1
if not errorlevel 1 set "FFMPEG_FLAG=--collect-all imageio_ffmpeg"
if not defined FFMPEG_FLAG echo    ...not available, the app will look for ffmpeg on the PC it runs on.

rem ---------------------------------------------------------------------
rem 3. Build
rem
rem --onedir: with Chromium inside, a single-file exe would have to unpack
rem a few hundred MB to a temp folder on EVERY launch. So the result is a
rem folder, dist\Site Harvester\, with Site Harvester.exe at the top of it -
rem copy the whole folder, and it starts in a second or two.
rem ---------------------------------------------------------------------
echo.
echo == Building ^(a few minutes^)
if exist "%HERE%build" rd /s /q "%HERE%build"
if exist "%HERE%dist" rd /s /q "%HERE%dist"
if exist "%HERE%Site Harvester.spec" del /q "%HERE%Site Harvester.spec"

"%PY%" -m PyInstaller --noconfirm --clean --onedir --windowed --name "Site Harvester" ^
    --collect-all yt_dlp ^
    --collect-all playwright ^
    --collect-all pypdf ^
    --hidden-import pypdf ^
    --hidden-import site_harvester ^
    --hidden-import theme ^
    %WEASY_FLAG% %FFMPEG_FLAG% ^
    "%HERE%site_harvester_app.py" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo    Build failed.
    goto :failed
)
if not exist "%EXE%" (
    echo    The build finished but dist\Site Harvester\Site Harvester.exe is not there.
    goto :failed
)

rem ---------------------------------------------------------------------
rem 4. Prove it runs before saying it works.
rem
rem A windowed exe has no console of its own, so the self-test writes its
rem report to a file beside the exe and that file is shown here.
rem ---------------------------------------------------------------------
echo.
echo == Testing the built exe
set "REPORT=%HERE%dist\Site Harvester\harvester-selftest.txt"
if exist "%REPORT%" del /q "%REPORT%"
"%EXE%" selftest >nul 2>&1
set "RC=%ERRORLEVEL%"
if exist "%REPORT%" (
    type "%REPORT%"
    type "%REPORT%" >> "%LOG%"
) else (
    echo    The exe did not produce a self-test report.
    set "RC=1"
)

echo.
if "%RC%"=="0" (
    echo Done: %EXE%
    echo.
    echo Copy the whole "dist\Site Harvester" folder anywhere - Chromium and
    echo ffmpeg are inside it. Settings are kept beside the exe.
) else (
    echo The exe was built but the self-test above found problems, so it may
    echo not open properly. Send build-win-log.txt if you want it looked at.
)
echo.
echo Log: %LOG%
pause
exit /b 0

:nopython
echo    ERROR: Python 3.9+ is needed to BUILD the exe ^(not to run it^), and
echo    it could not be installed automatically.
echo    Install it from https://www.python.org/downloads/windows/
echo    ^(tick "Add python.exe to PATH"^), then run this again.
goto :failed

:failed
echo.
echo ---- last 40 lines of the log ----
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 40" 2>nul
echo ----------------------------------
echo Full log: %LOG%
echo.
pause
exit /b 1
