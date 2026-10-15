# Naughty Site Aggregator

<img src="static/icon-512.png" width="96" alt="">

One search page across twelve free tube sites: Pornhub, XVideos, SpankBang, Tube8, YouPorn,
RedTube, Eporner, XNXX, xHamster, YouJizz, PornTrex and HQPorner. It fetches **metadata only**
(titles, lengths, thumbnails, links). It never downloads or re-hosts any video.
Videos play in each site's own embed player, or open on the site.

## Run

```bash
docker run -d -p 3160:8000 --restart unless-stopped ghcr.io/yo-repo87/naughty-site-aggregator:latest
# or build locally:
docker compose up -d --build     # http://<host>:3160
# or, without Docker:
./run.sh                         # http://localhost:8000
```

## Phones and tablets

Phones, tablets (touch without a mouse) and narrow windows get a mobile layout automatically;
desktops keep the sidebar layout. The mobile layout has a compact search bar (⋯ opens
performer / channel / tags), swipeable length chips, filters in a slide-up sheet, a bottom bar,
infinite scroll, a full-screen player, Back-button support, and optional auto-play previews.
It is installable ("Add to Home Screen") and opens like an app.

## What each source supports

| Site | How | Performer / tag search | Channel shown | Length filter on the site | Length precision | In-page player |
|---|---|---|---|---|---|---|
| Pornhub | JSON API + HTML search, merged | Real (`stars[]`, `tags[]`) | Yes (HTML hits) | Min 10/20/30 min | Exact | Yes |
| XVideos | HTML | Keywords | Yes | Buckets 1–3, 3–10, 10–20, 10+, 20+ | Minute (~) | Yes |
| SpankBang | HTML (Cloudflare; Chrome-impersonating client) | Keywords | Yes | Min 10/20/40 min | Minute (~) | Yes |
| Tube8 | HTML | Keywords; performers from cards | Yes | Exact min/max minutes (≤60) | Exact | Yes |
| YouPorn | HTML (same platform as Tube8) | Keywords; performers from cards | Yes | Exact min/max minutes (≤60) | Exact | Yes |
| RedTube | JSON API | Real tags (`tags[]`); performer as keyword | No | None | Exact | Yes |
| Eporner | JSON API | Keywords; keyword list shown as tags | No | None | Exact | Yes |
| XNXX | HTML (same platform as XVideos) | Keywords | Creator uploads | Buckets 0–10, 10–20, 10+, 20+ | Minute (~) | Yes |
| xHamster | JSON embedded in the search page | Keywords | Yes | Min/max minutes | Exact | Yes |
| YouJizz | HTML | Keywords | No | None | Exact | No (opens the site) |
| PornTrex | HTML (KVS script) | Keywords | No | Buckets <10, 10–30, 30+ | Exact | Yes |
| HQPorner | HTML (HD-only site) | Keywords (matched against the site's tags) | No | None | Exact | No (opens the site) |

The precise length slider, title-word excludes, channel include/hide and sorting all
run in the browser, so they update as you change them. When you widen the length range
past what the sites were asked for, the app searches again on its own.

Pornhub's newer `pix-*` thumbnails only load with a Pornhub referer, so `/api/thumb`
relays those thumbnail images (allow-listed `phncdn.com` hosts, images only, size-capped).
Videos are never relayed.

## API

`GET /api/search?q=&actor=&channel=&tags=a,b&min_sec=&max_sec=&sources=pornhub,xvideos,...&page=1&pages=2`
(`sources` defaults to all twelve)

## Caveats

- These are scrapers. If a site changes its HTML, its parser in `aggregator/sources/` needs updating.
- Some studio videos turn off embedding. The player then shows "watch on site"; use **Open on site**.
- Pornhub and its sister sites (Tube8, YouPorn, RedTube) are blocked in some regions (several US states, for example). They show as error sources there.
