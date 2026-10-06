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

rem ddgs is the Find tab's free, key-less web search. Optional in the same
rem way: attempted on its own, and when it is missing the app still builds
rem and the Find tab uses its built-in search. "import ddgs.ddgs" rather than
rem "import ddgs" because the package loads lazily - only the inner module
rem proves that primp and lxml, which it cannot work without, are there too.
rem Its search engines are found by scanning a folder at run time, which
rem PyInstaller cannot see, hence --collect-all.
echo    Web search for the Find tab ^(ddgs^)...
"%PY%" -m pip install --only-binary :all: -r "%HERE%requirements-find.txt" >> "%LOG%" 2>&1
set "FIND_FLAG="
"%PY%" -c "import ddgs.ddgs, primp" >nul 2>&1
if not errorlevel 1 set "FIND_FLAG=--collect-all ddgs --collect-all primp"
if defined FIND_FLAG (echo    ...will be bundled) else (echo    ...not available, the Find tab will use its built-in search)

rem AI help for the Find tab: Ollama, the program that runs a language model
rem on this PC, and the model itself.
rem
rem Neither goes INSIDE the build - the model alone is about 2 GB and belongs
rem to Ollama, which keeps it in the user profile for every program that uses
rem it. They are installed here so that a freshly built app has AI help ready
rem to switch on under Find, Settings, AI help. The app starts Ollama by
rem itself when it is needed, and fetches the model by itself on a PC that
rem does not have it yet.
rem
rem Optional like the two above: a failure is a warning, and the Find tab
rem then works on the words in the description as before.
rem   set HARVESTER_SKIP_AI=1        before running this leaves the step out
rem   set HARVESTER_AI_MODEL=name    downloads a different model
echo    AI help for the Find tab ^(Ollama and its model^)...
set "AI_MODEL=llama3.2"
if defined HARVESTER_AI_MODEL set "AI_MODEL=%HARVESTER_AI_MODEL%"
if defined HARVESTER_SKIP_AI (
    echo    ...skipped, HARVESTER_SKIP_AI is set
    goto :ai_done
)
call :find_ollama
if defined OLLAMA goto :ai_have
echo    ...Ollama is not installed, adding it with winget
winget install -e --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
call :find_ollama
if defined OLLAMA goto :ai_have
echo    ...winget could not do it, fetching the installer from ollama.com
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $f = Join-Path $env:TEMP 'OllamaSetup.exe'; Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile $f -UseBasicParsing; Start-Process -FilePath $f -ArgumentList '/VERYSILENT','/NORESTART' -Wait" >> "%LOG%" 2>&1
call :find_ollama
if defined OLLAMA goto :ai_have
echo    WARNING: Ollama could not be installed - AI help will say so until it is.
echo    Everything else works. To add it later: https://ollama.com
goto :ai_done

:ai_have
echo    Ollama: %OLLAMA%
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:11434/api/version | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 start "" /b "%OLLAMA%" serve >nul 2>&1
powershell -NoProfile -Command "for ($i = 0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:11434/api/version | Out-Null; exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1" >nul 2>&1
if errorlevel 1 (
    echo    WARNING: Ollama did not start, so the model was not downloaded.
    echo    The app fetches it itself the first time AI help is used.
    goto :ai_done
)
"%OLLAMA%" list 2>nul | findstr /b /i /c:"%AI_MODEL%" >nul
if not errorlevel 1 (
    echo    ...model %AI_MODEL% already downloaded
    goto :ai_done
)
echo    ...downloading the model %AI_MODEL%, about 2 GB, once. This is the slow part.
"%OLLAMA%" pull %AI_MODEL%
"%OLLAMA%" list 2>nul | findstr /b /i /c:"%AI_MODEL%" >nul
if errorlevel 1 (
    echo    WARNING: the model did not download.
    echo    The app fetches it itself the first time AI help is used.
) else (
    echo    ...model %AI_MODEL% ready
)
:ai_done

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
    --hidden-import find_tab ^
    --hidden-import find_engine ^
    %WEASY_FLAG% %FFMPEG_FLAG% %FIND_FLAG% ^
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

rem Sets OLLAMA to the full path of ollama.exe, or leaves it undefined.
:find_ollama
set "OLLAMA="
for %%p in (ollama.exe) do set "OLLAMA=%%~$PATH:p"
if not defined OLLAMA if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA if exist "%ProgramFiles%\Ollama\ollama.exe" set "OLLAMA=%ProgramFiles%\Ollama\ollama.exe"
exit /b 0

:failed
echo.
echo ---- last 40 lines of the log ----
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 40" 2>nul
echo ----------------------------------
echo Full log: %LOG%
echo.
pause
exit /b 1
