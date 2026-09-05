#!/usr/bin/env python3
"""
Site Harvester
A desktop app for Windows, macOS and Linux that crawls a website and downloads
every file it can find (documents, images, videos, audio, archives, and anything
else), sorting the results into folders by file type — and optionally saves a
single, clickable PDF copy of every page it visits.

GUI: Tkinter (bundled with Python, nothing extra to install for the interface)
Crawling/parsing: requests + beautifulsoup4
Page-to-PDF: the headless Chromium that Playwright installs prints each page,
    and pypdf merges them. WeasyPrint is an optional lower-fidelity fallback
    for when Chromium cannot start (see requirements-fallback.txt).
Bundling: PyInstaller — build.sh on macOS, build.bat on Windows

This crawler does not consult robots.txt and does not pause between requests.
Use it on sites you own or have permission to copy.
"""

import os
import re
import shutil
import queue
import subprocess
import sys
import threading
import mimetypes
from html import escape
from collections import deque
from urllib.parse import urljoin, urlparse, unquote

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# The launcher can drop a portable ffmpeg into ./tools rather than installing
# anything system-wide, so that folder is searched before PATH.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(APP_DIR, "tools")


def _in_tools(exe_name):
    """First matching executable anywhere under ./tools, or None."""
    if not os.path.isdir(TOOLS_DIR):
        return None
    for root, _dirs, files in os.walk(TOOLS_DIR):
        if exe_name in files:
            return os.path.join(root, exe_name)
    return None

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --------------------------------------------------------------------------- #
# Appearance: light and dark mode.
#
# Follows the operating system's setting automatically; the Appearance menu
# overrides it, and that choice is remembered in a small file next to the app.
# The clam ttk theme is the one theme that recolours fully, so both modes
# style it from a single palette - same approach as findex.
# --------------------------------------------------------------------------- #

LIGHT_PALETTE = {
    "bg": "#f5f6f8", "fg": "#16191d", "field": "#ffffff",
    "btn": "#e8eaee", "btn_hi": "#dde0e6",
    "sel": "#1f6fd6", "sel_fg": "#ffffff",
    "hint": "#5c6672", "accent": "#1a5fb4", "border": "#cfd4db",
}
DARK_PALETTE = {
    "bg": "#1e2226", "fg": "#e2e8ee", "field": "#272c31",
    "btn": "#343b42", "btn_hi": "#3f474f",
    "sel": "#2f74c9", "sel_fg": "#ffffff",
    "hint": "#9aa5b0", "accent": "#79b3ef", "border": "#3d444c",
}

# In a PyInstaller build, "next to the app" is next to the .exe, not inside
# the unpacked _internal folder.
if getattr(sys, "frozen", False):
    UI_SETTINGS = os.path.join(os.path.dirname(sys.executable),
                               "site_harvester_ui.json")
else:
    UI_SETTINGS = os.path.join(APP_DIR, "site_harvester_ui.json")


def system_dark():
    """Is the operating system currently in dark mode?"""
    try:
        if IS_MAC:
            r = subprocess.run(["defaults", "read", "-g",
                                "AppleInterfaceStyle"],
                               capture_output=True, text=True, timeout=5)
            return "dark" in r.stdout.lower()
        if IS_WINDOWS:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes"
                r"\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except Exception:
        pass
    return False


def load_theme_choice():
    """'system', 'light' or 'dark' - whatever was chosen last time."""
    import json
    try:
        with open(UI_SETTINGS, encoding="utf-8") as fh:
            choice = json.load(fh).get("theme", "system")
        return choice if choice in ("system", "light", "dark") else "system"
    except Exception:
        return "system"


def save_theme_choice(choice):
    import json
    try:
        with open(UI_SETTINGS, "w", encoding="utf-8") as fh:
            json.dump({"theme": choice}, fh)
    except OSError:
        pass

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Run:  pip3 install requests beautifulsoup4")
    raise

# Imported lazily where used, but referenced here too so the app bundler
# (PyInstaller) reliably includes it in the built .app.
try:
    import pypdf  # noqa: F401
except Exception:
    pypdf = None

# --- Make the bundled .app find Playwright's browser ------------------------ #
# In a PyInstaller build, Playwright looks for its browser INSIDE the app bundle
# (an empty ".local-browsers" folder) and fails. Point it at the standard cache
# where the build's 'playwright install chromium' actually saved the browser,
# unless the environment already specifies a location.
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    for _pw_cache in (
        os.path.expanduser("~/Library/Caches/ms-playwright"),   # macOS
        os.path.expanduser("~/.cache/ms-playwright"),           # Linux
        os.path.expanduser("~/AppData/Local/ms-playwright"),    # Windows
    ):
        if os.path.isdir(_pw_cache):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _pw_cache
            break


# --------------------------------------------------------------------------- #
# File-type categories. "Other" is a catch-all that adapts to any extension.
# --------------------------------------------------------------------------- #
CATEGORIES = {
    "Images": {
        "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "tiff", "tif",
        "ico", "heic", "heif", "avif", "psd", "raw",
    },
    "Videos": {
        "mp4", "mov", "webm", "avi", "mkv", "flv", "wmv", "m4v", "mpg",
        "mpeg", "3gp", "ogv",
    },
    "Documents": {
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv",
        "rtf", "odt", "ods", "odp", "md", "epub", "pages", "key", "numbers",
    },
    "Audio": {
        "mp3", "wav", "m4a", "aac", "flac", "ogg", "oga", "wma", "aiff",
        "opus",
    },
    "Archives": {
        "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "dmg", "iso",
        "pkg",
    },
}

EXT_TO_CATEGORY = {}
for _cat, _exts in CATEGORIES.items():
    for _e in _exts:
        EXT_TO_CATEGORY[_e] = _cat

DEPTH_OPTIONS = {
    "Just this page (no crawling)": 0,
    "This page + 1 level deep": 1,
    "This page + 2 levels deep": 2,
    "This page + 3 levels deep": 3,
    "This page + 5 levels deep": 5,
    "Entire site (unlimited depth)": -1,
}

SCOPE_OPTIONS = {
    "Stay on this domain (includes www + subdomains)": "domain",
    "Follow links to other websites too": "any",
}

# How many sites to crawl at the same time (1 = one after another).
CONCURRENCY_OPTIONS = ["1", "2", "3", "4", "6", "8"]

PAGE_EXTS = {"html", "htm", "php", "asp", "aspx", "jsp", "cfm", ""}

# Streaming manifests yt-dlp can turn into a real video file.
STREAM_EXTS = {"m3u8", "mpd"}

# Hosts whose embedded players yt-dlp knows how to download.
VIDEO_EMBED_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "vimeo.com",
    "dailymotion.com", "wistia.com", "wistia.net", "streamable.com",
    "brightcove", "jwplatform.com", "jwplayer.com", "facebook.com/plugins/video",
    "player.twitch.tv", "ted.com", "bitchute.com", "rumble.com",
)

# All video extensions (for routing to yt-dlp) plus stream manifests.
VIDEO_EXTS = CATEGORIES["Videos"] | STREAM_EXTS

# Media CDNs used by common site builders — their URLs often have no clean
# file extension, so we recognise them by host and confirm type on download.
MEDIA_CDNS = (
    "img1.wsimg.com", "wsimg.com", "static.wixstatic.com", "video.wixstatic.com",
    "images.squarespace-cdn.com", "static1.squarespace.com", "cdn.shopify.com",
    "res.cloudinary.com", "imgix.net", "cloudfront.net", "akamaihd.net",
    "fbcdn.net", "twimg.com", "ggpht.com", "googleusercontent.com",
)

# Find a real media extension anywhere in a URL (handles CDN URLs like
# .../photo.jpg/:/rs=w:1280 where the extension isn't at the very end).
MEDIA_EXT_RE = re.compile(
    r'\.(jpg|jpeg|png|gif|webp|svg|bmp|tif|tiff|ico|heic|heif|avif|'
    r'mp4|mov|webm|m4v|mkv|avi|wmv|flv|mpg|mpeg|3gp|ogv|'
    r'mp3|wav|m4a|aac|flac|ogg|oga|opus|wma|aiff|'
    r'pdf|docx?|xlsx?|pptx?|rtf|odt|ods|odp|csv|txt|md|epub|'
    r'zip|rar|7z|tar|gz|tgz|bz2|xz|dmg|iso|pkg|m3u8|mpd)'
    r'(?=$|[/?#&])', re.I)

URL_RE = re.compile(r'''https?://[^\s"'`()<>\\]+''')


def find_media_ext(url):
    """Return the (last) media extension found anywhere in the URL, or ''."""
    matches = list(MEDIA_EXT_RE.finditer(url))
    return matches[-1].group(1).lower() if matches else ""


def looks_like_media(url):
    """True if a URL is worth trying as a media file."""
    if "{" in url or "%7B" in url or "}" in url:   # templated placeholder URL
        return False
    if find_media_ext(url):
        return True
    low = url.lower()
    return any(host in low for host in MEDIA_CDNS)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 SiteHarvester/1.0"
)


def get_extension(url):
    path = urlparse(url).path
    name = unquote(os.path.basename(path))
    if "." in name:
        return name.rsplit(".", 1)[1].lower().strip()
    return ""


def category_for_ext(ext):
    if not ext:
        return None
    return EXT_TO_CATEGORY.get(ext, "Other")


def base_domain(host):
    """Reduce a hostname to its registered domain so subdomains match."""
    host = (host or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def safe_filename(url):
    path = urlparse(url).path
    name = unquote(os.path.basename(path)) or "file"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "file"
    return name[:180]


def safe_dirname(name):
    name = re.sub(r'[^A-Za-z0-9._-]', "_", name).strip("._")
    return name or "site"


def find_ffmpeg_dir():
    """Locate ffmpeg.

    PATH is checked first, then the usual install locations for each platform.
    The explicit list matters because GUI apps launched from Finder (macOS) or
    from a shortcut (Windows) often start with a bare PATH that does not include
    Homebrew, winget shims or a manually unzipped build."""
    exe = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"

    local = _in_tools(exe)
    if local:
        return os.path.dirname(local)

    w = shutil.which(exe) or shutil.which("ffmpeg")
    if w:
        return os.path.dirname(w)

    candidates = []
    if IS_WINDOWS:
        import glob
        local = os.environ.get("LOCALAPPDATA", "")
        for pattern in (
            r"C:\ffmpeg*\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg*\bin\ffmpeg.exe",
            os.path.join(local, "Microsoft", "WinGet", "Packages",
                         "*FFmpeg*", "**", "ffmpeg.exe"),
        ):
            candidates += sorted(glob.glob(pattern, recursive=True), reverse=True)
    else:
        candidates += ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                       "/usr/bin/ffmpeg"]

    for c in candidates:
        if os.path.exists(c):
            return os.path.dirname(c)
    return None


# Map real content-types to a file extension. This is more reliable than
# mimetypes.guess_extension for the document/archive types we care about (which
# it either misses or maps to odd values), and it lets us rescue files that
# servers hand out as a generic download.
CONTENT_TYPE_EXT = {
    # Documents
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/rtf": "rtf", "text/rtf": "rtf",
    "application/epub+zip": "epub",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "text/csv": "csv", "text/plain": "txt", "text/markdown": "md",
    # Archives
    "application/zip": "zip", "application/x-zip-compressed": "zip",
    "application/x-rar-compressed": "rar", "application/vnd.rar": "rar",
    "application/x-7z-compressed": "7z",
    "application/x-tar": "tar", "application/gzip": "gz",
    "application/x-gzip": "gz", "application/x-bzip2": "bz2",
    "application/x-xz": "xz", "application/x-apple-diskimage": "dmg",
    # Audio / video / images that sometimes arrive without a clean extension
    "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/aac": "aac",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/flac": "flac",
    "audio/ogg": "ogg", "audio/opus": "opus",
    "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
    "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
    "image/webp": "webp", "image/svg+xml": "svg", "image/avif": "avif",
    "image/heic": "heic", "image/tiff": "tif", "image/bmp": "bmp",
}


def ext_from_content_disposition(headers):
    """Pull the extension from a Content-Disposition filename, if present.
    This is the most reliable signal for /download-style endpoints."""
    cd = headers.get("Content-Disposition", "")
    if not cd:
        return ""
    # filename*=UTF-8''name.pdf  or  filename="name.pdf"  or  filename=name.pdf
    m = re.search(r'filename\*\s*=\s*(?:[^\']*\'\')?([^;]+)', cd, re.I)
    if not m:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    if not m:
        return ""
    fn = unquote(m.group(1).strip().strip('"')).strip()
    if "." in fn:
        e = fn.rsplit(".", 1)[1].lower().strip()
        if e and re.fullmatch(r'[a-z0-9]{1,5}', e):
            return e
    return ""


def ext_from_response(headers):
    """Best-effort extension for a fetched response, using (in order) the
    Content-Disposition filename, a curated content-type map, then mimetypes."""
    e = ext_from_content_disposition(headers)
    if e:
        return e
    ctype = headers.get("Content-Type", "").split(";")[0].strip().lower()
    if ctype in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[ctype]
    guessed = (mimetypes.guess_extension(ctype) or "").lstrip(".").lower()
    if guessed in ("jpe", "jpeg"):
        guessed = "jpg"
    return guessed or ""


def name_from_content_disposition(headers):
    """Return a sanitised filename from a Content-Disposition header, or ''.
    Lets /download endpoints keep their real name instead of 'download.pdf'."""
    cd = headers.get("Content-Disposition", "")
    if not cd:
        return ""
    m = (re.search(r'filename\*\s*=\s*(?:[^\']*\'\')?([^;]+)', cd, re.I)
         or re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I))
    if not m:
        return ""
    fn = unquote(m.group(1).strip().strip('"')).strip()
    fn = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", fn).strip()
    return fn[:180]


# JavaScript run in the page before printing, to clear cookie/consent banners
# and other fixed overlays that would otherwise sit on top of every page in the
# PDF. It removes fixed/sticky elements whose id/class/aria match consent
# keywords, removes full-screen dimming backdrops, and unlocks page scrolling
# (consent modals often lock the body). It does NOT accept anything — it just
# hides the overlay so the underlying page prints cleanly.
JS_KILL_OVERLAYS = r"""
(() => {
  try {
    const kw = ['cookie','consent','gdpr','ccpa','cmp','onetrust','cookiebot',
      'truste','didomi','usercentrics','silktide','klaro','osano','quantcast',
      'privacy-banner','privacy-notice','consent-banner','cc-banner','cc-window',
      'age-gate','gdpr-','-gdpr'];
    const hit = (s) => { s=(s||'').toString().toLowerCase();
      return kw.some(k => s.includes(k)); };
    const kill = [];
    document.querySelectorAll('body *').forEach(el => {
      let pos;
      try { pos = getComputedStyle(el).position; } catch (e) { return; }
      if (pos !== 'fixed' && pos !== 'sticky') return;
      const id = el.id || '';
      const cls = (el.className && el.className.toString) ? el.className.toString() : '';
      const al = (el.getAttribute && (el.getAttribute('aria-label') ||
                 el.getAttribute('data-testid') || el.getAttribute('data-cookie'))) || '';
      const role = (el.getAttribute && el.getAttribute('role')) || '';
      const txt = (el.textContent || '');
      if (hit(id) || hit(cls) || hit(al) ||
          ((role === 'dialog' || role === 'alertdialog') && hit(txt))) {
        kill.push(el);
      }
    });
    kill.forEach(el => { try { el.remove(); } catch (e) {} });
    // Full-screen high z-index dimmers/backdrops with almost no text.
    document.querySelectorAll('body *').forEach(el => {
      try {
        const s = getComputedStyle(el);
        if (s.position === 'fixed' && parseInt(s.zIndex || '0', 10) >= 1000 &&
            el.offsetWidth >= window.innerWidth * 0.9 &&
            el.offsetHeight >= window.innerHeight * 0.9 &&
            (el.textContent || '').trim().length < 40) {
          el.remove();
        }
      } catch (e) {}
    });
    // Un-lock scrolling that consent modals often force on <html>/<body>.
    ['html', 'body'].forEach(t => {
      const x = document.querySelector(t);
      if (x) { x.style.overflow = 'auto'; x.style.position = 'static';
               x.style.height = 'auto'; }
    });
  } catch (e) {}
})();
"""

# Injected into every page inside the single-file mirror. It makes the page's
# own internal links drive the mirror's page switcher (via postMessage to the
# parent) instead of trying to go to the network.
MIRROR_PAGE_JS = r"""
(function(){
  document.addEventListener('click', function(e){
    var a = e.target && e.target.closest ? e.target.closest('a[data-mirror]') : null;
    if(a){
      e.preventDefault();
      try { parent.postMessage({__mirror_go: parseInt(a.getAttribute('data-mirror'), 10)}, '*'); } catch(_) {}
    }
  }, true);
})();
"""

# Shell for the single-file mirror. Placeholders __TITLE__, __NAV__, __PAGES__
# are filled in (in one pass) by _assemble_mirror.
MIRROR_SHELL = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — offline mirror</title>
<style>
  :root { --bar:#1f2937; --side:#111827; --accent:#2563eb; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  #bar { position:fixed; top:0; left:0; right:0; height:46px; background:var(--bar); color:#fff;
         display:flex; align-items:center; gap:12px; padding:0 14px; z-index:5; }
  #bar b { font-size:15px; font-weight:600; }
  #bar .muted { color:#9ca3af; font-size:12px; }
  #toggle { background:#374151; color:#fff; border:0; border-radius:6px; padding:6px 10px; cursor:pointer; }
  #side { position:fixed; top:46px; left:0; bottom:0; width:280px; background:var(--side); color:#e5e7eb;
          overflow:auto; padding:8px 0; z-index:4; }
  #side ol { list-style:none; margin:0; padding:0; counter-reset:n; }
  #side li { counter-increment:n; }
  #side a { display:block; color:#cbd5e1; text-decoration:none; padding:7px 14px 7px 42px; font-size:13px;
            position:relative; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #side a:before { content:counter(n); position:absolute; left:12px; color:#6b7280; font-size:11px; }
  #side a:hover { background:#1f2937; color:#fff; }
  #side a.active { background:var(--accent); color:#fff; }
  #view { position:fixed; top:46px; left:280px; right:0; bottom:0; width:calc(100% - 280px);
          height:calc(100% - 46px); border:0; background:#fff; }
  body.collapsed #side { display:none; }
  body.collapsed #view { left:0; width:100%; }
</style></head>
<body>
  <div id="bar">
    <button id="toggle">&#9776; Pages</button>
    <b>__TITLE__</b>
    <span class="muted">offline mirror &mdash; click a page on the left</span>
  </div>
  <nav id="side"><ol>__NAV__</ol></nav>
  <iframe id="view" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation"></iframe>
  <script>
    var PAGES = __PAGES__;
    var view = document.getElementById('view');
    function decodeB64(b){
      try { return new TextDecoder('utf-8').decode(Uint8Array.from(atob(b), function(c){ return c.charCodeAt(0); })); }
      catch(e){ return atob(b); }
    }
    function show(i){
      if(i==null || i<0 || i>=PAGES.length) return;
      view.srcdoc = decodeB64(PAGES[i].b);
      var links = document.querySelectorAll('#side a');
      for(var k=0;k<links.length;k++){ links[k].classList.toggle('active', links[k].getAttribute('data-go')==String(i)); }
      try { document.title = PAGES[i].t + ' — offline mirror'; } catch(e){}
    }
    var navlinks = document.querySelectorAll('#side a');
    for(var j=0;j<navlinks.length;j++){
      navlinks[j].addEventListener('click', function(e){ e.preventDefault(); show(parseInt(this.getAttribute('data-go'),10)); });
    }
    document.getElementById('toggle').addEventListener('click', function(){ document.body.classList.toggle('collapsed'); });
    window.addEventListener('message', function(e){
      if(e.data && typeof e.data.__mirror_go === 'number'){ show(e.data.__mirror_go); }
    });
    show(0);
  </script>
</body></html>
"""


class Harvester:
    """Runs the crawl + download (and optional PDF) in a background thread."""

    def __init__(self, start_url, max_depth, wanted_categories,
                 grab_other, out_dir, scope, make_pdf, use_ytdlp,
                 use_render, log_q, stop_event, tag="s1", site_label=None,
                 make_mirror=False):
        self.start_url = start_url
        self.max_depth = max_depth
        self.wanted = wanted_categories
        self.grab_other = grab_other
        self.scope = scope
        self.make_pdf = make_pdf
        self.make_mirror = make_mirror
        # yt-dlp only matters if the user is collecting videos at all.
        self.use_ytdlp = use_ytdlp and ("Videos" in wanted_categories)
        self.render = use_render
        self.log_q = log_q
        self.stop_event = stop_event
        # Identity used to tag messages so the UI can group them per site
        # when several sites are being crawled at once.
        self.tag = tag
        self.site_label = site_label or urlparse(start_url).netloc or start_url

        # Note: this crawler does not consult or obey robots.txt — it fetches
        # pages directly. Browser-like headers help avoid sites that reject
        # requests which don't look like a real browser.
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.visited_pages = set()
        self.downloaded = set()

        host = urlparse(start_url).netloc
        self.start_base = base_domain(host)
        self.site_name = safe_dirname(host)
        # All files + the PDF live in a folder named after the site.
        self.site_dir = os.path.join(out_dir, self.site_name)

        self.videos_dir = os.path.join(self.site_dir, "Videos")

        self.counts = {}
        self.file_total = 0
        self.pages = []          # (url, html) captured for the PDF
        self.queue_size = 0

        # yt-dlp state (set up lazily on first video found).
        self._ydl = None
        self._ydl_failed = False
        self._video_seen = set()

        # Headless-browser state (set up lazily on first page if rendering).
        self._pw = None
        self._browser = None
        self._browser_failed = False
        self._browser_error = ""
        self._pdf_ctx = None
        self._pdf_page = None

    # ---- logging / status helpers ---------------------------------------- #
    def log(self, msg):
        self.log_q.put(("log", self.tag, msg))

    def status(self):
        self.log_q.put(("status", self.tag, {
            "pages": len(self.visited_pages),
            "queued": self.queue_size,
            "files": self.file_total,
        }))

    def wants(self, ext):
        cat = category_for_ext(ext)
        if cat is None:
            return False
        if cat == "Other":
            return self.grab_other
        return cat in self.wanted

    # ---- main entry ------------------------------------------------------- #
    def run(self):
        try:
            os.makedirs(self.site_dir, exist_ok=True)
            depth_label = "unlimited" if self.max_depth == -1 else self.max_depth
            self.log(f"Starting on {self.start_url}")
            self.log(f"Scope: {self.scope} | Max depth: {depth_label}")
            self.log(f"Saving into: {self.site_dir}")
            self.log("-" * 48)
            self._crawl()

            if not self.stop_event.is_set():
                if self.make_pdf:
                    self._build_pdf()
                if self.make_mirror:
                    self._build_mirror()

            total = sum(self.counts.values())
            self.log("-" * 48)
            if self.counts:
                summary = ", ".join(f"{k}: {v}" for k, v in sorted(self.counts.items()))
                self.log(f"Done. Visited {len(self.visited_pages)} page(s), "
                         f"downloaded {total} file(s) — {summary}")
            else:
                self.log(f"Done. Visited {len(self.visited_pages)} page(s). "
                         "No matching files were found.")
        except Exception as e:
            self.log(f"ERROR: {e}")
        finally:
            self._close_browser()
            self.status()

    # ---- crawling --------------------------------------------------------- #
    def _crawl(self):
        q = deque()
        q.append((self.start_url, 0))
        queued = {self.start_url}

        while q:
            if self.stop_event.is_set():
                self.log("Stopped.")
                return
            url, depth = q.popleft()
            self.queue_size = len(q)
            if url in self.visited_pages:
                continue
            self.visited_pages.add(url)

            depth_txt = "start" if depth == 0 else f"depth {depth}"
            self.log(f"→ Visiting [{depth_txt}]: {url}")
            self.status()

            new_pages = self._process_page(url)

            if self.max_depth == -1 or depth < self.max_depth:
                for link in new_pages:
                    if link not in queued and link not in self.visited_pages:
                        if self._is_crawlable(link):
                            queued.add(link)
                            q.append((link, depth + 1))
            self.queue_size = len(q)
            self.status()

    # Attributes across the web that carry a media URL (incl. lazy-loading).
    MEDIA_ATTRS = [
        ("img", "src"), ("img", "data-src"), ("img", "data-lazy-src"),
        ("img", "data-original"), ("img", "data-image"), ("img", "data-url"),
        ("img", "data-hi-res-src"), ("img", "data-flickity-lazyload"),
        ("source", "src"), ("video", "src"), ("video", "poster"),
        ("audio", "src"), ("embed", "src"), ("object", "data"),
        ("link", "href"),
    ]

    def _collect(self, base, soup, media, links):
        """Pull media URLs and page links out of one parsed document."""
        for tag, attr in self.MEDIA_ATTRS:
            for el in soup.find_all(tag):
                v = el.get(attr)
                if v:
                    media.add(urljoin(base, v))
        # srcset can hold several URLs.
        for el in soup.find_all(["img", "source"]):
            ss = el.get("srcset") or el.get("data-srcset")
            if ss:
                for part in ss.split(","):
                    u = part.strip().split(" ")[0]
                    # Only take absolute URLs. Builders like Wix pack commas
                    # into their sizing params (…/fill/w_150,h_178,q_80,…), and
                    # splitting on "," yields junk fragments ("h_178", "q_80")
                    # that would otherwise be fetched and 404.
                    if u.startswith(("http://", "https://", "//")):
                        media.add(urljoin(base, u))
        # CSS background-image URLs (inline styles + <style> blocks).
        for el in soup.find_all(style=True):
            for u in re.findall(r'url\(([^)]+)\)', el["style"]):
                media.add(urljoin(base, u.strip('\'" ')))
        for st in soup.find_all("style"):
            for u in re.findall(r'url\(([^)]+)\)', st.get_text() or ""):
                media.add(urljoin(base, u.strip('\'" ')))
        # Anchors: a file link becomes media, otherwise a page to crawl.
        for a in soup.find_all("a", href=True):
            absu = urljoin(base, a["href"])
            if find_media_ext(absu):
                media.add(absu)
            else:
                links.add(absu.split("#")[0])

    # ---- optional headless-browser rendering ----------------------------- #
    def _ensure_browser(self):
        """Start headless Chromium on first use; None if unavailable."""
        if self._browser is not None or self._browser_failed:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            self._browser_failed = True
            self._browser_error = f"playwright import failed: {e}"
            self.log("JavaScript rendering needs Playwright, which isn't "
                     f"available — falling back to fast mode. ({e})")
            return None
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self.log("Headless browser ready.")
        except Exception as e:
            self._browser_failed = True
            self._browser_error = f"chromium launch failed: {e}"
            self.log(f"Could not start the headless browser: {e}")
            # Report where it looked, to make packaging problems obvious.
            bpath = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
            try:
                exe = self._pw.chromium.executable_path if self._pw else ""
            except Exception:
                exe = ""
            self.log(f"        Browsers path: {bpath or '(default cache)'}")
            self.log(f"        Expected Chromium: {exe or '(unknown)'} "
                     f"— exists: {bool(exe) and os.path.exists(exe)}")
            self.log("        Try:  python3 -m playwright install chromium")
            self.log("        Continuing in fast (no-JavaScript) mode.")
            return None
        return self._browser

    def _close_browser(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None

    def _render_page(self, url, extra_media):
        """Load a page in the browser, run its JS, scroll for lazy content,
        capture media seen on the network, and return the rendered HTML.
        Returns None if the page isn't HTML; falls back to plain fetch on error."""
        browser = self._ensure_browser()
        if browser is None:
            try:
                resp = self.session.get(url, timeout=20)
                if "text/html" in resp.headers.get("Content-Type", "").lower():
                    return resp.text
            except Exception as e:
                self.log(f"   (skipped, error loading: {e})")
            return None

        page = None
        try:
            page = browser.new_page(user_agent=USER_AGENT)

            def _on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "").lower()
                    if ct.startswith(("image/", "video/", "audio/")) or ct.startswith("application/pdf"):
                        extra_media.add(resp.url)
                except Exception:
                    pass

            page.on("response", _on_response)
            page.goto(url, wait_until="load", timeout=30000)

            # Scroll to trigger lazy-loaded images / infinite content.
            for _ in range(8):
                if self.stop_event.is_set():
                    break
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(400)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            return page.content()
        except Exception as e:
            self.log(f"   (render failed, using fast mode: {e})")
            try:
                resp = self.session.get(url, timeout=20)
                if "text/html" in resp.headers.get("Content-Type", "").lower():
                    return resp.text
            except Exception:
                pass
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _process_page(self, url):
        extra_media = set()
        if self.render:
            html = self._render_page(url, extra_media)
            if html is None:
                return set()
        else:
            try:
                resp = self.session.get(url, timeout=20)
            except Exception as e:
                self.log(f"   (skipped, error loading: {e})")
                return set()
            ctype = resp.headers.get("Content-Type", "").lower()
            if "text/html" not in ctype and "application/xhtml" not in ctype:
                # Not a web page — it may be a file reached via a plain link
                # (e.g. a /download endpoint that serves a PDF or DOCX with no
                # extension in the URL). Save it rather than dropping it.
                self._save_fetched_file(url, resp)
                return set()
            html = resp.text

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            self.log(f"   (skipped, could not read page: {e})")
            return set()

        if self.make_pdf or self.make_mirror:
            self.pages.append((url, html))

        media = set(extra_media)
        page_links = set()
        self._collect(url, soup, media, page_links)

        # Some builders (GoDaddy, etc.) embed the real page inside an iframe
        # 'srcdoc' attribute — parse that as its own document.
        for iframe in soup.find_all("iframe"):
            sd = iframe.get("srcdoc")
            if sd:
                try:
                    inner = BeautifulSoup(sd, "html.parser")
                    self._collect(url, inner, media, page_links)
                    self._scan_for_embeds(url, inner)
                except Exception:
                    pass

        # Catch-all: harvest any media-looking URL anywhere in the raw HTML/JS,
        # including extensionless CDN links and URLs buried in JSON/scripts.
        for m in URL_RE.finditer(html):
            u = m.group(0).replace("\\/", "/").rstrip('\\",;\'')
            if looks_like_media(u):
                media.add(u)

        # Act on each media URL.
        for u in media:
            if self.stop_event.is_set():
                return set()
            ext = find_media_ext(u)
            if self.use_ytdlp and (ext in VIDEO_EXTS):
                self._grab_video(u)
            elif ext in CATEGORIES["Videos"] and not self.use_ytdlp:
                self._download(u, ext)
            else:
                # Images / documents / audio / archives, or an unknown-extension
                # CDN URL whose type we'll confirm from the response headers.
                self._download(u, ext or None)

        # Embedded players / HTML5 video on the main page.
        self._scan_for_embeds(url, soup)

        return page_links

    def _is_crawlable(self, url):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if self.scope == "domain" and base_domain(parsed.netloc) != self.start_base:
            return False
        ext = get_extension(url)
        if ext and ext not in PAGE_EXTS and category_for_ext(ext) is not None:
            return False
        return True

    def _filename_for(self, url, ext):
        """Pick a sensible filename, preferring the URL segment that holds the
        extension (handles CDN URLs like .../photo.jpg/:/rs=w:1280)."""
        seg = None
        for s in reversed(urlparse(url).path.split("/")):
            if s and find_media_ext(s):
                seg = s
                break
        name = unquote(seg) if seg else safe_filename(url)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "file"
        if not name.lower().endswith("." + ext):
            name = f"{name}.{ext}"
        return name[:180]

    def _download(self, url, ext=None):
        # Only real web URLs are downloadable. Skip data:/blob:/mailto: etc.
        # (inline data URIs would otherwise error with "No connection adapters").
        if not url.lower().startswith(("http://", "https://")):
            return
        key = url.split("#")[0]
        if key in self.downloaded:
            return
        self.downloaded.add(key)

        try:
            r = self.session.get(url, stream=True, timeout=30)
            r.raise_for_status()
        except Exception as e:
            self.log(f"   ✗ failed: {url} — {e}")
            return

        try:
            # If the URL didn't reveal an extension, learn it from the headers
            # (Content-Disposition filename, then a real content-type map). This
            # rescues PDFs/Office docs served as a generic download.
            if not ext:
                ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if ctype.startswith("text/html") or ctype in ("application/xml", "application/xhtml+xml"):
                    return  # a web page, not a file we want
                ext = ext_from_response(r.headers) or find_media_ext(url) or get_extension(url)
                if not ext:
                    return

            cat = category_for_ext(ext)
            if cat is None:
                return
            if cat == "Other":
                if not self.grab_other:
                    return
                folder = os.path.join(self.site_dir, "Other", ext)
            else:
                if cat not in self.wanted:
                    return
                folder = os.path.join(self.site_dir, cat)
            os.makedirs(folder, exist_ok=True)

            dest = os.path.join(folder, self._filename_for(url, ext))
            b, x = os.path.splitext(dest)
            i = 1
            while os.path.exists(dest):
                dest = f"{b}_{i}{x}"
                i += 1

            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if self.stop_event.is_set():
                        f.close()
                        os.remove(dest)
                        return
                    if chunk:
                        f.write(chunk)
            self.counts[cat] = self.counts.get(cat, 0) + 1
            self.file_total += 1
            self.log(f"   ✓ saved [{cat}] {os.path.basename(dest)}")
            self.status()
        except Exception as e:
            self.log(f"   ✗ failed: {url} — {e}")
        finally:
            r.close()

    def _save_fetched_file(self, url, resp):
        """Save an already-fetched, non-HTML response as a file if its type is
        one the user asked for. Used when a crawled link turns out to be a
        document / image / archive rather than a page (common with /download
        endpoints that have no extension in the URL)."""
        key = url.split("#")[0]
        if key in self.downloaded:
            return
        # Prefer a real extension from the URL, then the response headers.
        ext = find_media_ext(url) or ext_from_response(resp.headers) or get_extension(url)
        if not ext:
            return
        cat = category_for_ext(ext)
        if cat is None:
            return
        if cat == "Other":
            if not self.grab_other:
                return
            folder = os.path.join(self.site_dir, "Other", ext)
        else:
            if cat not in self.wanted:
                return
            folder = os.path.join(self.site_dir, cat)

        self.downloaded.add(key)
        try:
            os.makedirs(folder, exist_ok=True)
            # Keep the server-provided filename when there is one.
            cd_name = name_from_content_disposition(resp.headers)
            if cd_name:
                if not cd_name.lower().endswith("." + ext):
                    cd_name = f"{cd_name}.{ext}"
                name = cd_name[:180]
            else:
                name = self._filename_for(url, ext)
            dest = os.path.join(folder, name)
            b, x = os.path.splitext(dest)
            i = 1
            while os.path.exists(dest):
                dest = f"{b}_{i}{x}"
                i += 1
            with open(dest, "wb") as f:
                f.write(resp.content)
            self.counts[cat] = self.counts.get(cat, 0) + 1
            self.file_total += 1
            self.log(f"   ✓ saved [{cat}] {os.path.basename(dest)}")
            self.status()
        except Exception as e:
            self.log(f"   ✗ failed: {url} — {e}")

    # ---- best-quality videos via yt-dlp ---------------------------------- #
    def _ensure_ydl(self):
        """Create the yt-dlp downloader on first use; None if unavailable."""
        if self._ydl is not None or self._ydl_failed:
            return self._ydl
        try:
            import yt_dlp
        except Exception as e:
            self._ydl_failed = True
            self.log("Streaming/embedded videos need yt-dlp, which isn't "
                     f"available. ({e})")
            return None

        os.makedirs(self.videos_dir, exist_ok=True)
        ffmpeg_dir = find_ffmpeg_dir()
        if ffmpeg_dir:
            # Best quality usually means merging separate video + audio streams.
            fmt = "bestvideo*+bestaudio/best"
        else:
            fmt = "best"
            self.log("Note: ffmpeg not found — videos will be pulled at the best "
                     "single-file quality. Install with 'brew install ffmpeg' for "
                     "the very highest quality.")

        opts = {
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(self.videos_dir, "%(title).150B [%(id)s].%(ext)s"),
            "restrictfilenames": True,
            "noplaylist": True,          # don't accidentally pull whole channels
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "retries": 3,
            "concurrent_fragment_downloads": 4,
        }
        if ffmpeg_dir:
            opts["ffmpeg_location"] = ffmpeg_dir

        self._ydl = yt_dlp.YoutubeDL(opts)
        return self._ydl

    def _grab_video(self, src):
        """Download one video (page, embed, or stream URL) at best quality."""
        key = src.split("#")[0]
        if key in self._video_seen:
            return
        self._video_seen.add(key)

        ydl = self._ensure_ydl()
        if ydl is None:
            return

        self.log(f"   ↓ checking for video: {src}")
        try:
            info = ydl.extract_info(src, download=True)
        except Exception as e:
            self.log(f"   ✗ video failed: {src} — {e}")
            return
        if not info:
            return  # nothing downloadable there — ignore quietly

        title = info.get("title") or src
        self.counts["Videos"] = self.counts.get("Videos", 0) + 1
        self.file_total += 1
        self.log(f"   ✓ saved [Videos] {title}")
        self.status()

    def _scan_for_embeds(self, url, soup):
        """Find embedded/streaming videos on a page and pull them via yt-dlp."""
        if not self.use_ytdlp or self.stop_event.is_set():
            return
        # Embedded players (YouTube/Vimeo/etc.) in iframes.
        for iframe in soup.find_all("iframe", src=True):
            if self.stop_event.is_set():
                return
            src = urljoin(url, iframe["src"])
            if any(h in src for h in VIDEO_EMBED_HOSTS):
                self._grab_video(src)
        # HTML5 <video> or an og:video hint — hand the page itself to yt-dlp.
        og = soup.find("meta", attrs={"property": "og:video"})
        if soup.find("video") or og:
            self._grab_video(url)

    # ---- combined navigable PDF ------------------------------------------ #
    def _write_pdf_debug(self, message):
        """Leave a small note next to the output saying which PDF engine ran and
        why — makes it easy to see whether the printed-page path worked."""
        try:
            os.makedirs(self.site_dir, exist_ok=True)
            path = os.path.join(self.site_dir, "_pdf_engine.txt")
            with open(path, "w") as f:
                f.write(message + "\n")
        except Exception:
            pass

    def _build_pdf(self):
        """Build one big PDF where each visited page looks like it was printed
        from the browser, with a clickable bookmark index and the site's own
        internal links rewired to jump between pages inside the PDF.

        Each captured page is printed to PDF by the headless browser (so it
        keeps its real layout, CSS and images), then all the pages are merged
        with pypdf. Falls back to the older WeasyPrint layout if the browser
        or pypdf isn't available."""
        if not self.pages:
            self.log("PDF: no pages were captured, skipping.")
            return

        browser = self._ensure_browser()
        if browser is None:
            self.log("!! PDF FALLBACK: the headless browser (Chromium) could not "
                     "start, so you'll get the BASIC text-only PDF, not the "
                     "printed-page version. See the browser error just above.")
            self.log("   Fix:  rebuild the app, and if it persists run in Terminal: "
                     "python3 -m playwright install chromium")
            self._write_pdf_debug("FALLBACK (browser) — " +
                                  (self._browser_error or "browser unavailable"))
            return self._build_pdf_weasyprint()
        try:
            from pypdf import PdfReader, PdfWriter
        except Exception as e:
            self.log(f"!! PDF FALLBACK: pypdf isn't available ({e}) — you'll get "
                     "the BASIC text-only PDF. Rebuild the app so 'pypdf' installs.")
            self._write_pdf_debug(f"FALLBACK (pypdf) — {e}")
            return self._build_pdf_weasyprint()

        # Good path — don't leave a diagnostic file in the output folder, and
        # clear any stale one left behind by an earlier fallback run.
        try:
            _note = os.path.join(self.site_dir, "_pdf_engine.txt")
            if os.path.exists(_note):
                os.remove(_note)
        except Exception:
            pass

        self.log("PDF engine: printed-page (Chromium) — this is the good path.")

        self.log("-" * 48)
        self.log(f"Building printed-page PDF from {len(self.pages)} page(s)…")

        # One shared browser context + page for the whole PDF (cookies persist
        # and there's no per-page browser setup cost — much faster).
        self._pdf_ctx = None
        self._pdf_page = None
        try:
            self._pdf_ctx = browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1280, "height": 1600})
            self._pdf_page = self._pdf_ctx.new_page()
        except Exception:
            self._pdf_ctx = None
            self._pdf_page = None

        self.log("(each page is loaded live and printed — heavy sites take a moment per page)")
        parts = []   # (title, url, pdf_bytes)
        try:
            for i, (url, html) in enumerate(self.pages):
                if self.stop_event.is_set():
                    self.log("PDF cancelled.")
                    return
                title = self._page_title(html) or url
                self.log(f"   • rendering [{i + 1}/{len(self.pages)}] {title[:56]}…")
                data = self._page_pdf_bytes(url, html)
                if data:
                    parts.append((title, url, data))
                else:
                    self.log(f"      ✗ could not print this page")
        finally:
            try:
                if self._pdf_ctx is not None:
                    self._pdf_ctx.close()
            except Exception:
                pass
            self._pdf_ctx = None
            self._pdf_page = None

        if not parts:
            self.log("   ✗ PDF build failed: no pages could be printed.")
            return

        dest = os.path.join(self.site_dir, f"{self.site_name}-pages.pdf")
        try:
            self._merge_pdfs(PdfReader, PdfWriter, parts, dest)
            self.log(f"   ✓ PDF saved: {os.path.basename(dest)} "
                     f"({len(parts)} page(s), clickable index)")
        except Exception as e:
            self.log(f"   ✗ PDF build failed: {e}")

    def _page_title(self, html):
        try:
            soup = BeautifulSoup(html, "html.parser")
            if soup.title and soup.title.string:
                return soup.title.string.strip()
        except Exception:
            pass
        return ""

    def _with_base(self, html, url):
        """Return the captured HTML with a <base> tag (so relative CSS/images
        resolve) and scripts removed (so we print the page exactly as captured
        rather than letting its JS re-run and change or blank it)."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            for junk in soup(["script", "noscript"]):
                junk.decompose()
            head = soup.head
            if head is None:
                head = soup.new_tag("head")
                if soup.html:
                    soup.html.insert(0, head)
                else:
                    soup.insert(0, head)
            if not head.find("base"):
                head.insert(0, soup.new_tag("base", href=url))
            return str(soup)
        except Exception:
            return html

    def _dismiss_overlays(self, page):
        """Hide cookie/consent banners and other fixed overlays so they don't
        cover every page in the PDF. Purely cosmetic — it hides the overlay, it
        does not click 'accept' or submit anything."""
        try:
            page.evaluate(JS_KILL_OVERLAYS)
        except Exception:
            pass

    def _page_pdf_bytes(self, url, html):
        """Print one page to PDF bytes via the headless browser.

        Loads the page LIVE (so JavaScript-built sites — GoDaddy, Wix, Squarespace,
        etc. — render their real content and styling), clears cookie/consent
        overlays, briefly scrolls to pull in lazy images, then prints the
        on-screen layout. Reuses a single shared page for speed. If the live load
        fails, falls back to printing the copy captured during the crawl."""
        browser = self._ensure_browser()
        if browser is None:
            return None

        # Reuse one shared page across all captured pages (much faster than
        # creating a fresh page/context each time). Fall back to a throwaway
        # page if the shared one isn't set up.
        page = getattr(self, "_pdf_page", None)
        own = False
        if page is None:
            try:
                page = browser.new_page(user_agent=USER_AGENT,
                                        viewport={"width": 1280, "height": 1600})
                own = True
            except Exception as e:
                self.log(f"   (print failed: {e})")
                return None

        try:
            page.set_default_timeout(20000)
            loaded = False
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                loaded = True
            except Exception as e:
                self.log(f"   (print: couldn't load live page, using captured copy: {e})")

            if loaded:
                self._dismiss_overlays(page)
                # A short scroll pulls in lazy images without a long wait.
                try:
                    for _ in range(3):
                        if self.stop_event.is_set():
                            break
                        page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(120)
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_load_state("networkidle", timeout=3500)
                except Exception:
                    pass
                self._dismiss_overlays(page)   # again, in case it re-appeared
            elif html is not None:
                try:
                    page.goto("about:blank", timeout=5000)
                except Exception:
                    pass
                page.set_content(self._with_base(html, url), wait_until="load")
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                self._dismiss_overlays(page)
            else:
                return None

            # Print the page as it looks on screen (not the print stylesheet,
            # which many sites use to hide most of the page).
            try:
                page.emulate_media(media="screen")
            except Exception:
                pass

            # Size the PDF page to the full rendered content so each web page
            # becomes ONE continuous page at its natural ratio (no A4 chopping).
            # Width matches the render viewport; height is the full page height.
            # PDF pages have a hard max (~14400pt ≈ 200in), so we cap the height —
            # an unusually long page then splits into a few tall pages.
            width_px = 1280
            height_px = 2000
            try:
                h = page.evaluate(
                    "() => { const d = document.documentElement, b = document.body;"
                    " return Math.max(b ? b.scrollHeight : 0, d ? d.scrollHeight : 0,"
                    " b ? b.offsetHeight : 0, d ? d.offsetHeight : 0); }")
                if h and int(h) > 0:
                    height_px = max(200, min(int(h) + 16, 14000))
            except Exception:
                pass
            return page.pdf(
                width=f"{width_px}px", height=f"{height_px}px",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        except Exception as e:
            self.log(f"   (print failed: {e})")
            return None
        finally:
            if own and page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _merge_pdfs(self, PdfReader, PdfWriter, parts, dest):
        """Merge the per-page PDFs, add a clickable bookmark for each page, and
        rewire the site's own internal links to jump within the merged PDF."""
        import io
        from pypdf.generic import ArrayObject, NameObject, DictionaryObject

        writer = PdfWriter()
        url_to_index = {}
        starts = []
        for title, url, data in parts:
            idx = len(writer.pages)
            reader = PdfReader(io.BytesIO(data))
            for pg in reader.pages:
                writer.add_page(pg)
            url_to_index[url.split("#")[0]] = idx
            starts.append((title or url, idx))

        # Bookmark outline — one clickable entry per captured page.
        for title, idx in starts:
            try:
                writer.add_outline_item((title or "Page")[:120], idx)
            except Exception:
                pass
        try:
            writer.page_mode = "/UseOutlines"   # open the bookmarks panel
        except Exception:
            pass

        # Turn links that point at another captured page into in-PDF jumps.
        try:
            for pg in writer.pages:
                annots = pg.get("/Annots")
                if not annots:
                    continue
                for ref in annots:
                    obj = ref.get_object()
                    if obj.get("/Subtype") != "/Link":
                        continue
                    action = obj.get("/A")
                    if not action:
                        continue
                    uri = action.get_object().get("/URI")
                    if uri is None:
                        continue
                    key = str(uri).split("#")[0]
                    if key in url_to_index:
                        target = writer.pages[url_to_index[key]].indirect_reference
                        obj[NameObject("/A")] = DictionaryObject({
                            NameObject("/S"): NameObject("/GoTo"),
                            NameObject("/D"): ArrayObject([target, NameObject("/Fit")]),
                        })
        except Exception as e:
            self.log(f"   (internal-link linking skipped: {e})")

        with open(dest, "wb") as f:
            writer.write(f)

    def _build_pdf_weasyprint(self):
        """Fallback: a single WeasyPrint document with a table of contents.
        Lower fidelity than the printed-page version, used only when the
        headless browser / pypdf isn't available."""
        if not self.pages:
            return
        try:
            from weasyprint import HTML
        except Exception as e:
            self.log("PDF skipped — neither the headless browser nor WeasyPrint "
                     "is available.")
            self.log(f"        ({e})")
            return

        self.log("-" * 48)
        self.log(f"Building combined PDF from {len(self.pages)} page(s)…")

        url_to_anchor = {}
        for i, (url, _html) in enumerate(self.pages):
            url_to_anchor[url.split("#")[0]] = f"page-{i + 1}"

        toc_items = []
        sections = []
        for i, (url, html) in enumerate(self.pages):
            if self.stop_event.is_set():
                self.log("PDF cancelled.")
                return
            anchor = f"page-{i + 1}"
            soup = BeautifulSoup(html, "html.parser")
            title = ""
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            title = title or url

            for junk in soup(["script", "noscript"]):
                junk.decompose()

            for tag, attr in [("a", "href"), ("img", "src"), ("link", "href"),
                              ("source", "src"), ("video", "src"),
                              ("audio", "src"), ("object", "data")]:
                for el in soup.find_all(tag):
                    v = el.get(attr)
                    if not v:
                        continue
                    absu = urljoin(url, v)
                    if tag == "a":
                        target = absu.split("#")[0]
                        el[attr] = "#" + url_to_anchor[target] if target in url_to_anchor else absu
                    else:
                        el[attr] = absu

            body = soup.body if soup.body else soup
            inner = body.decode_contents() if hasattr(body, "decode_contents") else str(body)
            sections.append(
                f'<section id="{anchor}" class="page">'
                f'<h1 class="ph-title">{escape(title)}</h1>'
                f'<p class="ph-url">{escape(url)}</p>'
                f'{inner}</section>'
            )
            toc_items.append(f'<li><a href="#{anchor}">{escape(title)}</a></li>')

        document = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<style>"
            "@page { size: A4; margin: 1.6cm; "
            "  @bottom-right { content: 'Page ' counter(page); font-size: 9pt; color: #888; } }"
            "body { font-family: -apple-system, Helvetica, Arial, sans-serif; }"
            "section.page { break-before: page; }"
            "h1.ph-title { font-size: 16pt; border-bottom: 2px solid #333; padding-bottom: 4px;"
            "  bookmark-level: 1; bookmark-label: content(text); }"
            ".ph-url { color: #2563eb; font-size: 9pt; margin-top: 0; word-break: break-all; }"
            "#toc h1 { bookmark-level: 1; bookmark-label: 'Table of contents'; }"
            "#toc li { margin: 3px 0; }"
            "img { max-width: 100%; height: auto; }"
            "</style></head><body>"
            f"<section id='toc'><h1>Table of contents (BASIC FALLBACK) — {escape(self.site_name)}</h1>"
            "<p style='color:#b00;font-size:10pt'>This is the basic text-only PDF. "
            "The full printed-page version needs the headless browser (Chromium) — "
            "see the app's activity log for why it fell back.</p>"
            f"<ol>{''.join(toc_items)}</ol></section>"
            f"{''.join(sections)}"
            "</body></html>"
        )

        dest = os.path.join(self.site_dir, f"{self.site_name}-pages.pdf")
        try:
            HTML(string=document, base_url=self.start_url).write_pdf(dest)
            self.log(f"   ✓ PDF saved: {os.path.basename(dest)}")
        except Exception as e:
            self.log(f"   ✗ PDF build failed: {e}")

    # ---- single-file offline mirror -------------------------------------- #
    def _build_mirror(self):
        """Build ONE self-contained .html file that is a browsable snapshot of
        every visited page: each page is rendered, all its assets (CSS, images,
        fonts) are inlined as data: URIs, the site's own internal links are
        rewired to switch pages inside the file, and everything is bundled into a
        single document with a sidebar. Double-click it — no folder needed."""
        if not self.pages:
            self.log("Mirror: no pages were captured, skipping.")
            return
        browser = self._ensure_browser()
        if browser is None:
            self.log("!! MIRROR SKIPPED: needs the headless browser (Chromium), "
                     "which couldn't start. See the browser error above.")
            return

        self.log("-" * 48)
        self.log(f"Building single-file offline mirror from {len(self.pages)} page(s)…")

        url_to_index = {}
        for i, (u, _h) in enumerate(self.pages):
            url_to_index[u.split("#")[0]] = i

        entries = []      # (title, base64-of-inlined-html)
        cache = {}        # url -> data URI (shared across pages, fetch once)
        ctx = None
        try:
            ctx = browser.new_context(user_agent=USER_AGENT,
                                      viewport={"width": 1280, "height": 1600})
            page = ctx.new_page()
            page.set_default_timeout(20000)
            import base64
            for i, (url, html) in enumerate(self.pages):
                if self.stop_event.is_set():
                    self.log("Mirror cancelled.")
                    return
                title = self._page_title(html) or url
                loaded = False
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    loaded = True
                except Exception as e:
                    self.log(f"   (mirror: couldn't load live page, using captured copy: {e})")
                if loaded:
                    self._dismiss_overlays(page)
                    try:
                        for _ in range(3):
                            if self.stop_event.is_set():
                                break
                            page.mouse.wheel(0, 3000)
                            page.wait_for_timeout(120)
                        page.evaluate("window.scrollTo(0, 0)")
                        page.wait_for_load_state("networkidle", timeout=3500)
                    except Exception:
                        pass
                    self._dismiss_overlays(page)
                    try:
                        rendered = page.content()
                    except Exception:
                        rendered = html
                else:
                    rendered = html
                inlined = self._inline_page(ctx, url, rendered, url_to_index, cache)
                b64 = base64.b64encode(inlined.encode("utf-8", "replace")).decode("ascii")
                entries.append((title, b64))
                self.log(f"   • captured [{i + 1}/{len(self.pages)}] {title[:60]}")
        except Exception as e:
            self.log(f"   ✗ Mirror build failed: {e}")
            return
        finally:
            try:
                if ctx is not None:
                    ctx.close()
            except Exception:
                pass

        if not entries:
            self.log("   ✗ Mirror build failed: no pages captured.")
            return

        document = self._assemble_mirror(entries)
        dest = os.path.join(self.site_dir, f"{self.site_name}-mirror.html")
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(document)
            size_mb = os.path.getsize(dest) / 1048576.0
            self.log(f"   ✓ Mirror saved: {os.path.basename(dest)} "
                     f"({len(entries)} page(s), {size_mb:.1f} MB)")
        except Exception as e:
            self.log(f"   ✗ Mirror build failed: {e}")

    def _inline_page(self, ctx, url, html, url_to_index, cache):
        """Return a self-contained HTML string: assets inlined as data: URIs,
        scripts removed, internal links rewired to the mirror switcher."""
        import base64
        req = ctx.request
        MAX_BYTES = 8 * 1024 * 1024   # skip inlining anything larger than 8 MB

        def data_uri(u):
            if u in cache:
                return cache[u]
            val = None
            try:
                r = req.get(u, timeout=20000)
                if r.ok:
                    body = r.body()
                    if len(body) <= MAX_BYTES:
                        ct = (r.headers.get("content-type") or "").split(";")[0].strip()
                        if not ct:
                            ct = "application/octet-stream"
                        val = "data:%s;base64,%s" % (
                            ct, base64.b64encode(body).decode("ascii"))
            except Exception:
                val = None
            cache[u] = val
            return val

        def text_res(u):
            try:
                r = req.get(u, timeout=20000)
                return r.text() if r.ok else None
            except Exception:
                return None

        def inline_css(css, base):
            def repl(m):
                raw = m.group(1).strip().strip('\'"')
                if not raw or raw.startswith("data:") or raw.startswith("#"):
                    return m.group(0)
                d = data_uri(urljoin(base, raw))
                return "url(%s)" % d if d else m.group(0)
            return re.sub(r'url\(([^)]+)\)', repl, css)

        soup = BeautifulSoup(html, "html.parser")
        for junk in soup(["script", "noscript", "base"]):
            junk.decompose()

        for img in soup.find_all("img"):
            src = img.get("src")
            if src and not src.startswith("data:"):
                d = data_uri(urljoin(url, src))
                if d:
                    img["src"] = d
            for attr in ("srcset", "data-src", "data-lazy-src", "data-srcset",
                         "loading", "decoding"):
                if img.has_attr(attr):
                    del img[attr]

        for s in soup.find_all("source"):
            if s.has_attr("srcset"):
                del s["srcset"]
            if s.get("src") and not s["src"].startswith("data:"):
                d = data_uri(urljoin(url, s["src"]))
                if d:
                    s["src"] = d

        for link in list(soup.find_all("link")):
            rel = link.get("rel") or []
            relstr = " ".join(rel).lower() if isinstance(rel, list) else str(rel).lower()
            if "stylesheet" in relstr and link.get("href"):
                css_url = urljoin(url, link["href"])
                css = text_res(css_url)
                if css is not None:
                    style = soup.new_tag("style")
                    style.string = inline_css(css, css_url)
                    link.replace_with(style)
                else:
                    link.decompose()
            elif any(k in relstr for k in ("icon", "preload", "prefetch",
                                           "dns-prefetch", "preconnect", "manifest")):
                link.decompose()

        for st in soup.find_all("style"):
            # NB: for <style>, BeautifulSoup's get_text() returns '' — the CSS
            # lives in st.string. Using get_text() here would wipe the styles.
            css_txt = st.string
            if css_txt:
                st.string = inline_css(str(css_txt), url)

        for el in soup.find_all(style=True):
            el["style"] = inline_css(el["style"], url)

        for a in soup.find_all("a", href=True):
            absu = urljoin(url, a["href"]).split("#")[0]
            if absu in url_to_index:
                a["href"] = "#"
                a["data-mirror"] = str(url_to_index[absu])
            elif a["href"].startswith(("http://", "https://")):
                a["target"] = "_blank"
                a["rel"] = "noopener noreferrer"

        scr = soup.new_tag("script")
        scr.string = MIRROR_PAGE_JS
        (soup.body or soup).append(scr)
        return str(soup)

    def _assemble_mirror(self, entries):
        """Bundle the per-page HTML strings into one navigable document."""
        import json
        nav = "".join(
            '<li><a href="#" data-go="{0}">{1}</a></li>'.format(
                i, escape(t or ("Page " + str(i + 1))))
            for i, (t, _b) in enumerate(entries))
        pages_js = "[" + ",".join(
            '{"t":%s,"b":"%s"}' % (json.dumps(t or ("Page " + str(i + 1))), b)
            for i, (t, b) in enumerate(entries)) + "]"

        # Single-pass fill so page titles/content can never collide with a token.
        tokens = {"__TITLE__": escape(self.site_name), "__NAV__": nav,
                  "__PAGES__": pages_js}
        return re.sub("|".join(re.escape(k) for k in tokens),
                      lambda m: tokens[m.group(0)], MIRROR_SHELL)


class BatchRunner:
    """Crawl one or more sites, up to `concurrency` of them at the same time.

    Runs in its own background thread. Each site gets its own Harvester (and its
    own <output>/<domain>/ folder). Messages are posted onto the shared queue as
    3-tuples (kind, tag, payload):

        ("site_start", tag, {"url": ..., "label": ...})   # a site began
        ("log",        tag, message)                       # from that Harvester
        ("status",     tag, {pages, queued, files})        # live counts
        ("site_done",  tag, None)                          # a site finished
        ("batch_done", None, {"done": N, "total": M})      # whole batch finished
    """

    def __init__(self, sites, concurrency, common, log_q, stop_event):
        # sites: list of (tag, url, label)
        self.sites = sites
        self.concurrency = max(1, int(concurrency))
        self.common = common          # shared Harvester kwargs (dict)
        self.log_q = log_q
        self.stop_event = stop_event

    def _run_one(self, tag, url, label, sem):
        try:
            self.log_q.put(("site_start", tag, {"url": url, "label": label}))
            harvester = Harvester(
                start_url=url, tag=tag, site_label=label,
                log_q=self.log_q, stop_event=self.stop_event, **self.common)
            harvester.run()
        except Exception as e:
            self.log_q.put(("log", tag, f"ERROR: {e}"))
        finally:
            self.log_q.put(("site_done", tag, None))
            sem.release()

    def run(self):
        sem = threading.Semaphore(self.concurrency)
        threads = []
        started = 0
        for tag, url, label in self.sites:
            if self.stop_event.is_set():
                break
            sem.acquire()               # wait for a free slot
            if self.stop_event.is_set():
                sem.release()
                break
            t = threading.Thread(
                target=self._run_one, args=(tag, url, label, sem), daemon=True)
            t.start()
            threads.append(t)
            started += 1
        for t in threads:
            t.join()
        self.log_q.put(("batch_done", None,
                        {"done": started, "total": len(self.sites)}))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Site Harvester")
        self.geometry("720x860")
        self.minsize(640, 760)

        self.log_q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None

        # Per-batch aggregation state (across all sites in one run).
        self.total_sites = 0
        self.sites_started = 0
        self.sites_done = 0
        self.site_status = {}   # tag -> {pages, queued, files}
        self.labels = {}        # tag -> short display label

        self.theme_var = tk.StringVar(value=load_theme_choice())
        self._last_dark = None
        self._build_menu()
        self._build_ui()
        self._apply_theme()
        self.after(100, self._drain_log)
        self.after(20000, self._theme_tick)

    def _build_menu(self):
        bar = tk.Menu(self)
        m = tk.Menu(bar, tearoff=0)
        for label, value in (("Match system", "system"), ("Light", "light"),
                             ("Dark", "dark")):
            m.add_radiobutton(label=label, value=value,
                              variable=self.theme_var,
                              command=self._theme_chosen)
        bar.add_cascade(label="Appearance", menu=m)
        self.config(menu=bar)

    def _theme_chosen(self):
        save_theme_choice(self.theme_var.get())
        self._apply_theme()

    def _theme_tick(self):
        """Follow the OS when it switches mode while the app is open."""
        try:
            if (self.theme_var.get() == "system"
                    and system_dark() != self._last_dark):
                self._apply_theme()
        except Exception:
            pass
        self.after(20000, self._theme_tick)

    def _apply_theme(self):
        choice = self.theme_var.get()
        dark = choice == "dark" or (choice == "system" and system_dark())
        self._last_dark = dark
        pal = DARK_PALETTE if dark else LIGHT_PALETTE
        style = ttk.Style()
        try:
            style.theme_use("clam")     # the one theme that recolours fully
        except tk.TclError:
            pass
        style.configure(".", background=pal["bg"], foreground=pal["fg"],
                        bordercolor=pal["border"], focuscolor=pal["accent"],
                        lightcolor=pal["bg"], darkcolor=pal["bg"],
                        troughcolor=pal["btn"])
        for cls in ("TFrame", "TLabel", "TCheckbutton"):
            style.configure(cls, background=pal["bg"], foreground=pal["fg"])
        style.map("TCheckbutton", background=[("active", pal["bg"])],
                  foreground=[("disabled", pal["hint"])])
        style.configure("TButton", background=pal["btn"],
                        foreground=pal["fg"], bordercolor=pal["border"],
                        lightcolor=pal["btn"], darkcolor=pal["btn"],
                        padding=(12, 5))
        style.map("TButton",
                  background=[("pressed", pal["btn_hi"]),
                              ("active", pal["btn_hi"])],
                  foreground=[("disabled", pal["hint"])])
        for cls in ("TEntry", "TCombobox"):
            style.configure(cls, fieldbackground=pal["field"],
                            foreground=pal["fg"], insertcolor=pal["fg"],
                            background=pal["btn"], arrowcolor=pal["fg"],
                            bordercolor=pal["border"],
                            lightcolor=pal["field"], darkcolor=pal["field"],
                            padding=4)
            style.map(cls, bordercolor=[("focus", pal["accent"])],
                      lightcolor=[("focus", pal["accent"])],
                      darkcolor=[("focus", pal["accent"])],
                      fieldbackground=[("readonly", pal["field"])],
                      foreground=[("readonly", pal["fg"])],
                      selectbackground=[("readonly", pal["field"])],
                      selectforeground=[("readonly", pal["fg"])])
        style.configure("Vertical.TScrollbar", background=pal["btn"],
                        troughcolor=pal["bg"], bordercolor=pal["bg"],
                        arrowcolor=pal["hint"], lightcolor=pal["btn"],
                        darkcolor=pal["btn"])
        style.configure("Horizontal.TProgressbar", background=pal["sel"],
                        troughcolor=pal["btn"], bordercolor=pal["border"],
                        lightcolor=pal["sel"], darkcolor=pal["sel"])
        self.configure(background=pal["bg"])
        for txt in (self.url_text, self.log_text):
            txt.configure(background=pal["field"], foreground=pal["fg"],
                          insertbackground=pal["fg"],
                          selectbackground=pal["sel"],
                          selectforeground=pal["sel_fg"],
                          relief="flat", highlightthickness=1,
                          highlightbackground=pal["border"],
                          highlightcolor=pal["accent"])

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=8, pady=8)

        ttk.Label(main, text="Website addresses (one per line)").pack(anchor="w", **pad)
        url_frame = ttk.Frame(main)
        url_frame.pack(fill="x", padx=12)
        self.url_text = tk.Text(url_frame, height=4, wrap="none")
        self.url_text.pack(side="left", fill="x", expand=True)
        url_scroll = ttk.Scrollbar(url_frame, command=self.url_text.yview)
        url_scroll.pack(side="right", fill="y")
        self.url_text.config(yscrollcommand=url_scroll.set)
        self.url_text.insert("1.0", "https://")

        # Depth + how many sites at once, side by side.
        row = ttk.Frame(main)
        row.pack(fill="x", padx=12, pady=(6, 0))
        depth_col = ttk.Frame(row)
        depth_col.pack(side="left", fill="x", expand=True)
        ttk.Label(depth_col, text="How deep to crawl").pack(anchor="w")
        self.depth_var = tk.StringVar(value=list(DEPTH_OPTIONS.keys())[2])
        ttk.Combobox(depth_col, textvariable=self.depth_var,
                     values=list(DEPTH_OPTIONS.keys()), state="readonly").pack(fill="x")
        conc_col = ttk.Frame(row)
        conc_col.pack(side="left", padx=(12, 0))
        ttk.Label(conc_col, text="Sites at the same time").pack(anchor="w")
        self.concurrency_var = tk.StringVar(value=CONCURRENCY_OPTIONS[0])
        ttk.Combobox(conc_col, textvariable=self.concurrency_var,
                     values=CONCURRENCY_OPTIONS, state="readonly", width=6).pack(anchor="w")

        ttk.Label(main, text="Which links to follow").pack(anchor="w", **pad)
        self.scope_var = tk.StringVar(value=list(SCOPE_OPTIONS.keys())[0])
        ttk.Combobox(main, textvariable=self.scope_var,
                     values=list(SCOPE_OPTIONS.keys()), state="readonly").pack(fill="x", padx=12)

        ttk.Label(main, text="File types to collect").pack(anchor="w", **pad)
        types_frame = ttk.Frame(main)
        types_frame.pack(fill="x", padx=12)
        self.cat_vars = {}
        for i, cat in enumerate(CATEGORIES.keys()):
            v = tk.BooleanVar(value=True)
            self.cat_vars[cat] = v
            ttk.Checkbutton(types_frame, text=cat, variable=v).grid(
                row=0, column=i, sticky="w", padx=(0, 14))
        self.other_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(main,
                        text="Also grab any other file type (sorted into Other/ by extension)",
                        variable=self.other_var).pack(anchor="w", padx=12, pady=(4, 0))
        self.pdf_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(main,
                        text="Also save one clickable PDF of every page visited",
                        variable=self.pdf_var).pack(anchor="w", padx=12, pady=(2, 0))
        self.mirror_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(main,
                        text="Also save a single-file offline mirror (one .html you can browse offline)",
                        variable=self.mirror_var).pack(anchor="w", padx=12, pady=(2, 0))
        self.ytdlp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(main,
                        text="Use yt-dlp for embedded & streaming videos at best quality (needs ffmpeg)",
                        variable=self.ytdlp_var).pack(anchor="w", padx=12, pady=(2, 0))
        self.render_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(main,
                        text="Render JavaScript first — slower, but catches sites that load content dynamically",
                        variable=self.render_var).pack(anchor="w", padx=12, pady=(2, 0))

        ttk.Label(main, text="Save to folder").pack(anchor="w", **pad)
        out_frame = ttk.Frame(main)
        out_frame.pack(fill="x", padx=12)
        default_out = os.path.join(os.path.expanduser("~"), "Downloads", "SiteHarvester")
        self.out_var = tk.StringVar(value=default_out)
        ttk.Entry(out_frame, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        ttk.Button(out_frame, text="Choose…", command=self._choose_folder).pack(side="left", padx=(8, 0))

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", padx=12, pady=10)
        self.start_btn = ttk.Button(btn_frame, text="Start", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        self.open_btn = ttk.Button(btn_frame, text="Open folder", command=self._open_folder)
        self.open_btn.pack(side="left")

        # Progress tracker: animated bar + live counts.
        prog_frame = ttk.Frame(main)
        prog_frame.pack(fill="x", padx=12, pady=(0, 4))
        self.progress = ttk.Progressbar(prog_frame, mode="indeterminate")
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(main, textvariable=self.status_var).pack(anchor="w", padx=12)

        ttk.Label(main, text="Activity log").pack(anchor="w", padx=12)
        log_frame = ttk.Frame(main)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scroll.set)

    def _choose_folder(self):
        d = filedialog.askdirectory(initialdir=os.path.expanduser("~"))
        if d:
            self.out_var.set(d)

    def _open_folder(self):
        path = self.out_var.get()
        if not os.path.isdir(path):
            messagebox.showinfo("Site Harvester", "That folder doesn't exist yet.")
            return
        # os.system with an interpolated path breaks on quotes and is shell-
        # dependent; each platform has a proper call for this.
        try:
            if IS_WINDOWS:
                os.startfile(path)                                  # noqa: S606
            elif IS_MAC:
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as e:
            messagebox.showinfo(
                "Site Harvester",
                f"Could not open the folder automatically ({e}).\n\n{path}")

    def _append_log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _parse_urls(self):
        """Read the URL box: one address per line, blanks/placeholder skipped,
        scheme added if missing, duplicates removed (order preserved)."""
        raw = self.url_text.get("1.0", "end")
        urls, seen = [], set()
        for line in raw.splitlines():
            u = line.strip()
            if not u or u.lower() in ("https://", "http://"):
                continue
            if not urlparse(u).scheme:
                u = "https://" + u
            if u not in seen:
                seen.add(u)
                urls.append(u)
        return urls

    def _refresh_status(self):
        pages = sum(s.get("pages", 0) for s in self.site_status.values())
        queued = sum(s.get("queued", 0) for s in self.site_status.values())
        files = sum(s.get("files", 0) for s in self.site_status.values())
        running = max(0, self.sites_started - self.sites_done)
        self.status_var.set(
            f"Sites: {self.sites_done}/{self.total_sites} done · {running} running"
            f"     |     Pages: {pages}  ·  Queue: {queued}  ·  Files: {files}"
        )

    def _drain_log(self):
        try:
            while True:
                kind, tag, payload = self.log_q.get_nowait()
                if kind == "log":
                    label = self.labels.get(tag)
                    # Prefix with the site when several are running, so the
                    # interleaved lines stay readable.
                    if self.total_sites > 1 and label:
                        self._append_log(f"[{label}] {payload}")
                    else:
                        self._append_log(payload)
                elif kind == "status":
                    self.site_status[tag] = payload
                    self._refresh_status()
                elif kind == "site_start":
                    self.labels[tag] = payload["label"]
                    self.sites_started += 1
                    self._append_log("")
                    self._append_log(
                        f"===== [{self.sites_started}/{self.total_sites}] "
                        f"Starting: {payload['url']} =====")
                    self._refresh_status()
                elif kind == "site_done":
                    self.sites_done += 1
                    self._refresh_status()
                elif kind == "batch_done":
                    self._finished(payload)
        except queue.Empty:
            pass
        self.after(100, self._drain_log)

    def _start(self):
        urls = self._parse_urls()
        if not urls:
            messagebox.showwarning("Site Harvester",
                                   "Please enter at least one website address.")
            return

        wanted = {c for c, v in self.cat_vars.items() if v.get()}
        if not wanted and not self.other_var.get():
            messagebox.showwarning("Site Harvester", "Please select at least one file type.")
            return

        out_dir = self.out_var.get().strip()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Site Harvester", f"Can't create output folder:\n{e}")
            return

        # Reset log + per-batch aggregation state.
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        self.total_sites = len(urls)
        self.sites_started = 0
        self.sites_done = 0
        self.site_status = {}
        self.labels = {}

        try:
            concurrency = int(self.concurrency_var.get())
        except (ValueError, AttributeError):
            concurrency = 1
        concurrency = max(1, min(concurrency, len(urls)))

        max_depth = DEPTH_OPTIONS[self.depth_var.get()]
        scope = SCOPE_OPTIONS[self.scope_var.get()]

        if len(urls) > 1:
            self._append_log(f"Queued {len(urls)} site(s); running {concurrency} at a time.")
        if concurrency > 1 and self.render_var.get():
            self._append_log("Note: 'Render JavaScript' with several sites at once is heavier "
                             "and can occasionally be flaky. Lower the count if you hit trouble.")

        sites = []
        for i, u in enumerate(urls):
            label = urlparse(u).netloc or u
            sites.append((f"s{i + 1}", u, label))

        common = dict(
            max_depth=max_depth,
            wanted_categories=wanted,
            grab_other=self.other_var.get(),
            out_dir=out_dir,
            scope=scope,
            make_pdf=self.pdf_var.get(),
            make_mirror=self.mirror_var.get(),
            use_ytdlp=self.ytdlp_var.get(),
            use_render=self.render_var.get(),
        )

        self.stop_event.clear()
        runner = BatchRunner(sites, concurrency, common, self.log_q, self.stop_event)
        self.worker = threading.Thread(target=runner.run, daemon=True)
        self.worker.start()

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("Working…")
        self.progress.start(12)

    def _stop(self):
        self.stop_event.set()
        self._append_log("Stopping… (finishing the current downloads first)")

    def _finished(self, payload=None):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.progress.stop()
        if payload:
            total = payload.get("total", self.total_sites)
            if self.stop_event.is_set():
                self._append_log("=" * 48)
                self._append_log(f"Stopped. {self.sites_done} of {total} site(s) finished.")
                self.status_var.set(f"Stopped — {self.sites_done}/{total} site(s) finished.")
            else:
                self._append_log("=" * 48)
                self._append_log(f"All done — {self.sites_done} of {total} site(s) processed.")
                self.status_var.set(f"Finished — {self.sites_done}/{total} site(s) processed.")


if __name__ == "__main__":
    mimetypes.init()
    App().mainloop()
