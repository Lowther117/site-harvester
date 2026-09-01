# Site Harvester

A small Mac app that takes a website address, lets you pick how much of the site
to scan and which file types to grab, then downloads everything it finds into
tidy folders (Images, Videos, Documents, Audio, Archives — and an "Other" folder
that adapts to any file extension it runs into).

## What you get

- `Build Site Harvester.command` — double-click this to build the app
- `site_harvester.py` — the app itself
- `build.sh` — the builder that `Build Site Harvester.command` runs
- `requirements.txt` — the libraries it needs
- `README.md` — this file

## How to build the app (the easy way — just double-click)

1. **Double-click `Build Site Harvester.command`.** A Terminal window opens,
   installs everything, and builds the app. When it finishes it opens a `dist`
   folder containing **Site Harvester.app**.
   - The first time, macOS may say it's from an unidentified developer. If so,
     **right-click the file → Open → Open**. You only need to do this once.
2. Drag **Site Harvester.app** into your **Applications** folder.
3. The first time you open the app, **right-click it → Open** and confirm. macOS
   asks this once for any app you built yourself; after that it opens normally.

Rebuild any time after changes by double-clicking `Build Site Harvester.command`
again.

### Prefer the Terminal instead?

Run `bash ` (with a space) followed by the path to `build.sh`, e.g.
`bash ~/Downloads/SiteHarvester/build.sh`.

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

This needs one extra system library, `pango`. The build script installs it for you
via Homebrew. If it's missing, downloading still works — only the PDF is skipped,
and the log tells you how to add it (`brew install pango`).

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
- Please use it on sites you own or have permission to download from, and be
  mindful of each site's terms of use and copyright.

## Troubleshooting

- **"Python 3 is not installed."** Install it from
  https://www.python.org/downloads/macos/ and run `build.sh` again.
- **App won't open (unidentified developer).** Right-click it → Open → Open. This
  is normal for self-built apps and only happens once.
- **Want to test it without building?** In Terminal, from inside this folder:
  `pip3 install -r requirements.txt` then `python3 site_harvester.py`.

---

*Built for my own use, in collaboration with AI (Anthropic's Claude). I described the problems, made the decisions and tested the results; Claude wrote much of the code. Shared as-is — a personal fix, not a product. No support and no warranty.*
