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

## Accounts (optional)

Without a database the app is search-only. With one, people can create accounts to sync their
filters, hidden channels and source choices across devices.

- **Sign in:** email + password (Argon2id), **passkeys** (fingerprint / face / device PIN, no
  password), **security keys** (USB/NFC; with their PIN they sign in on their own), and
  **Google / Facebook / Discord** — each button appears only when its keys are configured.
- **Two-step sign-in:** authenticator app (TOTP, replay-protected) or a security key after the
  password, plus 10 single-use backup codes.
- **Security tab** for each user: passkeys, security keys, authenticator app, backup codes,
  password, connected accounts, signed-in devices (sign out remotely) and recent activity.
  Sensitive changes ask you to confirm it's you; you can't remove your last way to sign in.
- **Encryption:** email, name, authenticator secret, provider ids, device/IP info and synced
  settings are AES-256-GCM encrypted (each value bound to its row); email lookups use keyed
  hashes; session tokens and backup codes are stored only as keyed hashes. The database
  connection can require TLS with certificate verification (`DB_SSL=verify-full`). Cookies are
  `__Host-`, `Secure`, `HttpOnly`, `SameSite=Lax`; responses carry HSTS and a strict CSP;
  cross-site requests are refused.

### Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres URL. Leave empty for search-only. |
| `DB_SSL` | `disable`, `require` or `verify-full` (recommended). |
| `DB_SSL_ROOT_CERT` | CA/server certificate used by `verify-full`. |
| `DATA_KEY` / `INDEX_KEY` | Two different 32-byte keys, base64url (`python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`). **Never change or lose them** — encrypted data can't be read without `DATA_KEY`. |
| `PUBLIC_URL` | The public HTTPS address (passkeys, OAuth redirects, secure cookies). |
| `REQUIRE_HTTPS` | `true` redirects any other address to `PUBLIC_URL`. |
| `REQUIRE_LOGIN` | `true` means searching needs an account. |
| `ALLOW_REGISTRATION` | `false` stops new sign-ups. |
| `GOOGLE_CLIENT_ID` / `_SECRET`, `FACEBOOK_…`, `DISCORD_…` | Optional providers. |

Provider redirect URL to register: `<PUBLIC_URL>/api/auth/oauth/<google|facebook|discord>/callback`

- **Google:** Google Cloud Console → APIs & Services → Credentials → OAuth client ID (Web).
- **Facebook:** developers.facebook.com → Create app → Facebook Login → Valid OAuth Redirect URIs.
- **Discord:** discord.com/developers → Applications → OAuth2 → Redirects.

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
