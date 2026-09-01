#!/bin/bash
# Double-click this file to build Site Harvester.app.
# It runs the build script and keeps this window open so you can read the result.
cd "$(dirname "$0")" || exit 1
echo "==============================================="
echo "  Building Site Harvester"
echo "  (the first build can take a few minutes)"
echo "==============================================="
echo
bash ./build.sh
status=$?
echo
if [ "$status" -eq 0 ]; then
  echo "Done. Open the 'dist' folder, then drag 'Site Harvester.app' into Applications."
else
  echo "Build ended with an error (code $status). Scroll up to see what went wrong."
fi
echo
read -n 1 -s -r -p "Press any key to close this window."
echo
