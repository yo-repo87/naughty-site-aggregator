# NaughtySiteAggregator

Metadata-only search across twelve free tube sites (see README.md for the per-site table). FastAPI backend
(`aggregator/`, one adapter per site in `aggregator/sources/`), static UI in `static/`.
Nothing is downloaded or proxied; the UI uses each site's own thumbnails, previews and embeds.

## Running

- Docker: `docker compose up -d --build` -> container `naughty_aggregator_app`, host port **3160**.
- Dev without Docker: `./run.sh` (port 8000). Python deps install into `.venv` (created with
  `--without-pip`; the system lacks python3-venv/ensurepip, so pip runs as `pip --python .venv/bin/python`).

## Public access (Cloudflare + NPM) — set up 2026-10-15

Public URL: **https://search.selfhosted.vip**. Since 2026-10-17 there is no NPM basic auth: the app's own
accounts gate it (`REQUIRE_LOGIN=true` in `.env`; open registration).

Path: Cloudflare edge (HTTPS) -> tunnel `selfhosted` (`ab4001da-dc93-4228-9fa3-6790300afb97`,
connector = `redroid-cloudflared` container) -> `http://192.168.50.211:80` (Nginx Proxy Manager,
proxy host id **28**, no access list; the old list id **2** "Aggregator" still exists, unused) -> `http://192.168.50.211:3160`.

- Same pattern as EquipmentMgr: proxied CNAME `search.selfhosted.vip` -> `<tunnel id>.cfargotunnel.com`,
  ingress rule placed before the `http_status:404` catch-all, NPM host with **no certificate and
  Force SSL off** (the tunnel speaks plain HTTP to NPM; Force SSL would loop).
- Old basic-auth login is in `.access_password` (mode 600). To put it back: NPM host 28 -> Access List -> Aggregator.
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
- PornTrex: only cards inside `#list_videos_videos`; length buckets are path segments
  (`ten-min`, `ten-thirty-min`, `thirty-all-min`) before the page number; unrated shows 0%.
- HQPorner: titles rarely contain the query (search matches site tags); no keywords -> `/top/week`;
  the player is a per-video third-party iframe, so `embed_url` is empty (opens the site).
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

## Accounts — added 2026-10-15

- Code: `aggregator/auth/` (config, crypto, db + `schema.sql`, sessions, challenges, passkeys,
  oauth, routes, web). UI: `static/auth.js` (+ styles at the end of `static/style.css`).
  Accounts switch on only when `DATABASE_URL`, `DATA_KEY` and `INDEX_KEY` are set.
- Config lives in `.env` (mode 600, gitignored, not in the image). **`DATA_KEY` and `INDEX_KEY`
  must never change or be lost**: every personal field is AES-256-GCM under `DATA_KEY`, and email
  lookups / session / backup-code hashes are HMACs under `INDEX_KEY`. There is no key rotation yet
  (ciphertexts carry a version byte for when there is).
- Database: shared Postgres (`/home/pi/Claude/shared-postgres`), database + role `naughtyaggregator`,
  password in `.env`. Schema is applied on every start (idempotent, no migration tool). Added to the
  shared backup sidecar's `POSTGRES_DB` list (compose backup: `docker-compose.yml.bak-before-naughtyaggregator`).
- **TLS on the shared Postgres (changed 2026-10-15, affects the whole server):** self-signed EC cert
  `server.crt`/`server.key` in its data dir (CN/SAN `shared_postgres`, valid to 2036), `ssl = on`,
  `ssl_min_protocol_version = TLSv1.2` via `ALTER SYSTEM` + reload (no restart; other apps
  unaffected, they connect as before). `pg_hba.conf` has `hostssl` for `naughtyaggregator` and
  `hostnossl … reject`, so this app can only connect with TLS (backup: `pg_hba.conf.bak-before-naughtyaggregator`).
  The app uses `DB_SSL=verify-full` against `certs/shared-postgres-ca.crt` (copy of `server.crt`,
  gitignored, mounted read-only at `/certs`). To undo TLS server-wide:
  `ALTER SYSTEM RESET ssl; ALTER SYSTEM RESET ssl_min_protocol_version; SELECT pg_reload_conf();`
  and restore the pg_hba backup (then set `DB_SSL=disable` here).
- Public site: `PUBLIC_URL=https://search.selfhosted.vip`, `REQUIRE_HTTPS=true` (LAN http redirects
  there; localhost exempt for the health check). WebAuthn RP id = that hostname, so passkeys made
  there only work there. `REQUIRE_LOGIN=true`: search and thumbnails need a signed-in session.
- Rate limits are in memory (per process): register 5/10 min, login 10/min per IP; accounts lock
  for 15 min after 8 wrong passwords. Sensitive changes need a "confirm it's you" within 15 min.
- OAuth providers: none configured yet. Redirect URLs: `<PUBLIC_URL>/api/auth/oauth/<provider>/callback`.
  Accounts are never auto-linked by email (takeover risk): link from the Security tab.
- Tests used throwaway accounts on the live database and deleted them; 0 users after testing.

## Performer / channel dropdowns — added 2026-10-17

- Backend `aggregator/directory.py`, endpoint `/api/suggest?kind=performer|channel&q=&offset=&limit=`
  (login-gated like search). UI `static/combo.js` (+ `.combo*` styles); the dropdown lists are
  `#actor-suggest` / `#channel-suggest` (`#channel-list` is the sidebar facet list).
- Sources: Pornhub `/webmasters/stars_detailed` (every performer, one 8 MB JSON; entries with 0 videos
  dropped), top 20 pages of xHamster `/pornstars` + `/channels` (`window.initials`), top 15 pages of
  XVideos `/pornstars-index/N` (image is inside a `document.write` script), all of Pornhub
  `/channels?o=rk&page=N` (36/page, ~56 pages, 404 at the end). Merged by normalised name.
- Rebuilt daily in the background (~75 s); cached as `aggregator-directory-v1.json` in `DIRECTORY_DIR`
  (`/data`, Docker volume `aggregator-data`). A failed list retries after 1 h and a build less than
  half the previous size is discarded.
- Pornhub throttles bursts with a ~1.4 KB HTML stub (HTTP 200, no cards): channel pages are fetched one
  at a time with retries, and the crawl aborts when a page stays blocked. Repeated full crawls while
  testing got this IP blocked for a while — test against the cached file instead.

## Favorite performers — added 2026-10-17

- Stored as `favorites: [{name, thumb}]` inside the normal settings blob (`agg-settings-v1` in
  localStorage, synced encrypted via `/api/me/settings`; `SETTINGS_MAX` raised to 256 KB, UI caps at 300).
  No new table. Logic: `favs` / `window.AggFavs` + `renderFavs()` in `static/app.js`; stars and the
  Favorites section of the dropdown in `static/combo.js`.
- Sync rules (`pullSettings` in `static/auth.js`): every page load of a signed-in device pulls the
  account copy (account wins, page reloads if it differs). Only right after signing in are this
  device's favorites merged in (union), so favorites starred while signed out survive but removals
  made on another device are not undone.

## Static files and caching — 2026-10-17

- Cloudflare caches `.js`/`.css` at the edge (and forces `max-age=14400` on them). That once served a
  new `index.html` with an old `app.js`, so new features silently did nothing. Now `/` is rendered by
  `_index_html()` in `aggregator/app.py`, which appends `?v=<sha256 prefix>` to every `"/static/…"`
  reference; versioned files get `immutable`, everything else `no-cache`. Reference new static files
  from `index.html` as `"/static/name"` and they're versioned automatically.
- `sw.js` loads the page network-first (cache only offline); bump `CACHE` when changing `sw.js` itself.

## Send to, feed, library, alerts, admin, plug-ins — added 2026-10-22

- **Targets** (`aggregator/targets/`: metube, jdownloader, webhook, discord, ntfy; same shape for plug-ins).
  Per-user rows in `user_targets` (config sealed per row, AAD `user_targets.config:<uid>:<id>`); routes in
  `aggregator/auth/send.py`. The first MeTube version's `users.metube_enc` is migrated on start-up
  (`migrate_metube`); the owner's MeTube moved over that way. `/api/me` returns `targets` and `is_admin`;
  `body.has-targets` shows the Download buttons. All outgoing calls go through `aggregator/net.py`
  `guarded_request` (public IPs only unless `SEND_PRIVATE_HOSTS`, pinned IP, no redirects). Unset here:
  the owner's MeTube is used via `https://metube.selfhosted.vip` (NPM host 27, access list 1 basic auth).
- **Search core** moved to `aggregator/searching.py` (page cache + `health.record` for the admin page).
  `Query.sort = "new"`: adapters with `SORTS = {"new"}` (pornhub API `ordering=newest` / HTML `o=mr`,
  xvideos `sort=uploaddate`, xhamster `sort=newest`, porntrex `sort_by=post_date`, redtube
  `ordering=newest`, eporner `order=latest`, youporn `/search/date/`). Tube8, SpankBang, XNXX, YouJizz,
  HQPorner had no working newest-first parameter (probed 2026-10-22).
- **Feed** `aggregator/feed.py`: shared per-name cache in `/data/aggregator-feed-v1.json`, one name at a time
  with a 2 s pause (Pornhub throttling); first lookup of a name marks videos `first_seen = 0` (not "new").
- **Alerts** `aggregator/auth/alerts.py`: table `alerts`, loop every 15 min, runs due ones (`ALERT_HOURS`);
  first run only records; notifies via a target's `notify()`; link = `PUBLIC_URL/?actor=…&sort=new`.
- **Admin** `aggregator/auth/admin.py`: `ADMIN_EMAILS` (set to the owner's account email in `.env`),
  registration switch stored in `app_settings`, users, site health, directory rebuild (`directory.rebuild_now`).
- **Plug-ins** `aggregator/plugins.py` loads `/plugins/{sources,targets}/*.py` at start-up (compose mounts
  `./plugins`, gitignored; templates in `plugins-example/`).
- **Client**: `static/library.js` (Watch later, watched, sent + MeTube progress, select mode/bulk bar, saved
  searches, feed view) shares app.js's globals; app.js `init()` runs on DOMContentLoaded so library.js is
  loaded first. Settings blob now also holds favoriteChannels, later, watched, sent, saved, feedSeenAt,
  blockPerformers/Tags, hideWatched, mergeDups (`SETTINGS_MAX` 1 MB). The URL mirrors the current search.
- Tested 2026-10-22 with a throwaway admin account and a local fake receiver (webhook/ntfy/JDownloader)
  plus a test source plug-in and a broken one; all removed afterwards (1 user, 1 target, 0 alerts left).

## Bundled optional plug-ins — added 2026-10-22

- `plugins-available/{sources,targets}/` (copied into the image): sites tnaflix, empflix (shared parser
  `aggregator/sources/flix.py`), drtuber; targets telegram, gotify, pushover, apprise, linkding, karakeep.
  Off by default. `plugins.set_enabled()` registers/unregisters them live; the admin switch stores the list
  in `app_settings.enabled_plugins` (overrides env `ENABLED_PLUGINS`). Own plug-ins in `/plugins` with the
  same name win.
- Tested 2026-10-22: the three sites live; Gotify, linkding (needs a token from its newer ApiToken model)
  and Apprise against throwaway local containers (removed, images deleted); Telegram/Pushover against the
  real APIs with fake keys (error paths) plus captured request bodies; Karakeep request bodies only.
  None are enabled on this server (the test's `enabled_plugins` row was deleted).

## Download updates — added 2026-10-22

- `aggregator/auth/downloads.py`: per-user `users.dl_notify_target` / `dl_notify_events` ("start,finish");
  `send.py` calls `downloads.on_sent()` after a successful send. "Started" is coalesced per user over 6 s;
  "finish" creates a `download_watches` row (url/title sealed) when the target has `status()` (MeTube),
  checked every 60 s by `downloads.loop()`, dropped after 2 days. MeTube `status()` now returns `added`
  (its timestamp) so an older finished copy of the same URL isn't reported.
- Tested 2026-10-22 with a throwaway account, a local fake ntfy and the owner's MeTube (thumbnail send →
  "started" + "failed"; "finished" and coalescing via a patched status); MeTube entry/file, account and
  temporary SEND_PRIVATE_HOSTS removed.
