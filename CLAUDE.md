# NaughtySiteAggregator

Metadata-only search across twelve free tube sites (see README.md for the per-site table). FastAPI backend
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
  there only work there. NPM basic auth is still in front of everything.
- Rate limits are in memory (per process): register 5/10 min, login 10/min per IP; accounts lock
  for 15 min after 8 wrong passwords. Sensitive changes need a "confirm it's you" within 15 min.
- OAuth providers: none configured yet. Redirect URLs: `<PUBLIC_URL>/api/auth/oauth/<provider>/callback`.
  Accounts are never auto-linked by email (takeover risk): link from the Security tab.
- Tests used throwaway accounts on the live database and deleted them; 0 users after testing.
