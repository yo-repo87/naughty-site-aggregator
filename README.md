# Naughty Site Aggregator

<img src="static/icon-512.png" width="96" alt="">

One search page across Pornhub, XVideos and SpankBang. It fetches **metadata only**
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

## What each source supports

| | Pornhub | XVideos | SpankBang |
|---|---|---|---|
| How | Public JSON API + HTML search, merged | HTML search page | HTML search page (Cloudflare; uses a Chrome-impersonating HTTP client) |
| Performer search | Real (`stars[]`) | Keyword | Keyword |
| Tag search | Real (`tags[]`), tags shown on cards | Keyword | Keyword (some cards carry one tag) |
| Channel shown | Yes, for results found by the HTML search | Yes | Yes |
| Server-side length filter | Min 10/20/30 min | Buckets: 1–3, 3–10, 10–20, 10+, 20+ min | Min 10/20/40 min |
| Length precision | Exact (mm:ss) | Rounded to the minute (~) | Rounded to the minute (~) |

The precise length slider, title-word excludes, channel include/hide and sorting all
run in the browser, so they update as you change them. When you widen the length range
past what the sites were asked for, the app searches again on its own.

## API

`GET /api/search?q=&actor=&channel=&tags=a,b&min_sec=&max_sec=&sources=pornhub,xvideos,spankbang&page=1&pages=2`

## Caveats

- These are scrapers. If a site changes its HTML, its parser in `aggregator/sources/` needs updating.
- Some studio videos turn off embedding. The player then shows "watch on site"; use **Open on site**.
- Pornhub is blocked in some regions (several US states, for example). It will show as an error source there.
