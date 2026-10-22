"""Performer and channel directory behind the search-box dropdowns.

Built from the lists the sites publish, merged by name:

- Performers: Pornhub's webmasters `stars_detailed` (every performer it knows,
  one JSON download; RedTube / YouPorn / Tube8 share that catalogue) plus the
  most popular pages of xHamster's and XVideos' performer indexes (their full
  indexes are tens of thousands of mostly amateur profiles).
- Channels: every page of Pornhub's channel list plus the most popular pages
  of xHamster's.

The directory is rebuilt once a day in the background and kept in a JSON file
in the temp dir, so a restart serves the previous copy straight away. Only
names, counts, profile links and the sites' own image URLs are stored.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .sources.base import parse_count, session, unescape

log = logging.getLogger("aggregator.directory")

REFRESH_EVERY = 24 * 3600
CACHE_FILE = os.path.join(os.environ.get("DIRECTORY_DIR") or tempfile.gettempdir(), "aggregator-directory-v1.json")
XH_PAGES = 20   # 60 per page
XV_PAGES = 15   # 80 per page
CONCURRENCY = 4

# Which site's picture to show when several list the same name: real portraits
# first, XVideos' video stills next, Pornhub's grey silhouette last.
THUMB_PREF = {"xhamster": 0, "pornhub": 1, "xvideos": 2, "placeholder": 3}

# For the admin page: what the last build did; rebuild_now() starts one early.
status = {"building": False, "last_build": 0.0, "last_errors": [], "seconds": 0.0}
_wake: asyncio.Event | None = None

PH = "https://www.pornhub.com"
XH = "https://xhamster.com"
XV = "https://www.xvideos.com"


def norm(name: str) -> str:
    """'Riley Reid', 'riley-reid' and 'Rílëy Reid' are the same entry."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))


@dataclass
class Entry:
    name: str
    thumb: str = ""
    videos: int = 0
    sites: dict[str, str] = field(default_factory=dict)  # source -> profile URL
    rank: float = 0.0  # sum over sites of 0..1 from each site's popularity order (1 = top)
    thumb_rank: int = 99  # lower = better picture (see THUMB_PREF)

    @property
    def score(self) -> float:
        # Popular on several sites beats a huge video count (aggregator "channels"
        # like FapHouse would otherwise top the list).
        return 2 * self.rank + 0.5 * math.log10(1 + self.videos)


def _add(book: dict[str, Entry], name: str, source: str, url: str, thumb: str = "",
         videos: int = 0, rank: float = 0.0, placeholder: bool = False) -> None:
    name = unescape(name)
    key = norm(name)
    if not key:
        return
    e = book.get(key)
    if e is None:
        e = book[key] = Entry(name=name)
    if source not in e.sites:
        e.sites[source] = url
        e.rank += rank
    e.videos = max(e.videos, videos)
    pref = THUMB_PREF["placeholder" if placeholder else source]
    if thumb and pref < e.thumb_rank:
        e.thumb, e.thumb_rank = thumb, pref


async def _gather_pages(urls: list[str], parse) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(i: int, url: str):
        async with sem:
            try:
                async with session() as s:
                    r = await s.get(url)
                if r.status_code == 200:
                    parse(i, r.text)
                else:
                    log.warning("directory: %s -> HTTP %s", url, r.status_code)
            except Exception as e:  # one bad page shouldn't sink the rest
                log.warning("directory: %s -> %s", url, e)

    await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))


def _xh_initials(text: str) -> dict:
    m = re.search(r"window\.initials\s*=\s*(\{.*?\});\s*</script>", text, re.S)
    return json.loads(m.group(1)) if m else {}


# ---------- performers ----------

async def _ph_stars(book: dict[str, Entry]) -> None:
    # One 8 MB download; under load Pornhub sometimes answers with an HTML stub instead.
    for attempt in range(3):
        try:
            async with session() as s:
                s.timeout = 120
                r = await s.get(PH + "/webmasters/stars_detailed")
                r.raise_for_status()
                stars = r.json().get("stars") or []
            break
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(30 * (attempt + 1))
    for item in stars:
        st = item.get("star") or {}
        videos = int(st.get("videos_count_all") or 0)
        if not videos:  # placeholder / junk profiles
            continue
        thumb = st.get("star_thumb") or ""
        _add(book, st.get("star_name", ""), "pornhub", st.get("star_url", ""), thumb, videos,
             placeholder="/pornstars/default/" in thumb)


async def _xh_stars(book: dict[str, Entry]) -> None:
    total = XH_PAGES * 60

    def parse(i: int, text: str):
        stars = (_xh_initials(text).get("layoutPage") or {}).get("pornstarListProps", {}).get("pornstars") or []
        for j, st in enumerate(stars):
            pos = i * 60 + j
            _add(book, st.get("name", ""), "xhamster", st.get("pageURL", ""),
                 st.get("imageThumbUrl") or st.get("logoThumbUrl") or "", int(st.get("videoCount") or 0),
                 rank=1 - pos / total)

    await _gather_pages([f"{XH}/pornstars" + (f"/{p}" if p > 1 else "") for p in range(1, XH_PAGES + 1)], parse)


_XV_IMG = re.compile(r'<img src="(https://[^"]+)"')


def _xv_parse(i: int, text: str, book: dict[str, Entry]) -> None:
    total = XV_PAGES * 80
    doc = HTMLParser(text)
    for j, card in enumerate(doc.css("div.thumb-block-profile")):
        a = card.css_first("p.profile-name a")
        if not a:
            continue
        # The image sits inside a document.write() script, not in the DOM.
        script = card.css_first("script")
        img = _XV_IMG.search(script.text() if script else "")
        counts = card.css_first("p.profile-counts")
        videos = parse_count(re.sub(r"[^\d,.KMB]", "", counts.text())) if counts else None
        _add(book, a.text(), "xvideos", XV + (a.attributes.get("href") or ""),
             img.group(1) if img else "", videos or 0, rank=1 - (i * 80 + j) / total)


async def _xv_stars(book: dict[str, Entry]) -> None:
    urls = [f"{XV}/pornstars-index"] + [f"{XV}/pornstars-index/{p}" for p in range(1, XV_PAGES)]
    await _gather_pages(urls, lambda i, t: _xv_parse(i, t, book))


# ---------- channels ----------

async def _ph_channels(book: dict[str, Entry]) -> None:
    # Ranked list, 36 per page (about 56 pages).
    def parse(i: int, text: str) -> int:
        root = HTMLParser(text).css_first("#filterChannelsSection")
        cards = root.css("div.channelsWrapper") if root else []
        for j, card in enumerate(cards):
            a = card.css_first("a.usernameLink")
            if not a:
                continue
            img = card.css_first(".avatar img")
            videos = 0
            for li in card.css(".descriptionContainer li"):
                label, span = li.text().strip(), li.css_first("span")
                if span and label.startswith("Videos") and not label.startswith("Videos Views"):
                    videos = parse_count(span.text()) or 0
            _add(book, a.text(), "pornhub", PH + (a.attributes.get("href") or ""),
                 img.attributes.get("src", "") if img else "", videos, rank=1 - (i * 36 + j) / 2000)
        return len(cards)

    # Pornhub throttles bursts with a truncated page (200, no cards), so go one
    # page at a time with a pause and retry those; the real end is a 404. A page
    # that still fails means this IP is blocked for now: stop and let the
    # caller retry later rather than hammering.
    async with session() as s:
        for page in range(1, 201):
            for attempt in range(3):
                await asyncio.sleep(1 + attempt * 8)
                try:
                    r = await s.get(f"{PH}/channels?o=rk&page={page}")
                except Exception:
                    continue
                if r.status_code == 404:
                    return
                if r.status_code == 200 and parse(page - 1, r.text):
                    break
            else:
                raise RuntimeError(f"pornhub channels blocked at page {page}")


async def _xh_channels(book: dict[str, Entry]) -> None:
    total = XH_PAGES * 60

    def parse(i: int, text: str):
        chans = (_xh_initials(text).get("layoutPage") or {}).get("channels") or []
        for j, ch in enumerate(chans):
            _add(book, ch.get("channelName", ""), "xhamster", ch.get("channelURL", ""),
                 ch.get("siteLogoURL") or "", int(ch.get("videoCount") or 0), rank=1 - (i * 60 + j) / total)

    await _gather_pages([f"{XH}/channels" + (f"/{p}" if p > 1 else "") for p in range(1, XH_PAGES + 1)], parse)


# ---------- index + lookup ----------

class Directory:
    def __init__(self, kind: str):
        self.kind = kind
        self.entries: list[Entry] = []   # sorted by popularity
        self.keys: list[str] = []        # norm(name), parallel to entries
        self.built = 0.0

    def load(self, rows: list[dict], built: float) -> None:
        entries = [Entry(name=r["name"], thumb=r["thumb"], videos=r["videos"], sites=r["sites"], rank=r["rank"]) for r in rows]
        entries.sort(key=lambda e: -e.score)
        self.entries, self.keys, self.built = entries, [norm(e.name) for e in entries], built

    def dump(self) -> list[dict]:
        return [{"name": e.name, "thumb": e.thumb, "videos": e.videos, "sites": e.sites, "rank": round(e.rank, 4)} for e in self.entries]

    def find(self, q: str, offset: int, limit: int) -> tuple[list[Entry], int]:
        q = norm(q)
        if not q:
            return self.entries[offset:offset + limit], len(self.entries)
        squashed = q.replace(" ", "")
        starts, words, contains = [], [], []
        for e, k in zip(self.entries, self.keys):
            if k.startswith(q) or k.replace(" ", "").startswith(squashed):  # "teamskeet" finds Team Skeet
                starts.append(e)
            elif (" " + q) in (" " + k):
                words.append(e)
            elif q in k:
                contains.append(e)
        hits = starts + words + contains
        return hits[offset:offset + limit], len(hits)


performers = Directory("performer")
channels = Directory("channel")


async def _build() -> bool:
    """Returns False when any list failed, so the caller retries sooner."""
    p_book: dict[str, Entry] = {}
    c_book: dict[str, Entry] = {}
    started = time.time()
    async def pornhub():  # one after the other: Pornhub throttles bursts
        failed = None
        for job, book in ((_ph_stars, p_book), (_ph_channels, c_book)):
            try:
                await job(book)
            except Exception as e:
                failed = e
        if failed:
            raise failed

    results = await asyncio.gather(
        pornhub(), _xh_stars(p_book), _xv_stars(p_book), _xh_channels(c_book),
        return_exceptions=True,
    )
    status["last_errors"] = [f"{type(r).__name__}: {r}"[:300] for r in results if isinstance(r, BaseException)]
    for r in results:
        if isinstance(r, BaseException):
            log.warning("directory: list failed: %s: %s", type(r).__name__, r)
    now = time.time()
    # Keep the old list if a rebuild came back much smaller (a site down or
    # blocking, a layout change); it is retried at the next refresh.
    for book, d in ((p_book, performers), (c_book, channels)):
        if book and len(book) >= len(d.entries) // 2:
            d.load([{**e.__dict__} for e in book.values()], now)
        else:
            log.warning("directory: kept the previous %s list (%d entries, new build had %d)", d.kind, len(d.entries), len(book))
    status["last_build"], status["seconds"] = now, now - started
    log.warning("directory: %d performers, %d channels in %.0fs",
                len(performers.entries), len(channels.entries), now - started)
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"built": now, "performers": performers.dump(), "channels": channels.dump()}, f)
        os.replace(tmp, CACHE_FILE)
    except OSError as e:
        log.warning("directory: cache not written: %s", e)
    return not any(isinstance(r, BaseException) for r in results)


def _load_cache() -> None:
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        performers.load(data["performers"], data["built"])
        channels.load(data["channels"], data["built"])
    except (OSError, ValueError, KeyError):
        pass


def rebuild_now() -> bool:
    """Start a rebuild without waiting for the daily timer (admin page). False if one is running."""
    if status["building"] or _wake is None:
        return False
    _wake.set()
    return True


async def keep_fresh() -> None:
    """Background task: load the cached copy, then rebuild whenever it is a day old."""
    global _wake
    _wake = asyncio.Event()
    _load_cache()
    while True:
        age = time.time() - min(performers.built, channels.built)
        wait = REFRESH_EVERY - age
        if _wake.is_set() or age >= REFRESH_EVERY or not performers.entries or not channels.entries:
            _wake.clear()
            status["building"] = True
            try:
                ok = await _build()
            finally:
                status["building"] = False
            wait = REFRESH_EVERY if ok else 3600
        try:
            await asyncio.wait_for(_wake.wait(), timeout=max(60, wait))
        except asyncio.TimeoutError:
            pass
