# NaughtySiteAggregator

Metadata-only search across ten free tube sites (see README.md for the per-site table). FastAPI backend
(`aggregator/`, one adapter per site in `aggregator/sources/`), static UI in `static/`.
Nothing is downloaded or proxied; the UI uses each site's own thumbnails, previews and embeds.

## Running

- Docker: `docker compose up -d --build` -> container `naughty_aggregator_app`, host port **3160**.
- Dev without Docker: `./run.sh` (port 8000). Python deps install into `.venv` (created with
  `--without-pip`; the system lacks python3-venv/ensurepip, so pip runs as `pip --python .venv/bin/python`).

## Public access (Cloudflare + NPM) — set up 2026-10-15

Public URL: **https://search.selfhosted.vip** (HTTP Basic auth via NPM).

Path: Cloudflare edge (HTTPS) -> tunnel `selfhosted` (`ab4001da-dc93-4228-9fa3-6790300afb97`,
connector = `redroid-cloudflared` container) -> `http://192.168.50.211:80` (Nginx Proxy Manager,
proxy host id **28**, access list id **2** "Aggregator") -> `http://192.168.50.211:3160`.

- Same pattern as EquipmentMgr: proxied CNAME `search.selfhosted.vip` -> `<tunnel id>.cfargotunnel.com`,
  ingress rule placed before the `http_status:404` catch-all, NPM host with **no certificate and
  Force SSL off** (the tunnel speaks plain HTTP to NPM; Force SSL would loop).
- Login: username/password in `.access_password` (mode 600, excluded from Docker builds).
  Change it in NPM -> Access Lists -> Aggregator.
- Credentials are not stored here. NPM admin password: `/home/pi/Claude/HeroBuilder/.npm_admin_password`.
  Cloudflare API token: the DNS-challenge credential stored in NPM's
  `/portainer/Files/AppData/Config/nginx-proxy-manager/data/database.sqlite`. Account id
  `06c7a58f6d1c27415e28aabb8b0baacd`, zone `selfhosted.vip`.
- The tunnel's ingress list is shared with ~29 other rules: always GET it and PUT the whole list.
  The list before this change is in `.tunnel-backup-selfhosted-before-aggregator.json`.
- To take it offline publicly: delete the DNS record, the tunnel rule, NPM host 28 (and access list 2).

## Site parser notes

- SpankBang is behind Cloudflare: requests use curl_cffi with Chrome impersonation. Real results
  start at `data-testid="search-result"` (a sponsored strip precedes it). The name under each card
  is badged `channel` / `pornstar` / `tag`; only channel goes into `uploader`.
- Pornhub: webmasters JSON API (tags, performers) merged by viewkey with the HTML search
  (`ul#videoSearchResult`, uploader/channel, `min_duration`).
- XVideos: `div.thumb-block[data-eid]`, `durf` length buckets, pages are 0-based (`p`). An empty
  `k` is a 500: keyword-less browsing uses the homepage, then `/new/N`.
- Tube8 + YouPorn share `aylo_html.py` (`article.video-box`, `min_minutes`/`max_minutes`, `page`).
- RedTube: JSON API like Pornhub's, but `stars[]` is ignored (performer goes into the text).
- Eporner: JSON API; decode `r.content` as UTF-8 (the charset header is unreliable); rate is 0-5.
- XNXX: path `/search/<bucket>/<query>/<page-1>`; embed is `/embedframe/<data-eid>`.
- xHamster: parse `window.initials` JSON (`searchResult.videoThumbProps`); embed uses the code
  at the end of `pageURL`, not the numeric id.
- YouJizz: lexbor lower-cases attributes (`data-videoid`); embed URL is a 500, so `embed_url`
  is empty and the UI opens the site instead.
- Pornhub `pix-*` thumbnails need a Pornhub Referer: routed through `/api/thumb` (allow-listed).
- Client settings store `sourcesOff` so newly added sites start enabled; legacy `sources`
  (enabled list) is converted once using `LEGACY_SOURCES` in `static/app.js`.

## Image and Portainer template — added 2026-10-15

- Repo: https://github.com/yo-repo87/naughty-site-aggregator (public). Push with the token in
  `gitkey` via a one-off credential helper; it is not stored in the remote URL.
- Every push to `main` runs `.github/workflows/docker.yml`, publishing
  `ghcr.io/yo-repo87/naughty-site-aggregator:latest` (+ `:<sha>`) for linux/amd64 and linux/arm64.
  The package is public (anonymous pulls work).
- Portainer template entry lives in the `pi-hosted` fork (`/home/pi/Claude/AppRepo/pi-hosted`, remote
  `fork`): `template/apps/naughty-site-aggregator.json`, `stack/naughty-site-aggregator.yml`,
  `images/naughty-site-aggregator.png`. After editing, run `build/joinApps.sh` and push `fork master`.
- Icon source: `static/icon.svg`; PNGs (`icon-512.png`, `apple-touch-icon.png`) are rendered from it
  with headless Chromium.
