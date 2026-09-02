#!/bin/bash
# Site Harvester - macOS / Linux launcher.
#
# First run: builds a Python environment inside this folder, downloads the
# headless browser it needs, and installs ffmpeg if Homebrew is available.
# After that it just opens.
set -e
cd "$(dirname "$0")"

say()  { printf '\n== %s\n' "$1"; }
note() { printf '   %s\n' "$1"; }

if ! command -v python3 >/dev/null 2>&1; then
    say "Python 3 is not installed"
    note "Install it:  brew install python"
    read -r -p "Press Return to close." _
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    say "Your Python is missing Tkinter, which draws the app window"
    note "Install it:  brew install python-tk"
    read -r -p "Press Return to close." _
    exit 1
fi

VENV=".venv-mac"
if [ ! -x "$VENV/bin/python" ]; then
    say "Setting up (first run only) - this takes a few minutes"
    note "Creating the Python environment..."
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip --quiet
    note "Installing libraries (yt-dlp, Playwright and friends)..."
    "$VENV/bin/python" -m pip install -r requirements.txt --quiet
    note "Downloading the headless browser (Chromium)..."
    "$VENV/bin/python" -m playwright install chromium
fi

# ffmpeg is optional - only embedded and best-quality video need it, so a
# missing one is a note rather than a blocker.
if ! command -v ffmpeg >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        say "Installing ffmpeg (for embedded and best-quality video)"
        brew install ffmpeg || note "Could not install it. Everything else still works."
    else
        say "ffmpeg is not installed"
        note "Everything works without it except embedded and best-quality video."
        note "To add it later, install Homebrew from https://brew.sh then:"
        note "  brew install ffmpeg"
    fi
fi

"$VENV/bin/python" site_harvester.py
