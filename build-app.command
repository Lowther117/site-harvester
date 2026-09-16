#!/bin/bash
# OPTIONAL: build a standalone "Site Harvester.app" that runs without Python.
# The normal folder + run.command setup is unchanged by this.
#
# Everything this prints is also written to build-mac-log.txt, and the built
# app is tested before this script claims success.

cd "$(dirname "$0")" || exit 1
LOG="build-mac-log.txt"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

VENV=".venv-build-mac"
PY="$VENV/bin/python"
APP="dist/Site Harvester.app"
BIN="$APP/Contents/MacOS/Site Harvester"

say() { printf '\n== %s\n' "$1"; }
fail() { printf '\nBuild stopped: %s\nFull log: %s/%s\n' "$1" "$PWD" "$LOG"; exit 1; }

printf 'Site Harvester standalone build - %s\n' "$(date)"
printf 'macOS %s on %s\n' "$(sw_vers -productVersion 2>/dev/null)" "$(uname -m)"

# --------------------------------------------------------------------------
# Homebrew - where a bundle-able Python and any external tools come from.
#
# A double-clicked .command starts with a bare PATH, so Homebrew's folders are
# added by hand, and Homebrew itself is installed if the Mac has none (its
# installer asks for the Mac password once, in this window).
# --------------------------------------------------------------------------
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ensure_brew() {
    command -v brew >/dev/null 2>&1 && return 0
    say "Installing Homebrew"
    echo "   This asks for your Mac password once, then takes a few minutes."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/tty
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
    command -v brew >/dev/null 2>&1
}

# brew_install <formula>... - installs each one (a formula that is already
# there is a no-op). Non-zero if Homebrew is unavailable or an install failed.
brew_install() {
    ensure_brew || { echo "   Homebrew is not available, so $* cannot be installed automatically."; return 1; }
    local f rc=0
    for f in "$@"; do
        echo "   brew install $f"
        HOMEBREW_NO_AUTO_UPDATE=1 brew install "$f" < /dev/null || rc=1
    done
    return $rc
}

# --------------------------------------------------------------------------
# 1. Pick an interpreter that can actually be bundled.
#
# Apple's /usr/bin/python3 is tied to the system Tcl/Tk 8.5 frameworks, which
# do not survive being copied into an app bundle - the build succeeds and the
# app then dies on launch with no window and no message. So a Python with its
# own Tk 8.6 is required, and the system one is refused by name.
# --------------------------------------------------------------------------
say "Choosing a Python to build with"

tk_version() {   # prints e.g. 8.6, or nothing if tkinter is unusable
    "$1" -c 'import tkinter;print(tkinter.TkVersion)' 2>/dev/null
}

CANDIDATES=()
[ -n "$HARVESTER_BUILD_PYTHON" ] && CANDIDATES+=("$HARVESTER_BUILD_PYTHON")
for v in 3.14 3.13 3.12 3.11 3.10 3.9; do
    CANDIDATES+=("/opt/homebrew/bin/python$v" "/usr/local/bin/python$v" \
                 "/Library/Frameworks/Python.framework/Versions/$v/bin/python3")
done
CANDIDATES+=("$(command -v python3 2>/dev/null)")

pick_python() {
CHOSEN=""
for c in "${CANDIDATES[@]}"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    # readlink, not python: on a fresh Mac the only "python3" is Apple's stub,
    # and merely running it pops up the Xcode command-line-tools installer.
    real="$(readlink -f "$c" 2>/dev/null || echo "$c")"
    case "$real" in
        /usr/bin/python3|/Library/Developer/CommandLineTools/*|/Applications/Xcode.app/*)
            printf '   skipping %s - Apple system Python, its Tk cannot be bundled\n' "$c"
            continue ;;
    esac
    tkv="$(tk_version "$c")"
    if [ -z "$tkv" ]; then
        printf '   skipping %s - no working tkinter\n' "$c"
        continue
    fi
    case "$tkv" in
        8.6|8.7|9.*) CHOSEN="$c"; printf '   using %s (Tk %s)\n' "$c" "$tkv"; break ;;
        *) printf '   skipping %s - Tk %s is too old to bundle\n' "$c" "$tkv" ;;
    esac
done
}
pick_python

if [ -z "$CHOSEN" ]; then
    echo "   None of the Pythons here can be bundled - adding one with Homebrew."
    echo "   (a Python with its own Tk 8.6; Apple's own /usr/bin/python3 does not qualify.)"
    if brew_install python python-tk; then
        pick_python
    fi
fi

if [ -z "$CHOSEN" ]; then
    cat <<'MSG'

None of the Pythons on this Mac can be used to build the app.

The app needs a Python that carries its own Tk 8.6. The one Apple ships
(/usr/bin/python3) uses the system Tk 8.5, which cannot be copied into an
app bundle - that is why a build can finish and the app still not open.

Install one of these, then run this again:

    brew install python python-tk          (Homebrew - simplest)
    https://www.python.org/downloads/macos/ (official installer)

If you already have one somewhere unusual, point this script at it:

    HARVESTER_BUILD_PYTHON=/path/to/python3 ./build-app.command

Nothing else on this Mac is affected - run.command keeps working exactly as
before whether or not you ever build the app.
MSG
    fail "no suitable Python found"
fi

# --------------------------------------------------------------------------
# 2. Build environment
# --------------------------------------------------------------------------
say "Build environment"
if [ ! -x "$PY" ]; then
    "$CHOSEN" -m venv "$VENV" || fail "could not create the build environment"
else
    # a venv built by a different (or moved) Python is worse than none
    if ! "$PY" -c 'import sys' >/dev/null 2>&1; then
        rm -rf "$VENV"
        "$CHOSEN" -m venv "$VENV" || fail "could not recreate the build environment"
    fi
fi
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install --upgrade --only-binary :all: pyinstaller \
    || fail "could not install PyInstaller"

# --only-binary :all: everywhere: a missing wheel then fails in seconds
# instead of trying to compile from source and hunting for a C toolchain.
say "Libraries"
"$PY" -m pip install --only-binary :all: -r requirements.txt \
    || fail "could not install requirements.txt - the app cannot work without it"

# requirements-fallback.txt is NOT a second try at the line above: it holds
# WeasyPrint, the lower-fidelity PDF fallback used only when Chromium refuses
# to start, and it needs the Pango system libraries as well. So it is attempted
# separately and a failure here is fine - the app just builds without it, and
# --collect-all is only passed when the import actually works.
say "Optional PDF fallback (WeasyPrint)"
"$PY" -m pip install --only-binary :all: -r requirements-fallback.txt \
    || echo "   not installed - the app builds without it (Chromium does the PDFs)"
WEASY_FLAG=""
if "$PY" -c 'import weasyprint' >/dev/null 2>&1; then
    WEASY_FLAG="--collect-all weasyprint"
    echo "   will be bundled"
else
    echo "   will not be bundled (needs: brew install pango)"
fi

# The headless Chromium prints the pages for the clickable PDF, so it is not
# optional. PLAYWRIGHT_BROWSERS_PATH=0 makes Playwright save it INSIDE its own
# package, and --collect-all playwright below then carries it into the app -
# so the app works on a Mac that has never seen Playwright. (The app sets the
# same variable on start-up when it finds the bundled copy.) Adds ~200 MB.
say "Headless browser (Chromium) - bundled into the app"
PLAYWRIGHT_BROWSERS_PATH=0 "$PY" -m playwright install chromium \
    || echo "   WARNING: not downloaded - the PDF and 'Render JavaScript' need it."

# ffmpeg is what merges best-quality video and audio streams. imageio-ffmpeg
# is a static ffmpeg wrapped as an ordinary pip package, so it is baked in
# the same way as everything else; the app finds it through imageio_ffmpeg.
say "ffmpeg - bundled into the app"
FFMPEG_FLAG=""
if "$PY" -m pip install --only-binary :all: imageio-ffmpeg \
        && "$PY" -c 'import imageio_ffmpeg' >/dev/null 2>&1; then
    FFMPEG_FLAG="--collect-all imageio_ffmpeg"
    echo "   $("$PY" -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')"
else
    echo "   not available - the app will look for ffmpeg on the Mac it runs on."
fi

# --------------------------------------------------------------------------
# 3. Build
# --------------------------------------------------------------------------
say "Building (a few minutes)"
rm -rf build dist "Site Harvester.spec"
"$PY" -m PyInstaller --noconfirm --clean --windowed --name "Site Harvester" \
    --osx-bundle-identifier com.lowther.siteharvester \
    --collect-all yt_dlp \
    --collect-all playwright \
    --collect-all pypdf \
    --hidden-import pypdf \
    --hidden-import site_harvester \
    --hidden-import theme \
    $WEASY_FLAG $FFMPEG_FLAG \
    site_harvester_app.py \
    || fail "PyInstaller failed - the messages above say why"

[ -x "$BIN" ] || fail "the build finished but $APP is not there"

# --------------------------------------------------------------------------
# 4. Make it launchable
#
# An ad-hoc signature is what lets a locally built app open at all on Apple
# silicon, and stray extended attributes invalidate it.
# --------------------------------------------------------------------------
say "Signing"
xattr -cr "$APP" 2>/dev/null || true
if command -v codesign >/dev/null 2>&1; then
    codesign --force --deep --sign - --timestamp=none "$APP" \
        && codesign --verify --deep --strict "$APP" \
        && echo "   ad-hoc signature ok" \
        || echo "   WARNING: signing did not complete - the app may be blocked on first open"
else
    echo "   codesign not available (install the Xcode command line tools)"
fi

# --------------------------------------------------------------------------
# 5. Prove it runs before saying it works
# --------------------------------------------------------------------------
say "Testing the built app"
if "$BIN" selftest; then
    RESULT=ok
else
    RESULT=problems
fi

echo
if [ "$RESULT" = ok ]; then
    cat <<MSG
Done: $PWD/$APP

Move it wherever you like. Chromium and ffmpeg travel inside it. It keeps its
settings in the folder the app sits in - not inside the app - so give it a
folder of its own rather than dropping it loose in Applications.

The first time you open it, right-click the app and choose Open, and confirm.
That is a one-off for anything you build yourself.
MSG
else
    cat <<MSG
The app was built but the self-test above found problems, so it may not open
properly. The full report is in $PWD/dist/harvester-selftest.txt and the whole
run is in $PWD/$LOG.
MSG
fi
echo "Log: $PWD/$LOG"
