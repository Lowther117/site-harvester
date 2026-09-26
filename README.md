# Site Harvester

A desktop app for **Windows and macOS** (Linux works but isn't a supported
target) that takes one or more website addresses, lets you pick how much of
each site to scan and which file types to grab, then downloads everything it
finds into tidy folders (Images, Videos, Documents, Audio, Archives — and an
"Other" folder that adapts to any file extension it runs into). It can also
save each site as one clickable PDF and/or one self-contained offline HTML
mirror, and it will crawl several sites at the same time.

> **Before you point it at anything:** this crawler does not consult or obey
> `robots.txt`, and it does not pause between requests. It also sends a
> browser-like User-Agent (with `SiteHarvester/1.0` appended) so that servers
> which reject unknown clients still respond. Use it on sites you own or have
> permission to copy, and read the terms of any site you don't.

## Which file do I use?

Double-click the launcher for your system and ignore the rest. Everything else
here is either called by that launcher or is the optional standalone build.

| File | Windows | Mac | What it is |
|---|---|---|---|
| `run.bat` | **double-click this** | – | Launcher: sets everything up on first run, then opens the app |
| `run.command` | – | **double-click this** | Launcher: same job on macOS (right-click → Open the first time) |
| `setup.ps1` | called by `run.bat` | – | The Windows first-run setup (Python environment, Chromium, portable ffmpeg) |
| `ensure_python.ps1` | called by `setup.ps1` and `build-exe.bat` | – | Finds Python on Windows, or installs it when there is none |
| `build-exe.bat` | optional: double-click to build | – | Makes a standalone `dist\Site Harvester\` folder (Chromium and ffmpeg inside) |
| `build-app.command` | – | optional: double-click to build | Makes a standalone `dist/Site Harvester.app` |
| `site_harvester.py` | the app | the app | The whole program; the launchers run it, or `python site_harvester.py` by hand |
| `theme.py` | used by the app | used by the app | The light/dark palettes and widget styling (shared with my other desktop apps) |
| `site_harvester_app.py` | bundled by the builder | bundled by the builder | Start-up wrapper inside the standalone builds (adds the `selftest` and crash log) - not for running by hand |
| `requirements.txt` | used by setup | used by setup | The Python libraries it needs |
| `requirements-fallback.txt` | optional | optional | WeasyPrint, the lower-fidelity PDF fallback (see below) |

## Setting it up

**Windows** — double-click `run.bat`
**macOS** — double-click `run.command`

That is the whole setup. The first run installs everything the app needs and
then opens it; every run after that just opens it. Expect a few minutes the
first time, most of it downloading the headless browser.

On **Windows** even Python takes care of itself: if none is found, the first
run installs it automatically (winget first, python.org directly when winget
is unwell). On **macOS** install it once with `brew install python` (plus
`brew install python-tk` if the window won't open — some builds omit Tk).

### What the first run actually does

So there are no surprises:

| | Windows | macOS |
|---|---|---|
| Python packages | into `.venv-win\` in this folder | into `.venv-mac/` in this folder |
| Headless Chromium | Playwright's own cache | Playwright's own cache |
| ffmpeg (optional) | portable copy downloaded into `tools\` | `brew install ffmpeg` |

Nothing needs administrator permission and nothing goes on your PATH. On Windows
the Python environment and ffmpeg land inside this folder, so deleting the
folder removes them; Chromium sits in Playwright's own cache (see the table).

ffmpeg is genuinely optional — without it everything works except embedded and
best-quality video, so a failure there is a note rather than a stop. On macOS it
comes from Homebrew; if Homebrew isn't installed you get the link and the app
still opens.

On macOS the first double-click may be refused because the file came from the
internet: right-click → Open and confirm once, or run `chmod +x run.command`.

## Building a standalone app

Optional — `run.bat` / `run.command` are already double-clickable, and they stay
the normal way to use this. Build only if you want a single thing you can copy to
a machine that has no Python on it at all.

- **Windows** — double-click `build-exe.bat`, then find `dist\Site Harvester\`
  (copy the whole folder; `Site Harvester.exe` is at the top of it)
- **macOS** — double-click `build-app.command`, then find
  `dist/Site Harvester.app`

Both builders look after themselves. On Windows, Python is found or installed
automatically; on macOS the script picks an interpreter that can actually be
bundled, which means a Homebrew or python.org one — Apple's `/usr/bin/python3`
is refused on purpose, because its Tk 8.5 cannot be copied into an app bundle
and the result is an app that builds cleanly and then never opens. If nothing
suitable is on the Mac, the script installs one itself (`brew install python
python-tk`, installing Homebrew first if the Mac has none — that step asks for
your password once). To use a particular interpreter, set
`HARVESTER_BUILD_PYTHON=/path/to/python3` before running it.

Both builds carry Chromium and ffmpeg inside them, so the result works on a
machine that has never had Python, Playwright or Homebrew on it. That is why
the Windows result is a folder rather than a single exe: a single file would
have to unpack a few hundred megabytes to a temp folder on every launch.

Everything either builder prints is also written to `build-mac-log.txt` /
`build-win-log.txt`, and neither script claims success on faith: when the build
finishes it runs the finished app's self-test, which reports the Python and Tk
versions, which optional parts made it in (yt-dlp, Playwright's Chromium, pypdf,
WeasyPrint, ffmpeg) and whether the folder it saves settings into is writable.
That report is printed and also saved as `harvester-selftest.txt` beside the
built app (`dist/` on macOS, `dist\Site Harvester\` on Windows). If it
says PROBLEMS FOUND, the build is not usable regardless of what PyInstaller
said. You can run it yourself at any time — `"dist/Site Harvester.app/Contents/MacOS/Site Harvester" selftest`
on macOS, `"dist\Site Harvester\Site Harvester.exe" selftest` on Windows.

The built app keeps its settings in the folder it sits in — beside the exe, and
beside (not inside) `Site Harvester.app`. Give
it a folder of its own rather than dropping it loose in Applications. On macOS
the build is ad-hoc signed so it will open at all on Apple silicon; the first
launch still wants a right-click → Open. If the app ever fails while starting
up, before it has a window to complain in, it writes `harvester-crash.log` next
to itself.

## What you get

- `site_harvester.py` — the app itself
- `theme.py` — its light/dark palettes and widget styling
- `run.bat` / `run.command` — launchers (build an environment, then start it)
- `build-exe.bat` / `build-app.command` — optional standalone builders
- `site_harvester_app.py` — the start-up wrapper those builders bundle
- `setup.ps1` — the Windows first-run setup that `run.bat` calls
- `ensure_python.ps1` — finds or installs Python on Windows, for both of those
- `requirements.txt` — the libraries it needs
- `requirements-fallback.txt` — the optional PDF fallback (see below)
- `README.md` — this file

## How to use it

The window is one column of controls, top to bottom:

1. **Website addresses (one per line)** — paste one address per line (e.g.
   `https://example.com`). `https://` is added if you leave it off, blank
   lines and duplicates are ignored. One line is one site; several lines is a
   batch (see "Several sites at once").
2. **How deep to crawl**:
   - *Just this page* — only files linked on that one page
   - *1 / 2 / 3 / 5 levels deep* — follows links that many steps out
     (2 is the default)
   - *Entire site* — follows every reachable link (slowest, most complete)
3. **Sites at the same time** — `1` (the default) crawls the batch one site
   after another; `2`–`8` run that many sites in parallel, each with its own
   crawler and its own output folder. Irrelevant with a single address.
4. **Which links to follow**:
   - *Stay on this domain* — keeps to the site you entered, including its `www`
     and other subdomains (recommended). In this mode files are also only
     taken from the site's own hosts and known media CDNs — see "Where media
     may come from".
   - *Follow links to other websites too* — will hop onto external sites it finds
     (can grow large fast — best paired with a shallow depth)
5. **File types to collect** — tick Images, Videos, Documents, Audio, Archives
   as wanted (all on by default).
6. **Also grab any other file type** — catches anything with an extension the
   five groups don't cover, sorted into `Other/<extension>/`. On by default.
7. **Also save one clickable PDF of every page visited** — one navigable PDF
   per site (see "The combined PDF"). On by default.
8. **Also save a single-file offline mirror** — one `.html` per site that you
   can open and browse with no internet (see "The offline mirror"). Off by
   default; it can get large.
9. **Use yt-dlp for embedded & streaming videos** — grabs videos that aren't
   plain file links (see "Videos"). On by default.
10. **Render JavaScript first** — opens each page in a headless browser before
    reading it (see "How it finds files"). Off by default; slower.
11. **Allow media from any host** — switches off the media host check in
    *Stay on this domain* mode, so off-site images, badges and embeds are
    downloaded too (see "Where media may come from"). Off by default.
12. **Save to folder** — where the per-site folders go. Defaults to a
    `SiteHarvester` folder in your Downloads; **Choose…** picks another for
    this run, and File → *Default save folder…* changes the default for good
    (*Reset to Downloads* undoes it).
13. **Start / Stop / Open folder** — Start begins the run; Stop is a
    two-stage stop (see "Stopping"); Open folder shows the save folder.

Below the buttons an animated bar runs while it works, and the **status line**
shows sites done / running, pages scanned, pages still queued, and files saved
(totals across every site in the batch). The **activity log** shows each page
as it is visited (with its depth) and every file as it saves; with more than
one site running each line is prefixed with the site it belongs to. It keeps
the last 4,000 lines.

The **View** menu has Dark mode (`Ctrl+D`); the choice is remembered. The
other crawl options start from their defaults every time the app opens.

Everything is saved into a folder named after the website — one per address
in the batch. Files are sorted like this:

```
SiteHarvester/
├── example.com/                  (a folder per website you pull)
│   ├── example.com-pages.pdf     (the combined clickable PDF, if ticked)
│   ├── example.com-mirror.html   (the single-file offline mirror, if ticked)
│   ├── Images/
│   ├── Videos/
│   ├── Documents/
│   ├── Audio/
│   ├── Archives/
│   └── Other/
│       ├── json/
│       ├── xml/
│       └── ...                   (a subfolder per unexpected extension)
└── another-site.org/             (the next address in the batch)
```

A file whose name is already taken gets `_1`, `_2`, … added. If the PDF had
to fall back to the basic engine there is also a `_pdf_engine.txt` note in
the site folder saying why.

## Several sites at once

Put one address per line in the box and click Start. Every site gets its own
crawler, its own `<site>/` folder, and its own PDF/mirror; all the other
settings (depth, scope, file types, options) apply to every site in the batch.
**Sites at the same time** says how many crawlers run in parallel: `1` works
through the list in order, `2`–`8` keep that many going at once and start the
next site as soon as one finishes. The status line and the log cover the whole
batch, with each log line prefixed `[site]` so the interleaved output stays
readable.

Running several sites at once with **Render JavaScript first** on means that
many headless browsers at once; it works, but it is heavy and occasionally
flaky, so drop the count if you hit trouble.

## Stopping

Stop is two clicks:

- **First click — graceful.** Nothing new starts: no more pages are visited
  and no new downloads begin, on any site in the batch. Files that are already
  downloading (including a video yt-dlp is in the middle of) run to completion
  and are kept. The button changes to *Stopping… (finishing current files) —
  click again to abort* and the status line shows how many files are still
  finishing. If the PDF or mirror option is on, they are then still built from
  the pages collected so far — stopping a long crawl shouldn't cost you the
  PDF of everything it already read. A Stop that arrives while the PDF or
  mirror is being built finishes the page in hand and then writes what it has.
- **Second click — abort.** Downloads in flight are cut off and their partial
  files deleted, yt-dlp is cancelled, and the PDF/mirror steps are skipped
  (or abandoned, if one was running). This is also what closing the window
  mid-run does.

Either way the log says which happened and Start comes back when the workers
have gone.

## Videos (yt-dlp)

Plain scraping only finds videos that are direct file links (e.g. a `.mp4` URL).
Lots of video on the web isn't like that — it's an embedded YouTube/Vimeo player,
an HTML5 `<video>`, or a stream (`.m3u8` / `.mpd`). With the yt-dlp option on,
Site Harvester hands those to **yt-dlp**, which downloads them at the **best
available quality** into the `Videos` folder:

- embedded players from YouTube, Vimeo, Dailymotion, Wistia, Streamable, Twitch,
  Rumble and others
- HTML5 `<video>` tags and `og:video` pages
- HLS/DASH streams

Highest quality often means downloading the video and audio streams separately and
merging them, which needs **ffmpeg**. The build script installs it for you. If
ffmpeg is missing, videos still download at the best single-file quality and the
log tells you how to add it (`brew install ffmpeg`).

Note: yt-dlp is deliberately set to **not** pull entire channels or playlists —
just the video on the page it's looking at.

In *Stay on this domain* mode an embedded player or a stream is judged by the
**page it sits on**, not by the video's own host — a YouTube embed on your
site is your site's video even though it is served from youtube.com, and a
stream manifest always lives on some video CDN. A plain video file link
(`.mp4` and friends) is treated like any other file and follows the media host
rule below.

## The combined PDF

If the PDF option is on, Site Harvester saves every page it visited on a site
into a single `<website>-pages.pdf` (one per site in the batch) with:

- a **table of contents** on the first page — click any entry to jump to that page
- a **bookmark sidebar** (one bookmark per page) for navigating in Preview or any
  PDF reader
- the site's **own internal links rewritten to jump between sections** inside the
  PDF, so clicking a link on one captured page takes you to that page in the PDF
- **external links kept clickable** so they open in your browser

Each page is printed by the headless Chromium that Playwright downloads on first
run, so pages keep their real layout, CSS and images; the pages are then merged
with pypdf. Both are installed for you, on every platform, so this works out of
the box — nothing else to install.

If Chromium can't start for some reason, there is a lower-fidelity text fallback
using WeasyPrint. It is **not** installed by default, because on Windows it also
needs the Pango system libraries via MSYS2 — a heavy prerequisite for something
that should never run. If you want it anyway:
`pip install -r requirements-fallback.txt` (see that file for the system
libraries each platform needs). Without it, a Chromium failure means the PDF is
skipped and the log says why; downloading files is unaffected either way.

## The offline mirror

If the mirror option is on, Site Harvester also saves every page it visited on
a site into one self-contained `<website>-mirror.html`. Double-click it and it
opens in any browser with no internet and no folder of files beside it:

- a **sidebar** listing every captured page (the ☰ Pages button hides it)
- each page rendered by the headless browser, then its **CSS, images and
  fonts inlined as `data:` URIs** so nothing is fetched from the network
- the site's **own internal links rewired** to switch pages inside the mirror;
  external links open in a new tab as normal
- scripts stripped, and cookie/consent overlays hidden the same way as in the
  PDF

It is off by default because everything is inside the one file: a site with a
lot of imagery makes a big `.html`. Anything over 8 MB per asset is left as
its original link rather than inlined. Like the PDF, it needs the headless
Chromium; if that can't start the mirror is skipped and the log says so. The
mirror is built after the crawl, from the pages the crawl captured.

## Where media may come from

In *Stay on this domain* mode, pages have always been kept to the site you
entered, but the **files** on those pages used to be downloaded from wherever
they lived — which meant tracking pixels, third-party badges, social widgets
and off-site embeds ended up in your `Images/` folder. Now a file is only
downloaded from:

- the site's own hosts — the same domain, its `www`, and any subdomain
  (`cdn.example.com`, `static.example.com`); two-part country suffixes are
  understood, so `shop.example.co.uk` belongs to `example.co.uk` and
  `other.co.uk` does not
- the known **media CDNs** that site builders serve their own images from
  (Wix, Squarespace, Shopify, GoDaddy, Cloudinary, imgix, CloudFront and so on)

Anything else is skipped, with one line in the log per skipped host (not per
file) saying so. Tick **Allow media from any host** to switch the check off and
get the old behaviour. *Follow links to other websites too* is unaffected —
in that mode everything is fair game anyway. Embedded video is judged by the
page it sits on, not the video host (see "Videos").

## How it finds files (works across most site builders)

To handle modern sites (Wix, Squarespace, Shopify, WordPress, GoDaddy, etc.),
Site Harvester looks well beyond plain `<img src>` tags. For every page it also:

- reads lazy-loading attributes (`data-src`, `data-srcset`, `data-original`, …)
- pulls image URLs out of CSS `background-image` rules
- parses content embedded inside iframe `srcdoc` (used by GoDaddy and others)
- harvests any media URL found anywhere in the page's HTML or JavaScript,
  including CDN links that have no obvious file extension
- confirms the real file type from the server's response when the URL is unclear

This recovers images and files that a naive scraper would miss.

**For the tough cases — "Render JavaScript first":** some sites build their image
and video URLs *in the browser* after the page loads (single-page apps,
infinite-scroll galleries). Those URLs aren't in the raw page, so fast mode can't
see them. Tick **"Render JavaScript first"** and Site Harvester opens each page in
a real (invisible) browser, runs its JavaScript, scrolls to trigger lazy content,
and also grabs any image/video/audio it sees loading over the network — catching
media that no static scraper could. It's slower, so leave it off for normal sites
and switch it on when a site comes back nearly empty.

This mode uses a headless browser (Chromium) that the build script downloads for
you. If it's ever unavailable, the app automatically falls back to fast mode
rather than failing, and the log tells you how to install it
(`python3 -m playwright install chromium`).

## Good to know

- **Dark mode is the default.** View → Dark mode (or `Ctrl+D`) switches to the
  light look and back; the choice is remembered in `site_harvester_ui.json`
  next to the app.
- **Stay on this domain** is the default so it doesn't wander onto other sites.
  Subdomains (`www.`, `blog.`, etc.) count as the same domain, for pages and
  for files alike. Switch to *Follow links to other websites too* only when
  you really want it to go off-site.
- It only downloads files that are actually linked in the page's HTML (or, with
  *Render JavaScript first*, that the page loads while it is open). It can't
  reach content that's hidden behind a login.
- The crawl options (depth, scope, file types, the tickboxes) are not
  remembered between runs — only the theme and the default save folder are.
- **It does not obey `robots.txt` and does not rate-limit itself.** There is no
  delay between requests, so a large crawl hits a server hard. Use it on sites
  you own or have permission to download from, be mindful of each site's terms
  of use and copyright, and prefer a shallow depth on anything you don't own.

## Troubleshooting

- **"Python was not found."** On Windows this now installs itself - if the
  automatic install could not finish, the messages in the window say why; a
  freshly installed Python needs the window closed and the launcher run again.
  On macOS: `brew install python`, then run the launcher again.
- **The window doesn't open on macOS.** Some Python builds ship without Tk:
  `brew install python-tk`.
- **App won't open (unidentified developer).** macOS only. Right-click it → Open
  → Open. Normal for self-built apps, and only once.
- **"PDF FALLBACK" in the log.** Chromium didn't start. Delete the `.venv-win` /
  `.venv-mac` folder and run the launcher again so it re-downloads, or run
  `python -m playwright install chromium` inside that environment.
- **Embedded video is missing or low quality.** ffmpeg isn't there. On Windows,
  delete the `tools\ffmpeg` folder and run `run.bat` again so it re-downloads,
  or install it yourself with `winget install -e --id Gyan.FFmpeg`. On macOS:
  `brew install ffmpeg`.
- **A build finished but the app won't open.** Read
  `harvester-selftest.txt` beside the built app — it says which piece is missing. `harvester-crash.log`
  next to the app catches anything that goes wrong later than that, and the full
  build is in `build-mac-log.txt` / `build-win-log.txt`.
- **Want to run it by hand?** From inside this folder:
  `pip install -r requirements.txt` then `python site_harvester.py`
  (`python3` on macOS).

---

*Built for my own use, in collaboration with AI (Anthropic's Claude). I described the problems, made the decisions and tested the results; Claude wrote much of the code. Shared as-is — a personal fix, not a product. No support and no warranty.*

## Licence

MIT No Attribution (MIT-0): do whatever you like with it - no credit needed, no warranty. See `LICENSE`.
