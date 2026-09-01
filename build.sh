#!/bin/bash
#
# One-click builder for Site Harvester.
# Run it in Terminal like:  bash /path/to/SiteHarvester/build.sh
# It will:
#   1. Make sure Python 3 + Tkinter are available
#   2. Create a self-contained virtual environment (won't touch your system Python)
#   3. Install the needed libraries into it
#   4. Build "Site Harvester.app"
#   5. Show you where the finished app is
#
set -e

# Move into the folder this script lives in, so it works no matter where it's run.
cd "$(dirname "$0")"

echo "================================================"
echo "  Building Site Harvester.app"
echo "================================================"
echo

# --- 1. Check for Python 3 --------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed."
  echo "Install it from https://www.python.org/downloads/macos/ and run this again."
  exit 1
fi
echo "Using $(python3 --version)"

# --- 1b. Check for Tkinter (the GUI toolkit) --------------------------------
if ! python3 -c "import tkinter" >/dev/null 2>&1; then
  echo
  echo "Your Python is missing Tkinter (needed for the app's window)."
  if command -v brew >/dev/null 2>&1; then
    echo "Installing it now with:  brew install python-tk"
    brew install python-tk || {
      echo "Could not install python-tk automatically."
      echo "Please run:  brew install python-tk"
      echo "then run this build script again."
      exit 1
    }
  else
    echo "Please install it, then run this script again. Easiest options:"
    echo "  - Homebrew users:  brew install python-tk"
    echo "  - Or install Python from https://www.python.org/downloads/macos/"
    echo "    (that build already includes Tkinter)."
    exit 1
  fi
fi
echo

# --- 1c. Install system tools: pango (PDF) + ffmpeg (best-quality video) -----
if command -v brew >/dev/null 2>&1; then
  for tool in pango ffmpeg; do
    if ! brew list "$tool" >/dev/null 2>&1; then
      echo "Installing '$tool'…"
      brew install "$tool" || echo "Warning: could not install $tool."
    else
      echo "$tool is already installed."
    fi
  done
else
  echo "Note: Homebrew not found. Downloading files will still work, but:"
  echo "      - saving pages as PDF needs 'pango'"
  echo "      - best-quality/embedded video needs 'ffmpeg'"
  echo "      Install Homebrew from https://brew.sh then run:"
  echo "        brew install pango ffmpeg"
fi
echo

# --- 2. Create an isolated virtual environment ------------------------------
# Start fresh each build so newly-added libraries (e.g. pypdf) are always
# installed and picked up by the bundler.
VENV=".buildvenv"
if [ -d "$VENV" ]; then
  echo "Removing the previous build environment for a clean build…"
  rm -rf "$VENV"
fi
echo "Creating a self-contained build environment…"
python3 -m venv "$VENV"
# Use the venv's python/pip directly (no need to 'activate').
VPY="$VENV/bin/python"

# --- 3. Install dependencies into the venv ----------------------------------
echo "Installing required libraries (this includes yt-dlp and Playwright — may take a minute)…"
"$VPY" -m pip install --upgrade pip >/dev/null 2>&1 || true
"$VPY" -m pip install -r requirements.txt
echo

# Download the headless browser. It is used both by the optional 'Render
# JavaScript' mode AND by the clickable-PDF feature (each page is printed by
# this browser), so it's needed for the good PDF output.
echo "Downloading the headless browser (Chromium)…"
"$VPY" -m playwright install chromium || \
  echo "Warning: could not download Chromium. The app still works, but the "\
       "printed-page PDF and 'Render JavaScript' option won't until you run: "\
       "python3 -m playwright install chromium"
echo

# --- 4. Build the .app ------------------------------------------------------
echo "Building the app (this can take a minute or two)…"
"$VPY" -m PyInstaller \
  --name "Site Harvester" \
  --windowed \
  --noconfirm \
  --clean \
  --collect-all weasyprint \
  --collect-all yt_dlp \
  --collect-all playwright \
  --collect-all pypdf \
  --hidden-import pypdf \
  site_harvester.py

echo
echo "================================================"
echo "  Done!"
echo "================================================"
echo
echo "Your app is here:"
echo "  $(pwd)/dist/Site Harvester.app"
echo
echo "Next steps:"
echo "  1. Open the 'dist' folder that was just created."
echo "  2. Drag 'Site Harvester.app' into your Applications folder."
echo "  3. The first time you open it, right-click the app and choose 'Open'"
echo "     (this is a one-time macOS security step for apps you built yourself)."
echo

# Open the dist folder in Finder for convenience.
open dist 2>/dev/null || true
