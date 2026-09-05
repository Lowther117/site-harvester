# Site Harvester

A desktop app for **Windows and macOS** that takes a website address, lets you
pick how much of the site to scan and which file types to grab, then downloads
everything it finds into tidy folders (Images, Videos, Documents, Audio,
Archives — and an "Other" folder that adapts to any file extension it runs into).

> **Before you point it at anything:** this crawler does not consult or obey
> `robots.txt`, and it does not pause between requests. It also sends a
> browser-like User-Agent (with `SiteHarvester/1.0` appended) so that servers
> which reject unknown clients still respond. Use it on sites you own or have
> permission to copy, and read the terms of any site you don't.

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
everything lands inside this folder, so deleting the folder removes all of it.

ffmpeg is genuinely optional — without it everything works except embedded and
best-quality video, so a failure there is a note rather than a stop. On macOS it
comes from Homebrew; if Homebrew isn't installed you get the link and the app
still opens.

On macOS the first double-click may be refused because the file came from the
internet: right-click → Open and confirm once, or run `chmod +x run.command`.

## Building a standalone app

Optional — `run.bat` / `run.command` are already double-clickable. Build only if
you want something you can move to another machine.

- **Windows** — double-click `build.bat`, then find
  `dist\Site Harvester\Site Harvester.exe`
- **macOS** — double-click `Build Site Harvester.command`, then drag
  `dist/Site Harvester.app` into Applications. The first time you open it,
  right-click → Open and confirm.

## What you get

- `site_harvester.py` — the app itself
- `run.bat` / `run.command` — launchers (build an environment, then start it)
- `build.bat` / `build.sh` — optional standalone builders
- `Build Site Harvester.command` — double-clickable wrapper around `build.sh`
- `setup.ps1` — the Windows first-run setup that `run.bat` calls
- `requirements.txt` — the libraries it needs
- `requirements-fallback.txt` — the optional PDF fallback (see below)
- `README.md` — this file

## How to use it

1. Open Site Harvester.
2. Paste the website address (e.g. `https://example.com`).
3. Pick **how deep to crawl** from the dropdown:
   - *Just this page* — only files linked on that one page
   - *1 / 2 / 3 / 5 levels deep* — follows links that many steps out
   - *Entire site* — follows every reachable link (slowest, most complete)
4. Pick **which links to follow**:
   - *Stay on this domain* — keeps to the site you entered, including its `www`
     and other subdomains (recommended)
   - *Follow links to other websites too* — will hop onto external sites it finds
     (can grow large fast — best paired with a shallow depth)
5. Tick the file types you want. Leave "Also grab any other file type" on if you
   want it to catch anything unexpected too.
6. Leave **"Also save one clickable PDF of every page visited"** on if you want a
   single navigable PDF of the whole crawl (see below).
   Leave **"Use yt-dlp for embedded & streaming videos"** on to grab videos that
   aren't plain file links (see "Videos" below).
7. Choose where to save (defaults to `Downloads/SiteHarvester`).
8. Click **Start**. The **progress tracker** shows a running count of pages
   scanned, pages still queued, and files saved, with an animated bar while it
   works. The **activity log** shows each page as it visits it (with its depth)
   and every file as it saves. Use **Stop** any time, and **Open folder** to see
   the results.

Everything is saved into a folder named after the website. Files are sorted like
this:

```
SiteHarvester/
└── example.com/                 (a folder per website you pull)
    ├── example.com-pages.pdf     (the combined clickable PDF)
    ├── Images/
    ├── Videos/
    ├── Documents/
    ├── Audio/
    ├── Archives/
    └── Other/
        ├── json/
        ├── xml/
        └── ...                   (a subfolder per unexpected extension)
```

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

## The combined PDF

If the PDF option is on, Site Harvester saves every page it visited into a single
`<website>-pages.pdf` with:

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

- **Stay on this domain** is the default so it doesn't wander onto other sites.
  Subdomains (`www.`, `blog.`, etc.) count as the same domain. Switch to *Follow
  links to other websites too* only when you really want it to go off-site.
- It only downloads files that are actually linked in the page's HTML. It can't
  reach content that's hidden behind logins or loaded by heavy JavaScript.
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
- **Want to run it by hand?** From inside this folder:
  `pip install -r requirements.txt` then `python site_harvester.py`
  (`python3` on macOS).

---

*Built for my own use, in collaboration with AI (Anthropic's Claude). I described the problems, made the decisions and tested the results; Claude wrote much of the code. Shared as-is — a personal fix, not a product. No support and no warranty.*
