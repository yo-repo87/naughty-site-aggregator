"""Per-site health for the admin page: recent successes/failures, last error, speed."""
from __future__ import annotations

import time
from collections import deque

WINDOW = 24 * 3600
_events: dict[str, deque] = {}     # name -> (time, ok, ms)
_last_error: dict[str, tuple[float, str]] = {}
_last_ok: dict[str, float] = {}


def record(name: str, ok: bool, ms: float, error: str = "") -> None:
    now = time.time()
    q = _events.setdefault(name, deque(maxlen=5000))
    q.append((now, ok, ms))
    if ok:
        _last_ok[name] = now
    else:
        _last_error[name] = (now, error[:300])


def report(names) -> list[dict]:
    now = time.time()
    out = []
    for name in names:
        recent = [e for e in _events.get(name, ()) if now - e[0] < WINDOW]
        ok = [e for e in recent if e[1]]
        err = _last_error.get(name)
        out.append({
            "name": name, "ok_24h": len(ok), "failed_24h": len(recent) - len(ok),
            "avg_ms": round(sum(e[2] for e in ok) / len(ok)) if ok else None,
            "last_ok": _last_ok.get(name), "last_error_at": err[0] if err else None, "last_error": err[1] if err else "",
            # Healthy unless the most recent request failed.
            "state": "unknown" if not recent else ("ok" if recent[-1][1] else "failing"),
        })
    return out
