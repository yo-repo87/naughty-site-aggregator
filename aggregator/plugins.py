"""Plug-ins: extra site adapters and 'send to' targets dropped into PLUGINS_DIR
(default /plugins, mounted read-only in Docker), loaded once at start-up.

    PLUGINS_DIR/sources/<name>.py   a site adapter (same shape as aggregator/sources/*.py)
    PLUGINS_DIR/targets/<name>.py   a send-to target (see aggregator/targets/__init__.py)

A plug-in with the same NAME / KIND as a built-in replaces it (e.g. to patch a
site whose layout changed before the image is updated). Plug-ins are ordinary
Python running inside the server with full access to it: only install code you
trust. A plug-in that fails to load is skipped and reported on the admin page.
See plugins-example/ in the repository for templates.
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

loaded: list[dict] = []   # {"kind", "file", "name", "ok", "error"} for the admin page
SOURCE_INFO: dict[str, dict] = {}  # label/colour of plug-in sources, for the UI


def _import(path: Path, kind: str):
    mod_name = f"aggregator_plugin_{kind}_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module  # dataclasses and relative lookups need it registered
    spec.loader.exec_module(module)
    return module


def _source(m) -> str:
    name = getattr(m, "NAME", "")
    if not (isinstance(name, str) and name.isidentifier() and callable(getattr(m, "search", None))):
        raise ValueError("needs NAME (a simple identifier) and an async search(query) function")
    SOURCES[name] = m
    for d in getattr(m, "DOMAINS", ()):
        if d not in SITE_DOMAINS:
            SITE_DOMAINS.append(d.lower())
    SOURCE_INFO[name] = {"label": str(getattr(m, "LABEL", name))[:30], "color": str(getattr(m, "COLOR", "#64748b"))[:20],
                         "ink": str(getattr(m, "INK", "#fff"))[:20]}
    return name


def _target(m) -> str:
    kind = getattr(m, "KIND", "")
    if not (isinstance(kind, str) and kind.isidentifier() and hasattr(m, "check") and (hasattr(m, "send") or hasattr(m, "notify"))):
        raise ValueError("needs KIND, LABEL, FIELDS, check() and send() and/or notify()")
    TARGETS[kind] = m
    return kind


def load() -> None:
    for kind, register in (("sources", _source), ("targets", _target)):
        folder = PLUGINS_DIR / kind
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            entry = {"kind": kind[:-1], "file": path.name, "name": "", "ok": False, "error": ""}
            try:
                entry["name"] = register(_import(path, kind))
                entry["ok"] = True
                log.warning("plugin loaded: %s/%s", kind, path.name)
            except Exception as e:  # a broken plug-in must not take the app down
                entry["error"] = f"{type(e).__name__}: {e}"[:300]
                log.warning("plugin %s/%s failed: %s", kind, path.name, entry["error"])
            loaded.append(entry)
