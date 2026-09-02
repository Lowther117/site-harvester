#!/bin/bash
# Site Harvester - macOS / Linux launcher.
# Builds its own Python environment inside this folder on first run, downloads
# the headless browser it needs, then opens the app window. Nothing is
# installed system-wide. You do not need to build the .app to use it.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 was not found. Install it:  brew install python"
    read -r -p "Press Return to close." _
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "Your Python is missing Tkinter (needed for the app window)."
    echo "  Install it:  brew install python-tk"
    read -r -p "Press Return to close." _
    exit 1
fi

VENV=".venv-mac"
if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating the Python environment (first run only)..."
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip --quiet
    echo "Installing libraries (yt-dlp and Playwright - this takes a minute)..."
    "$VENV/bin/python" -m pip install -r requirements.txt --quiet
    echo "Downloading the headless browser (Chromium)..."
    "$VENV/bin/python" -m playwright install chromium
fi

command -v ffmpeg >/dev/null 2>&1 || \
    echo "Note: ffmpeg not found. Everything works except best-quality/embedded video:  brew install ffmpeg"

"$VENV/bin/python" site_harvester.py
