"""Plug-ins: extra site adapters and 'send to' targets.

Two kinds:
- Bundled optional plug-ins ship in the image (plugins-available/{sources,targets}/).
  They are off until switched on: on the admin page (saved in the database), or
  with ENABLED_PLUGINS=telegram,tnaflix,... (used until the admin page changes it).
- Your own plug-ins: files dropped into PLUGINS_DIR (default /plugins, mounted
  read-only in Docker), always loaded at start-up:
      PLUGINS_DIR/sources/<name>.py   a site adapter (same shape as aggregator/sources/*.py)
      PLUGINS_DIR/targets/<name>.py   a send-to target (see aggregator/targets/__init__.py)
  One with the same NAME / KIND as a built-in or bundled one replaces it (e.g. to
  patch a site whose layout changed before the image is updated).

Plug-ins are ordinary Python running inside the server with full access to it:
only install code you trust. One that fails to load is skipped and reported on
the admin page. See plugins-example/ in the repository for templates.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

from .sources import SITE_DOMAINS, SOURCES
from .targets import TARGETS

log = logging.getLogger("aggregator.plugins")
PLUGINS_DIR = Path(os.environ.get("PLUGINS_DIR") or "/plugins")
AVAILABLE_DIR = Path(__file__).resolve().parent.parent / "plugins-available"

loaded: list[dict] = []    # your own plug-ins: {"kind", "file", "name", "ok", "error"} for the admin page
bundled: dict[str, dict] = {}  # "targets/telegram" -> {id, kind, name, label, description, module, enabled, error}
SOURCE_INFO: dict[str, dict] = {}  # label/colour of plug-in sources, for the UI
_added_domains: dict[str, list[str]] = {}
_own_names: set[tuple[str, str]] = set()  # (kind, name) taken by your own plug-ins


def _import(path: Path, prefix: str):
    mod_name = f"aggregator_plugin_{prefix}_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module  # dataclasses and relative lookups need it registered
    spec.loader.exec_module(module)
    return module


def _check(kind: str, m) -> str:
    if kind == "sources":
        name = getattr(m, "NAME", "")
        if not (isinstance(name, str) and name.isidentifier() and callable(getattr(m, "search", None))):
            raise ValueError("needs NAME (a simple identifier) and an async search(query) function")
        return name
    name = getattr(m, "KIND", "")
    if not (isinstance(name, str) and name.isidentifier() and hasattr(m, "check") and (hasattr(m, "send") or hasattr(m, "notify"))):
        raise ValueError("needs KIND, LABEL, FIELDS, check() and send() and/or notify()")
    return name


def _register(kind: str, m) -> None:
    name = _check(kind, m)
    if kind == "targets":
        TARGETS[name] = m
        return
    SOURCES[name] = m
    added = [d.lower() for d in getattr(m, "DOMAINS", ()) if d.lower() not in SITE_DOMAINS]
    SITE_DOMAINS.extend(added)
    _added_domains[name] = added
    SOURCE_INFO[name] = {"label": str(getattr(m, "LABEL", name))[:30], "color": str(getattr(m, "COLOR", "#64748b"))[:20],
                         "ink": str(getattr(m, "INK", "#fff"))[:20]}


def _unregister(kind: str, m) -> None:
    name = _check(kind, m)
    registry = TARGETS if kind == "targets" else SOURCES
    if registry.get(name) is not m:
        return
    del registry[name]
    if kind == "sources":
        SOURCE_INFO.pop(name, None)
        for d in _added_domains.pop(name, []):
            if d in SITE_DOMAINS:
                SITE_DOMAINS.remove(d)


def _load_own() -> None:
    for kind in ("sources", "targets"):
        folder = PLUGINS_DIR / kind
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            entry = {"kind": kind[:-1], "file": path.name, "name": "", "ok": False, "error": ""}
            try:
                m = _import(path, kind)
                _register(kind, m)
                entry["name"], entry["ok"] = _check(kind, m), True
                _own_names.add((kind, entry["name"]))
                log.warning("plugin loaded: %s/%s", kind, path.name)
            except Exception as e:  # a broken plug-in must not take the app down
                entry["error"] = f"{type(e).__name__}: {e}"[:300]
                log.warning("plugin %s/%s failed: %s", kind, path.name, entry["error"])
            loaded.append(entry)


def _catalog() -> None:
    for kind in ("sources", "targets"):
        folder = AVAILABLE_DIR / kind
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            pid = f"{kind}/{path.stem}"
            entry = {"id": pid, "kind": kind[:-1], "name": path.stem, "label": path.stem, "description": "",
                     "module": None, "enabled": False, "error": ""}
            try:
                m = _import(path, "bundled_" + kind)
                entry.update(module=m, name=_check(kind, m), label=str(getattr(m, "LABEL", path.stem)),
                             description=str(getattr(m, "DESCRIPTION", "")))
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {e}"[:300]
            bundled[pid] = entry


def _resolve(names) -> set[str]:
    """'telegram' or 'targets/telegram' -> bundled ids."""
    out = set()
    for n in names:
        n = n.strip().lower()
        out.update(pid for pid, e in bundled.items() if n in (pid, e["name"], pid.split("/", 1)[1]))
    return out


def set_enabled(names) -> list[str]:
    """Switch bundled plug-ins so exactly these are on. Returns the ids now enabled."""
    want = _resolve(names)
    for pid, e in bundled.items():
        m, kind = e["module"], pid.split("/", 1)[0]
        if m is None:
            continue
        on = pid in want and (kind, e["name"]) not in _own_names  # your own file of the same name wins
        if on and not e["enabled"]:
            _register(kind, m)
        elif not on and e["enabled"]:
            _unregister(kind, m)
        e["enabled"] = on
    return sorted(pid for pid, e in bundled.items() if e["enabled"])


def load() -> None:
    _load_own()
    _catalog()
    set_enabled(n for n in os.environ.get("ENABLED_PLUGINS", "").split(",") if n.strip())


def describe_bundled() -> list[dict]:
    return [{k: v for k, v in e.items() if k != "module"} | {"overridden": (e["id"].split("/")[0], e["name"]) in _own_names}
            for e in bundled.values()]
