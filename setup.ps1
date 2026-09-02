# Site Harvester - Windows first-run setup.
#
# Called by run.bat. Makes the app self-sufficient:
#   1. builds a Python environment inside this folder and installs the packages
#   2. downloads the headless browser it uses for page capture and PDF output
#   3. downloads a portable ffmpeg into .\tools - no admin, nothing on PATH
#
# Everything it creates lives inside this folder, so deleting the folder
# removes all of it. Re-running is cheap: each step checks whether it is
# already done and skips.

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$ToolsDir = Join-Path $PSScriptRoot 'tools'
$VenvDir  = Join-Path $PSScriptRoot '.venv-win'
$VenvPy   = Join-Path $VenvDir 'Scripts\python.exe'

function Write-Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   $msg" -ForegroundColor Yellow }

function Find-Tool($exeName) {
    $cmd = Get-Command $exeName -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if (Test-Path $ToolsDir) {
        $hit = Get-ChildItem -Path $ToolsDir -Filter $exeName -Recurse -File -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

# Download a release asset from a public GitHub repo and unzip it under .\tools.
# The asset is matched by pattern rather than hard-coded, so a new upstream
# build does not break this script.
function Install-Portable($repo, $tag, $namePattern, $destName) {
    $dest = Join-Path $ToolsDir $destName
    $api = if ($tag -eq 'latest') {
        "https://api.github.com/repos/$repo/releases/latest"
    } else {
        "https://api.github.com/repos/$repo/releases/tags/$tag"
    }

    Write-Host "   Asking GitHub for the current $destName build..."
    $release = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'site-harvester-setup' }
    $asset = $release.assets | Where-Object { $_.name -like $namePattern } | Select-Object -First 1
    if (-not $asset) {
        throw "No asset matching '$namePattern' in $repo ($($release.tag_name))."
    }

    $zip = Join-Path $env:TEMP $asset.name
    $mb = [math]::Round($asset.size / 1MB, 1)
    Write-Host "   Downloading $($asset.name) ($mb MB) - this is the slow bit..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing

    Write-Host "   Unpacking into tools\$destName..."
    if (Test-Path $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
}


# --------------------------------------------------------------------------- #
# 1. Python environment
# --------------------------------------------------------------------------- #
Write-Step 'Python environment'
if (Test-Path $VenvPy) {
    Write-Ok 'Already set up.'
} else {
    # Build the command and its arguments separately and splat them. An array
    # slice like $a[1..($a.Length-1)] silently reverses on a one-element array,
    # which would pass the interpreter its own name as an argument.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $pyCmd = 'py'; $pyArgs = @('-3')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $pyCmd = 'python'; $pyArgs = @()
    } else {
        Write-Host ''
        Write-Host 'Python was not found.' -ForegroundColor Red
        Write-Host '  Install it:  winget install -e --id Python.Python.3.12'
        exit 1
    }
    Write-Host '   Creating it (first run only)...'
    & $pyCmd @pyArgs -m venv $VenvDir
    & $VenvPy -m pip install --upgrade pip --quiet
    Write-Host '   Installing libraries (yt-dlp, Playwright and friends)...'
    & $VenvPy -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt') --quiet
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
    Write-Ok 'Done.'
}


# --------------------------------------------------------------------------- #
# 2. Headless browser
#
# Used to capture pages and to print them to PDF, so this is not optional.
# Playwright keeps its own cache and skips the download if it already has it.
# --------------------------------------------------------------------------- #
Write-Step 'Headless browser (Chromium)'
& $VenvPy -m playwright install chromium
if ($LASTEXITCODE -eq 0) { Write-Ok 'Ready.' }
else { Write-Warn 'Chromium did not download. PDF output will be skipped until it does.' }


# --------------------------------------------------------------------------- #
# 3. ffmpeg (portable - no installer, no admin)
#
# Optional: only embedded and best-quality video need it. A failure here is a
# warning, not an error.
# --------------------------------------------------------------------------- #
Write-Step 'ffmpeg'
$ff = Find-Tool 'ffmpeg.exe'
if ($ff) {
    Write-Ok "Found: $ff"
} else {
    try {
        Install-Portable 'BtbN/FFmpeg-Builds' 'latest' '*win64-gpl.zip' 'ffmpeg'
        $ff = Find-Tool 'ffmpeg.exe'
        if ($ff) { Write-Ok "Installed: $ff" }
        else { Write-Warn 'Unpacked, but ffmpeg.exe was not found inside it.' }
    } catch {
        Write-Warn "Could not fetch ffmpeg automatically: $($_.Exception.Message)"
        Write-Warn 'Everything works without it except embedded and best-quality'
        Write-Warn 'video. To add it later:  winget install -e --id Gyan.FFmpeg'
    }
}

Write-Host ''
