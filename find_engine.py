"""
Site Harvester - the engine behind the Find tab.

Where the Harvest tab is given an address and crawls it, Find is given a
description and goes looking: it asks a search engine, visits what comes back,
checks each result against the description, and lists what matched. Nothing is
downloaded until it has been ticked and approved in the window.

    describe  ->  search  ->  check  ->  review  ->  download

This file has no window code in it (that is find_tab.py), so it can be driven
and tested without one. It borrows the Harvest tab's helpers (file-type
tables, safe file names, HTML decoding) from site_harvester, which is handed
in as `sh` rather than imported: when the app is started as
"python site_harvester.py" that module is called __main__, and importing it by
name would load a second copy.

Search sources. With a Brave API key entered in Find > Settings that is the
only one used. Otherwise these are tried in turn until one returns results:
    ddgs       the free, key-less metasearch library (several engines behind
               it). Optional - skipped when it is not installed.
    bing       a plain fetch of Bing's HTML result page.
    brave      a plain fetch of Brave Search's HTML result page.
    browser    the same two pages loaded in the headless Chromium the Harvest
               tab already uses - slower, but it looks like a real browser to
               a site that turns plain fetches away.
    duckduckgo its HTML page, last: it is the quickest to demand a human
               check, and once it has, it keeps asking.
A source that fails twice running is left out for the rest of that search.
"""

import base64
import csv
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Settings (kept under "find" in site_harvester_ui.json, beside the app)
# --------------------------------------------------------------------------- #
DEFAULTS = {
    # where results come from
    "provider": "auto",            # auto | brave
    "brave_key": "",
    "region": "uk-en",
    # reading the description
    "ai": "off",                   # off | anthropic | ollama
    "anthropic_key": "",
    "anthropic_model": "claude-haiku-4-5-20251001",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "llama3.2",
    # politeness
    "delay": 1.0,                  # seconds between two requests to one site
    "workers": 6,                  # sites being checked at the same time
    "max_file_mb": 200,            # a single file bigger than this is skipped
    "max_total_mb": 1000,          # one "Download ticked" stops at this much
    # what the tab remembers between runs
    "types": ["Documents"],
    "pages": False,
    "inside": True,
    "deeper": True,
    "extra_exts": "",
    "max_check": "40",
    "min_score": "0",
    "auto": "Off",
}

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
BING_URL = "https://www.bing.com/search"
BRAVE_URL = "https://search.brave.com/search"
DDG_URL = "https://html.duckduckgo.com/html/"

PAGE_READ_LIMIT = 1_500_000       # bytes of a page that are read and searched
PER_PAGE_FILE_CAP = 25            # most files listed from any one page
DEEPER_PER_PAGE = 3               # most links followed onward from one page
AI_JUDGE_CAP = 80                 # most results sent to the AI judge
AI_BATCH = 20


class SearchError(Exception):
    """A search source failed in a way worth telling the user about."""


class RateLimited(SearchError):
    """The search source said slow down (or asked for a human check)."""


# --------------------------------------------------------------------------- #
# Reading the description
# --------------------------------------------------------------------------- #
STOPWORDS = set("""
a an and any are as at be but by can for from get give had has have how i if
in into is it its me my need of on or our out please so some than that the
their them then there these they this to up us want was we were what when
where which who will with would you your find finds finding looking look
search searching download downloads downloadable file files copy copies free
online about all also
""".split())

# Words that name a file type rather than describe the content. They are
# taken out of the scoring words (a PDF rarely says "pdf" in its title) and
# turned into extensions to look for instead.
TYPE_WORDS = {
    "pdf": ["pdf"], "pdfs": ["pdf"],
    "docx": ["docx"], "doc": ["doc", "docx"], "word": ["doc", "docx"],
    "xlsx": ["xlsx"], "xls": ["xls", "xlsx"], "excel": ["xls", "xlsx"],
    "spreadsheet": ["xlsx", "xls", "csv"], "spreadsheets": ["xlsx", "xls", "csv"],
    "csv": ["csv"], "pptx": ["pptx"], "ppt": ["ppt", "pptx"],
    "powerpoint": ["ppt", "pptx"], "epub": ["epub"], "ebook": ["epub", "pdf"],
    "ebooks": ["epub", "pdf"], "zip": ["zip"], "rar": ["rar"], "7z": ["7z"],
    "iso": ["iso"], "mp3": ["mp3"], "flac": ["flac"], "wav": ["wav"],
    "mp4": ["mp4"], "mkv": ["mkv"], "jpg": ["jpg", "jpeg"],
    "jpeg": ["jpg", "jpeg"], "png": ["png"], "gif": ["gif"], "svg": ["svg"],
    "txt": ["txt"],
}

# Which extensions get their own "filetype:" query, most useful first.
QUERY_EXTS = {
    "Documents": ["pdf", "docx", "xlsx", "pptx", "epub"],
    "Archives": ["zip"],
    "Audio": ["mp3"],
    "Videos": ["mp4"],
    "Images": [],
}

# A link that says it leads to the thing itself rather than to more talk.
DEEPER_HINT_RE = re.compile(
    r"\b(download|downloads|pdf|epub|full text|read online|read the|view|"
    r"open|get it|document|file|files|attachment|brochure|manual)\b", re.I)
DEEPER_PATH_RE = re.compile(
    r"/(download|downloads|files?|pdfs?|docs?|documents?|details|view|"
    r"attachments?|media|resources|library)(/|$)", re.I)

# Image file names that are page furniture, not content.
JUNK_IMAGE_RE = re.compile(
    r"(sprite|icon|logo|avatar|pixel|blank|spacer|badge|emoji|favicon|"
    r"button|banner-ad|tracking|loader|spinner|placeholder)", re.I)

_CAMEL_RE = re.compile(
    r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


def norm(text):
    """Lower-case, with every run of punctuation turned into one space, and a
    space at each end so " word" and " word " can be tested with `in`."""
    return " " + re.sub(r"[\W_]+", " ", (text or "").lower()).strip() + " "


def norm_name(text):
    """norm() for file names and addresses: "PoloManual2024" also has to be
    findable as polo / manual / 2024, so the split and unsplit forms are
    both kept."""
    text = unquote(text or "")
    return norm(_CAMEL_RE.sub(" ", text)) + norm(text)


class Spec:
    """A description boiled down to what can be searched for and scored."""

    def __init__(self, description):
        self.raw = " ".join((description or "").split())
        self.phrases = []       # "quoted phrases" - wanted exactly
        self.excluded = []      # -words - must not appear
        self.terms = []         # the words that count
        self.hinted_exts = []   # file types named in the description
        self._parse()

    def _parse(self):
        text = self.raw
        for m in re.finditer(r'(-?)"([^"]{2,80})"', text):
            phrase = norm(m.group(2)).strip()
            if not phrase:
                continue
            if m.group(1):                  # -"a phrase" = must not appear
                self.excluded.append(phrase)
            else:
                self.phrases.append(phrase)
        unquoted = re.sub(r'-?"[^"]*"', " ", text)
        seen = set()
        for tok in unquoted.split():
            neg = tok.startswith("-") and len(tok) > 1
            word = tok.lstrip("-").strip(".,;:!?()[]{}'‘’“”")
            word = re.sub(r"['’]s$", "", word.lower())
            if not word:
                continue
            if neg:
                n = norm(word).strip()
                if n and n not in self.excluded:
                    self.excluded.append(n)
                continue
            if word in TYPE_WORDS:
                for e in TYPE_WORDS[word]:
                    if e not in self.hinted_exts:
                        self.hinted_exts.append(e)
                continue
            if word in STOPWORDS:
                continue
            n = norm(word).strip()
            if not n or (len(n) < 2 and not n.isdigit()):
                continue
            if n not in seen:
                seen.add(n)
                self.terms.append(n)
        # Words inside a quoted phrase count as words too, so a result with
        # the words but not the exact phrase still scores something.
        for ph in self.phrases:
            for w in ph.split():
                if w not in STOPWORDS and w not in seen and len(w) > 1:
                    seen.add(w)
                    self.terms.append(w)

    @property
    def query_text(self):
        """The description as it is sent to a search engine: quotes kept,
        -words kept, everything else as typed."""
        return self.raw

    @property
    def plain_text(self):
        """The description without the -words (for folder names and AI)."""
        return " ".join(t for t in self.raw.split() if not t.startswith("-"))


def term_in(term, hay):
    """Is a (normalised) term in a (normalised, space-padded) text?

    Short terms and numbers must be whole words - "r" must not match every
    word starting with r, and 2024 is not 20245. Longer ones match the start
    of a word, so "manual" finds "manuals". A term made of several pieces
    (r-line -> "r line") also matches run together ("rline")."""
    if not term:
        return False
    if " " in term:
        return (" " + term) in hay or (" " + term.replace(" ", "")) in hay
    if len(term) < 4 or term.isdigit():
        return (" " + term + " ") in hay
    if (" " + term) in hay:
        return True
    if term.endswith("s") and len(term) > 4 and (" " + term[:-1]) in hay:
        return True
    # A long word that the page writes as two: "artbooks" must find
    # "art books" and "art book", "handbook" "hand book".
    if len(term) >= 7 and term.isalpha():
        stem = term[:-1] if term.endswith("s") else term
        for i in range(3, len(stem) - 2):
            if (" " + stem[:i] + " " + stem[i:]) in hay:
                return True
    return False


# How much a word counts depending on where it was found.
FIELD_LABELS = {
    "title": "title", "name": "file name", "link": "link text",
    "snippet": "search snippet", "page": "page text", "site": "site name",
    # for a file found on a page: words that are only on the page around it
    "ptitle": "the title of the page it is on",
    "ptext": "the text of the page it is on",
}


def score_candidate(spec, fields):
    """Score one candidate 0-100 against a Spec.

    fields: {name: (text, weight)} - weight 1.0 for the places that describe
    the thing itself (its title, file name, link text), less for the places
    that only describe its surroundings. Returns (score, reason)."""
    hays = {}
    for name, (text, weight) in fields.items():
        if not text:
            continue
        hays[name] = ((norm_name(text) if name in ("name", "site") else norm(text)),
                      weight)

    for bad in spec.excluded:
        for name in ("title", "name", "link", "snippet", "page"):
            if name in hays and term_in(bad, hays[name][0]):
                return 0, f"contains the excluded word \"{bad}\""

    if not spec.terms:
        return 50, "no describing words to score - matched on file type only"

    total = 0.0
    found, missing, where = [], [], []
    for term in spec.terms:
        best, best_field = 0.0, None
        for name, (hay, weight) in hays.items():
            if weight > best and term_in(term, hay):
                best, best_field = weight, name
        total += best
        if best_field:
            found.append(term)
            if best_field not in where:
                where.append(best_field)
        else:
            missing.append(term)
    score = 100.0 * total / len(spec.terms)

    notes = []
    for ph in spec.phrases:
        hit = any(term_in(ph, hay) for hay, _w in hays.values())
        if hit:
            score = min(100.0, score + 10)
            notes.append(f"exact phrase \"{ph}\"")
        else:
            score *= 0.5
            notes.append(f"exact phrase \"{ph}\" not found")

    reason = f"{len(found)}/{len(spec.terms)} words"
    if missing:
        reason += " (missing: " + ", ".join(missing[:5]) + ")"
    if where:
        reason += " in " + ", ".join(FIELD_LABELS.get(w, w) for w in where)
    if notes:
        reason += " · " + " · ".join(notes)
    return int(round(score)), reason


def parse_sites(text):
    """ "bbc.co.uk, https://www.gov.uk/x" -> ["bbc.co.uk", "gov.uk"] """
    out = []
    for piece in re.split(r"[,\s;]+", text or ""):
        piece = piece.strip().lower()
        if not piece:
            continue
        if "://" in piece:
            piece = urlparse(piece).hostname or ""
        piece = piece.split("/")[0].lstrip(".")
        if piece.startswith("www."):
            piece = piece[4:]
        if piece and "." in piece and piece not in out:
            out.append(piece)
    return out


def parse_exts(text):
    """ ".stl, apk  gcode" -> ["stl", "apk", "gcode"] """
    out = []
    for piece in re.split(r"[,\s;]+", text or ""):
        piece = piece.strip().lower().lstrip("*.")
        if re.fullmatch(r"[a-z0-9_+~-]{1,16}", piece) and piece not in out:
            out.append(piece)
    return out


def host_matches(host, site):
    host = (host or "").lower()
    return host == site or host.endswith("." + site)


def build_queries(spec, categories, extra_exts, include_sites, exclude_sites,
                  want_pages, limit=6, wordings=()):
    """Turn a description into a handful of search-engine queries. `wordings`
    are other ways of saying the same thing (from the AI, when it is on);
    each gets the same treatment as the description itself."""
    base = spec.query_text
    neg = "".join(f" -site:{s}" for s in exclude_sites[:6])

    # A type named outright ("... PDF", or typed into "Other file endings")
    # is the one to ask for. Only without one do the ticked boxes decide,
    # and then just their two commonest types - every extra query is another
    # request to a search engine that is quick to say "slow down".
    exts = list(spec.hinted_exts)
    for e in extra_exts:
        if e not in exts:
            exts.append(e)
    if exts:
        exts = exts[:3]
    else:
        for cat in ("Documents", "Archives", "Audio", "Videos"):
            if cat in categories:
                for e in QUERY_EXTS[cat]:
                    if e not in exts:
                        exts.append(e)
        exts = exts[:2]

    # Built in rounds - every site's first query, then every site's second -
    # so that with several "only these sites" the cap below cannot spend
    # itself on the first one or two and never reach the rest.
    sites = include_sites[:4] or [None]
    variants = [f" filetype:{e}" for e in exts] + [""]
    bases = [base] + [w for w in wordings if w]
    queries = []
    for variant in variants:
        for b in bases:
            for site in sites:
                prefix = f"site:{site} " if site else ""
                queries.append(f"{prefix}{b}{variant}{neg}")
        if variant == variants[0] and exts and not include_sites:
            # Early, so the cap below cannot cut it: a plain "download"
            # search finds the landing pages a filetype: search never does.
            queries.append(f"{base} download{neg}")
    limit = max(limit, len(sites))

    out, seen = [], set()
    for q in queries:
        q = " ".join(q.split())
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:limit]


# --------------------------------------------------------------------------- #
# Search sources
# --------------------------------------------------------------------------- #
def ddgs_version():
    """The installed ddgs version, or None when it is not installed."""
    try:
        import ddgs
        # The package loads lazily: "import ddgs" succeeds even when primp
        # or lxml, which it cannot search without, are missing. Importing
        # the inner module is what proves it is usable.
        import ddgs.ddgs                                   # noqa: F401
        return getattr(ddgs, "__version__", "installed")
    except Exception:
        return None


def _ddgs_backend():
    """Every text engine ddgs has except the encyclopaedias, which "auto"
    puts first and which never hold a file anyone is hunting for."""
    try:
        from ddgs.engines import ENGINES
        keys = [k for k in ENGINES.get("text", {})
                if k not in ("wikipedia", "grokipedia")]
        if keys:
            return ",".join(sorted(keys))
    except Exception:
        pass
    return "auto"


def _clean_hit(url, title, snippet, source):
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        if not urlparse(url).hostname:      # also raises on "http://[::1/x"
            return None
    except ValueError:
        return None
    return {"url": url, "title": " ".join((title or "").split()),
            "snippet": " ".join((snippet or "").split()), "source": source}


def search_ddgs(query, n, region, page=1):
    from ddgs import DDGS
    try:
        from ddgs.exceptions import (DDGSException, RatelimitException,
                                     TimeoutException)
    except Exception:                       # very old or very new layout
        DDGSException = RatelimitException = TimeoutException = Exception
    try:
        rows = DDGS(timeout=12).text(query, region=region, safesearch="moderate",
                                     max_results=n, page=page,
                                     backend=_ddgs_backend())
    except RatelimitException as e:
        raise RateLimited(str(e) or "rate limited")
    except TimeoutException as e:
        raise SearchError(f"timed out ({e})")
    except DDGSException as e:
        msg = str(e)
        if "No results" in msg:
            return []
        # Only the word, never the status digits: the message can quote the
        # request address, and a query with "2024" in it contains "202".
        if "ratelimit" in msg.lower() or "rate limit" in msg.lower():
            raise RateLimited(msg)
        raise SearchError(msg or type(e).__name__)
    hits = []
    for r in rows or []:
        h = _clean_hit(r.get("href") or r.get("url"), r.get("title"),
                       r.get("body"), "ddgs")
        if h:
            hits.append(h)
    return hits


def search_ddgs_images(query, n, region):
    from ddgs import DDGS
    try:
        rows = DDGS(timeout=12).images(query, region=region,
                                       safesearch="moderate", max_results=n)
    except Exception as e:
        if "No results" in str(e):
            return []
        raise SearchError(str(e) or type(e).__name__)
    hits = []
    for r in rows or []:
        h = _clean_hit(r.get("image"), r.get("title"), "", "ddgs images")
        if h:
            page = str(r.get("url") or "")
            h["page"] = page if page.lower().startswith(("http://", "https://")) else ""
            w, hgt = str(r.get("width") or ""), str(r.get("height") or "")
            h["dims"] = f"{w}x{hgt}" if w and hgt else ""
            hits.append(h)
    return hits


def search_brave_api(session, key, query, n, region, page=1):
    country = (region.split("-")[0] or "us").upper()
    if country == "UK":
        country = "GB"
    try:
        r = session.get(
            BRAVE_API_URL,
            params={"q": query, "count": max(1, min(int(n), 20)),
                    "country": country, "offset": max(0, min(page - 1, 9))},
            headers={"Accept": "application/json",
                     "X-Subscription-Token": key}, timeout=20)
    except requests.RequestException as e:
        raise SearchError(f"Brave API: {e}")
    if r.status_code == 429:
        raise RateLimited("Brave API rate limit reached")
    if r.status_code in (401, 403):
        raise SearchError("Brave API refused the key (check it in Settings)")
    if r.status_code == 422:
        raise SearchError("Brave API rejected the query")
    if r.status_code != 200:
        raise SearchError(f"Brave API answered HTTP {r.status_code}")
    try:
        rows = (r.json().get("web") or {}).get("results") or []
    except ValueError:
        raise SearchError("Brave API sent something that is not JSON")
    hits = []
    for row in rows:
        # Brave wraps the matched words in <strong> tags.
        title = re.sub(r"<[^>]+>", "", row.get("title") or "")
        desc = re.sub(r"<[^>]+>", "", row.get("description") or "")
        h = _clean_hit(row.get("url"), title, desc, "brave api")
        if h:
            hits.append(h)
    return hits


def parse_brave_html(html):
    """Results out of a Brave Search HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    hits = []
    for item in soup.select("div[data-type='web']"):
        a = item.find("a", href=re.compile(r"^https?://"))
        if not a:
            continue
        title_el = item.select_one(".title")
        snip_el = (item.select_one(".generic-snippet .content")
                   or item.select_one(".snippet-content")
                   or item.select_one(".snippet-description")
                   or item.select_one(".content"))
        h = _clean_hit(a["href"],
                       title_el.get_text(" ", strip=True) if title_el else "",
                       snip_el.get_text(" ", strip=True) if snip_el else "",
                       "brave")
        if h:
            hits.append(h)
    return hits


def _unwrap_ddg(href):
    """DuckDuckGo's HTML page links through //duckduckgo.com/l/?uddg=<url>."""
    if href.startswith("//"):
        href = "https:" + href
    p = urlparse(href)
    if p.hostname and p.hostname.endswith("duckduckgo.com") and p.path.startswith("/l/"):
        target = parse_qs(p.query).get("uddg", [""])[0]
        return unquote(target) if target else ""
    if p.hostname and p.hostname.endswith("duckduckgo.com") and p.path.startswith("/y.js"):
        return ""                                   # an advert
    return href


def parse_ddg_html(html):
    """Results out of html.duckduckgo.com's page."""
    soup = BeautifulSoup(html, "html.parser")
    hits = []
    for item in soup.select("div.result, div.web-result"):
        a = item.select_one("a.result__a") or item.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        snip = item.select_one(".result__snippet")
        h = _clean_hit(_unwrap_ddg(a["href"]), a.get_text(" ", strip=True),
                       snip.get_text(" ", strip=True) if snip else "",
                       "duckduckgo")
        if h:
            hits.append(h)
    return hits


def unwrap_bing(href):
    """bing.com/ck/a?...&u=a1<base64 url> -> the real address."""
    try:
        p = urlparse(href)
        if not (p.hostname or "").endswith("bing.com") or not p.path.startswith("/ck/"):
            return href
        u = parse_qs(p.query).get("u", [""])[0]
        if len(u) <= 2:
            return href
        b = u[2:]
        return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode()
    except Exception:
        return href


def parse_bing_html(html):
    """Results out of a Bing HTML page (markup checked 6 Oct 2026)."""
    soup = BeautifulSoup(html, "html.parser")
    hits = []
    for item in soup.select("li.b_algo"):
        a = item.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if "bing.com/aclick" in href:               # an advert
            continue
        snip = (item.select_one(".b_caption p")
                or item.select_one("p[class*='b_lineclamp']")
                or item.select_one(".b_caption"))
        h = _clean_hit(unwrap_bing(href), a.get_text(" ", strip=True),
                       snip.get_text(" ", strip=True) if snip else "", "bing")
        if h and "bing.com/ck/" not in h["url"]:
            hits.append(h)
    return hits


_FETCH_HEADERS = {"User-Agent": BROWSER_UA,
                  "Accept": "text/html,application/xhtml+xml",
                  "Accept-Language": "en-GB,en;q=0.9"}


def _bing_params(query, region, page=1):
    country = (region.split("-")[0] or "us").lower()
    lang = (region.split("-") + ["en"])[1] or "en"
    params = {"q": query, "cc": "gb" if country == "uk" else country,
              "setlang": lang}
    if page > 1:
        params["first"] = (page - 1) * 10 + 1
    return params


def _brave_params(query, page=1):
    params = {"q": query, "source": "web"}
    if page > 1:
        params["offset"] = page - 1
    return params


def plus_fixed(url):
    """The same address with every "+" in its path turned into "%20", or None
    when there is none. Search engines hand back file addresses with the
    spaces of the real name written as "+", which is only right in the part
    after a "?"; in the path it is a literal plus sign and the server says
    404. (archive.org file names are the usual victims.)"""
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if "+" not in p.path:
        return None
    return p._replace(path=p.path.replace("+", "%20")).geturl()


def looks_blocked(html):
    """A page that is a bot check rather than results."""
    head = (html or "")[:30000].lower()
    return any(mark in head for mark in (
        "anomaly", "captcha", "unusual traffic", "are you a robot",
        "verify you are human", "confirm this search was made by a human"))


def search_bing_html(session, query, n, region, page=1):
    try:
        r = session.get(BING_URL, params=_bing_params(query, region, page),
                        headers=_FETCH_HEADERS, timeout=20)
    except requests.RequestException as e:
        raise SearchError(f"Bing could not be reached ({type(e).__name__})")
    if r.status_code in (403, 429):
        raise RateLimited(f"Bing said no (HTTP {r.status_code})")
    if r.status_code != 200:
        raise SearchError(f"Bing answered HTTP {r.status_code}")
    hits = parse_bing_html(r.text)
    if not hits and looks_blocked(r.text):
        raise RateLimited("Bing asked for a human check")
    return hits[:n]


def search_brave_html(session, query, n, region, page=1):
    try:
        r = session.get(BRAVE_URL, params=_brave_params(query, page),
                        headers=_FETCH_HEADERS, timeout=20)
    except requests.RequestException as e:
        raise SearchError(f"Brave Search could not be reached ({type(e).__name__})")
    if r.status_code in (403, 429) or "captcha" in r.url.lower():
        raise RateLimited(f"Brave Search said no (HTTP {r.status_code})")
    if r.status_code != 200:
        raise SearchError(f"Brave Search answered HTTP {r.status_code}")
    hits = parse_brave_html(r.text)
    if not hits and looks_blocked(r.text):
        raise RateLimited("Brave Search asked for a human check")
    return hits[:n]


def search_ddg_html(session, query, n, region, page=1):
    data = {"q": query, "b": "", "l": region}
    if page > 1:
        data["s"] = str(10 + (page - 2) * 15)
    try:
        r = session.post(DDG_URL, data=data,
                         headers=_FETCH_HEADERS, timeout=20)
    except requests.RequestException as e:
        raise SearchError(f"DuckDuckGo could not be reached ({type(e).__name__})")
    if r.status_code == 202 or (r.status_code == 200 and looks_blocked(r.text)
                                and not parse_ddg_html(r.text)):
        raise RateLimited("DuckDuckGo asked for a human check")
    if r.status_code in (403, 429):
        raise RateLimited(f"DuckDuckGo said no (HTTP {r.status_code})")
    if r.status_code != 200:
        raise SearchError(f"DuckDuckGo answered HTTP {r.status_code}")
    return parse_ddg_html(r.text)[:n]


# --------------------------------------------------------------------------- #
# Optional AI help: writing the queries and judging the results
# --------------------------------------------------------------------------- #
def _json_from(text, opener="[", closer="]"):
    """Pull the first JSON array (or object) out of a model's reply, which
    may have prose or a code fence around it."""
    if not text:
        raise ValueError("empty reply")
    start = text.find(opener)
    end = text.rfind(closer)
    if start < 0 or end <= start:
        raise ValueError("no JSON in the reply")
    return json.loads(text[start:end + 1])


# --- Ollama: the free, local way to get AI help ------------------------------ #
# build-app.command / build-exe.bat install Ollama and download the model, so
# a freshly built app has both. The app then looks after the rest itself: it
# starts Ollama when it is not running, and fetches the model if it has gone
# missing (or was never downloaded, on a computer the app was copied to).
_ollama_proc = None            # an "ollama serve" this app started, if any


def find_ollama():
    """Path of the ollama program, or None. An app opened from Finder or a
    shortcut starts with a bare PATH, so the usual homes are checked too."""
    exe = "ollama.exe" if sys.platform.startswith("win") else "ollama"
    found = shutil.which(exe) or shutil.which("ollama")
    if found:
        return found
    if sys.platform.startswith("win"):
        candidates = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", exe),
            os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", exe),
        ]
    else:
        candidates = ["/opt/homebrew/bin/ollama", "/usr/local/bin/ollama",
                      "/Applications/Ollama.app/Contents/Resources/ollama",
                      os.path.expanduser("~/Applications/Ollama.app/Contents/Resources/ollama"),
                      "/usr/bin/ollama"]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def ollama_running(base, timeout=2.5):
    try:
        return requests.get(base + "/api/version", timeout=timeout).status_code == 200
    except requests.RequestException:
        return False


def _stop_ollama():
    global _ollama_proc
    proc, _ollama_proc = _ollama_proc, None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def start_ollama(base, stop=None, wait=30):
    """Make sure an Ollama server is answering at `base`, starting one if it
    is on this computer and not running. Raises RuntimeError with a sentence
    fit for the log when that cannot be done."""
    global _ollama_proc
    if ollama_running(base):
        return
    host = (urlparse(base).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError(f"Ollama at {base} is not answering")
    exe = find_ollama()
    if not exe:
        raise RuntimeError(
            "Ollama is not installed on this computer. Run the build script "
            "(build-app.command / build-exe.bat), which installs it, or get "
            "it from https://ollama.com")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x08000000        # CREATE_NO_WINDOW
    try:
        _ollama_proc = subprocess.Popen([exe, "serve"], **kwargs)
    except OSError as e:
        raise RuntimeError(f"Ollama could not be started ({e})")
    import atexit
    atexit.register(_stop_ollama)       # only ever stops the one started here
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if ollama_running(base, timeout=1.5):
            return
        if _ollama_proc.poll() is not None:
            break
        if stop is not None and stop.wait(0.5):
            raise RuntimeError("stopped")
        elif stop is None:
            time.sleep(0.5)
    raise RuntimeError("Ollama was started but did not answer")


def ollama_has_model(base, model):
    try:
        r = requests.get(base + "/api/tags", timeout=10)
        names = [m.get("name", "") for m in (r.json().get("models") or [])]
    except (requests.RequestException, ValueError):
        return False
    want = model if ":" in model else model + ":latest"
    return any(n == model or n == want for n in names)


def ollama_pull(base, model, log=None, status=None, stop=None):
    """Download a model, reporting progress every ten per cent."""
    try:
        r = requests.post(base + "/api/pull", json={"model": model, "stream": True},
                          stream=True, timeout=(10, 600))
    except requests.RequestException as e:
        raise RuntimeError(f"the model download could not start ({type(e).__name__})")
    if r.status_code != 200:
        raise RuntimeError(f"Ollama refused the model download (HTTP {r.status_code})")
    last = -1
    try:
        for line in r.iter_lines():
            if stop is not None and stop.is_set():
                raise RuntimeError("stopped")
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("error"):
                raise RuntimeError(f"model download failed: {row['error']}")
            total, done = row.get("total") or 0, row.get("completed") or 0
            if total > 50_000_000:              # the big layer, not the manifests
                pct = int(done * 100 / total)
                if status:
                    status(f"Downloading the AI model {model}: {pct}% of "
                           f"{human_size(total)}")
                if pct // 10 > last:
                    last = pct // 10
                    if log:
                        log(f"   AI model {model}: {pct}% of {human_size(total)}")
    finally:
        r.close()


class AIClient:
    """Talks to Anthropic's API or a local Ollama. Off unless configured."""

    def __init__(self, settings):
        self.mode = settings.get("ai", "off")
        self.s = settings
        self.session = requests.Session()

    @property
    def ollama_base(self):
        return (self.s.get("ollama_url") or "http://localhost:11434").rstrip("/")

    def prepare(self, log=None, status=None, stop=None, allow_pull=True):
        """Get the AI ready before it is first asked anything. Nothing to do
        for Anthropic. For Ollama: start it if it is not running, and
        download the model if it is not there. Raises RuntimeError with the
        reason when the AI cannot be made ready."""
        if self.mode != "ollama":
            return
        base, model = self.ollama_base, (self.s.get("ollama_model") or "").strip()
        if not model:
            raise RuntimeError("no Ollama model is named in Settings")
        if not ollama_running(base):
            if log:
                log("Starting Ollama…")
            if status:
                status("Starting Ollama…")
            start_ollama(base, stop)
        if not ollama_has_model(base, model):
            if not allow_pull:
                raise RuntimeError(
                    f"Ollama is running but the model \"{model}\" is not "
                    "downloaded yet. It is fetched automatically on the first "
                    "AI search (about 2 GB for the default model).")
            if log:
                log(f"The AI model \"{model}\" is not on this computer yet - "
                    "downloading it once (about 2 GB for the default model)…")
            ollama_pull(base, model, log, status, stop)
            if log:
                log(f"   AI model {model}: ready")

    @property
    def enabled(self):
        if self.mode == "anthropic":
            return bool(self.s.get("anthropic_key", "").strip())
        return self.mode == "ollama"

    @property
    def label(self):
        if self.mode == "anthropic":
            return f"Anthropic ({self.s.get('anthropic_model')})"
        if self.mode == "ollama":
            return f"Ollama ({self.s.get('ollama_model')})"
        return "off"

    def complete(self, system, user, max_tokens=1500):
        if self.mode == "anthropic":
            r = self.session.post(
                ANTHROPIC_URL,
                headers={"x-api-key": self.s.get("anthropic_key", "").strip(),
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.s.get("anthropic_model"),
                      "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=120)
            if r.status_code != 200:
                try:
                    detail = r.json().get("error", {}).get("message", "")
                except ValueError:
                    detail = ""
                raise RuntimeError(f"Anthropic API HTTP {r.status_code} {detail}".strip())
            parts = r.json().get("content") or []
            return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if self.mode == "ollama":
            base = self.ollama_base
            r = self.session.post(
                base + "/api/chat",
                json={"model": self.s.get("ollama_model"), "stream": False,
                      "options": {"temperature": 0},
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=300)
            if r.status_code != 200:
                raise RuntimeError(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
            return (r.json().get("message") or {}).get("content", "")
        raise RuntimeError("AI mode is off")

    def other_wordings(self, description, limit=3):
        """Other ways the same thing is commonly worded or titled.

        This is what a model is good for here. Asked to write whole search
        queries, a small local model reaches for operators it half
        understands (site:google.com, the word "quotes") and wastes the
        searches; asked only for synonyms and title patterns it adds real
        reach, and the engine puts the operators on itself."""
        system = ("You help people find things with a web search engine. "
                  "Reply with a JSON array of strings and nothing else.")
        user = (
            "Someone typed this into a search box:\n\n"
            f"{description}\n\n"
            f"Give up to {limit} other ways the SAME thing is commonly worded "
            "or titled: synonyms, the usual title pattern for that kind of "
            "thing, another spelling. Each must be a short plain phrase of 2 "
            "to 6 words that could appear in the title of what they want. "
            "Keep every name from the original. No search operators, no "
            "site names, no quotation marks, no file types.\n"
            'Example: for "polo owners manual" a good reply is '
            '["polo owner handbook", "polo instruction manual", "polo user guide"]')
        data = _json_from(self.complete(system, user, 300))
        out, seen = [], {norm(description)}
        for q in data:
            if not isinstance(q, str):
                continue
            words = [w.strip("\"'\u201c\u201d.,;") for w in q.split()
                     if ":" not in w and not w.startswith("-")]
            words = [w for w in words if w and w.lower() not in TYPE_WORDS]
            phrase = " ".join(words[:8])
            if len(words) >= 2 and norm(phrase) not in seen:
                seen.add(norm(phrase))
                out.append(phrase)
        return out[:limit]

    def judge(self, description, cands):
        """cands: list of dicts (i, kind, title, url, found_on, text).
        Returns {i: (score, reason)}."""
        system = ("You judge whether web search results match what someone is "
                  "looking for. Reply with a JSON array and nothing else.")
        user = (
            f"Looking for:\n\n{description}\n\n"
            "Score each candidate 0-100 for how likely it is to be exactly "
            "that (100 = certainly it, 50 = related but probably not it, "
            "0 = unrelated). Judge only from what is given.\n"
            'Reply as [{"i": <number>, "score": <0-100>, "why": "<max 12 words>"}].'
            "\n\nCandidates:\n" + json.dumps(cands, ensure_ascii=False))
        data = _json_from(self.complete(system, user, 2000))
        out = {}
        for row in data:
            try:
                out[int(row["i"])] = (max(0, min(100, int(round(float(row["score"]))))),
                                      str(row.get("why", "")).strip()[:120])
            except (KeyError, TypeError, ValueError):
                continue
        return out


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
def human_size(n):
    if n is None or n < 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


class FindEngine:
    """Runs one search (run_search) or one batch of downloads (run_download)
    on a background thread. Everything it has to say goes onto `events` as
    (kind, payload):

        ("log", text)                 a line for the activity log
        ("status", text)              the one-line status
        ("progress", (done, total))   how far the current stage has got
        ("result", item)              a new result row
        ("update", item)              a row that changed (score, status...)
        ("search_done", {...})        the search has finished
        ("download_done", {...})      the downloads have finished
    """

    def __init__(self, sh, settings, events, stop_event):
        self.sh = sh
        self.s = dict(DEFAULTS)
        self.s.update(settings or {})
        self.events = events
        self.stop = stop_event
        self.session = self._make_session(sh.USER_AGENT)
        self.search_session = requests.Session()
        self._host_lock = threading.Lock()
        self._host_next = {}
        self._seen_lock = threading.Lock()
        self._seen_urls = set()
        self._id = 0
        self._bytes_lock = threading.Lock()
        self._bytes_total = 0
        self._csv_lock = threading.Lock()
        self._name_lock = threading.Lock()
        self._src_fails = {}           # search source -> failures in a row
        self._stats_lock = threading.Lock()
        self.stats = {}                # what happened to the results checked
        self._seen_pages = set()       # pages already read (for "one deeper")
        self._deep_left = 0            # extra pages "one deeper" may still read
        self.deeper = False
        self.alt_specs = []
        self._pw = self._pw_browser = self._pw_page = None
        self._pw_failed = ""
        self.retry_wait = 20.0         # seconds to sit out a "slow down"
        self.search_pause = (1.5, 3.0)  # pause between two searches
        self.rate_limited = False

    # ---- plumbing --------------------------------------------------------- #
    def _make_session(self, ua):
        s = requests.Session()
        s.headers.update({
            "User-Agent": ua,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "en-GB,en;q=0.9",
        })
        try:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            retry = Retry(total=1, connect=1, read=1, backoff_factor=0.5,
                          status_forcelist=(502, 503, 504),
                          allowed_methods=("GET", "HEAD"),
                          raise_on_status=False,
                          respect_retry_after_header=False)
            adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
            s.mount("http://", adapter)
            s.mount("https://", adapter)
        except Exception:
            pass
        return s

    def emit(self, kind, payload=None):
        self.events.put((kind, payload))

    def log(self, msg):
        self.emit("log", msg)

    def _sleep(self, seconds):
        """Sleep that a Stop cuts short. True if it ran to the end."""
        return not self.stop.wait(max(0.0, seconds))

    def _wait_host(self, host):
        """Keep two requests to the same site at least `delay` apart, however
        many workers are running."""
        delay = float(self.s.get("delay") or 0)
        if delay <= 0 or not host:
            return
        key = self.sh.base_domain(host)
        with self._host_lock:
            now = time.monotonic()
            at = max(now, self._host_next.get(key, 0.0))
            self._host_next[key] = at + delay
        if at > now:
            self._sleep(at - now)

    def _bump(self, what, by=1):
        with self._stats_lock:
            self.stats[what] = self.stats.get(what, 0) + by

    def _request(self, method, url, **kw):
        self._wait_host(urlparse(url).hostname or "")
        kw.setdefault("stream", True)
        kw.setdefault("timeout", (10, 25))
        kw.setdefault("allow_redirects", True)
        return self.session.request(method, url, **kw)

    def _get(self, url, method="GET", **kw):
        """Fetch an address, with the two repairs that rescue results which
        would otherwise be thrown away:

        404 and a "+" in the path - asked for again with "%20" (see
            plus_fixed); the answer's .url is then the address that works.
        403 - asked for once more looking like an ordinary browser, which is
            all many servers want. A real refusal (a login wall, a lending
            library's restricted file) still comes back 403 and is left be.
        """
        r = self._request(method, url, **kw)
        retry_url, retry_kw, what = None, kw, ""
        if r.status_code == 404:
            retry_url, what = plus_fixed(url), "repaired"
        elif r.status_code == 403:
            headers = dict(kw.get("headers") or {})
            headers["User-Agent"] = BROWSER_UA
            retry_url, retry_kw, what = url, dict(kw, headers=headers), "unblocked"
        if retry_url:
            try:
                r2 = self._request(method, retry_url, **retry_kw)
            except requests.RequestException:
                return r
            if r2.status_code < 400:
                r.close()
                self._bump(what)
                return r2
            r2.close()
        return r

    def _page_first_time(self, url):
        key = self.sh.normalize_url(url)
        with self._seen_lock:
            if key in self._seen_pages:
                return False
            self._seen_pages.add(key)
            return True

    def _next_id(self):
        with self._seen_lock:
            self._id += 1
            return f"r{self._id}"

    def _first_time(self, url):
        key = self.sh.normalize_url(url)
        with self._seen_lock:
            if key in self._seen_urls:
                return False
            self._seen_urls.add(key)
            return True

    # ---- file types -------------------------------------------------------- #
    def _setup_wanted(self, job, spec):
        self.cats = set(job.get("categories") or [])
        self.extra_exts = set(parse_exts(job.get("extra_exts", "")))
        self.extra_exts.update(spec.hinted_exts)

    def wants_ext(self, ext):
        if not ext:
            return False
        if ext in self.extra_exts:
            return True
        return self.sh.EXT_TO_CATEGORY.get(ext) in self.cats

    def category_of(self, ext):
        return self.sh.EXT_TO_CATEGORY.get(ext, "Other")

    def url_ext(self, url):
        """The file extension an address shows, if it is one we know or one
        that was asked for by name."""
        ext = self.sh.find_media_ext(url)
        if ext:
            return ext
        ext = self.sh.get_extension(url)
        return ext if ext in self.extra_exts else ""

    # ---- stage 1 + 2: queries and searching ------------------------------- #
    def _source_list(self):
        """The free search sources, in the order they are tried."""
        region = self.s.get("region") or "uk-en"
        sources = []
        if ddgs_version():
            sources.append(("ddgs", lambda q, n, pg: search_ddgs(q, n, region, pg)))
        ss = self.search_session
        sources += [
            ("bing", lambda q, n, pg: search_bing_html(ss, q, n, region, pg)),
            ("brave", lambda q, n, pg: search_brave_html(ss, q, n, region, pg)),
            ("browser", lambda q, n, pg: self._search_browser(q, n, region, pg)),
            ("duckduckgo", lambda q, n, pg: search_ddg_html(ss, q, n, region, pg)),
        ]
        return sources

    def _search_once(self, query, n, page=1):
        """One page of one query. Returns (hits, name of the source that
        answered).

        With a Brave key that is the only source. Otherwise the free ones
        are tried in turn until one returns something. A source that has
        failed twice running is left out, so one that is blocking does not
        cost a timeout on every query; two sources agreeing that there is
        nothing is taken as nothing. RateLimited is raised only when no
        source produced results AND at least one was turning us away."""
        provider = self.s.get("provider", "auto")
        key = (self.s.get("brave_key") or "").strip()
        if provider == "brave" and key:
            region = self.s.get("region") or "uk-en"
            return (search_brave_api(self.search_session, key, query, n,
                                     region, page), "brave api")

        sources = self._source_list()
        live = [src for src in sources if self._src_fails.get(src[0], 0) < 2]
        if not live:                    # everything struck out - start again
            self._src_fails.clear()
            live = sources
        limited, empties = [], 0
        for name, fn in live:
            if self.stop.is_set():
                break
            try:
                hits = fn(query, n, page)
            except RateLimited as e:
                self._src_fails[name] = self._src_fails.get(name, 0) + 1
                limited.append(str(e))
                self.log(f"   [{name}] {e}")
                continue
            except SearchError as e:
                self._src_fails[name] = self._src_fails.get(name, 0) + 1
                self.log(f"   [{name}] {e}")
                continue
            except Exception as e:              # a library changed shape
                self._src_fails[name] = self._src_fails.get(name, 0) + 1
                self.log(f"   [{name}] failed - {type(e).__name__}: {e}")
                continue
            if hits:
                self._src_fails[name] = 0
                return hits, name
            # An honest "nothing found" is not a failure, but ddgs also
            # answers that way when every engine behind it refused, so one
            # empty answer gets a second opinion before it is believed.
            empties += 1
            if empties >= 2:
                return [], name
        if limited and not empties:
            raise RateLimited("every free search source turned the request away")
        return [], ""

    def _search(self, query, n, page=1):
        """One page of one query, with one patient retry if every source says
        slow down. Returns (hits, source)."""
        try:
            return self._search_once(query, n, page)
        except RateLimited as e:
            wait = self.retry_wait
            self.log(f"   {e}. Waiting {wait:.0f} seconds, then one more try…")
            self.emit("status", "The search sources asked us to slow down - "
                                f"waiting {wait:.0f}s…")
            if not self._sleep(wait):
                return [], ""
            try:
                return self._search_once(query, n, page)
            except RateLimited:
                self.log("   still being turned away - skipping this query.")
                self.rate_limited = True
                return [], ""

    # ---- searching through the headless browser --------------------------- #
    def _browser_page(self):
        """The headless Chromium the Harvest tab uses for PDFs, started on
        first use. Playwright objects belong to the thread that made them;
        every search runs on the one search thread, and close() is called
        from it too."""
        if self._pw_failed:
            raise SearchError(self._pw_failed)
        if self._pw_page is None:
            try:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
                self._pw_browser = self._pw.chromium.launch(headless=True)
                # The headless build announces itself in its user agent,
                # which is the first thing a search engine looks at.
                probe = self._pw_browser.new_page()
                ua = probe.evaluate("navigator.userAgent")
                probe.close()
                ctx = self._pw_browser.new_context(
                    user_agent=ua.replace("HeadlessChrome", "Chrome"),
                    locale="en-GB", viewport={"width": 1280, "height": 900})
                self._pw_page = ctx.new_page()
            except Exception as e:
                self._pw_failed = ("the headless browser could not start "
                                   f"({type(e).__name__})")
                self.close()
                raise SearchError(self._pw_failed)
        return self._pw_page

    def _search_browser(self, query, n, region, page_no=1):
        from urllib.parse import urlencode
        page = self._browser_page()
        pages = (
            ("bing", BING_URL + "?" + urlencode(_bing_params(query, region, page_no)),
             "li.b_algo", parse_bing_html),
            ("brave", BRAVE_URL + "?" + urlencode(_brave_params(query, page_no)),
             "div[data-type='web']", parse_brave_html),
        )
        blocked = []
        for name, url, selector, parser in pages:
            if self.stop.is_set():
                return []
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                try:
                    page.wait_for_selector(selector, timeout=6000)
                except Exception:
                    pass                # no results, or a check page
                html = page.content()
            except Exception as e:
                blocked.append(f"{name}: {type(e).__name__}")
                continue
            hits = parser(html)
            if hits:
                for h in hits:
                    h["source"] = f"{name} via browser"
                return hits[:n]
            if looks_blocked(html):
                blocked.append(f"{name} asked for a human check")
        if blocked:
            raise RateLimited("browser search: " + "; ".join(blocked))
        return []

    def close(self):
        """Shut the headless browser, if it was started. Same thread only."""
        for obj, method in ((self._pw_browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
        self._pw = self._pw_browser = self._pw_page = None

    def run_search(self, job):
        """job: description, categories, extra_exts, include, exclude,
        want_pages, inside, max_check."""
        summary = {"hits": 0, "checked": 0, "results": 0, "error": ""}
        self.rate_limited = False
        try:
            self._run_search(job, summary)
        except Exception as e:
            summary["error"] = f"{type(e).__name__}: {e}"
            self.log(f"ERROR: {summary['error']}")
        finally:
            self.close()
            summary["stopped"] = self.stop.is_set()
            summary["rate_limited"] = self.rate_limited
            self.emit("search_done", summary)

    def _run_search(self, job, summary):
        spec = Spec(job["description"])
        self.spec = spec
        self._setup_wanted(job, spec)
        include = parse_sites(job.get("include", ""))
        exclude = parse_sites(job.get("exclude", ""))
        want_pages = bool(job.get("want_pages"))
        self.want_pages = want_pages
        self.inside = bool(job.get("inside", True))
        self.deeper = bool(job.get("deeper", True)) and self.inside
        max_check = max(1, int(job.get("max_check") or 40))
        self._deep_left = max_check     # "one deeper" may read this many more
        self.stats = {}
        self.alt_specs = []
        ai = AIClient(self.s)

        wanted_words = sorted(self.cats) + sorted(self.extra_exts - set(
            e for c in self.cats for e in self.sh.CATEGORIES.get(c, ())))
        if want_pages:
            wanted_words.append("web pages")
        if not wanted_words:
            raise SearchError("nothing to look for - tick at least one type")

        self.log(f"Looking for: {spec.raw}")
        self.log("Types: " + ", ".join(wanted_words)
                 + (f" | only on: {', '.join(include)}" if include else "")
                 + (f" | never on: {', '.join(exclude)}" if exclude else ""))
        if spec.terms:
            self.log("Scoring on the words: " + ", ".join(spec.terms)
                     + (f" | must not contain: {', '.join(spec.excluded)}"
                        if spec.excluded else ""))

        # --- queries --------------------------------------------------------
        queries = []
        if ai.enabled:
            try:
                ai.prepare(self.log, lambda t: self.emit("status", t), self.stop)
            except Exception as e:
                self.log(f"AI help is not available: {e}")
                self.log("Carrying on with the keyword search.")
                ai.mode = "off"
        wordings = []
        if ai.enabled:
            self.emit("status", "Asking the AI for other ways to word it…")
            try:
                wordings = ai.other_wordings(spec.plain_text)
                if wordings:
                    self.log(f"AI ({ai.label}) - also searching for: "
                             + " | ".join(wordings))
                    self.alt_specs = [Spec(w) for w in wordings]
            except Exception as e:
                self.log(f"AI could not suggest other wordings ({e}) - "
                         "searching for the description as typed.")
        queries = build_queries(spec, self.cats, sorted(self.extra_exts),
                                include, exclude, want_pages,
                                limit=6 + 2 * len(wordings), wordings=wordings)
        key = (self.s.get("brave_key") or "").strip()
        if self.s.get("provider") == "brave" and key:
            source = "Brave Search API"
        else:
            source = ("free search - " + ", ".join(n for n, _f in self._source_list())
                      + (f"  (ddgs {ddgs_version()})" if ddgs_version()
                         else "  (ddgs is not installed)"))
        self.log(f"Search source: {source}")

        # --- search ---------------------------------------------------------
        # A search engine gives about ten results a page, and the first page
        # of one query is rarely enough to fill "Results to check" - so each
        # query is followed onto further pages until it has given its share
        # or runs dry. Twice as many are gathered as will be checked: the
        # surplus is what lets the weak ones be dropped below.
        per_query = max(10, -(-max_check * 2 // len(queries)))
        max_pages = (1 if max_check <= 20 else 2 if max_check <= 40
                     else 3 if max_check <= 80 else 4)
        per_q_hits = []
        for qi, q in enumerate(queries):
            if self.stop.is_set():
                break
            self.emit("status", f"Searching ({qi + 1}/{len(queries)}): {q}")
            self.emit("progress", (qi, len(queries)))
            got, seen_q, vias, pages_read = [], set(), [], 0
            for page in range(1, max_pages + 1):
                if self.stop.is_set():
                    break
                if page > 1:
                    self.emit("status", f"Searching ({qi + 1}/{len(queries)}), "
                                        f"page {page}: {q}")
                    self._sleep(random.uniform(*self.search_pause))
                try:
                    hits, via = self._search(q, 30, page)
                except SearchError as e:
                    self.log(f"   ✗ search failed: {q} — {e}")
                    hits, via = [], ""
                fresh = []
                for h in hits:
                    k = self.sh.normalize_url(h["url"])
                    if k not in seen_q:
                        seen_q.add(k)
                        fresh.append(h)
                pages_read += 1
                if via and fresh and via not in vias:
                    vias.append(via)
                got.extend(fresh)
                if not fresh or len(got) >= per_query:
                    break
            self.log(f"   {len(got):>3} result(s)  ←  {q}"
                     + (f"   [{', '.join(vias)}"
                        + (f", {pages_read} pages" if pages_read > 1 else "")
                        + "]" if got else ""))
            per_q_hits.append(got)
            if qi + 1 < len(queries):
                self._sleep(random.uniform(*self.search_pause))   # pace them

        if "Images" in self.cats and ddgs_version() and not self.stop.is_set():
            self.emit("status", "Searching images…")
            try:
                q = spec.plain_text
                if include:
                    q = f"site:{include[0]} {q}"
                img = search_ddgs_images(q, min(60, max_check * 2),
                                         self.s.get("region") or "uk-en")
                self.log(f"   {len(img):>3} image result(s)")
                per_q_hits.append(img)
            except Exception as e:
                self.log(f"   image search failed — {e}")

        # Interleave the queries (best of each first), drop repeats and
        # anything outside the site filters.
        hits, seen = [], set()
        longest = max((len(h) for h in per_q_hits), default=0)
        for rank in range(longest):
            for qi, qhits in enumerate(per_q_hits):
                if rank >= len(qhits):
                    continue
                h = qhits[rank]
                h["url"] = unwrap_bing(h["url"])
                try:
                    host = urlparse(h["url"]).hostname or ""
                except ValueError:
                    continue                # a malformed address - skip it
                if not host:
                    continue
                if include and not any(host_matches(host, s) for s in include):
                    continue
                if any(host_matches(host, s) for s in exclude):
                    continue
                k = self.sh.normalize_url(h["url"])
                if k in seen:
                    continue
                seen.add(k)
                h["rank"] = rank
                hits.append(h)
        summary["hits"] = len(hits)
        if not hits:
            self.log("No search results came back."
                     + (" Every free search source was turning requests "
                        "away - wait a few minutes, or add a Brave API key "
                        "in Settings."
                        if self.rate_limited else
                        " Try fewer or different words."))
            return
        found = len(hits)
        hits = self._best_first(hits, max_check, bool(include))
        self.log(f"{found} different result(s) in all. Checking the "
                 f"{len(hits)} most promising against the description…"
                 if found > len(hits) else
                 f"Checking {len(hits)} result(s) against the description…")
        for h in hits:
            self._page_first_time(h["url"])

        # --- check ----------------------------------------------------------
        items = []
        done = 0
        self.emit("progress", (0, len(hits)))
        workers = max(1, min(int(self.s.get("workers") or 6), 12))
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="find") as pool:
            futures = [pool.submit(self._check_hit, h) for h in hits]
            for fut in as_completed(futures):
                done += 1
                try:
                    items.extend(fut.result() or [])
                except Exception as e:
                    self.log(f"   ✗ check failed — {type(e).__name__}: {e}")
                self.emit("progress", (done, len(hits)))
                self.emit("status", f"Checked {done}/{len(hits)} · "
                                    f"{len(items)} match(es) so far")
                if self.stop.is_set():
                    for f in futures:
                        f.cancel()
        summary["checked"] = done
        summary["results"] = len(items)
        st = self.stats
        notes = [f"{len(items)} match(es) from {done} result(s) checked"]
        for key, text in (("deep", "page(s) followed one link deeper"),
                          ("repaired", "address(es) repaired"),
                          ("unblocked", "let in on a second, browser-like try"),
                          ("dead", "dead link(s)"),
                          ("refused", "refused by the site"),
                          ("failed", "could not be read")):
            if st.get(key):
                notes.append(f"{st[key]} {text}")
        self.log(" · ".join(notes))

        # --- AI second opinion ---------------------------------------------
        if ai.enabled and items and not self.stop.is_set():
            self._ai_judge(ai, spec, items)

    def _best_first(self, hits, max_check, sites_given):
        """Choose which search results get checked, best first.

        They arrive interleaved (every query's first result, then every
        query's second...). Before any site is visited each is scored on what
        the search engine said about it - title, snippet, address - so the
        ones that already look right are checked first and, when there are
        more than "Results to check", it is the weak ones that are left out.
        One site may not take more than its share, or a run of near-identical
        pages from one forum crowds everything else out."""
        specs = [self.spec] + list(self.alt_specs)
        per_host = max(4, max_check // 5)
        by_host, kept, overflow = {}, [], []
        scored = []
        for order, h in enumerate(hits):
            if h.get("source") == "ddgs images":
                scored.append((100, order, h))
                continue
            try:
                parsed = urlparse(h["url"])
            except ValueError:
                continue
            fields = {"title": (h["title"], 1.0), "name": (parsed.path, 1.0),
                      "snippet": (h["snippet"], 0.7),
                      "site": (parsed.hostname or "", 0.4)}
            best = max(score_candidate(sp, fields)[0] for sp in specs)
            if best == 0 and self.spec.excluded and score_candidate(
                    self.spec, fields)[1].startswith("contains the excluded"):
                continue
            ext = self.url_ext(h["url"])
            if ext and self.wants_ext(ext):
                best += 15              # it IS a file of a wanted type
            scored.append((best, order, h))
        scored.sort(key=lambda t: (-(t[0] // 10), t[1]))    # bands of ten
        for best, order, h in scored:
            host = self.sh.base_domain(urlparse(h["url"]).hostname or "")
            if not sites_given and by_host.get(host, 0) >= per_host:
                # Over its share. A strong one may still fill a spare slot;
                # a weak one is not worth the visit even if there is room.
                if best >= 30:
                    overflow.append(h)
                continue
            by_host[host] = by_host.get(host, 0) + 1
            kept.append(h)
        return (kept + overflow)[:max_check]

    def _ai_judge(self, ai, spec, items):
        ranked = sorted(items, key=lambda it: -it["score"])[:AI_JUDGE_CAP]
        self.log(f"AI ({ai.label}) is judging the top {len(ranked)} result(s)…")
        for start in range(0, len(ranked), AI_BATCH):
            if self.stop.is_set():
                return
            batch = ranked[start:start + AI_BATCH]
            self.emit("status", f"AI judging {start + len(batch)}/{len(ranked)}…")
            cands = []
            for i, it in enumerate(batch):
                cands.append({
                    "i": i, "kind": it["kind"] + (f" .{it['ext']}" if it["ext"] else ""),
                    "title": it["title"][:160], "url": it["url"][:300],
                    "found_on": it.get("source_title", "")[:120],
                    "text": it.get("_excerpt", "")[:400]})
            try:
                verdicts = ai.judge(spec.raw, cands)
            except Exception as e:
                self.log(f"   AI judging failed ({e}) - keeping the keyword scores.")
                return
            for i, it in enumerate(batch):
                if i in verdicts:
                    score, why = verdicts[i]
                    it["kw_score"] = it["score"]
                    it["score"] = score
                    it["reason"] = f"AI: {why or 'no reason given'}  [words: {it['reason']}]"
                    self.emit("update", it)

    # ---- stage 3: checking one search result ------------------------------ #
    def _new_item(self, kind, url, title, ext, score, reason, **extra):
        host = urlparse(url).hostname or ""
        item = {
            "id": self._next_id(), "kind": kind, "url": url,
            "title": title or self.sh.safe_filename(url), "ext": ext or "",
            "category": (self.category_of(ext) if kind == "file" else "Page"),
            "size": None, "dims": "", "score": score, "reason": reason,
            "site": host[4:] if host.startswith("www.") else host,
            "source": "", "source_title": "", "status": "", "dest": "",
        }
        item.update(extra)
        return item

    def _probe(self, url):
        """Ask a server what an address really is without downloading it:
        HEAD first, and a GET that is closed after the headers if HEAD is
        refused. Returns (final_url, headers, status) - status 0 on a
        connection failure. Both go through _get(), so a "+"-for-space
        address comes back repaired."""
        try:
            r = self._get(url, method="HEAD", timeout=(10, 15))
            try:
                if r.status_code < 400 and r.headers.get("Content-Type"):
                    return r.url, r.headers, r.status_code
            finally:
                r.close()
        except requests.RequestException:
            pass
        try:
            r = self._get(url)
            try:
                return r.url, r.headers, r.status_code
            finally:
                r.close()
        except requests.RequestException:
            return url, {}, 0

    @staticmethod
    def _is_html(headers):
        ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return ctype in ("text/html", "application/xhtml+xml")

    @staticmethod
    def _length(headers):
        try:
            n = int(headers.get("Content-Length", ""))
            return n if n >= 0 else None
        except (TypeError, ValueError):
            return None

    def _check_hit(self, hit):
        if self.stop.is_set():
            return []
        url = hit["url"]
        spec = self.spec

        # An image-search result: the address IS the file, already described.
        if hit.get("source") == "ddgs images":
            ext = self.url_ext(url) or "jpg"
            if not self._first_time(url):
                return []
            score, reason = score_candidate(spec, {
                "title": (hit["title"], 1.0),
                "name": (urlparse(url).path, 1.0)})
            item = self._new_item("file", url, hit["title"], ext, score, reason,
                                  source=hit.get("page", ""),
                                  dims=hit.get("dims", ""),
                                  _excerpt=hit["title"])
            item["category"] = "Images"
            self.emit("result", item)
            return [item]

        ext = self.url_ext(url)
        if ext and ext not in self.sh.PAGE_EXTS and self.wants_ext(ext):
            final, headers, status = self._probe(url)
            if status in (404, 410):
                self._bump("dead")
                self.log(f"   ✗ dead link ({status}): {url}")
                return []
            if not self._is_html(headers):
                if not self._first_time(final):
                    return []
                real_ext = self.url_ext(final) or self.sh.ext_from_response(headers) or ext
                name = (self.sh.name_from_content_disposition(headers)
                        or urlparse(final).path)
                score, reason = score_candidate(spec, {
                    "title": (hit["title"], 1.0), "name": (name, 1.0),
                    "snippet": (hit["snippet"], 0.7),
                    "site": (urlparse(final).hostname or "", 0.4)})
                item = self._new_item(
                    "file", final, hit["title"], real_ext, score, reason,
                    size=self._length(headers),
                    _excerpt=hit["snippet"],
                    status=("" if 0 < status < 400 else
                            "could not be reached" if status == 0 else
                            f"server said {status} - may need a browser"))
                self.emit("result", item)
                return [item]
            # The address looked like a file but the server handed back a web
            # page (a viewer, a login wall, a soft 404) - treat it as a page.

        return self._check_page(hit)

    def _check_page(self, hit, depth=0):
        url = hit["url"]
        spec = self.spec
        try:
            r = self._get(url)
        except requests.RequestException as e:
            self._bump("failed")
            self.log(f"   ✗ could not open {url} — {type(e).__name__}")
            return []
        try:
            if r.status_code >= 400:
                if r.status_code in (404, 410):
                    self._bump("dead")
                elif r.status_code in (401, 403, 429, 451):
                    self._bump("refused")
                else:
                    self._bump("failed")
                self.log(f"   ✗ {r.status_code} from {url}")
                return []
            final = r.url
            if not self._is_html(r.headers):
                # No extension in the address, but it is a file (a /download
                # endpoint): list it if it is a type that was asked for.
                ext = self.sh.ext_from_response(r.headers) or self.url_ext(final)
                if not ext or not self.wants_ext(ext) or not self._first_time(final):
                    return []
                name = (self.sh.name_from_content_disposition(r.headers)
                        or urlparse(final).path)
                score, reason = score_candidate(spec, {
                    "title": (hit["title"], 1.0), "name": (name, 1.0),
                    "snippet": (hit["snippet"], 0.7)})
                item = self._new_item("file", final, hit["title"], ext, score,
                                      reason, size=self._length(r.headers),
                                      _excerpt=hit["snippet"])
                self.emit("result", item)
                return [item]
            chunks, n = [], 0
            for chunk in r.iter_content(65536):
                chunks.append(chunk)
                n += len(chunk)
                if n >= PAGE_READ_LIMIT or self.stop.is_set():
                    break
            body = b"".join(chunks)
            headers = r.headers
        except requests.RequestException as e:
            self.log(f"   ✗ could not read {url} — {type(e).__name__}")
            return []
        finally:
            r.close()

        html = self.sh.decode_html(body, headers)
        soup = BeautifulSoup(html, "html.parser")
        title = ""
        if soup.title and soup.title.string:
            title = " ".join(soup.title.string.split())
        title = title or hit["title"]
        links = self._collect_file_links(soup, final) if self.inside else []
        onward = (self._promising_links(soup, final)
                  if self.deeper and depth == 0 else [])
        for tag in soup(["script", "style", "noscript", "svg", "template"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)[:30000]

        out = []
        page_fields = {"title": (title + " " + hit["title"], 1.0),
                       "snippet": (hit["snippet"], 0.7),
                       "page": (text, 0.8),
                       "site": (urlparse(final).hostname or "", 0.4)}
        if self.want_pages and self._first_time(final):
            score, reason = score_candidate(spec, page_fields)
            item = self._new_item("page", final, title, "", score, reason,
                                  size=len(body),
                                  _excerpt=(hit["snippet"] + " " + text)[:400])
            self.emit("result", item)
            out.append(item)

        scored = []
        for furl, label, ext in links:
            # The file's own name and link text count in full. Words that
            # are only on the page around it count for much less: a page
            # about the right thing links to plenty that is not it.
            score, reason = score_candidate(spec, {
                "link": (label, 1.0),
                "name": (urlparse(furl).path, 1.0),
                "ptitle": (title, 0.4),
                "ptext": (text, 0.25)})
            scored.append((score, furl, label, ext, reason))
        scored.sort(key=lambda t: -t[0])
        for score, furl, label, ext, reason in scored[:PER_PAGE_FILE_CAP]:
            if score <= 0 or not self._first_time(furl):
                continue
            item = self._new_item(
                "file", furl, label or self.sh.safe_filename(furl), ext, score,
                reason, source=final, source_title=title,
                _excerpt=f"linked from the page \"{title}\" as \"{label}\"")
            self.emit("result", item)
            out.append(item)

        # One page deeper. A search result is very often the page ABOUT the
        # file - a catalogue entry, a "downloads" index - with the file
        # itself one click further on. The links on this page that look
        # most like that click are followed, once, within a budget.
        for sub in onward:
            if self.stop.is_set():
                break
            with self._seen_lock:
                if self._deep_left <= 0:
                    break
                self._deep_left -= 1
            if not self._page_first_time(sub["url"]):
                continue
            self._bump("deep")
            try:
                out.extend(self._check_page(sub, depth=1))
            except Exception:
                self._bump("failed")
        return out

    def _promising_links(self, soup, base):
        """Up to three links on a page worth following once: ones whose own
        text or address matches what is being looked for, or that say
        "download" / "PDF" / "full text" and at least partly match."""
        try:
            base_tag = soup.find("base", href=True)
            if base_tag:
                base = urljoin(base, base_tag["href"])
        except Exception:
            pass
        specs = [self.spec] + list(self.alt_specs)
        found, seen = [], set()
        for a in soup.find_all("a", href=True):
            raw = a["href"].strip()
            if not raw or raw.startswith(("#", "javascript:", "mailto:", "data:", "tel:")):
                continue
            try:
                full = urljoin(base, raw).split("#")[0]
                parsed = urlparse(full)
            except ValueError:
                continue
            if parsed.scheme not in ("http", "https") or full in seen or full == base:
                continue
            seen.add(full)
            ext = self.url_ext(full) or self.sh.get_extension(full)
            if ext and ext not in self.sh.PAGE_EXTS:
                continue                    # a file - handled as a file
            label = " ".join((a.get_text(" ", strip=True) or a.get("title") or "").split())[:200]
            fields = {"link": (label, 1.0), "name": (parsed.path, 1.0)}
            score = max(score_candidate(sp, fields)[0] for sp in specs)
            hint = bool(DEEPER_HINT_RE.search(label) or DEEPER_PATH_RE.search(parsed.path))
            if score >= 60 or (hint and score >= 30):
                found.append((score + (25 if hint else 0), len(found),
                              {"url": full, "title": label, "snippet": "",
                               "source": "one deeper"}))
        found.sort(key=lambda t: (-t[0], t[1]))
        return [h for _s, _i, h in found[:DEEPER_PER_PAGE]]

    def _collect_file_links(self, soup, base):
        """Every wanted file a page links to: (url, label, ext)."""
        try:
            base_tag = soup.find("base", href=True)
            if base_tag:
                base = urljoin(base, base_tag["href"])
        except Exception:
            pass
        found, seen = [], set()

        def add(raw, label):
            if not raw or raw.startswith(("data:", "javascript:", "mailto:", "#", "blob:")):
                return
            try:
                full = urljoin(base, raw.strip())
            except ValueError:
                return
            if not full.lower().startswith(("http://", "https://")):
                return
            full = full.split("#")[0]
            if full in seen:
                return
            ext = self.url_ext(full)
            if not ext or ext in self.sh.PAGE_EXTS or not self.wants_ext(ext):
                return
            seen.add(full)
            found.append((full, " ".join((label or "").split())[:200], ext))

        for a in soup.find_all("a", href=True):
            label = a.get_text(" ", strip=True) or a.get("title") or a.get("download") or ""
            if not label:
                img = a.find("img", alt=True)
                label = img["alt"] if img else ""
            add(a["href"], label)
        if "Images" in self.cats:
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if JUNK_IMAGE_RE.search(src.rsplit("/", 1)[-1]):
                    continue
                try:
                    if (int(img.get("width") or 999) < 120
                            or int(img.get("height") or 999) < 120):
                        continue
                except ValueError:
                    pass
                add(src, img.get("alt") or img.get("title") or "")
        if self.cats & {"Videos", "Audio"}:
            for tag in soup.find_all(["video", "audio", "source"]):
                add(tag.get("src") or "", tag.get("title") or "")
        return found

    # ---- stage 5: downloading what was approved --------------------------- #
    def run_download(self, items, job_dir, description=""):
        summary = {"saved": 0, "failed": 0, "skipped": 0, "dir": job_dir}
        self.description = description
        try:
            os.makedirs(self.sh._fs(job_dir), exist_ok=True)
            self.log(f"Downloading {len(items)} approved item(s) into: {job_dir}")
            self.emit("progress", (0, len(items)))
            done = 0
            workers = max(1, min(int(self.s.get("workers") or 6), 4))
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="find-dl") as pool:
                futures = {pool.submit(self._download_one, it, job_dir): it
                           for it in items}
                for fut in as_completed(futures):
                    done += 1
                    it = futures[fut]
                    try:
                        outcome = fut.result()
                    except Exception as e:
                        outcome = "failed"
                        it["status"] = f"failed — {type(e).__name__}: {e}"
                        self.emit("update", it)
                    summary[outcome] = summary.get(outcome, 0) + 1
                    self.emit("progress", (done, len(items)))
                    self.emit("status",
                              f"Downloaded {summary['saved']} · failed "
                              f"{summary['failed']} · skipped {summary['skipped']} "
                              f"· {done}/{len(items)}")
        except Exception as e:
            self.log(f"ERROR: {type(e).__name__}: {e}")
        finally:
            summary["stopped"] = self.stop.is_set()
            self.emit("download_done", summary)

    def _filename_for(self, url, ext):
        """The same choice the Harvest tab makes: the address segment that
        carries the extension, cleaned up, with the extension guaranteed."""
        seg = None
        for s in reversed(urlparse(url).path.split("/")):
            if s and (self.sh.find_media_ext(s) or self.sh.get_extension(s) == ext):
                seg = s
                break
        name = unquote(seg) if seg else self.sh.safe_filename(url)
        name = self.sh._clean_name(name)
        if ext and not name.lower().endswith("." + ext):
            name = f"{name[:179 - len(ext)]}.{ext}"
        return name[:180]

    def _unique(self, dest):
        b, x = os.path.splitext(dest)
        i = 1
        while os.path.exists(self.sh._fs(dest)):
            dest = f"{b}_{i}{x}"
            i += 1
        return dest

    def _fail(self, item, why, outcome="failed"):
        item["status"] = why
        self.emit("update", item)
        self.log(f"   ✗ {why}: {item['url']}")
        return outcome

    def _download_one(self, item, job_dir):
        if self.stop.is_set():
            return self._fail(item, "skipped — stopped", "skipped")
        max_file = float(self.s.get("max_file_mb") or 0) * 1024 * 1024
        max_total = float(self.s.get("max_total_mb") or 0) * 1024 * 1024
        with self._bytes_lock:
            if max_total and self._bytes_total >= max_total:
                return self._fail(
                    item, f"skipped — the {self.s.get('max_total_mb')} MB "
                          "limit for one download run was reached", "skipped")
        item["status"] = "downloading…"
        self.emit("update", item)
        url = item["url"]
        headers = {"Referer": item["source"]} if item.get("source") else {}
        try:
            r = self._get(url, headers=headers, timeout=(10, 60))
        except requests.RequestException as e:
            return self._fail(item, f"failed — {type(e).__name__}")
        dest = None
        try:
            if r.status_code >= 400:
                return self._fail(item, f"failed — server said {r.status_code}")
            is_html = self._is_html(r.headers)
            if item["kind"] == "file" and is_html:
                return self._fail(
                    item, "failed — the server sent a web page, not the file "
                          "(open it in the browser instead)")
            if item["kind"] == "page":
                ext, folder = "html", os.path.join(job_dir, "Pages")
                stem = self.sh._clean_name(item["title"] or "page")[:120]
                # 255 BYTES is the limit for a file name, and a title in
                # Japanese or Arabic is three bytes a character.
                while len(stem.encode("utf-8")) > 200:
                    stem = stem[:-1]
                name = (stem or "page") + ".html"
            else:
                ext = (self.sh.find_media_ext(r.url) or item["ext"]
                       or self.sh.ext_from_response(r.headers)
                       or self.sh.get_extension(r.url))
                if not ext:
                    return self._fail(item, "failed — could not tell what type of file it is")
                cat = self.category_of(ext)
                folder = (os.path.join(job_dir, "Other", ext) if cat == "Other"
                          else os.path.join(job_dir, cat))
                name = self.sh.name_from_content_disposition(r.headers)
                if name:
                    if not name.lower().endswith("." + ext):
                        name = f"{name[:179 - len(ext)]}.{ext}"
                    name = name[:180]
                else:
                    name = self._filename_for(r.url, ext)
            size = self._length(r.headers)
            if max_file and size and size > max_file:
                return self._fail(
                    item, f"skipped — {human_size(size)} is over the "
                          f"{self.s.get('max_file_mb')} MB per-file limit", "skipped")
            os.makedirs(self.sh._fs(folder), exist_ok=True)
            # Picking a free name and creating the file is one step under a
            # lock: several downloads run at once, and two files with the
            # same name would otherwise both be handed it.
            with self._name_lock:
                dest = self._unique(os.path.join(folder, name))
                fh = open(self.sh._fs(dest), "xb")
            written = 0
            with fh:
                for chunk in r.iter_content(65536):
                    if self.stop.is_set():
                        raise InterruptedError("stopped")
                    if not chunk:
                        continue
                    fh.write(chunk)
                    written += len(chunk)
                    if max_file and written > max_file:
                        raise OverflowError("over the per-file limit")
            with self._bytes_lock:
                self._bytes_total += written
            item["size"] = written
            item["dest"] = dest
            item["status"] = "saved"
            saved_dest, dest = dest, None       # nothing below may delete it
            self.emit("update", item)
            self.log(f"   ✓ saved [{os.path.basename(folder)}] "
                     f"{os.path.basename(saved_dest)} ({human_size(written)})")
            self._manifest(job_dir, item)
            return "saved"
        except InterruptedError:
            self._discard(dest)
            return self._fail(item, "skipped — stopped, partial file removed", "skipped")
        except OverflowError:
            self._discard(dest)
            return self._fail(
                item, f"skipped — bigger than the {self.s.get('max_file_mb')} MB "
                      "per-file limit", "skipped")
        except Exception as e:
            self._discard(dest)
            return self._fail(item, f"failed — {type(e).__name__}: {e}")
        finally:
            r.close()

    def _discard(self, path):
        if path:
            try:
                os.remove(self.sh._fs(path))
            except OSError:
                pass

    def _manifest(self, job_dir, item):
        """One line per saved file: where it came from and why it matched."""
        path = os.path.join(job_dir, "find-results.csv")
        try:
            with self._csv_lock:
                new = not os.path.exists(self.sh._fs(path))
                with open(self.sh._fs(path), "a", newline="", encoding="utf-8-sig") as fh:
                    w = csv.writer(fh)
                    if new:
                        w.writerow(["saved", "file", "score", "why", "address",
                                    "found on", "looking for"])
                    w.writerow([
                        datetime.datetime.now().isoformat(timespec="seconds"),
                        os.path.relpath(item["dest"], job_dir), item["score"],
                        item["reason"], item["url"], item.get("source", ""),
                        getattr(self, "description", "")])
        except Exception:               # the file is saved; the list is a nicety
            pass
