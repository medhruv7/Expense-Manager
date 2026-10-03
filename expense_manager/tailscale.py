"""Helpers for the local Tailscale CLI (address, owner identity, `tailscale serve` URL)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time

from . import config

_cache: dict[str, tuple[float, object]] = {}


def _cli(*args: str, ttl: float = 60) -> str | None:
    key = " ".join(args)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]  # type: ignore[return-value]
    exe = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        out = None
    _cache[key] = (time.time(), out)
    return out


def ip() -> str | None:
    out = (_cli("ip", "-4") or "").split()
    return out[0] if out else None


def owner_logins() -> set[str]:
    """Tailscale logins allowed to use the dashboard without the token (default: this Mac's owner)."""
    if config.TAILSCALE_USERS:
        return set(config.TAILSCALE_USERS)
    try:
        d = json.loads(_cli("status", "--json", ttl=600) or "{}")
        user = d.get("User", {}).get(str(d["Self"]["UserID"]), {})
        return {user["LoginName"].lower()} if user.get("LoginName") else set()
    except (ValueError, KeyError, TypeError):
        return set()


def serve_url() -> str | None:
    """HTTPS URL under which `tailscale serve` proxies to this app, e.g. https://mac.tailnet.ts.net/expenses."""
    try:
        d = json.loads(_cli("serve", "status", "--json") or "{}")
    except ValueError:
        return None
    found = []
    for hostport, web in (d.get("Web") or {}).items():
        for path, h in (web.get("Handlers") or {}).items():
            if (h.get("Proxy") or "").rstrip("/").endswith(f":{config.PORT}"):
                host = hostport[:-4] if hostport.endswith(":443") else hostport
                found.append((path != "/", f"https://{host}{path.rstrip('/')}"))
    # Prefer a dedicated origin (path "/"): an installed PWA scoped to "/" on the same origin would capture sub-paths.
    return sorted(found)[0][1] if found else None
