# Plug-ins

Drop Python files into the folder mounted at `/plugins` (with Docker Compose: `./plugins` next to
`docker-compose.yml`) and restart the app. The admin page (Account → Admin → Plug-ins) shows what
loaded and why anything failed.

```
plugins/
  sources/<name>.py   an extra site to search (or a fixed copy of a built-in one: same NAME replaces it)
  targets/<name>.py   an extra "Send to" destination (or alert channel) users can connect in their Profile
```

Plug-ins run inside the server with full access to it (its keys and database included).
Only install code you have read and trust.

Templates: `sources/example_site.py` and `targets/example_target.py` here. Copy one into your
`plugins/` folder, rename it, and fill it in. The built-in adapters in `aggregator/sources/` and
targets in `aggregator/targets/` are the best worked examples.
